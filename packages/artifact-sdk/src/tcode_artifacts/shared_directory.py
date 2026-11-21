"""Authenticated, read-only Artifact view over an administrator's published directory."""
import hashlib
import os
import sqlite3
import stat
from contextlib import closing, contextmanager
from pathlib import Path

from .client import _identifier, _parent, _relative
from .personal_tokens import PersonalTokens


def shared_directory_app(data_dir, service_id, source, readers, max_file_bytes, *, readers_file=None):
    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.responses import Response
    from starlette.responses import JSONResponse

    if os.name != 'posix':
        raise ValueError('serving a shared directory requires POSIX safe directory handles')
    if (not readers and readers_file is None) or not service_id or len(_identifier(service_id)) > 64:
        raise ValueError('a shared directory requires --service-id and --reader or --readers-file')
    if not 0 < max_file_bytes <= 500 * 1024**2:
        raise ValueError('invalid shared file size limit')
    root = Path(source).expanduser().absolute()
    state = Path(data_dir).expanduser().resolve()
    if (root.is_symlink() or not root.is_dir() or state.is_relative_to(root.resolve())
            or root.resolve().is_relative_to(state)):
        raise ValueError('shared directory must exist and be separate from credential and task storage')
    root = root.resolve()
    if readers_file is not None:
        supplied = Path(readers_file).expanduser()
        readers_file = supplied.parent.resolve() / supplied.name
        if readers_file.is_relative_to(root):
            raise ValueError('the readers file must be outside the published directory')
    private_markers = ('.auth', 'artifacts.sqlite3', '.personal-files')
    if any(os.path.lexists(parent / marker) for parent in (root, *root.parents) for marker in private_markers):
        raise ValueError('cannot export credential or Artifact store directories')
    root_stat = root.stat()
    tokens = PersonalTokens(data_dir)
    readers = frozenset(readers)
    if readers - {name for name, _ in tokens.manage('list')}:
        raise ValueError('unknown --reader; issue the personal credential first')
    # Keep only path mappings, not file contents. Replace stale revisions for each path.
    with closing(sqlite3.connect(tokens.db)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS share_paths '
                   '(service TEXT, path TEXT, id TEXT UNIQUE, PRIMARY KEY(service,path))')

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service_id = service_id

    @app.middleware('http')
    async def authorize(request: Request, call_next):
        scheme, _, bearer = request.headers.get('authorization', '').partition(' ')
        token = bearer if scheme.lower() == 'bearer' else request.headers.get('x-artifact-token', '')
        allowed = readers
        if readers_file is not None:
            # Reopen on each request so atomic replacements and revocations take effect immediately.
            try:
                with _parent(readers_file) as (parent, name):
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                    with os.fdopen(fd, 'rb') as stream:
                        if not stat.S_ISREG(os.fstat(fd).st_mode):
                            raise ValueError
                        raw = stream.read(1024**2 + 1)
                if len(raw) > 1024**2:
                    raise ValueError
                names = raw.decode('utf-8-sig').replace('\r\n', '\n').split('\n')
                if any(n and (len(n) > 128 or not n.isprintable() or n.strip() != n) for n in names):
                    raise ValueError
                allowed = readers.union(filter(None, names))
            except (OSError, ValueError):
                return JSONResponse({'detail': 'Reader list unavailable'}, 503,
                                    headers={'Cache-Control': 'no-store'})
        identity = tokens.identity(token)
        if not identity:
            return JSONResponse({'detail': 'Authorized reader required'}, 401)
        if identity[1] not in allowed:
            return JSONResponse({'detail': 'Shared directory read permission required'}, 403,
                                headers={'X-Artifact-Error': 'shared_reader_required', 'Cache-Control': 'no-store'})
        if request.method != 'GET':
            return JSONResponse({'detail': 'Read-only share'}, 405)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    def checked(path):
        try:
            parts = _relative(path).parts
            if len(path) <= 1024 and path.isprintable() and not any(p.startswith('.') for p in parts):
                path.encode('utf-8')
                return path
        except ValueError:
            pass
        raise HTTPException(404, 'Not found')

    @contextmanager
    def directory(path):
        # Anchor the walk at the original root; reject symlinks and a replaced export root.
        flags = os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW
        try:
            with _parent(root) as (parent, name):
                fd = os.open(name, flags, dir_fd=parent)
            try:
                if not os.path.samestat(root_stat, os.fstat(fd)):
                    raise HTTPException(404, 'Not found')
                def protect_private():
                    for marker in private_markers:
                        try:
                            os.stat(marker, dir_fd=fd, follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        raise HTTPException(404, 'Not found')
                protect_private()
                for part in (checked(path).split('/') if path else ()):
                    child = os.open(part, flags, dir_fd=fd)
                    os.close(fd)
                    fd = child
                    protect_private()
                yield fd
            finally:
                os.close(fd)
        except OSError as exc:
            raise HTTPException(404, 'Not found') from exc

    def version(path, info):
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > max_file_bytes:
            raise HTTPException(404, 'Not found')
        key = (service_id, str(root), path, info.st_dev, info.st_ino, info.st_size, info.st_ctime_ns)
        return 'share-' + hashlib.sha256(repr(key).encode()).hexdigest()

    def remember(rows):
        with closing(sqlite3.connect(tokens.db)) as db, db:
            db.executemany('INSERT OR REPLACE INTO share_paths VALUES (?,?,?)',
                           ((service_id, e['path'], e['artifact_id']) for e in rows if e['kind'] == 'file'))

    @app.get('/health')
    def health():
        return {'status': 'ok', 'mode': 'restricted', 'service_id': service_id, 'read_only': True,
                'auth_mode': 'personal-tokens'}

    @app.get('/api/v1/files')
    def listing(directory_path: str = Query('', alias='directory', max_length=1024),
                offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        entries = []
        with directory(directory_path) as fd, os.scandir(fd) as children:
            for entry in children:
                path = f'{directory_path}/{entry.name}' if directory_path else entry.name
                try:
                    checked(path)
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        with directory(path):
                            pass
                        entries.append(dict(path=path, kind='directory'))
                    else:
                        entries.append(dict(path=path, kind='file', artifact_id=version(path, info), size=info.st_size))
                except (FileNotFoundError, HTTPException):
                    continue
        entries.sort(key=lambda e: (e['kind'], e['path']))
        page = entries[offset:offset+limit]
        remember(page)
        return {'entries': page, 'next_offset': offset+limit if offset+limit < len(entries) else None}

    def read(path, expected=None, *, content=False):
        parent, _, name = checked(path).rpartition('/')
        with directory(parent) as fd:
            # Check type before opening; never follow a substituted link or block on a FIFO.
            selected = version(path, os.stat(name, dir_fd=fd, follow_symlinks=False))
            if expected is not None and expected != selected:
                raise HTTPException(404, 'Not found')
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(file_fd, 'rb') as stream:
                info = os.fstat(file_fd)
                if version(path, info) != selected:
                    raise HTTPException(409, 'File changed; list it again')
                digest = hashlib.sha256()
                remaining = info.st_size
                if content:
                    data = stream.read(remaining)
                    remaining -= len(data)
                while remaining and not content:
                    block = stream.read(min(remaining, 1024**2))
                    if not block:
                        break
                    remaining -= len(block)
                    digest.update(block)
                if remaining or version(path, os.fstat(file_fd)) != selected:
                    raise HTTPException(409, 'File changed; list it again')
        if content:
            return Response(data, media_type='application/octet-stream',
                            headers={'X-Content-Type-Options': 'nosniff'})
        remember([dict(kind='file', path=path, artifact_id=selected)])
        return dict(artifact_id=selected, filename=name, size=info.st_size, sha256=digest.hexdigest(),
                    ref=dict(kind='artifact', artifact_id=selected, service_id=service_id))

    @app.get('/api/v1/files/by-path')
    def lookup(path: str, artifact_id: str | None = None):
        return read(path, artifact_id)

    def find(identifier):
        with closing(sqlite3.connect(tokens.db)) as db:
            row = db.execute('SELECT path FROM share_paths WHERE service=? AND id=?',
                             (service_id, identifier)).fetchone()
        if not row:
            raise HTTPException(404, 'Not found')
        return row[0]

    @app.get('/api/v1/artifacts/{identifier}')
    def metadata(identifier: str):
        return read(find(identifier), identifier)

    @app.get('/api/v1/artifacts/{identifier}/content')
    def download(identifier: str):
        return read(find(identifier), identifier, content=True)

    return app
