"""Persistent remote-job example. Run on the third-party server, not in plugin.py."""
import hashlib
import json
import os
import tempfile
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from fastmcp import FastMCP
from artifact_io import ArtifactIO

token = os.environ.get('EXAMPLE_MCP_TOKEN', '')
auth = None
if token:
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
    auth = StaticTokenVerifier(tokens={token: {'client_id': 'example', 'scopes': []}})
mcp = FastMCP('Third-party compute example', auth=auth)
root = Path(os.environ.get('EXAMPLE_DATA_ROOT', './example-compute-data')).resolve()
personal = os.environ.get('EXAMPLE_AUTH_MODE', 'shared')
if personal not in {'personal-tokens', 'shared'}:
    raise ValueError('unknown EXAMPLE_AUTH_MODE')
if personal == 'personal-tokens' and token:
    raise ValueError('shared MCP credential cannot be configured in personal mode')


def job_root():
    if personal == 'personal-tokens':
        from tcode_artifacts.personal_tokens import current_owner
        return root / 'personal-jobs' / current_owner()
    return root


@contextmanager
def database(directory=None):
    directory = directory or job_root()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(directory / 'jobs.sqlite', timeout=30)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, arguments TEXT, status TEXT, ready_at REAL, result TEXT)')
        if personal == 'personal-tokens':
            db.execute('CREATE TABLE IF NOT EXISTS worker_files (id TEXT PRIMARY KEY, credential TEXT, service_id TEXT)')
        with db:
            yield db
    finally:
        db.close()


@mcp.tool
def lookup(text: str) -> dict:
    """Short direct-call tool. This is not an asynchronous computation."""
    return {'text': text, 'source': 'community-lookup'}


@mcp.tool
def submit_prediction(request_id: str, tool: str, arguments: dict) -> dict:
    """Idempotent remote-job v1 submit. Repeated request_id never starts a second computation."""
    if tool != 'predict' or not (arguments.get('input_file') or arguments.get('input_directory')):
        raise ValueError('predict requires input_file or input_directory')
    key = request_id
    if personal == 'personal-tokens':
        key = job_root().name + ':' + request_id
    identifier = 'remote-' + hashlib.sha256(key.encode()).hexdigest()[:32]
    encoded = json.dumps(arguments, sort_keys=True)
    delay = max(0, min(int(arguments.get('delay_seconds', 2)), 300))
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT arguments,status FROM jobs WHERE id=?', (identifier,)).fetchone()
        if existing and existing[0] != encoded:
            raise ValueError('request_id was reused for different arguments')
        if existing:
            return {'job_id': identifier, 'status': existing[1]}
        if personal == 'personal-tokens':
            io = ArtifactIO()
            grant = io.delegate_job(root, [arguments[k] for k in ('input_file','input_directory') if arguments.get(k)])
            db.execute('INSERT INTO worker_files VALUES (?,?,?)',(identifier,grant,io.service_id))
        db.execute('INSERT OR IGNORE INTO jobs VALUES (?, ?, ?, ?, ?)', (identifier, encoded, 'running', time.time() + delay, ''))
    return {'job_id': identifier, 'status': 'running'}


