from tcode_agent.server import auth


def test_desktop_preserves_a_sole_legacy_owner_without_a_login(tmp_path, monkeypatch):
    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.setenv("TCODE_BOOTSTRAP_PASSWORD", "only-for-this-legacy-test")
    monkeypatch.setenv("TCODE_BOOTSTRAP_USER_ID", "original-owner")
    monkeypatch.setenv("TCODE_DEFAULT_USER_ID", "local-user")
    monkeypatch.delenv("TCODE_DESKTOP_SINGLE_USER", raising=False)
    auth.ensure_auth_store()
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("TCODE_DESKTOP_SINGLE_USER", "1")
    assert auth.desktop_local_user().user_id == "original-owner"
    auth.create_user_account(
        tenant_id=auth.workdir_utils.default_tenant_id(),
        email="another@example.test", password="another-fixture-password",
    )
    assert auth.desktop_local_user().user_id == "original-owner"
    with auth._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0


def test_desktop_never_merges_multiple_existing_users(tmp_path, monkeypatch):
    monkeypatch.delenv("TCODE_DESKTOP_SINGLE_USER", raising=False)
    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.setenv("TCODE_BOOTSTRAP_PASSWORD", "only-for-this-legacy-test")
    monkeypatch.setenv("TCODE_BOOTSTRAP_USER_ID", "local-user")
    monkeypatch.setenv("TCODE_DEFAULT_USER_ID", "local-user")
    auth.create_user_account(tenant_id=auth.workdir_utils.default_tenant_id(),
                             email="second@example.test", password="another-fixture-password")
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("TCODE_DESKTOP_SINGLE_USER", "1")
    user = auth.desktop_local_user()
    assert user.user_id not in {"local-user", "second"}
    assert auth.desktop_local_user() == user


def test_legacy_account_logout_and_history_survive_desktop_restart(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tcode_agent.server import auth_api
    from tcode_agent.server.desktop_launcher import DesktopGate, GATE_COOKIE

    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.setenv("TCODE_BOOTSTRAP_EMAIL", "owner@example.test")
    monkeypatch.setenv("TCODE_BOOTSTRAP_PASSWORD", "original-fixture-password")
    monkeypatch.setenv("TCODE_AUTH_REQUIRED", "1")
    monkeypatch.delenv("TCODE_DESKTOP_SINGLE_USER", raising=False)
    auth.ensure_auth_store()
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("TCODE_DESKTOP_SINGLE_USER", "1")
    app = FastAPI()
    app.include_router(auth_api.router)

    def launch():
        gate = DesktopGate(app, port=8080, token="fixture", local_user=auth.desktop_local_user())
        return TestClient(gate, base_url="http://127.0.0.1:8080", cookies={GATE_COOKIE: "fixture"})

    member = {"email": "other@example.test", "password": "member-fixture-password"}
    owner = {"email": "owner@example.test", "password": "original-fixture-password"}
    register = "/api/auth/desktop-accounts"
    change_password = "/api/auth/desktop-password"
    new_password = {"new_password": "new-fixture-password"}
    with TestClient(app) as web_client:
        assert web_client.post(register, json=member).status_code == 404
        assert web_client.post(change_password, json=new_password).status_code == 404
    with launch() as client:
        assert client.get("/api/auth/me").json()["user"]["email"] == owner["email"]
        assert client.get("/api/auth/me").json()["user"]["desktop_password_mode"] == "setup"
        assert client.post(change_password, json={"new_password": "short"}).status_code == 400
        assert client.post(change_password, json=new_password).status_code == 204
        assert auth.desktop_password_setup_pending(auth.desktop_local_user()) is False
        assert client.post(change_password, json=new_password).status_code == 403
        assert client.get("/api/auth/me").json()["user"]["desktop_password_mode"] == "change"
        assert client.post(register, json=member).status_code == 409
        assert client.get("/api/auth/desktop-accounts").json() == {
            "accounts": [owner["email"]], "local_workspace": False, "can_register": False,
        }
        assert client.post("/api/auth/login", json=member).status_code == 409
        assert client.post("/api/auth/logout").status_code == 200
    with launch() as client:
        assert client.get("/api/auth/me").status_code == 401
        assert client.post(change_password, json=new_password).status_code == 401
        assert client.post("/api/auth/local-workspace").status_code == 404
        assert client.get(register).json()["can_register"] is True
        assert client.post(register, json={**member, "password": "short"}).status_code == 400
        result = client.post(register, json={**member, "role": "admin", "user_id": "owner", "tenant_id": "other"})
        assert result.status_code == 201
        assert result.json()["user"]["role"] == "member"
        assert result.json()["user"]["tenant_id"] == auth.workdir_utils.default_tenant_id()
        assert result.json()["user"]["user_id"] != "owner"
        assert client.post(register, json=member).status_code == 409
        assert client.get(register).json()["accounts"] == [owner["email"]]
        assert client.post("/api/auth/login", json=member).status_code == 200
        assert client.get("/api/auth/desktop-accounts").json()["accounts"] == [member["email"], owner["email"]]
        assert client.post("/api/auth/login", json={**owner, "password": "wrong"}).status_code == 401
        assert client.get("/api/auth/me").json()["user"]["email"] == member["email"]
        other_session = auth.create_login_session(auth.authenticate(**member))
        assert client.post(change_password, json={**new_password, "user_id": "owner"}).status_code == 403
        assert client.post(change_password, json={**new_password, "current_password": member["password"]}).status_code == 204
        assert auth.user_from_session_id(other_session) is None
        assert client.get("/api/auth/me").json()["user"]["email"] == member["email"]
        assert client.post("/api/auth/login", json=owner).status_code == 401
        assert client.post("/api/auth/login", json={**owner, "password": new_password["new_password"]}).status_code == 200
        assert client.get("/api/auth/me").json()["user"]["email"] == owner["email"]


def test_explicit_login_fixture_offers_recent_accounts_only(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tcode_agent.server import auth_api
    from tcode_agent.server.desktop_launcher import DesktopGate, GATE_COOKIE

    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.setenv("TCODE_AUTH_REQUIRED", "1")
    monkeypatch.setenv("TCODE_BOOTSTRAP_EMAIL", "fixture@example.test")
    monkeypatch.setenv("TCODE_BOOTSTRAP_PASSWORD", "fixture-test-password")
    app = FastAPI()
    app.include_router(auth_api.router)
    gate = DesktopGate(app, port=8080, token="fixture")
    path = "/api/auth/desktop-accounts"
    with TestClient(gate, base_url="http://127.0.0.1:8080", cookies={GATE_COOKIE: "fixture"}) as client:
        assert client.get(path).json() == {"accounts": [], "local_workspace": False, "can_register": False}
        assert client.post("/api/auth/login", json={"email": "fixture@example.test", "password": "fixture-test-password"}).is_success
        assert client.post("/api/auth/logout").is_success
        assert client.get(path).json()["accounts"] == ["fixture@example.test"]
        assert client.post(path, json={}).status_code == 404
