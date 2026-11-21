"""Desktop project membership. The managed workspace is not a host directory grant."""
import os
import stat
import tempfile
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from fastapi import HTTPException

from . import workdirs
from ..sqlite_runtime import connect


KEY = "tenant=? AND user=? AND workspace=? AND generation=?"


def key(runtime, owner):
    return (owner.tenant_id, owner.user_id, owner.workspace_id, workdirs.workspace_generation(runtime, owner))


@contextmanager
def database(runtime):
    with workdirs._WORKSPACE_MUTATION_LOCK, closing(workdirs._workspace_state_connection(runtime)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_root_sets'").fetchone():
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_projects'").fetchone():
                # Snapshot via SQLite (includes committed WAL frames); exclusive,
                # owner-only file beside the private workspace DB, never in a project.
                with tempfile.NamedTemporaryFile(dir=workdirs.workspace_state_db_path(runtime).parent,
                                                 prefix="before-project-roots-", suffix=".sqlite", delete=False) as backup:
                    with closing(connect(backup.name)) as destination:
                        db.backup(destination)
            # SQLite DDL is transactional only with an explicit BEGIN. The marker,
            # migrated grants and removal of legacy authority must commit together.
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_root_sets'").fetchone():
                db.execute("""CREATE TABLE desktop_root_sets (tenant TEXT, user TEXT, workspace TEXT,
                    generation INTEGER, revision INTEGER NOT NULL DEFAULT 1,
                    default_id TEXT, PRIMARY KEY(tenant,user,workspace,generation))""")
                db.execute("""CREATE TABLE desktop_roots (tenant TEXT, user TEXT, workspace TEXT,
                    generation INTEGER, root_id TEXT, path TEXT NOT NULL, device INTEGER, inode INTEGER,
                    access TEXT NOT NULL CHECK(access IN ('ro','rw')), grant_id TEXT NOT NULL,
                    PRIMARY KEY(tenant,user,workspace,generation,root_id),
                    UNIQUE(tenant,user,workspace,generation,device,inode))""")
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_projects'").fetchone():
                    db.execute("""INSERT INTO desktop_root_sets (tenant,user,workspace,generation,default_id)
                        SELECT p.tenant,p.user,p.workspace,p.generation,CASE WHEN p.path<>'' THEN 'primary' END
                        FROM desktop_projects p JOIN workspace_states s ON
                        (s.tenant_id,s.user_id,s.workspace_id,s.generation)=(p.tenant,p.user,p.workspace,p.generation)
                        WHERE s.status<>'purged'""")
                    db.execute("""INSERT INTO desktop_roots SELECT p.tenant,p.user,p.workspace,p.generation,
                        'primary',p.path,p.device,p.inode,'rw',lower(hex(randomblob(16))) FROM desktop_projects p
                        JOIN desktop_root_sets s USING (tenant,user,workspace,generation) WHERE p.path<>''""")
                    db.execute("UPDATE desktop_projects SET path='' WHERE path<>''")
            db.commit()
        with db:
            yield db


def lineage(path):
    """Directory identity survives case/Unicode spelling differences on macOS."""
    return {(s.st_dev, s.st_ino) for p in (path, *path.parents) for s in (p.stat(),)}


def checked_directory(runtime, raw):
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise HTTPException(400, "请选择存在的项目文件夹")
    try:
        path = candidate.resolve(strict=True)
        info = path.stat()
    except OSError as exc:
        raise HTTPException(409, "目录不可用，请重新选择") from exc
    managed = Path(runtime).resolve()
    identity = (info.st_dev, info.st_ino)
    if (not stat.S_ISDIR(info.st_mode) or path.samefile(Path.home()) or path.samefile(path.anchor)
            or path.is_relative_to(managed) or managed.is_relative_to(path)
            or (managed.exists() and (identity in lineage(managed) or
                (managed.stat().st_dev, managed.stat().st_ino) in lineage(path)))):
        raise HTTPException(400, "请选择独立于应用运行目录的项目文件夹")
    return path, info


def list_roots(runtime, owner):
    if os.environ.get("TCODE_DESKTOP_MANAGED") != "1":
        return []
    with database(runtime) as db:
        return [dict(r) for r in db.execute(f"SELECT * FROM desktop_roots WHERE {KEY} ORDER BY root_id", key(runtime, owner))]


def inventory(runtime, owner):
    if os.environ.get("TCODE_DESKTOP_MANAGED") != "1":
        return None
    with database(runtime) as db:
        workdirs.assert_workspace_active(runtime, owner)
        k = key(runtime, owner)
        project = db.execute(f"SELECT generation,revision,default_id FROM desktop_root_sets WHERE {KEY}", k).fetchone()
        if not project:
            return None
        return {**dict(project), "roots": [dict(r) for r in db.execute(
            f"SELECT * FROM desktop_roots WHERE {KEY} ORDER BY path", k)]}


def explicit_reference(raw):
    return raw in ("@project", "@roots") or raw.startswith(("@project/", "@roots/"))


def attachment_path(raw):
    return (raw == "upload" or raw.startswith("upload/")) and all(
        part and not part.startswith(".") and part != "node_modules" for part in raw.split("/")
    )


def managed_input_relative(raw, workspace):
    """Resolve issued workspace paths without probing either tree for a namesake."""
    path = Path(raw)
    if raw.startswith("/workspace/"):
        relative = raw[len("/workspace/"):]
    elif path.is_absolute() and path.is_relative_to(workspace):
        relative = path.relative_to(workspace).as_posix()
    else:
        return raw if attachment_path(raw) else None
    if attachment_path(relative) or (relative.startswith(("project-inputs/", "artifacts/jobs/", "artifacts/local/"))
            and all(part and not part.startswith(".") and part != "node_modules" for part in relative.split("/"))):
        return relative
    return None


def uses_project_root(project, raw):
    if not project or not (project["roots"] or explicit_reference(raw)):
        return False
    # The upload receipt always points to its managed copy, never a later grant.
    return not attachment_path(raw.removeprefix("/workspace/"))


def resolve(project, raw, *, write=False):
    """Bind references before filesystem access; a missing explicit root never falls back."""
    roots = project["roots"]
    root_id = project["default_id"]
    relative = raw
    if attachment_path(raw.removeprefix("/workspace/")):
        raise HTTPException(409, "聊天附件是托管副本，请从项目文件列表重新选择要修改的原文件")
    elif raw == "@project" or raw.startswith("@project/"):
        root_id, relative = "primary", raw.partition("/")[2]
    elif raw == "@roots":
        raise HTTPException(400, "请选择一个项目文件夹")
    elif raw.startswith("@roots/"):
        root_id, _, relative = raw[len("@roots/"):].partition("/")
    elif Path(raw).is_absolute():
        candidate = Path(raw)
        root = next((r for r in roots if candidate.is_relative_to(r["path"])), None)
        if not root:
            raise HTTPException(403, "该目录不属于当前项目，未执行操作。如需访问，请在「新建任务」选择当前项目，点「管理 → 添加文件夹到项目」并确认权限，然后回到原对话继续。")
        root_id = root["root_id"]
        relative = "/".join(candidate.relative_to(root["path"]).parts)
    root = next((r for r in roots if r["root_id"] == root_id), None)
    if not root:
        raise HTTPException(404, "原目录已解除或不可用")
    if relative and any(not p or p.startswith(".") or p == "node_modules" for p in relative.split("/")):
        raise HTTPException(403, "项目路径不可访问")
    if write and root["access"] != "rw":
        raise HTTPException(403, "项目目录为只读")
    return root, relative


def update(runtime, owner, root_id, *, access=None, make_default=False):
    with database(runtime) as db:
        workdirs.assert_workspace_active(runtime, owner)
        k = key(runtime, owner)
        if not db.execute(f"SELECT 1 FROM desktop_roots WHERE {KEY} AND root_id=?", (*k,root_id)).fetchone():
            raise HTTPException(404, "原目录已解除")
        if access is not None:
            if access not in ("ro","rw"):
                raise HTTPException(400, "无效的目录权限")
            db.execute(f"UPDATE desktop_roots SET access=?,grant_id=? WHERE {KEY} AND root_id=?",
                       (access, uuid.uuid4().hex,*k,root_id))
        db.execute(f"""UPDATE desktop_root_sets SET revision=revision+1,
            default_id=CASE WHEN ? THEN ? ELSE default_id END WHERE {KEY}""", (make_default,root_id,*k))


def attach(runtime, owner, raw, access, *, primary=False, expected_identity=None):
    if os.environ.get("TCODE_DESKTOP_MANAGED") != "1" or access not in ("rw", "ro"):
        raise HTTPException(403, "无效的桌面目录授权")
    with database(runtime) as db:
        workdirs.assert_workspace_active(runtime, owner)
        path, info = checked_directory(runtime, raw)
        if expected_identity is not None and expected_identity != [str(info.st_dev),str(info.st_ino)]:
            raise HTTPException(409, "所选文件夹在确认期间已变更，请重新选择")
        k = key(runtime, owner)
        project = db.execute(f"SELECT * FROM desktop_root_sets WHERE {KEY}", k).fetchone()
        if not project and not primary:
            raise HTTPException(404, "请先打开一个项目")
        ancestors = lineage(path)
        for row in db.execute(f"SELECT * FROM desktop_roots WHERE {KEY}", k):
            other = Path(row["path"])
            if (row["device"], row["inode"]) == (info.st_dev, info.st_ino):
                if (primary and row["root_id"] != "primary") or (not primary and row["access"] != access):
                    raise HTTPException(409, "目录已关联，请单独修改权限")
                return dict(row)
            if ((primary and row["root_id"] == "primary") or path.is_relative_to(other) or other.is_relative_to(path)
                    or (row["device"], row["inode"]) in ancestors
                    or (other.exists() and (info.st_dev, info.st_ino) in lineage(other))):
                raise HTTPException(409, "目录重叠或已变更，请先核对项目关联")
        root_id = "primary" if primary else uuid.uuid4().hex
        db.execute("INSERT OR IGNORE INTO desktop_root_sets (tenant,user,workspace,generation) VALUES (?,?,?,?)", k)
        db.execute("INSERT INTO desktop_roots VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (*k, root_id, str(path), info.st_dev, info.st_ino, access, uuid.uuid4().hex))
        db.execute(f"UPDATE desktop_root_sets SET default_id=coalesce(default_id,?),revision=revision+1 WHERE {KEY}", (root_id,*k))
        return dict(db.execute(f"SELECT * FROM desktop_roots WHERE {KEY} AND root_id=?", (*k,root_id)).fetchone())


def unlink(runtime, owner, root_id=None):
    with database(runtime) as db:
        workdirs.assert_workspace_active(runtime, owner)
        k = key(runtime, owner)
        if root_id is None:
            db.execute(f"DELETE FROM desktop_roots WHERE {KEY}", k)
            db.execute(f"UPDATE desktop_root_sets SET default_id=NULL,revision=revision+1 WHERE {KEY}", k)
        else:
            project = db.execute(f"SELECT default_id FROM desktop_root_sets WHERE {KEY}", k).fetchone()
            if project and project["default_id"] == root_id and db.execute(
                    f"SELECT 1 FROM desktop_roots WHERE {KEY} AND root_id<>?", (*k,root_id)).fetchone():
                raise HTTPException(409, "请先将另一个文件夹设为默认目录，再解除关联")
            deleted = db.execute(f"DELETE FROM desktop_roots WHERE {KEY} AND root_id=?", (*k,root_id))
            if not deleted.rowcount:
                raise HTTPException(404, "目录已解除")
            db.execute(f"""UPDATE desktop_root_sets SET revision=revision+1,
                default_id=CASE WHEN default_id=? THEN NULL ELSE default_id END WHERE {KEY}""", (root_id,*k))
