from contextlib import closing
import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from tcode_agent.server import project_files, project_roots, workdirs


@pytest.mark.parametrize("kind", ["curl", "git"])
@pytest.mark.parametrize("change", ["switch_default", "revoke_at_prompt", "revoke_before_publish"])
def test_shortcut_keeps_confirmed_root_and_rechecks_grant(tmp_path, monkeypatch, kind, change):
    from unittest.mock import AsyncMock
    from tcode_agent.runtime.sandbox.project_network_exec import run_project_transfer
    from tcode_agent.tools.base import ToolContext
    from tcode_agent.tools.builtin.exec import ExecParameters
    from tcode_agent.tools.builtin.read import ReadTool

    if sys.platform != "darwin" and change == "switch_default":
        pytest.skip("native publication uses Darwin's exclusive rename")
    runtime = str(tmp_path / "runtime")
    for key, value in {"TCODE_WORKDIR":runtime, "TCODE_WORKSPACE_STATE_DB":str(tmp_path/"db"),
                       "TCODE_USER_WORKDIRS_ROOT":str(tmp_path/"runtime/workdirs"), "TCODE_DESKTOP_MANAGED":"1"}.items():
        monkeypatch.setenv(key, value)
    owner = workdirs.WorkspaceContext("tenant", "alice", "shortcuts")
    workdirs.create_user_workdir(runtime, owner.workspace_id, tenant_id=owner.tenant_id, user_id=owner.user_id)
    managed = workdirs.resolve_user_workdir_path(runtime, owner.workspace_id, tenant_id=owner.tenant_id, user_id=owner.user_id)
    a, b = tmp_path/"a", tmp_path/"b"
    a.mkdir(); b.mkdir()
    project_roots.attach(runtime, owner, str(a), "rw", primary=True)
    grant = project_roots.attach(runtime, owner, str(b), "rw")
    project_roots.update(runtime, owner, grant["root_id"], make_default=True)
    member = "report.txt" if kind == "git" else ""
    namesake = a / "result" / member
    namesake.parent.mkdir(exist_ok=True)
    namesake.write_text("untouched")
    url = "https://example.invalid/source"
    def approve(permission, patterns):
        if permission == "read":
            return True
        assert str(b) in patterns[0]
        if permission == "write":
            if change == "switch_default":
                project_roots.update(runtime, owner, "primary", make_default=True)
            elif change == "revoke_at_prompt":
                project_roots.update(runtime, owner, grant["root_id"], access="ro")
        return True
    ctx = ToolContext("session", "msg", "build", None, extra={"tenant_id":"tenant", "user_id":"alice",
        "workspace_id":owner.workspace_id, "workdir":str(managed), "__permission_ask__":approve})
    async def local_process(*argv, cwd, **kwargs):
        payload = cwd / "payload"
        if kind == "git":
            (payload/".git").mkdir(parents=True)
            (payload/".git/HEAD").write_text("ref: refs/heads/main")
            (payload/".git/config").write_text(f'[remote "origin"]\nurl = {url}\n')
        (payload / member).write_text("new")
        if change == "revoke_before_publish":
            project_roots.update(runtime, owner, grant["root_id"], access="ro")
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"200", b"")))
    async def exercise():
        dns = AsyncMock(return_value=[(None,None,None,None,("93.184.216.34",443))])
        child = AsyncMock(side_effect=local_process)
        command = f"git clone {url} result" if kind == "git" else f"curl -o result {url}"
        # Stub only the transfer; receipt reads below use the real desktop file worker.
        with monkeypatch.context() as transfer:
            transfer.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
            transfer.setattr(asyncio, "create_subprocess_exec", child)
            result = await run_project_transfer(ExecParameters(command=command), ctx, "exec")
        if change == "revoke_at_prompt":
            dns.assert_not_awaited()
            child.assert_not_awaited()
        else:
            child.assert_awaited_once()
        if change != "switch_default":
            assert result.error and "变更" in result.error and not (b/"result").exists()
        else:
            assert not result.error and str(b/"result") in result.output
            ref = result.metadata["project_path"]
            if kind == "git":
                assert result.metadata["published_checkout"] == {"origin":url,"path":ref}
                ref = result.metadata["published_checkout"]["path"] + "/" + member
            monkeypatch.setenv("TCODE_DESKTOP_NATIVE_READY", "1")
            read = ReadTool()
            original = await read.execute({"filePath": ref}, ctx)
            assert not original.error and original.output == "1: new"
            project_roots.unlink(runtime, owner, grant["root_id"])
            stale = await read.execute({"filePath": ref}, ctx)
            assert stale.error and "解除" in stale.error and "untouched" not in stale.output
            default = await read.execute({"filePath": namesake.relative_to(a).as_posix()}, ctx)
            assert not default.error and default.output == "1: untouched"
        assert namesake.read_text() == "untouched"
    asyncio.run(exercise())


