"""Login-scoped Web Artifact credentials. Background jobs never call this resolver."""
import base64
import json
import os
import sqlite3
import stat
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..artifact_services import _mapping
from . import auth
from .desktop_config import personal_url


def enabled():
    return auth.auth_required() and os.environ.get('TCODE_DESKTOP_MANAGED') != '1'


def personal_entries():
    return {k: v for k, v in _mapping('TMCP_ARTIFACT_SERVICES').items()
            if isinstance(v, dict) and v.get('auth_mode') == 'personal-tokens'}


def address(key):
    url = personal_entries().get(key, {}).get('url', '').rstrip('/')
    if not enabled() or not personal_url(url, allow_loopback=True):
        raise ValueError('当前没有可用的个人文件服务')
    return url


def _cipher():
    try:
        path = Path(os.environ['TCODE_ARTIFACT_BINDING_KEY_FILE'])
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or (os.name == 'posix' and stat.S_IMODE(path.stat().st_mode) & 0o077)):
            raise ValueError
        key = base64.b64decode(path.read_bytes().strip(), validate=True)
        if len(key) != 32:
            raise ValueError
        return AESGCM(key)
    except (KeyError, ValueError, OSError):
        raise ValueError('请管理员配置可读取的私有绑定加密密钥文件') from None


def saved_addresses(tenant, user):
    if not enabled() or not tenant or not user:
        return {}
    with auth._connect() as db:
        rows = db.execute('SELECT service,url FROM artifact_bindings WHERE tenant=? AND user=?',
                          (tenant, user)).fetchall()
    return dict(rows)


def bindings(tenant, user):
    entries = personal_entries()
    rows = saved_addresses(tenant, user).items()
    return {k: entries[k].get('name') or k for k, url in rows
            if k in entries and url == entries[k].get('url', '').rstrip('/')}


def _aad(tenant, user, key, url, account):
    return json.dumps([tenant, user, key, url, account]).encode()


def save(tenant, user, key, url, account, token):
    if url != address(key):
        raise ValueError('服务地址已变更，请重新绑定')
    nonce = os.urandom(12)
    secret = nonce + _cipher().encrypt(nonce, token.encode(), _aad(tenant, user, key, url, account))
    with auth._connect() as db:
        try:
            changed = db.execute('''INSERT INTO artifact_bindings VALUES (?,?,?,?,?,?)
                ON CONFLICT(tenant,user,service) DO UPDATE SET secret=excluded.secret
                WHERE url=excluded.url AND account=excluded.account''',
                (tenant, user, key, url, account, secret)).rowcount
        except sqlite3.IntegrityError:
            changed = False
        if not changed:
            raise ValueError('账号或地址与已有绑定冲突；请核对凭据或先解除旧绑定')


def remove(tenant, user, key):
    with auth._connect() as db:
        db.execute('DELETE FROM artifact_bindings WHERE tenant=? AND user=? AND service=?', (tenant,user,key))


def endpoint(key, owner):
    url = address(key)
    tenant, user = (owner or {}).get('tenant_id'), (owner or {}).get('user_id')
    with auth._connect() as db:
        row = db.execute('SELECT account,secret FROM artifact_bindings WHERE tenant=? AND user=? AND service=? AND url=?',
                         (tenant, user, key, url)).fetchone()
    if not row:
        raise ValueError('当前账号未绑定此个人文件服务')
    account, secret = row
    try:
        token = _cipher().decrypt(secret[:12], secret[12:], _aad(tenant,user,key,url,account)).decode()
    except InvalidTag:
        raise ValueError('无法解密当前账号凭据，请检查部署密钥') from None
    return key, url, {'X-Artifact-Token': token}
