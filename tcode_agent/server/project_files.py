"""Descriptor-relative reads from a user-selected desktop project."""
import hashlib
import os
import secrets
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path

from fastapi import HTTPException

from . import workdirs, project_roots

PREFIX = "@project"


def binding(runtime, context):
    if os.environ.get("TCODE_DESKTOP_MANAGED") != "1":
        return None
    return next((row for row in project_roots.list_roots(runtime, context) if row["root_id"] == "primary"), None)


def project_names(runtime, context):
    if os.environ.get("TCODE_DESKTOP_MANAGED") != "1":
        return {}
    with project_roots.database(runtime) as db:
        return {row["workspace"]: (Path(row["path"]).name if row["path"] else row["workspace"].rsplit("-",1)[0])
                for row in db.execute(
            """SELECT r.workspace,p.path FROM desktop_root_sets r JOIN workspace_states s
               ON (s.workspace_id,s.tenant_id,s.user_id,s.generation)=(r.workspace,r.tenant,r.user,r.generation)
               LEFT JOIN desktop_roots p ON (p.tenant,p.user,p.workspace,p.generation)=(r.tenant,r.user,r.workspace,r.generation)
               AND p.root_id='primary' WHERE r.tenant=? AND r.user=?""",
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
        if not directory and (not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1):
            raise HTTPException(400, "请选择普通文件")
        yield fd


def available(row):
    try:
        with opened(row, "", directory=True):
            return True
    except HTTPException:
        return False


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


def managed_input_snapshot(workspace, relative):
    info = workspace.stat()
    return snapshot({"path": workspace, "device": info.st_dev, "inode": info.st_ino}, relative, workspace)


def tool_input_binding(decision, extra):
    """Only the authenticated owner's exact managed workspace may use its grant."""
    if os.environ.get("TCODE_DESKTOP_MANAGED") != "1" or decision.blocked or not decision.relative_path:
        return None
    owner = tool_owner(extra)
    return binding(os.environ["TCODE_WORKDIR"], owner) if owner else None


def tool_owner(extra):
    if os.environ.get("TCODE_DESKTOP_MANAGED") != "1":
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
    return owner


def selected_tool_grant(decision, extra):
    """Never reinterpret a resolved file as belonging to the current default."""
    root_id = decision.metadata.get("desktop_root_id")
    if not root_id:
        return None
    owner = tool_owner(extra)
    if owner:
        for row in project_roots.list_roots(os.environ["TCODE_WORKDIR"], owner):
            if row["root_id"] == root_id and row["grant_id"] == decision.metadata.get("desktop_grant_id"):
                return row
    raise HTTPException(409, "项目目录授权已变更，请重新选择")


def publish_bound(runtime, owner, row, relative, data, *, expected=None):
    """Publish only while the same workspace generation still owns this directory."""
    from ..runtime.sandbox.macos_native import publish_file
    with workdirs._WORKSPACE_MUTATION_LOCK:
        workdirs.assert_workspace_active(runtime, owner)
        check_writable(runtime, owner, row)
        with opened(row, "", directory=True) as fd:
            publish_file(fd, relative, data, expected=expected)


def check_writable(runtime, owner, grant):
    current = next((r for r in project_roots.list_roots(runtime, owner) if r["root_id"] == grant["root_id"]), None)
    if current != dict(grant) or current["access"] != "rw":
        raise HTTPException(409, "项目关联或写入权限已变更")


def mirror_upload(runtime, owner, grant, relative, temporary):
    """A change to the optional project copy must not redirect or discard the chat attachment."""
    with workdirs._WORKSPACE_MUTATION_LOCK:
        workdirs.assert_workspace_active(runtime, owner)
        if grant["access"] == "rw" and binding(runtime, owner) == grant:
            publish_bound(runtime, owner, grant, relative, temporary.read_bytes())


def rename_bound(runtime, owner, grant, relative, name):
    with workdirs._WORKSPACE_MUTATION_LOCK:
        workdirs.assert_workspace_active(runtime, owner)
        check_writable(runtime, owner, grant)
        change_entry(grant, relative, name)


def entries(row, rel):
    with opened(row, rel, directory=True) as fd, os.scandir(fd) as scan:
        result = []
        for item in scan:
            if item.name.startswith(".") or item.name == "node_modules" or item.is_symlink():
                continue
            if not (item.is_file(follow_symlinks=False) or item.is_dir(follow_symlinks=False)):
                continue
            result.append({"name": item.name, "path": "/".join(filter(None, (rel, item.name))),
                           "size": item.stat(follow_symlinks=False).st_size if item.is_file(follow_symlinks=False) else 0,
                           "type": "directory" if item.is_dir(follow_symlinks=False) else "file"})
        return sorted(result, key=lambda item: (item["type"] != "directory", item["name"].casefold()))


def change_entry(row, rel, new_name=None):
    if any(not part or part.startswith(".") for part in rel.split("/")):
        raise HTTPException(403, "项目路径不可访问")
    parent, _, name = rel.rpartition("/")
    with opened(row, parent, directory=True) as fd:
        if not name or name.startswith(".") or not stat.S_ISREG(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
            raise HTTPException(400, "只能操作普通项目文件")
        if new_name is not None:
            try:
                os.link(name, new_name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            except FileExistsError:
                raise HTTPException(409, "目标名称已存在")
        os.unlink(name, dir_fd=fd)


def trash_bound(runtime, scope, grant, rel):
    """Use the macOS Trash (including Put Back); never fall back to unlink."""
    import subprocess
    import sys
    if sys.platform != "darwin" or any(not p or p.startswith(".") or p == "node_modules" for p in rel.split("/")):
        raise HTTPException(400, "只能将已关联项目内的普通文件或文件夹移到废纸篓")
    parent, _, name = rel.rpartition("/")
    with workdirs._WORKSPACE_MUTATION_LOCK:
        workdirs.assert_workspace_active(runtime, scope)
        check_writable(runtime, scope, grant)
        with opened(grant, parent, directory=True) as src:
            mode = os.stat(name, dir_fd=src, follow_symlinks=False).st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise HTTPException(400, "不支持移动符号链接或特殊文件")
            try:
                directory = os.fstat(src)
                # Darwin's device/inode namespace addresses the directory we validated,
                # even if a path component is renamed while the command starts.
                subprocess.run(["/usr/bin/trash", "./" + name], cwd=f"/.vol/{directory.st_dev}/{directory.st_ino}",
                               stdin=subprocess.DEVNULL, capture_output=True,
                               check=True, timeout=15)
            except (OSError, subprocess.SubprocessError) as exc:
                raise HTTPException(409, "系统未确认移入废纸篓，请检查原位置；不会永久删除") from exc


def search(row, query, limit, scan_limit=4000):
    pending, hits, scanned = [""], [], 0
    words = query.casefold().split()
    while pending:
        for item in entries(row, pending.pop()):
            scanned += 1
            if scanned > scan_limit:
                return hits, scanned, True
            if item["type"] == "directory":
                pending.append(item["path"])
            elif all(word in item["path"].casefold() for word in words):
                hits.append({**item, "origin": "project"})
                if len(hits) >= limit:
                    return hits, scanned, True
    return hits, scanned, False