def test_web_keeps_literal_root_paths_and_has_no_native_routes(tmp_path, monkeypatch):
    from tcode_agent.server.app import app
    from tcode_agent.server import files_api
    monkeypatch.delenv("TCODE_DESKTOP_MANAGED", raising=False)
    runtime = str(tmp_path / "runtime")
    monkeypatch.setenv("TCODE_WORKSPACE_STATE_DB", str(tmp_path/"state.sqlite"))
    monkeypatch.setattr(files_api.ServerState, "_workdir", runtime)
    owner = workdirs.WorkspaceContext("tenant","alice","ordinary")
    root = workdirs.resolve_user_workdir_path(runtime,"ordinary",tenant_id="tenant",user_id="alice")
    path = root/"@roots"/"primary"/"example.txt"
    path.parent.mkdir(parents=True)
    path.write_text("normal Web file")
    assert files_api._resolve_local_file_access("@roots/primary/example.txt",owner)[1] == path
    assert not project_roots.inventory(runtime, owner)
    assert not any(getattr(route,"path","").startswith("/api/desktop/") for route in app.routes)


def test_chat_selected_file_is_scoped_and_survives_default_change(tmp_path, monkeypatch):
    from tcode_agent.server.message_resources import _build_message_resource_context
    from tcode_agent.server.state import ServerState
    runtime = str(tmp_path / "runtime")
    monkeypatch.setenv("TCODE_WORKDIR", runtime)
    monkeypatch.setattr(ServerState, "_workdir", runtime)
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("TCODE_WORKSPACE_STATE_DB", str(tmp_path / "state.sqlite"))
    owner = workdirs.WorkspaceContext("tenant", "alice", "chat")
    workdirs.create_user_workdir(runtime, "chat", tenant_id="tenant", user_id="alice")
    workspace = workdirs.resolve_user_workdir_path(runtime, "chat", tenant_id="tenant", user_id="alice")
    for name in ("main", "reports"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "note.txt").write_text(name)
        grant = project_roots.attach(runtime, owner, str(directory), "rw", primary=name == "main")
    project_roots.update(runtime, owner, grant["root_id"], make_default=True)
    ref = "@roots/primary/note.txt"
    def send(scope):
        return _build_message_resource_context("### File: note.txt\nPath: " + ref,
            workdir=str(workspace), owner=scope, file_refs=[{"path": ref}])
    selected, = send(owner)["uploaded_files"]
    assert Path(selected["source_path"]).read_text() == "main"
    assert Path(selected["source_path"]).is_relative_to(workspace)
    with pytest.raises(ValueError, match="不属于"):
        send(workdirs.WorkspaceContext("tenant", "bob", "chat"))
    project_roots.unlink(runtime, owner, "primary")
    with pytest.raises(ValueError, match="已解除"):
        send(owner)


def test_case_alias_does_not_create_overlapping_project_grants(tmp_path, monkeypatch):
    parent = tmp_path / "DataRoot"
    nested = parent / "nested"
    nested.mkdir(parents=True)
    alias = tmp_path / "dataroot"
    if not alias.exists():
        pytest.skip("host volume is case-sensitive")
    runtime = str(tmp_path / "runtime")
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("TCODE_WORKSPACE_STATE_DB", str(tmp_path / "state.sqlite"))
    owner = workdirs.WorkspaceContext("t", "user", "case")
    workdirs.set_workspace_state(runtime, owner, "active")
    original = project_roots.attach(runtime, owner, str(parent), "rw", primary=True)
    assert project_roots.attach(runtime, owner, str(alias), "rw") == original
    with pytest.raises(HTTPException, match="重叠"):
        project_roots.attach(runtime, owner, str(alias/"nested"), "ro")
    project_roots.unlink(runtime, owner)
    project_roots.attach(runtime, owner, str(nested), "rw", primary=True)
    with pytest.raises(HTTPException, match="重叠"):
        project_roots.attach(runtime, owner, str(alias), "rw")


