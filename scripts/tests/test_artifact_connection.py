from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tcode_agent.server import artifact_services_api as api


@pytest.mark.parametrize("mode,status,reported,expected", [
    ("restricted", 200, "store", "connected"),
    ("local_trusted", 200, "store", "reachable"),
    ("restricted", 200, None, "reachable"),
    ("restricted", 200, "another", "failed"),
    ("restricted", 401, None, "failed"),
    ("restricted", 403, None, "failed"),
])
def test_artifact_health_never_exposes_credentials(monkeypatch, mode, status, reported, expected):
    url = "https://artifact.example.org"
    monkeypatch.delenv("TCODE_DESKTOP_MANAGED", raising=False)
    monkeypatch.setenv("TMCP_ARTIFACT_SERVICES", '{"store":{"url":"https://artifact.example.org","token_env":"TEST_ARTIFACT_TOKEN"}}')
    monkeypatch.setenv("TMCP_ARTIFACT_TARGET_SERVICES", "{}")
    monkeypatch.setenv("TMCP_ARTIFACT_SERVICE_ID", "store")
    monkeypatch.setenv("TEST_ARTIFACT_TOKEN", "secret-value")
    monkeypatch.setattr(api, "require_current_user", lambda request: SimpleNamespace(
        role="admin", tenant_id=api.workdirs.default_tenant_id()))
    remote = MagicMock()
    remote.__aenter__.return_value = remote
    headers = {"X-Artifact-Error": "shared_reader_required"} if status == 403 else {}
    body = {"status": "ok", "mode": mode}
    if reported is not None:
        body["service_id"] = reported
    remote.get = AsyncMock(return_value=httpx.Response(status, headers=headers, json=body))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: remote)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    path = "/api/artifact-services/test"
    payload = {"service_id": "store", "url": url, "token_env": "TEST_ARTIFACT_TOKEN"}
    assert client.post(path, json={**payload, "url": "https://other.example.org"}).json()["state"] == "unverified"
    remote.get.assert_not_called()
    response = client.post(path, json=payload)
    assert response.json()["state"] == expected
    if status == 200 and not reported:
        assert "身份未验证" in response.json()["reason"]
    if reported == "another":
        assert "ID 与配置不一致" in response.json()["reason"]
    if status == 403:
        assert response.json()["reason"] == "没有共享目录读取权限"
    assert "secret-value" not in response.text
    remote.get.assert_awaited_once_with(url + "/health", headers={"X-Artifact-Token": "secret-value"})


def test_legacy_health_without_identity_still_allows_bound_download(tmp_path, monkeypatch):
    import hashlib
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from tcode_agent.artifact_transport import materialize_output_bundle

    content = b"legacy fixture"
    responses = {
        "/health": json.dumps({"status": "ok", "mode": "restricted"}).encode(),
        "/api/v1/bundles/bundle_fixture": json.dumps({"state": "ready", "bundle_id": "bundle_fixture",
            "files": [{"relative_path": "old.txt", "artifact_id": "artifact_fixture",
                       "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}]}).encode(),
        "/api/v1/artifacts/artifact_fixture/content": content,
    }
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("X-Artifact-Token") != "fixture-only":
                return self.send_error(403)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(responses[self.path])
        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            monkeypatch.setenv("NO_PROXY", "127.0.0.1")
            monkeypatch.delenv("TCODE_DESKTOP_MANAGED", raising=False)
            monkeypatch.setenv("TMCP_ARTIFACT_SERVICES", json.dumps({"lab": {"url": url, "token_env": "TEST_ARTIFACT_TOKEN"}}))
            monkeypatch.setenv("TEST_ARTIFACT_TOKEN", "fixture-only")
            monkeypatch.setenv("TMCP_ARTIFACT_SERVICE_ID", "lab")
            monkeypatch.setenv("TMCP_ARTIFACT_TARGET_SERVICES", '{"echo-mcp":"lab"}')
            monkeypatch.setattr(api, "require_current_user", lambda request: SimpleNamespace(
                role="admin", tenant_id=api.workdirs.default_tenant_id()))
            app = FastAPI()
            app.include_router(api.router)
            with TestClient(app) as client:
                check = client.post("/api/artifact-services/test", json={
                    "service_id": "lab", "url": url, "token_env": "TEST_ARTIFACT_TOKEN"}).json()
            assert check["state"] == "reachable" and "身份未验证" in check["reason"]
            materialize_output_bundle({"output_bundle": {"bundle_id": "bundle_fixture"}}, tmp_path,
                                      target_server="echo-mcp", automatic=True)
            assert (tmp_path / "lab--bundle_fixture/old.txt").read_bytes() == content
        finally:
            server.shutdown()
            thread.join()
