"""Descriptor-relative reads from a user-selected desktop project."""
import hashlib
import os
import secrets
import stat
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

from fastapi import HTTPException

from . import workdirs

PREFIX = "@project"


def binding(runtime, context):
    if os.environ.get("TCODE_DESKTOP_MANAGED") != "1":
        return None
    with closing(workdirs._workspace_state_connection(runtime)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='desktop_projects'").fetchone():
            return None
        return db.execute("SELECT * FROM desktop_projects WHERE tenant=? AND user=? AND workspace=? AND generation=? AND path<>''",
                          (context.tenant_id, context.user_id, context.workspace_id,
                           workdirs.workspace_generation(runtime, context))).fetchone()


def project_names(runtime, context):
    if os.environ.get("TCODE_DESKTOP_MANAGED") != "1":
        return {}
    with closing(workdirs._workspace_state_connection(runtime)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='desktop_projects'").fetchone():
            return {}
        return {row["workspace"]: Path(row["path"]).name for row in db.execute(
            """SELECT p.workspace,p.path FROM desktop_projects p JOIN workspace_states s
               ON s.workspace_id=p.workspace AND s.tenant_id=p.tenant AND s.user_id=p.user
               AND s.generation=p.generation WHERE p.tenant=? AND p.user=? AND p.path<>''""",
            (context.tenant_id, context.user_id))}


@contextmanager
def opened(row, rel, directory=False):
    parts = rel.split("/") if rel else []
    if any(not p or p.startswith(".") or p == "node_modules" for p in parts):
        raise HTTPException(403, "项目路径不可访问")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with ExitStack() as stack:
        def op(name, mode, parent=None):
            fd = os.open(name, mode, dir_fd=parent)
            stack.callback(os.close, fd)
            return fd
        try:
            fd = op(row["path"], flags | os.O_DIRECTORY)
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (row["device"], row["inode"]):
                raise HTTPException(409, "项目目录已移动或替换")
            for i, part in enumerate(parts):
                fd = op(part, flags | (os.O_DIRECTORY if directory or i < len(parts)-1 else 0), fd)
        except OSError as exc:
            raise HTTPException(404, "项目文件不可用") from exc
        if not directory and not stat.S_ISREG(os.fstat(fd).st_mode):
            raise HTTPException(400, "请选择普通文件")
        yield fd


def snapshot(row, rel, workspace):
    # Publish the selected version under its digest for sandbox reads and remote uploads.
    with opened(row, rel) as fd:
        before = os.fstat(fd)
        limit = 100 * 1024 * 1024
        if before.st_size > limit:
            raise HTTPException(413, "输入文件超过 100 MiB")
        data = bytearray()
        while chunk := os.read(fd, 1024 * 1024):
            data.extend(chunk)
            if len(data) > limit:
                raise HTTPException(413, "输入文件超过 100 MiB")
        after = os.fstat(fd)
        if before.st_mtime_ns != after.st_mtime_ns or before.st_size != len(data):
            raise HTTPException(409, "文件在读取期间发生变化，请重试")
    cache = workspace / "project-inputs"
    cache.mkdir(exist_ok=True)
    with ExitStack() as stack:
        def op(path, flags, **kwargs):
            fd = os.open(path, flags | os.O_NOFOLLOW, **kwargs)
            stack.callback(os.close, fd)
            return fd
        parent = op(cache, os.O_DIRECTORY)
        digest = hashlib.sha256(data).hexdigest()
        try:
            os.mkdir(digest, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        dest = op(digest, os.O_DIRECTORY, dir_fd=parent)
        name = Path(rel).name
        temporary = ".tmp-" + secrets.token_hex(12)
        out = op(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode=0o600, dir_fd=dest)
        try:
            with os.fdopen(os.dup(out), "wb") as output:
                output.write(data)
            os.replace(temporary, name, src_dir_fd=dest, dst_dir_fd=dest)
        finally:
            try:
                os.unlink(temporary, dir_fd=dest)
            except FileNotFoundError:
                pass
    return cache / digest / name


def tool_input_binding(decision, extra):
    """Only the authenticated owner's exact managed workspace may use its grant."""
    if os.environ.get("TCODE_DESKTOP_MANAGED") != "1" or decision.blocked or not decision.relative_path:
        return None
    if not decision.relative_path.startswith(PREFIX + "/") and decision.host_path.exists():
        return None
    runtime = os.environ.get("TCODE_WORKDIR", "")
    workspace = extra.get("workdir")
    if not runtime or not workspace:
        return None
    owner = workdirs.workspace_context_from_managed_path(runtime, workspace)
    if not owner or any(extra.get(key) != getattr(owner, key) for key in ("workspace_id", "tenant_id", "user_id")):
        return None
    expected = workdirs.resolve_user_workdir_path(runtime, owner.workspace_id, tenant_id=owner.tenant_id, user_id=owner.user_id)
    if Path(workspace).resolve() != expected:
        return None
    workdirs.assert_workspace_active(runtime, owner)
    return binding(runtime, owner)


def entries(row, rel):
    with opened(row, rel, directory=True) as fd, os.scandir(fd) as scan:
        result = []
        for item in scan:
            if item.name.startswith(".") or item.name == "node_modules" or item.is_symlink():
                continue
            if not (item.is_file(follow_symlinks=False) or item.is_dir(follow_symlinks=False)):
                continue
            result.append({"name": item.name, "path": "/".join(filter(None, (PREFIX, rel, item.name))),
                           "type": "directory" if item.is_dir(follow_symlinks=False) else "file"})
        return sorted(result, key=lambda item: (item["type"] != "directory", item["name"].casefold()))