def test_project_membership_migration_and_revocation(tmp_path, monkeypatch):
    runtime = str(tmp_path / "runtime")
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("TCODE_WORKSPACE_STATE_DB", str(tmp_path / "state.sqlite"))
    owner = workdirs.WorkspaceContext("tenant", "alice", "existing")
    legacy, reports = tmp_path / "scripts", tmp_path / "reports"
    legacy.mkdir()
    reports.mkdir()
    workdirs.set_workspace_state(runtime, owner, "active")
    with closing(workdirs._workspace_state_connection(runtime)) as db:
        db.execute("CREATE TABLE desktop_projects (tenant,user,workspace,generation,path,device,inode)")
        db.execute("INSERT INTO desktop_projects VALUES (?,?,?,?,?,?,?)",
                   (*project_roots.key(runtime,owner),str(legacy),legacy.stat().st_dev,legacy.stat().st_ino))
        db.commit()
    # Simulate interruption after migration inserts but before old authority is cleared.
    with closing(workdirs._workspace_state_connection(runtime)) as db:
        db.execute("""CREATE TRIGGER stop_migration BEFORE UPDATE ON desktop_projects
                      BEGIN SELECT RAISE(ABORT,'fixture interruption'); END""")
        db.commit()
    with pytest.raises(sqlite3.DatabaseError, match="fixture interruption"):
        project_files.binding(runtime,owner)
    with closing(workdirs._workspace_state_connection(runtime)) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_root_sets'").fetchone()
        assert db.execute("SELECT path FROM desktop_projects").fetchone()[0] == str(legacy)
        db.execute("DROP TRIGGER stop_migration")
        db.commit()
    primary = project_files.binding(runtime,owner)
    assert primary["root_id"] == "primary"
    backups = set(tmp_path.glob("before-project-roots-*.sqlite"))
    assert len(backups) == 2  # one pre-transaction snapshot per migration attempt
    for backup in backups:
        assert backup.stat().st_mode & 0o077 == 0
        with closing(sqlite3.connect(backup)) as db:
            assert db.execute("SELECT path FROM desktop_projects").fetchone()[0] == str(legacy)
    grant = project_roots.attach(runtime,owner,str(reports),"ro")
    assert project_roots.attach(runtime,owner,str(reports),"ro") == grant
    project = project_roots.inventory(runtime,owner)
    assert len(project["roots"]) == 2
    assert project_roots.resolve(project,str(reports / "out.txt")) == (grant,"out.txt")
    with pytest.raises(HTTPException):
        project_roots.resolve(project,str(reports / "out.txt"),write=True)
    with pytest.raises(HTTPException):
        project_roots.resolve(project,str(tmp_path / "other.txt"))
    assert not project_roots.list_roots(runtime,workdirs.WorkspaceContext("tenant","bob","existing"))
    project_roots.update(runtime,owner,grant["root_id"],access="rw",make_default=True)
    project = project_roots.inventory(runtime,owner)
    current, rel = project_roots.resolve(project,"out.txt",write=True)
    assert project_roots.resolve(project,"@project/out.txt") == (primary,"out.txt")
    with pytest.raises(HTTPException):
        project_files.publish_bound(runtime, owner, grant, rel, b"stale")
    project_files.publish_bound(runtime, owner, current, rel, b"written")
    assert (reports / rel).read_bytes() == b"written"
    with pytest.raises(HTTPException, match="默认目录"):
        project_roots.unlink(runtime,owner,grant["root_id"])
    project_roots.update(runtime,owner,primary["root_id"],make_default=True)
    project_roots.unlink(runtime,owner,grant["root_id"])
    with pytest.raises(HTTPException):
        project_files.publish_bound(runtime, owner, current, "late.txt", b"late")
    project_roots.unlink(runtime,owner)
    assert not project_files.binding(runtime,owner)
    assert not project_roots.list_roots(runtime,owner)
    assert set(tmp_path.glob("before-project-roots-*.sqlite")) == backups
    assert (reports / rel).is_file() and not (reports / "late.txt").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="requires real Seatbelt file workers")