def record(identifier):
    with database() as db:
        row = db.execute('SELECT arguments,status,ready_at,result FROM jobs WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ValueError('job not found')
    return row


@mcp.tool
def get_prediction_status(job_id: str) -> dict:
    row = record(job_id)
    status = row[1]
    if personal != 'personal-tokens' and status == 'running' and time.time() >= row[2]:
        status = 'completed'
    return {'job_id': job_id, 'status': status}


@mcp.tool
def cancel_prediction(job_id: str) -> dict:
    record(job_id)
    with database() as db:
        if personal == 'personal-tokens':
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row[0] != 'running':
                return {'job_id':job_id,'status':row[0]}
            _finish_files(db,job_id)
        db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
    return {'job_id': job_id, 'status': 'cancelled'}


@mcp.tool
def get_prediction_result(job_id: str) -> dict:
    args, status, ready_at, saved = record(job_id)
    if status == 'cancelled' or time.time() < ready_at:
        raise ValueError('job is not completed')
    if saved:
        return json.loads(saved)
    if personal == 'personal-tokens':
        raise ValueError('job output is not ready')
    arguments = json.loads(args)
    with tempfile.TemporaryDirectory(dir=job_root()) as task:
        result = publish_result(arguments, job_id, Path(task).resolve())
    with database() as db:
        db.execute("UPDATE jobs SET status='completed',result=? WHERE id=?", (json.dumps(result), job_id))
    return result


def publish_result(arguments, job_id, output, io=None):
    io = io or ArtifactIO()
    if arguments.get('input_file'):
        io.materialize(arguments['input_file'], output / 'inputs/input.dat')
    if arguments.get('input_directory'):
        io.materialize(arguments['input_directory'], output / 'inputs/directory')
    report = output / 'reports/summary.json'
    report.parent.mkdir()
    report.write_text(json.dumps({'job_id': job_id, 'message': '第三方计算完成', 'files': [p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()]}, ensure_ascii=False), encoding='utf-8')
    return {'status': 'completed', 'summary': '示例计算完成，已上传输入副本和报告', 'output_bundle': io.upload_directory(output)}


def _finish_files(db, identifier):
    from tcode_artifacts.personal_tokens import PersonalTokens
    grant = db.execute('SELECT credential FROM worker_files WHERE id=?',(identifier,)).fetchone()
    if grant:
        PersonalTokens(root).finish_grant(grant[0])
        db.execute('DELETE FROM worker_files WHERE id=?',(identifier,))


def _worker(base):
    # Single-process example. Persist the publication boundary: never blindly replay an
    # interrupted upload. Queued jobs survive restart; interrupted publications need review.
    import httpx
    while True:
        try:
            if httpx.get(base+'/health', trust_env=False, timeout=1).status_code == 401:
                break
        except httpx.TransportError:
            pass
        time.sleep(.1)
    startup = True
    while True:
        for path in (root/'personal-jobs').glob('*/jobs.sqlite'):
            with database(path.parent) as db:
                db.execute('BEGIN IMMEDIATE')
                if startup:
                    for (identifier,) in db.execute("SELECT id FROM jobs WHERE status='publishing'").fetchall():
                        _finish_files(db,identifier)
                        db.execute("UPDATE jobs SET status='failed' WHERE id=?",(identifier,))
                row = db.execute("SELECT j.id,j.arguments,f.credential,f.service_id FROM jobs j JOIN worker_files f USING(id) WHERE j.status='running' AND j.ready_at<=? LIMIT 1",(time.time(),)).fetchone()
                if not row:
                    continue
                identifier, args, credential, service = row
                db.execute("UPDATE jobs SET status='publishing' WHERE id=?",(identifier,))
            try:
                io = ArtifactIO(base,token=credential,service_id=service,services={})
                with tempfile.TemporaryDirectory(dir=path.parent) as task:
                    result = publish_result(json.loads(args),identifier,Path(task).resolve(),io)
                state, output = 'completed',json.dumps(result)
            except Exception:
                state, output = 'failed',''
            with database(path.parent) as db:
                db.execute('UPDATE jobs SET status=?,result=? WHERE id=?',(state,output,identifier))
                _finish_files(db,identifier)
        startup = False
        time.sleep(.25)


if __name__ == '__main__':
    address = dict(host=os.environ.get('EXAMPLE_HOST', '127.0.0.1'), port=int(os.environ.get('EXAMPLE_PORT', '10230')))
    if personal == 'personal-tokens':
        from tcode_artifacts.server import run_personal_mcp
        host = {'0.0.0.0':'127.0.0.1','::':'::1'}.get(address['host'], address['host'])
        base = f"http://{'['+host+']' if ':' in host else host}:{address['port']}/artifact"
        threading.Thread(target=_worker,args=(base,),daemon=True).start()
        run_personal_mcp(mcp, data_dir=root, files=True, service_id=os.environ.get('TMCP_ARTIFACT_SERVICE_ID'), **address)
    else:
        mcp.run(transport='streamable-http', **address)
