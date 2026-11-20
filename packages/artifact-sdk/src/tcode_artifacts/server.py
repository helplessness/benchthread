"""Optional Artifact HTTP v1 service. Installing or importing the client never starts it."""
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from uuid import uuid4

from .client import ArtifactIO, _identifier, _parent, _relative
from .windows_paths import bundle_paths

PREFIX = '/artifact'
DEFAULT_FILE_LIMIT = 64 * 1024**2
DEFAULT_STORE_LIMIT = 1024**3
BUNDLE_LIMIT = 500 * 1024**2
OBJECT_LIMIT = 10_000
MAX_BUNDLE_FILES = 1024
TOKEN_ENV = 'TMCP_ARTIFACT_API_TOKEN'
PROXY_TOKEN_ENV = 'TMCP_ARTIFACT_PROXY_TOKEN'
URL_ENV = 'TMCP_ARTIFACT_SERVICE_URL'
ID_ENV = 'TMCP_ARTIFACT_SERVICE_ID'


class _Problem(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


class _Store:
    def __init__(self, data_dir, service_id, max_file_bytes, max_store_bytes):
        if type(max_file_bytes) is not int or type(max_store_bytes) is not int or not (
            0 < max_file_bytes <= min(BUNDLE_LIMIT, max_store_bytes)
        ):
            raise ValueError('invalid Artifact file or store size limit')
        if service_id is not None and len(_identifier(service_id)) > 64:
            raise ValueError('Artifact service ID must be 1–64 characters')
        root = Path(data_dir).expanduser()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or (os.name == 'posix' and root.stat().st_mode & 0o077):
            raise ValueError('Artifact data directory must be private (mode 0700) and not a symlink')
        self.root = root.resolve()
        self.db = self.root / 'artifacts.sqlite3'
        if self.db.is_symlink():
            raise ValueError('Artifact database must not be a symlink')
        self.max_file_bytes = max_file_bytes
        self.max_store_bytes = max_store_bytes
        with self._connection(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS identity (id TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE IF NOT EXISTS bundles (id TEXT PRIMARY KEY, ready INTEGER NOT NULL DEFAULT 0)')
            db.execute('''CREATE TABLE IF NOT EXISTS bundle_owners (
                       bundle_id TEXT PRIMARY KEY REFERENCES bundles(id),
                       workspace_id TEXT NOT NULL, owner_id TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS files (
                       artifact_id TEXT PRIMARY KEY, filename TEXT NOT NULL, size INTEGER NOT NULL,
                       sha256 TEXT NOT NULL, bundle_id TEXT REFERENCES bundles(id), relative_path TEXT,
                       UNIQUE(bundle_id, relative_path))''')
            db.execute('''CREATE TABLE IF NOT EXISTS named_files (
                       path TEXT PRIMARY KEY, artifact_id TEXT NOT NULL UNIQUE REFERENCES files(artifact_id))''')
            row = db.execute('SELECT id FROM identity').fetchone()
            self.service_id = row['id'] if row else service_id or f'artifact-{uuid4().hex}'
            if service_id is not None and service_id != self.service_id:
                raise ValueError('Artifact service ID differs from the existing data directory')
            if row is None:
                db.execute('INSERT INTO identity VALUES (?)', (self.service_id,))
            # A killed upload can leave a blob after writing but before SQLite commits.
            # Under the same writer lock, remove only unindexed internal blob names.
            stored = {row[0] for row in db.execute('SELECT artifact_id FROM files')}
            for path in self.root.glob('art-*'):
                if re.fullmatch(r'art-[0-9a-f]{32}', path.name) and path.name not in stored:
                    path.unlink()

    @contextmanager
    def _connection(self, *, write=False):
        # ponytail: serialize bounded writes via SQLite; this is a single-host store.
        with closing(sqlite3.connect(self.db, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db

    def _ref(self, kind, key, identifier):
        return {'ref': {'kind': kind, key: identifier, 'service_id': self.service_id}}

    def _object_capacity(self, db):
        count = db.execute('SELECT (SELECT COUNT(*) FROM files) + (SELECT COUNT(*) FROM bundles)').fetchone()[0]
        if count >= OBJECT_LIMIT:
            raise _Problem(507, 'Artifact object quota reached')

    def _bundle(self, db, identifier):
        row = db.execute('SELECT ready FROM bundles WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise _Problem(404, 'Bundle not found')
        files = [dict(item) for item in db.execute(
            'SELECT artifact_id, size, sha256, relative_path FROM files WHERE bundle_id=? ORDER BY relative_path',
            (identifier,),
        )]
        return {'bundle_id': identifier, 'state': 'ready' if row['ready'] else 'draft',
                'files': files, 'total_files': len(files), 'total_size': sum(item['size'] for item in files),
                **self._ref('artifact_bundle', 'bundle_id', identifier)}

    def create_bundle(self, scope=None):
        identifier = f'bundle-{uuid4().hex}'
        with self._connection(write=True) as db:
            self._object_capacity(db)
            db.execute('INSERT INTO bundles(id) VALUES (?)', (identifier,))
            if scope is not None:
                db.execute('INSERT INTO bundle_owners VALUES (?,?,?)', (identifier, *scope))
            return self._bundle(db, identifier)

    def bundle(self, identifier):
        with self._connection() as db:
            return self._bundle(db, identifier)

    def _writable_bundle(self, db, identifier, scope):
        bundle = self._bundle(db, identifier)
        owner = db.execute('SELECT workspace_id, owner_id FROM bundle_owners WHERE bundle_id=?',
                           (identifier,)).fetchone()
        if (tuple(owner) if owner is not None else None) != scope:
            raise _Problem(403, 'Bundle workspace or owner does not match')
        return bundle

    def complete_bundle(self, identifier, expected_files, scope=None):
        with self._connection(write=True) as db:
            bundle = self._writable_bundle(db, identifier, scope)
            if bundle['total_files'] != expected_files:
                raise _Problem(409, 'Bundle file count does not match')
            db.execute('UPDATE bundles SET ready=1 WHERE id=?', (identifier,))
            return {**bundle, 'state': 'ready'}

    def file(self, identifier):
        with self._connection() as db:
            row = db.execute('SELECT artifact_id, filename, size, sha256 FROM files WHERE artifact_id=?',
                             (identifier,)).fetchone()
        if row is None:
            raise _Problem(404, 'Artifact not found')
        return {**dict(row), **self._ref('artifact', 'artifact_id', identifier)}

    @staticmethod
    def _checked_path(value):
        try:
            path = _relative(value).as_posix()
        except ValueError as exc:
            raise _Problem(400, 'Invalid relative file path') from exc
        if path != value or len(path) > 1024:
            raise _Problem(400, 'Invalid relative file path')
        return path

    @staticmethod
    def _legacy_path_matches(db, path):
        return db.execute('''SELECT f.artifact_id FROM files f LEFT JOIN bundles b ON b.id=f.bundle_id
            LEFT JOIN named_files n ON n.artifact_id=f.artifact_id
            WHERE n.artifact_id IS NULL
              AND ((f.bundle_id IS NULL AND f.filename=?) OR (b.ready=1 AND f.relative_path=?))
            LIMIT 2''', (path, path)).fetchall()

    def _visible_files(self, db, *, artifact_id=None):
        return db.execute('''SELECT f.artifact_id, f.size,
            COALESCE(n.path, f.relative_path, f.filename) AS path
            FROM files f LEFT JOIN bundles b ON b.id=f.bundle_id
            LEFT JOIN named_files n ON n.artifact_id=f.artifact_id
            WHERE (f.bundle_id IS NULL OR b.ready=1)
              AND (? IS NULL OR f.artifact_id=?)''', (artifact_id, artifact_id))

    def list_files(self, directory, offset, limit):
        prefix = self._checked_path(directory) + '/' if directory else ''
        folders, entries = set(), []
        with self._connection() as db:
            # ponytail: the store caps total objects. Build one directory page from its bounded index.
            for row in self._visible_files(db):
                path = row['path']
                if not path.startswith(prefix):
                    continue
                name, slash, _ = path[len(prefix):].partition('/')
                if slash:
                    folders.add(prefix + name)
                elif name:
                    entries.append({**dict(row), 'kind': 'file'})
        entries.extend({'path': path, 'kind': 'directory'} for path in folders)
        entries.sort(key=lambda item: (item['kind'], item['path'], item.get('artifact_id', '')))
        end = offset + limit
        return {'entries': entries[offset:end], 'next_offset': end if end < len(entries) else None}

    def file_by_path(self, value, artifact_id=None):
        path = self._checked_path(value)
        if artifact_id:
            with self._connection() as db:
                match = self._visible_files(db, artifact_id=artifact_id).fetchone()
            if match is None or match['path'] != path:
                raise _Problem(404, 'File selection does not match the path')
            return self.file(artifact_id)
        bundle_id, _, member = path.partition('/')
        with self._connection() as db:
            rows = db.execute('SELECT artifact_id FROM named_files WHERE path=?', (path,)).fetchall()
            if not rows:
                if member and re.fullmatch(r'bundle-[0-9a-f]{32}', bundle_id):
                    rows = db.execute('''SELECT f.artifact_id FROM files f JOIN bundles b ON b.id=f.bundle_id
                        WHERE b.ready=1 AND b.id=? AND f.relative_path=? LIMIT 2''', (bundle_id, member)).fetchall()
                else:
                    rows = self._legacy_path_matches(db, path)
        if not rows:
            raise _Problem(404, 'File path not found')
        if len(rows) > 1:
            raise _Problem(409, 'File path is ambiguous')
        return self.file(rows[0]['artifact_id'])

    @contextmanager
    def _publish_named_copy(self, blob, named_path):
        # This readable copy must never share an inode with an immutable task input.
        target = self.root / 'files' / named_path
        with ExitStack() as parents:
            linked = False
            try:
                try:
                    parent, name = parents.enter_context(_parent(target, create=True))
                    with tempfile.TemporaryDirectory(prefix='.named-', dir=self.root) as work:
                        staged = Path(work) / 'copy'
                        with blob.open('rb') as source, staged.open('xb') as output:
                            shutil.copyfileobj(source, output, length=1024**2)
                            os.fchmod(output.fileno(), 0o400)
                            output.flush()
                            os.fsync(output.fileno())
                        os.link(staged, name, dst_dir_fd=parent, follow_symlinks=False)
                        linked = True
                except (FileExistsError, NotADirectoryError) as exc:
                    raise _Problem(409, 'File path conflicts with the server file directory') from exc
                os.fsync(parent)
                yield
            except BaseException:
                if linked:
                    os.unlink(name, dir_fd=parent)
                raise

    def upload(self, stream, filename, bundle_id=None, relative_path=None, *, named_path=None, scope=None):
        identifier = f'art-{uuid4().hex}'
        path = self.root / identifier
        created = False
        checksum, size = hashlib.sha256(), 0
        # File names are metadata only; blobs always use generated names.
        filename = (filename or '').replace('\\', '/').rsplit('/', 1)[-1][:255]
        if not filename or filename in {'.', '..'} or not filename.isprintable():
            filename = 'artifact.bin'
        try:
            with ExitStack() as publication, self._connection(write=True) as db:
                if named_path is not None:
                    if (db.execute('SELECT 1 FROM named_files WHERE path=?', (named_path,)).fetchone()
                            or self._legacy_path_matches(db, named_path)):
                        raise _Problem(409, 'File path already exists')
                self._object_capacity(db)
                free = self.max_store_bytes - db.execute('SELECT COALESCE(SUM(size),0) FROM files').fetchone()[0]
                bundle_free = BUNDLE_LIMIT
                if bundle_id is not None:
                    bundle = self._writable_bundle(db, bundle_id, scope)
                    if bundle['state'] == 'ready':
                        raise _Problem(409, 'A completed bundle cannot be modified')
                    if bundle['total_files'] >= MAX_BUNDLE_FILES:
                        raise _Problem(413, 'Bundle file count limit reached')
                    try:
                        relative = _relative(relative_path)
                        if len(relative_path) > 1024 or len(relative.parts) > 32:
                            raise ValueError('bundle path too long')
                        bundle_paths([item['relative_path'] for item in bundle['files']] + [relative_path])
                    except ValueError:
                        raise _Problem(400, 'Invalid or conflicting bundle path') from None
                    bundle_free -= bundle['total_size']
                with path.open('xb') as output:
                    created = True
                    while chunk := stream.read(1024**2):
                        size += len(chunk)
                        if size > self.max_file_bytes or size > bundle_free:
                            raise _Problem(413, 'Artifact or bundle exceeds its size limit')
                        if size > free:
                            raise _Problem(507, 'Artifact storage quota reached')
                        checksum.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if os.name == 'posix':
                    directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                db.execute('INSERT INTO files VALUES (?,?,?,?,?,?)',
                           (identifier, filename, size, checksum.hexdigest(), bundle_id, relative_path))
                if named_path is not None:
                    db.execute('INSERT INTO named_files VALUES (?,?)', (named_path, identifier))
                    if os.name == 'posix':
                        publication.enter_context(self._publish_named_copy(path, named_path))
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise
        return self.file(identifier)


def _artifact_app(data_dir, service_id, token, max_file_bytes, max_store_bytes):
    from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
    from fastapi.responses import FileResponse, JSONResponse
    from pydantic import BaseModel, Field
    from starlette.datastructures import Headers
    from starlette.formparsers import MultiPartException

    token = os.environ.get(TOKEN_ENV, '') if token is None else token
    if not isinstance(token, str) or not re.fullmatch(r'[!-~]{24,512}', token):
        raise ValueError(f'{TOKEN_ENV} must be a secret of at least 24 printable ASCII characters')
    proxy_token = os.environ.get(PROXY_TOKEN_ENV, '')
    if proxy_token and (not re.fullmatch(r'[!-~]{24,512}', proxy_token)
                        or secrets.compare_digest(proxy_token, token)):
        raise ValueError(f'{PROXY_TOKEN_ENV} must be a distinct secret of at least 24 printable ASCII characters')
    service_key, proxy_key = token.encode('ascii'), proxy_token.encode('ascii')
    store = _Store(data_dir, service_id, max_file_bytes, max_store_bytes)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service_id = store.service_id

    class Boundary:
        def __init__(self, app):
            self.app = app
            self.writes = 0

        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http':
                return await self.app(scope, receive, send)
            headers = Headers(scope=scope)
            supplied = headers.get('X-Artifact-Token', '').encode('utf-8')
            trusted_proxy = bool(proxy_key) and secrets.compare_digest(supplied, proxy_key)
            if not (secrets.compare_digest(supplied, service_key) or trusted_proxy):
                return await JSONResponse({'detail': 'Artifact authentication required'}, 401)(scope, receive, send)
            if scope['method'] not in {'POST', 'PUT'}:
                return await self.app(scope, receive, send)
            scope.setdefault('state', {})['artifact_trusted_proxy'] = trusted_proxy
            limit = 64 * 1024
            multipart = headers.get('content-type', '').lower().startswith('multipart/form-data')
            if multipart:
                limit += store.max_file_bytes
            declared = headers.get('content-length')
            if declared is not None and (not declared.isascii() or not declared.isdecimal() or len(declared) > 12):
                return await JSONResponse({'detail': 'Invalid request length'}, 400)(scope, receive, send)
            if declared is not None and int(declared) > limit:
                return await JSONResponse({'detail': 'Artifact request too large'}, 413)(scope, receive, send)
            if self.writes >= 4:
                return await JSONResponse({'detail': 'Too many simultaneous Artifact uploads'}, 429)(scope, receive, send)
            self.writes += 1
            received = 0

            async def bounded_receive():
                nonlocal received
                message = await receive()
                received += len(message.get('body', b''))
                if received > limit:
                    if multipart:
                        # Let Starlette close partially spooled multipart files.
                        raise MultiPartException('Artifact request too large')
                    raise HTTPException(413, 'Artifact request too large')
                return message

            async def bounded_send(message):
                if message['type'] == 'http.response.start' and received > limit:
                    message = {**message, 'status': 413}
                await send(message)

            try:
                await self.app(scope, bounded_receive, bounded_send)
            finally:
                self.writes -= 1

    app.add_middleware(Boundary)

    @app.exception_handler(_Problem)
    async def problem_handler(_request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=exc.status)

    @app.get('/health')
    def health():
        return {'status': 'ok', 'mode': 'restricted', 'service_id': store.service_id}

    def bundle_scope(request, workspace_id, owner_id):
        # Workspace alone is allowed as a legacy SDK hint, never as an identity.
        if not owner_id:
            return None
        if not request.state.artifact_trusted_proxy:
            raise _Problem(403, 'Bundle owner assertions require the trusted proxy credential')
        # BenchThread owner IDs can be two 128-character IDs joined by a colon.
        if any(not value or len(value) > 257 or value != value.strip() or not value.isprintable()
               for value in (workspace_id, owner_id)):
            raise _Problem(400, 'Invalid bundle workspace or owner')
        return workspace_id, owner_id

    class BundleScope(BaseModel):
        workspace_id: str = ''
        owner_id: str = ''

    class BundleComplete(BundleScope):
        expected_files: int = Field(ge=0, le=MAX_BUNDLE_FILES, strict=True)

    @app.post('/api/v1/artifacts')
    def upload(file: UploadFile = File(...)):
        return store.upload(file.file, file.filename)

    @app.get('/api/v1/artifacts/{artifact_id}')
    def artifact(artifact_id: str):
        return store.file(artifact_id)

    @app.get('/api/v1/files/by-path')
    def lookup_file(path: str, artifact_id: str | None = Query(None, max_length=128)):
        return store.file_by_path(path, artifact_id)

    @app.get('/api/v1/files')
    def list_files(directory: str = Query('', max_length=1024), offset: int = Query(0, ge=0),
                   limit: int = Query(50, ge=1, le=100)):
        return store.list_files(directory, offset, limit)

    @app.put('/api/v1/files/by-path', status_code=201)
    def create_named_file(path: str, file: UploadFile = File(...)):
        path = store._checked_path(path)
        if (not path.isprintable() or path.count('/') >= 32 or any(char in path for char in '\\:*?"<>|')
                or re.fullmatch(r'bundle-[0-9a-f]{32}', path.split('/')[0])):
            raise _Problem(400, 'Invalid or reserved file path')
        return store.upload(file.file, path.rsplit('/', 1)[-1], named_path=path)

    @app.get('/api/v1/artifacts/{artifact_id}/content')
    def content(artifact_id: str):
        info = store.file(artifact_id)
        path = store.root / info['artifact_id']
        if not path.is_file():
            raise _Problem(404, 'Artifact content not found')
        return FileResponse(path, media_type='application/octet-stream', filename=info['filename'],
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.post('/api/v1/bundles')
    def create_bundle(request: Request, payload: BundleScope):
        return store.create_bundle(bundle_scope(request, payload.workspace_id, payload.owner_id))

    @app.get('/api/v1/bundles/{bundle_id}')
    def bundle(bundle_id: str):
        return store.bundle(bundle_id)

    @app.put('/api/v1/bundles/{bundle_id}/files')
    def upload_member(request: Request, bundle_id: str, relative_path: str, file: UploadFile = File(...),
                      workspace_id: str = '', owner_id: str = ''):
        return store.upload(file.file, file.filename, bundle_id, relative_path,
                            scope=bundle_scope(request, workspace_id, owner_id))

    @app.post('/api/v1/bundles/{bundle_id}/complete')
    def complete_bundle(request: Request, bundle_id: str, payload: BundleComplete):
        return store.complete_bundle(bundle_id, payload.expected_files,
                                     bundle_scope(request, payload.workspace_id, payload.owner_id))

    return app


def run_artifact(*, data_dir, service_id=None, token=None, host='127.0.0.1', port=18081,
                 max_file_bytes=DEFAULT_FILE_LIMIT, max_store_bytes=DEFAULT_STORE_LIMIT):
    """Serve only Artifact HTTP, under the same /artifact prefix as combined mode."""
    if not 1 <= port <= 65535:
        raise ValueError('port must be between 1 and 65535')
    from fastapi import FastAPI
    import uvicorn

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    files = _artifact_app(data_dir, service_id, token, max_file_bytes, max_store_bytes)
    app.mount(PREFIX, files)

    class ArtifactServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                client_host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
                url_host = f'[{client_host}]' if ':' in client_host else client_host
                print(f'Artifact base URL: http://{url_host}:{port}{PREFIX}\n'
                      f'Artifact service ID: {files.state.service_id}\n'
                      'BenchThread: Settings > File services (or Remote services). Enter the URL, service ID, and '
                      'backend credential environment variable name (not the token). Save, restart Web, then test.\n'
                      'Remote clients need a reachable address (--host or reverse proxy), not a loopback URL.',
                      flush=True)

    ArtifactServer(uvicorn.Config(app, host=host, port=port, workers=1)).run()


def run_with_artifact(mcp, *, data_dir, service_id=None, token=None, host='127.0.0.1', port=10240,
                      max_file_bytes=DEFAULT_FILE_LIMIT, max_store_bytes=DEFAULT_STORE_LIMIT):
    """Run FastMCP and optional Artifact routes on one listener, with no external failover."""
    if not 1 <= port <= 65535:
        raise ValueError('set a stable TCP port (1–65535) for combined MCP and Artifact')
    # Reuse the client's existing validation and service mapping, without probing.
    services = json.loads(os.environ.get('TMCP_ARTIFACT_SERVICES', '').strip() or '{}')
    if not isinstance(services, dict):
        raise ValueError('TMCP_ARTIFACT_SERVICES must be a JSON object')
    if os.environ.get(URL_ENV, '').strip() or services:
        if token is not None:
            raise ValueError('token= is only for built-in Artifact; configure the external credential via its environment')
        ArtifactIO()
        return mcp.run(transport='streamable-http', path='/mcp', host=host, port=port)

    from fastapi import FastAPI
    import uvicorn

    token = os.environ.get(TOKEN_ENV, '') if token is None else token
    files = _artifact_app(data_dir, service_id or os.environ.get(ID_ENV) or None, token,
                          max_file_bytes, max_store_bytes)
    mcp_http = mcp.http_app(path='/mcp')
    app = FastAPI(lifespan=mcp_http.lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount(PREFIX, files)
    app.mount('/', mcp_http)

    client_host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
    url_host = f'[{client_host}]' if ':' in client_host else client_host
    # Never send the bundled service's token to an inherited HTTP proxy.
    no_proxy = os.environ.get('no_proxy') or os.environ.get('NO_PROXY', '')
    direct = ','.join(filter(None, (no_proxy, client_host)))
    values = {URL_ENV: f'http://{url_host}:{port}{PREFIX}', ID_ENV: files.state.service_id,
              TOKEN_ENV: token, 'TMCP_ARTIFACT_SERVICES': '{}', 'NO_PROXY': direct, 'no_proxy': direct}
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        uvicorn.run(app, host=host, port=port, workers=1)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main():
    parser = argparse.ArgumentParser(prog='tcode-artifact', description='Standalone Artifact HTTP file service')
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=18081)
    serve.add_argument('--data-dir', type=Path, required=True)
    serve.add_argument('--service-id')
    serve.add_argument('--max-file-bytes', type=int, default=DEFAULT_FILE_LIMIT)
    serve.add_argument('--max-store-bytes', type=int, default=DEFAULT_STORE_LIMIT)
    options = vars(parser.parse_args())
    options.pop('command')
    try:
        run_artifact(**options)
    except ModuleNotFoundError as exc:
        if exc.name not in {'fastapi', 'uvicorn', 'python_multipart'}:
            raise
        parser.error('install tcode-artifact-sdk with the [server] extra')
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