def test_bash_keeps_multi_root_output_in_managed_workspace(tmp_path, monkeypatch):
    from tcode_agent.tools.base import ToolContext
    from tcode_agent.tools.builtin.bash import BashTool
    from tcode_agent.tools.builtin.read import ReadTool
    runtime = str(tmp_path / "runtime")
    for key, value in {"TCODE_WORKDIR": runtime, "TCODE_WORKSPACE_STATE_DB": str(tmp_path/"db"),
                       "TCODE_DESKTOP_MANAGED": "1", "TCODE_DESKTOP_NATIVE_READY": "1"}.items():
        monkeypatch.setenv(key, value)
    owner = workdirs.WorkspaceContext("tenant", "alice", "shell")
    workdirs.create_user_workdir(runtime, "shell", tenant_id="tenant", user_id="alice")
    managed = workdirs.resolve_user_workdir_path(runtime, "shell", tenant_id="tenant", user_id="alice")
    primary, other = tmp_path/"main", tmp_path/"readonly"
    primary.mkdir(); other.mkdir()
    project_roots.attach(runtime, owner, str(primary), "rw", primary=True)
    ctx = ToolContext("session", "msg", "build", None, extra={"workdir":str(managed),
        "tenant_id":"tenant", "user_id":"alice", "workspace_id":"shell", "__permission_ask__":lambda *_: True})
    def shell(value):
        return asyncio.run(BashTool().execute({"command":f"printf {value} > same.txt"}, ctx))
    assert not shell("primary").error and (primary/"same.txt").read_text() == "primary"
    (other/"same.txt").write_text("readonly")
    grant = project_roots.attach(runtime, owner, str(other), "ro")
    project_roots.update(runtime, owner, grant["root_id"], make_default=True)
    result = shell("result")
    assert not result.error and (primary/"same.txt").read_text() == "primary"
    assert (other/"same.txt").read_text() == "readonly"
    ref, = result.metadata["artifacts"]
    assert ref.startswith("/workspace/artifacts/local/") and ref in result.output
    assert (managed/ref.removeprefix("/workspace/")).read_text() == "result"
    namesake = other/ref.removeprefix("/workspace/")
    namesake.parent.mkdir(parents=True); namesake.write_text("wrong")
    read = asyncio.run(ReadTool().execute({"filePath":ref}, ctx))
    assert not read.error and read.output == "1: result"


