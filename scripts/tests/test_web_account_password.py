from fastapi import FastAPI
from fastapi.testclient import TestClient

from tcode_agent.server import auth, auth_api
import pytest


@pytest.mark.parametrize("revoke", ["logout", "switch", "expire"])
def test_passive_preview_stops_before_next_frame(tmp_path, monkeypatch, revoke):
    from tcode_agent.server import browser_live_api as live
    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.setattr(live.ServerState, "_workdir", str(tmp_path))
    monkeypatch.setenv("TCODE_AUTH_REQUIRED", "1")
    monkeypatch.setenv("TCODE_BOOTSTRAP_EMAIL", "owner@example.test")
    monkeypatch.setenv("TCODE_BOOTSTRAP_PASSWORD", "fixture-owner-password")
    owner = {"email": "owner@example.test", "password": "fixture-owner-password"}
    member = {"email": "member@example.test", "password": "fixture-member-password"}
    auth.create_user_account(tenant_id=auth.workdir_utils.default_tenant_id(), **member)
    app = FastAPI()
    app.include_router(auth_api.router)
    app.include_router(live.router)
    with TestClient(app) as client:
        user = client.post("/api/auth/login", json=owner).json()["user"]
        workspace = auth.workdir_utils.resolve_user_workdir_path(str(tmp_path), "test", tenant_id=user["tenant_id"], user_id=user["user_id"])
        paths = live.browser_live_paths(workspace, "preview")
        paths.root.mkdir(parents=True)
        paths.state.write_text('{"frame_seq":1}')
        paths.frame.write_bytes(b"first")
        with client.websocket_connect("/ws/browser?workspace_id=test&browser_session_id=preview") as socket:
            assert socket.receive_json()["frame_seq"] == 1
            assert socket.receive_bytes() == b"first"
            assert socket.receive_json()["type"] == "browser_heartbeat"
            if revoke == "switch":
                client.post("/api/auth/login", json=member).raise_for_status()
            elif revoke == "logout":
                client.post("/api/auth/logout").raise_for_status()
            else:
                with auth._connect() as conn:
                    conn.execute("UPDATE auth_sessions SET expires_at=0")
            paths.frame.write_bytes(b"second")
            paths.state.write_text('{"frame_seq":2}')
            assert socket.receive() == {"type": "websocket.close", "code": 4401, "reason": ""}


def test_web_password_belongs_to_authenticated_user(tmp_path, monkeypatch):
    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.delenv("TCODE_ENTERPRISE_MODE", raising=False)
    monkeypatch.delenv("TCODE_DESKTOP_MANAGED", raising=False)
    monkeypatch.setenv("TCODE_AUTH_REQUIRED", "1")
    monkeypatch.setenv("TCODE_BOOTSTRAP_EMAIL", "owner@example.test")
    monkeypatch.setenv("TCODE_BOOTSTRAP_PASSWORD", "old-owner-password")
    member = {"email": "member@example.test", "password": "old-member-password"}
    auth.create_user_account(tenant_id=auth.workdir_utils.default_tenant_id(), **member)
    app = FastAPI()
    from tcode_agent.server.chat_socket import router as chat_router
    app.include_router(chat_router)
    app.include_router(auth_api.router)
    path = "/api/auth/password"
    change = {"new_password": "new-member-password", "user_id": "owner", "desktop_default_identity": True}
    with TestClient(app) as client, TestClient(app) as other:
        assert client.post(path, json=change).status_code == 401
        assert client.post("/api/auth/login", json=member).json()["user"]["password_mode"] == "change"
        assert other.post("/api/auth/login", json=member).status_code == 200
        assert client.post(path, json=change).status_code == 403
        assert client.post(path, json={**change, "current_password": "old-owner-password"}).status_code == 403
        assert client.post(path, json={**change, "current_password": member["password"], "new_password": "short"}).status_code == 400
        with other.websocket_connect("/ws/chat") as old_chat, client.websocket_connect("/ws/chat") as current_chat:
            assert client.post(path, json={**change, "current_password": member["password"]}).status_code == 204
            old_chat.send_json({"action": "replay"})
            assert old_chat.receive() == {"type": "websocket.close", "code": 4401, "reason": ""}
            current_chat.send_json({"action": "replay"})
            assert current_chat.receive_json()["code"] == "invalid_replay_request"
        assert client.get("/api/auth/me").json()["user"]["email"] == member["email"]
        assert other.get("/api/auth/me").status_code == 401
        assert other.post("/api/auth/login", json=member).status_code == 401
        assert other.post("/api/auth/login", json={**member, "password": change["new_password"]}).status_code == 200
        assert other.post("/api/auth/login", json={"email": "owner@example.test", "password": "old-owner-password"}).status_code == 200
        monkeypatch.setenv("TCODE_AUTH_REQUIRED", "0")
        assert "password_mode" not in client.get("/api/auth/me").json()["user"]
        assert client.post(path, json={**change, "current_password": change["new_password"]}).status_code == 403
