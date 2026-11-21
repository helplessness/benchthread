"""Provider-local personal credentials; store only digests, never bearer tokens."""
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4


# Bound by the authenticated request middleware, never process-wide credentials.
request_identity = ContextVar('tcode_personal_request', default=None)


def current_owner():
    identity = request_identity.get()
    if identity is None:
        raise PermissionError('personal authentication required')
    return identity[0]


class PersonalTokens:
    def __init__(self, data_dir):
        root = Path(data_dir).expanduser()
        private = root / '.auth'
        for directory in (root, private):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if directory.is_symlink() or (os.name == 'posix' and directory.stat().st_mode & 0o077):
                raise ValueError('personal credential directory must be private and not a symlink')
        self.db = private / 'users.sqlite3'
        if self.db.is_symlink():
            raise ValueError('personal credential database must not be a symlink')
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS users '
                       '(name TEXT PRIMARY KEY, owner TEXT UNIQUE NOT NULL, digest TEXT UNIQUE)')
            db.execute('CREATE TABLE IF NOT EXISTS job_grants '
                       '(digest TEXT PRIMARY KEY, owner TEXT NOT NULL, permissions TEXT NOT NULL, output TEXT NOT NULL)')

    def delegate_files(self, permissions, output):
        """Called locally on authenticated submit, never an HTTP or model-facing tool."""
        owner = current_owner()
        # Expiry is covered by the stored digest, so a caller cannot extend it.
        token = f'job.{int(time.time()) + 86400}.' + secrets.token_urlsafe(32)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('INSERT INTO job_grants VALUES (?,?,?,?)', (
                hashlib.sha256(token.encode()).hexdigest(), owner, json.dumps(sorted(permissions)), output))
        return token

    def job_grant(self, token):
        if not token.startswith('job.') or len(token) > 100:
            return None
        parts = token.split('.')
        if len(parts) != 3 or not parts[1].isascii() or not parts[1].isdecimal() or int(parts[1]) <= time.time():
            return None
        with closing(sqlite3.connect(self.db)) as db:
            row = db.execute('SELECT owner,permissions,output FROM job_grants WHERE digest=?',
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            return (row[0], set(json.loads(row[1])), row[2]) if row else None

    def finish_grant(self, token):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('DELETE FROM job_grants WHERE digest=?',(hashlib.sha256(token.encode()).hexdigest(),))

    def manage(self, action, name=None):
        with closing(sqlite3.connect(self.db)) as db, db:
            if action == 'list':
                return db.execute('SELECT name, digest IS NOT NULL FROM users ORDER BY name').fetchall()
            if not name or len(name) > 128 or not name.isprintable() or name.strip() != name:
                raise ValueError('invalid user name')
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode()).hexdigest()
            if action == 'issue':
                try:
                    db.execute('INSERT INTO users VALUES (?, ?, ?)', (name, uuid4().hex, digest))
                except sqlite3.IntegrityError as exc:
                    raise ValueError('user already exists; rotate instead') from exc
            elif action in ('rotate', 'revoke'):
                if not db.execute('UPDATE users SET digest=? WHERE name=?',
                                  (digest if action == 'rotate' else None, name)).rowcount:
                    raise ValueError('unknown user')
            else:
                raise ValueError('unknown operation')
            return token if action != 'revoke' else None

    def identity(self, token):
        if not token or len(token) > 512:
            return None
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute('SELECT owner,name FROM users WHERE digest=?',
                              (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()

    def owner(self, token, *, readers=None):
        row = self.identity(token)
        return row[0] if row and (readers is None or row[1] in readers) else None