@pytest.mark.skipif(sys.platform != "darwin", reason="requires real Seatbelt file workers")
def test_chat_file_tools_use_named_root_without_changing_primary(tmp_path, monkeypatch):
    from tcode_agent.tools.base import ToolContext
    from tcode_agent.tools.builtin.edit import EditTool
    from tcode_agent.tools.builtin.glob import GlobTool
    from tcode_agent.tools.builtin.grep import GrepTool
    from tcode_agent.tools.builtin.read import ReadTool
    from tcode_agent.tools.builtin.write import WriteTool
    runtime = str(tmp_path / "runtime")
    for key, value in {"TCODE_WORKDIR":runtime, "TCODE_WORKSPACE_STATE_DB":str(tmp_path/"db"),
                       "TCODE_DESKTOP_MANAGED":"1","TCODE_DESKTOP_NATIVE_READY":"1"}.items():
        monkeypatch.setenv(key,value)
    owner = workdirs.WorkspaceContext("tenant","alice","example")
    workdirs.create_user_workdir(runtime,owner.workspace_id,tenant_id="tenant",user_id="alice")
    managed = workdirs.resolve_user_workdir_path(runtime,"example",tenant_id="tenant",user_id="alice")
    data, scripts, output = [tmp_path / name for name in ("data","scripts","output")]
    from tcode_agent.server import files_api
    monkeypatch.setattr(files_api.ServerState, "_workdir", runtime)
    receipt = managed / "artifacts/jobs/unlinked/result.txt"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("unlinked desktop")
    assert files_api._resolve_local_file_access("/workspace/artifacts/jobs/unlinked/result.txt", owner)[1] == receipt
    for directory in (data,scripts,output):
        directory.mkdir()
        (directory / "same.txt").write_text(directory.name)
    project_roots.attach(runtime,owner,str(scripts),"rw",primary=True)
    project_roots.attach(runtime,owner,str(data),"ro")
    out = project_roots.attach(runtime,owner,str(output),"rw")
    assert project_files.available(out)
    ctx = ToolContext("session","message","build",None,extra={
        "workdir":str(managed),"tenant_id":"tenant","user_id":"alice","workspace_id":"example"})
    def run(tool, **args):
        return asyncio.run(tool.execute(args,ctx))
    found = run(GlobTool(), path=str(data), pattern="same.txt").output
    matched = run(GrepTool(), path=str(data), pattern="data").output
    assert matched == f"{found}:1: data"
    assert run(ReadTool(), filePath=found).output == "1: data"
    assert run(WriteTool(), filePath=found, content="wrong root").error
    for directory in (scripts, data, managed):
        (directory/"upload").mkdir(exist_ok=True)
        (directory/"upload"/"evidence.txt").write_text(directory.name)
    project_roots.update(runtime,owner,out["root_id"],make_default=True)
    assert run(ReadTool(),filePath="/workspace/upload/evidence.txt").output == "1: " + managed.name
    assert run(ReadTool(),filePath="upload/evidence.txt").output == "1: " + managed.name
    from tcode_agent.server import files_api
    monkeypatch.setattr(files_api.ServerState, "_workdir", runtime)
    assert files_api._resolve_local_file_access("/workspace/upload/evidence.txt",owner)[1].read_text() == managed.name
    rel = "artifacts/jobs/0123456789abcdefabcd/same.txt"
    for folder, content in ((managed, "original result"), (output, "project copy")):
        (folder / rel).parent.mkdir(parents=True)
        (folder / rel).write_text(content)
    ref = "/workspace/" + rel
    assert files_api._resolve_local_file_access(ref, owner)[1].read_text() == "original result"
    assert run(ReadTool(), filePath=str(managed / rel)).output == "1: original result"
    assert run(WriteTool(), filePath=ref, content="unexpected").error
    (managed / rel).unlink()
    assert run(ReadTool(), filePath=ref).error
    with pytest.raises(HTTPException):
        files_api._resolve_local_file_access(ref, owner)
    assert run(ReadTool(), filePath=rel).output == "1: project copy"
    assert run(ReadTool(),filePath="@project/upload/evidence.txt").output == "1: scripts"
    assert run(WriteTool(),filePath="upload/evidence.txt",content="changed").error
    assert not run(WriteTool(),filePath=f"@roots/{out['root_id']}/upload/report.txt",content="report").error
    assert (output/"upload"/"report.txt").read_text()=="report"
    assert run(ReadTool(),filePath=str(data/"upload"/"evidence.txt")).output == "1: data"
    (managed/"upload"/"absent-link.txt").symlink_to(managed/"upload"/"evidence.txt")
    assert run(ReadTool(),filePath="upload/absent-link.txt").error
    with pytest.raises(HTTPException):
        files_api._resolve_local_file_access("upload/absent-link.txt",owner)
    project_roots.update(runtime,owner,"primary",make_default=True)
    assert "data" in run(ReadTool(),filePath=str(data / "same.txt")).output
    assert run(WriteTool(),filePath=str(data / "same.txt"),content="bad").error
    assert run(WriteTool(),filePath=str(tmp_path / "outside.txt"),content="bad").error
    assert not run(EditTool(),filePath=f"@roots/{out['root_id']}/same.txt",oldString="output",newString="report").error
    assert (output/"same.txt").read_text()=="report" and (scripts/"same.txt").read_text()=="scripts"
    assert (data/"same.txt").read_text()=="data" and not (tmp_path/"outside.txt").exists()
    from tcode_agent.tools.builtin.bash import BashTool
    from tcode_agent.tools.builtin.exec import ExecTool
    for command_tool in (BashTool(), ExecTool()):
        result = run(command_tool, command="printf fixture > direct.txt", workdir=str(output))
        assert result.error, "extra project root must not enable native command execution"
    assert not (output/"direct.txt").exists()
    moved = tmp_path/"previous-output"
    output.rename(moved)
    output.mkdir()
    (output/"same.txt").write_text("replacement")
    assert not project_files.available(out)
    refused = run(WriteTool(),filePath=str(output/"same.txt"),content="incorrect")
    assert refused.error and (output/"same.txt").read_text()=="replacement"
    assert (moved/"same.txt").read_text()=="report"
    project_roots.unlink(runtime,owner,out["root_id"])
    assert run(ReadTool(),filePath=str(output / "same.txt")).error
    data_grant = next(g for g in project_roots.list_roots(runtime,owner) if g["path"]==str(data))
    project_roots.update(runtime,owner,data_grant["root_id"],make_default=True)
    project_roots.unlink(runtime,owner,"primary")
    assert run(ReadTool(),filePath="/workspace/upload/evidence.txt").output == "1: "+managed.name
    assert run(ReadTool(),filePath="@project/upload/evidence.txt").error
    project_roots.unlink(runtime,owner,data_grant["root_id"])
    assert run(ReadTool(),filePath=found).error


