import asyncio
import json

import httpx
import pytest

from tcode_agent.tools.base import ToolContext
from tcode_agent.tools.builtin import artifact_import


def test_artifact_import_uses_authorized_service_and_workspace(tmp_path, monkeypatch):
    server = pytest.importorskip("tcode_artifacts.server")
    if artifact_import.os.name != "posix":
        pytest.skip("requires descriptor-relative no-follow publication")
    token = "test-only-artifact-import-token"
    root = tmp_path / "workspace"
    root.mkdir()
    apps = {
        key: server._artifact_app(tmp_path / key, key, token, 1024, 8192)
        for key in ("lab-production", "lab-testing")
    }
    sources = {
        key: {"name": label, "url": f"http://{key}", "token_env": "TEST_ARTIFACT_IMPORT_TOKEN"}
        for key, label in (("lab-production", "实验室生产"), ("lab-testing", "实验室测试"))
    }
    monkeypatch.setenv("TEST_ARTIFACT_IMPORT_TOKEN", token)
    monkeypatch.setenv("TMCP_ARTIFACT_SERVICES", json.dumps(sources))
    monkeypatch.setenv("TMCP_ARTIFACT_SERVICE_ID", "lab-production")
    monkeypatch.setenv("TMCP_ARTIFACT_TARGET_SERVICES", '{"unrelated-mcp":"lab-testing"}')
    monkeypatch.setenv("TCODE_ARTIFACT_IMPORT_ACCESS", json.dumps({
        key: {"tenant": ["alice"]} for key in sources
    }))
    real_client = httpx.AsyncClient
    calls = []
    fault = None

    def client(**kwargs):
        async def route(request):
            calls.append((request.url.host, request.url.path))
            if fault == "connect":
                raise httpx.ConnectError("fixture is offline", request=request)
            if fault == "checksum" and request.url.path.endswith("/content"):
                return httpx.Response(200, content=b"x" * len("lab-production"))
            return await httpx.ASGITransport(app=apps[request.url.host]).handle_async_request(request)
        return real_client(transport=httpx.MockTransport(route), **kwargs)

    async def session_get(_session, **owner):
        return object() if owner["workspace_id"] == "active" else None

    approvals = []

    def permission(kind, patterns):
        approvals.append((kind, patterns))
        return True

    async def choice(payload):
        assert len(payload["options"]) == 2
        return next(option for option in payload["options"] if "测试" in option)

    monkeypatch.setattr(artifact_import.httpx, "AsyncClient", client)
    monkeypatch.setattr(artifact_import.SessionManager, "get", session_get)
    context = ToolContext("session", "message", "build", None, extra={
        "tenant_id": "tenant", "user_id": "alice", "workspace_id": "active",
        "workdir": str(root), "__permission_ask__": permission, "__question_ask__": choice,
    })

    async def exercise():
        nonlocal fault
        for key in apps:
            async with client(base_url=f"http://{key}", headers={"X-Artifact-Token": token}) as fixture:
                bundle = (await fixture.post("/api/v1/bundles", json={})).json()["bundle_id"]
                for name in ("results/a.csv", "results/b.csv"):
                    response = await fixture.put(f"/api/v1/bundles/{bundle}/files",
                                                 params={"relative_path": name},
                                                 files={"file": ("report.csv", key.encode())})
                    response.raise_for_status()
                assert (await fixture.get("/api/v1/files/by-path", params={"path": "results/a.csv"})).status_code == 404
                draft_page = await fixture.get("/api/v1/files", params={"directory": "results"})
                assert draft_page.json()["entries"] == []
                (await fixture.post(f"/api/v1/bundles/{bundle}/complete",
                                    json={"expected_files": 2})).raise_for_status()
        tool = artifact_import.ArtifactImportTool()
        browse = artifact_import.ArtifactBrowseTool()
        calls.clear()
        assert (await tool.execute({"path": "../a.csv"}, context)).error
        context.extra["workspace_id"] = "other"
        assert (await tool.execute({"path": "results/a.csv"}, context)).error
        context.extra["workspace_id"] = "active"
        context.extra["user_id"] = "mallory"
        assert (await tool.execute({"path": "results/a.csv"}, context)).error
        assert (await browse.execute({"directory": "results"}, context)).error
        context.extra.update(user_id="alice", __permission_ask__=lambda *_: False)
        assert (await tool.execute({"path": "results/a.csv"}, context)).error
        assert (await browse.execute({"directory": "results"}, context)).error
        assert not calls
        context.extra["__permission_ask__"] = permission
        root_page = json.loads((await browse.execute({}, context)).output)
        assert root_page["entries"] == [{"kind": "directory", "path": "results", "display_path": "results/"}]
        assert not (await tool.execute({"path": "results/a.csv"}, context)).error
        assert (root / "imports/a.csv").read_bytes() == b"lab-production"
        page = json.loads((await browse.execute({
            "directory": "results/", "service_name": "实验室", "limit": 1,
        }, context)).output)
        assert page["service_id"] == "lab-testing"
        assert [entry["path"] for entry in page["entries"]] == ["results/a.csv"]
        assert page["next_offset"] == 1
        last = json.loads((await browse.execute({
            "directory": "results", "service_id": page["service_id"], "limit": 1, "offset": page["next_offset"],
        }, context)).output)
        assert last["next_offset"] is None
        selected = last["entries"][0]
        assert selected["path"] == "results/b.csv"
        assert (await tool.execute({
            "path": "results/nonmatching.csv", "service_id": last["service_id"], "artifact_id": selected["artifact_id"],
        }, context)).error
        assert not (await tool.execute({
            "path": selected["path"], "service_id": last["service_id"], "artifact_id": selected["artifact_id"],
        }, context)).error
        assert (root / "imports/b.csv").read_bytes() == b"lab-testing"
        conflict = await tool.execute({"path": "results/a.csv", "service_name": "实验室测试"}, context)
        assert "已有同名文件" in conflict.error
        assert (root / "imports/a.csv").read_bytes() == b"lab-production"
        (root / "imports/b.csv").unlink()
        for fault, expected in (("connect", "连接文件服务失败"), ("checksum", "文件校验失败")):
            result = await tool.execute({"path": "results/b.csv"}, context)
            assert expected in result.error
            assert not (root / "imports/b.csv").exists()
        fault = None
        assert not (await tool.execute({"path": "results/b.csv", "service_name": "实验室测试"}, context)).error
        assert {item[0] for item in approvals} == {"read", "write"}
        async with client(base_url="http://lab-production", headers={"X-Artifact-Token": token}) as fixture:
            uploaded = await fixture.put("/api/v1/files/by-path", params={"path": "uploads/c.csv"},
                                         files={"file": ("c.csv", b"new upload")})
            uploaded.raise_for_status()
        named_page = json.loads((await browse.execute({"directory": "uploads"}, context)).output)
        assert named_page["entries"] == [{
            "path": "uploads/c.csv", "kind": "file", "size": 10, "artifact_id": uploaded.json()["artifact_id"],
        }]
        upload = artifact_import.ArtifactUploadTool()
        upload_args = {"local_path": "imports/a.csv", "remote_path": "exports/report.csv"}
        calls.clear()
        assert (await upload.execute(upload_args, context)).error
        assert not calls
        monkeypatch.setenv("TCODE_ARTIFACT_UPLOAD_ACCESS", json.dumps({
            key: {"tenant": ["alice"]} for key in sources
        }))
        for denied in ("read", "write"):
            context.extra["__permission_ask__"] = lambda kind, _patterns: kind != denied
            assert (await upload.execute(upload_args, context)).error
        assert not calls
        context.extra["__permission_ask__"] = permission
        assert (await upload.execute({**upload_args, "local_path": str(tmp_path / "outside.csv")}, context)).error
        assert not calls
        assert not (await upload.execute(upload_args, context)).error
        assert calls == [("lab-production", "/api/v1/files/by-path")]
        assert not (await upload.execute({**upload_args, "service_name": "实验室"}, context)).error
        assert calls[-1] == ("lab-testing", "/api/v1/files/by-path")
        assert (await upload.execute({**upload_args, "local_path": "imports/b.csv"}, context)).error
        imported = await tool.execute({"path": "exports/report.csv"}, context)
        assert not imported.error
        assert (root / "imports/report.csv").read_bytes() == b"lab-production"

        async with client(base_url="http://lab-production", headers={"X-Artifact-Token": token}) as fixture:
            legacy = (await fixture.post("/api/v1/artifacts",
                                         files={"file": ("legacy:report.csv", b"old reference")})).raise_for_status().json()["ref"]
            (await fixture.put("/api/v1/files/by-path", params={"path": "results"},
                               files={"file": ("results", b"regular file")})).raise_for_status()
            fetched = await fixture.get(f"/api/v1/artifacts/{legacy['artifact_id']}/content")
            assert fetched.raise_for_status().content == b"old reference"
        page = json.loads((await browse.execute({}, context)).output)["entries"]
        assert [(item["kind"], item.get("display_path")) for item in page if item["path"] == "results"] == [
            ("directory", "results/"), ("file", None),
        ]
        legacy_row = next(item for item in page if item["path"] == "legacy:report.csv")
        assert legacy_row["artifact_id"] == legacy["artifact_id"]
        assert legacy_row["importable"] is False
        assert not (await browse.execute({"directory": "results/"}, context)).error

    asyncio.run(exercise())