def test_tenant_cleanup_revokes_only_its_own_roots(tmp_path, monkeypatch):
    runtime = str(tmp_path/"runtime")
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED","1")
    monkeypatch.setenv("TCODE_WORKSPACE_STATE_DB",str(tmp_path/"db"))
    owners = [workdirs.WorkspaceContext(tenant,"alice","project") for tenant in ("former","other")]
    workdirs.delete_tenant_workspace_states(runtime,"absent")
    with closing(workdirs._workspace_state_connection(runtime)) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'desktop_%'").fetchone()
        db.execute("CREATE TABLE desktop_projects (tenant,user,workspace,generation,path,device,inode)")
        for owner in owners:
            db.execute("INSERT INTO desktop_projects VALUES (?,?,?,1,'',0,0)", (owner.tenant_id,"alice","archived"))
        db.commit()
    for owner in owners:
        workdirs.set_workspace_state(runtime,owner,"active")
        folder = tmp_path/owner.tenant_id
        folder.mkdir()
        (folder/"kept.txt").write_text("untouched")
        project_roots.attach(runtime,owner,str(folder),"rw",primary=True)
    workdirs.delete_tenant_workspace_states(runtime,"former")
    with closing(workdirs._workspace_state_connection(runtime)) as db:
        assert [r[0] for r in db.execute("SELECT tenant FROM desktop_projects")] == ["other"]
    workdirs.set_workspace_state(runtime,owners[0],"active")
    assert not project_roots.inventory(runtime,owners[0]) and not project_roots.list_roots(runtime,owners[0])
    assert project_roots.inventory(runtime,owners[1])["roots"][0]["path"]==str(tmp_path/"other")
    assert (tmp_path/"former"/"kept.txt").read_text()=="untouched"


@pytest.mark.skipif(sys.platform != "darwin", reason="checks Darwin descriptor-relative Trash target")
def test_literal_project_subfolder_is_not_stripped_twice(tmp_path, monkeypatch):
    runtime = str(tmp_path/"runtime")
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED","1")
    monkeypatch.setenv("TCODE_WORKSPACE_STATE_DB",str(tmp_path/"db"))
    owner = workdirs.WorkspaceContext("tenant","alice","project")
    workdirs.set_workspace_state(runtime,owner,"active")
    root = tmp_path/"root"
    nested = root/"@project"
    nested.mkdir(parents=True)
    for name in ("before.txt","after.txt"):
        (root/name).write_text("keep outer")
    (nested/"before.txt").write_text("inner")
    project_roots.attach(runtime,owner,str(root),"rw",primary=True)
    project = project_roots.inventory(runtime,owner)
    grant, rel = project_roots.resolve(project,"@roots/primary/@project/before.txt",write=True)
    project_files.rename_bound(runtime,owner,grant,rel,"after.txt")
    assert (nested/"after.txt").read_text()=="inner" and not (nested/"before.txt").exists()
    def fake_trash(argv, *, cwd, **kwargs):
        assert argv==["/usr/bin/trash","./after.txt"] and Path(cwd).samefile(nested)
        assert (Path(cwd)/"after.txt").read_text()=="inner"
        calls.append(1)
    calls = []
    monkeypatch.setattr("subprocess.run",fake_trash)
    grant, rel = project_roots.resolve(project,"@project/@project/after.txt",write=True)
    project_files.trash_bound(runtime,owner,grant,rel)
    assert calls==[1] and all((root/n).read_text()=="keep outer" for n in ("before.txt","after.txt"))
