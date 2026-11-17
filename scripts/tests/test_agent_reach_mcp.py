import json
import os
from subprocess import CompletedProcess

import pytest

from tcode_agent.server import agent_reach_mcp as reach


def test_desktop_reader_is_registered():
    from tcode_agent.tools.registry import ToolRegistry

    assert ToolRegistry.get("internet_content").id == "internet_content"


def test_desktop_channel_routes_are_fixed_and_require_action_specific_evidence():
    from tcode_agent.browser.channel_reads import channel_read

    url, rule = channel_read("bilibili", "search", "Python & keyword=other", 2)
    assert url.endswith("Python%20%26%20keyword%3Dother")
    assert rule["link_host"] == "www.bilibili.com" and rule["limit"] == 2
    url, rule = channel_read("reddit", "comments", "https://www.reddit.com/r/python/comments/abc/title/?tracking=1", 3)
    assert url.endswith("/r/python/comments/abc/title/") and rule["limit"] == 3
    assert "shreddit-comment" in rule["selector"]
    for value in ("https://www.reddit.com@evil.example/r/python/comments/abc",
                  "https://www.reddit.com:443/r/python/comments/abc",
                  "https://www.reddit.com/r/python/"):
        with pytest.raises(ValueError, match="BAD_INPUT"):
            channel_read("reddit", "read", value, 5)


def test_pending_login_lookup_rejects_cross_owner_and_parent(monkeypatch):
    from tcode_agent.browser import desktop

    event = {"status": "waiting_login", "browser_session_id": "example", "title": "example.com"}
    monkeypatch.setattr(desktop, "_pending_login_ids", {"pending": (("tenant", "user", "ws1", "parent"), event)})
    assert desktop.pending_browser_login("tenant", "user", "ws1", "parent") == event
    assert desktop.pending_browser_login("tenant", "other", "ws1", "parent") is None
    assert desktop.pending_browser_login("tenant", "user", "ws2", "parent") is None
    assert desktop.pending_browser_login("tenant", "user", "ws1", "other") is None


def test_restart_checkpoint_is_owner_scoped_and_cancelled_checkpoint_cannot_resume(monkeypatch, tmp_path):
    from tcode_agent.browser import checkpoint

    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    scope = ("owner-tenant", "user", "ws1", "parent")
    checkpoint.save(scope, {"channel": "reddit", "site": "www.reddit.com"}, "browser-attempt")
    assert checkpoint.get_interrupted(scope) == {}  # still in the original process
    monkeypatch.setattr(checkpoint, "BOOT_ID", "restarted")
    assert checkpoint.get_interrupted(scope)["identifier"] == "browser-attempt"
    assert checkpoint.get_interrupted(("owner-tenant", "user", "ws2", "parent")) == {}
    checkpoint.finish(scope, "different-attempt")
    assert checkpoint.get_interrupted(scope)["identifier"] == "browser-attempt"
    checkpoint.finish(scope)
    assert checkpoint.get_interrupted(scope) == {}


def test_experimental_channels_require_opt_in_and_cannot_grant_operator_denied(monkeypatch, tmp_path):
    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.setenv("TCODE_AGENT_REACH_CHANNELS", "x,reddit")
    assert reach.enabled_channels() == {"reddit"}
    reach.write_channel_settings("x", enabled=True)
    assert reach.enabled_channels() == {"x", "reddit"}
    monkeypatch.setenv("TCODE_AGENT_REACH_CHANNELS", "reddit")
    assert reach.enabled_channels() == {"reddit"}


@pytest.mark.parametrize("cancel", [False, True])
def test_restart_resumes_only_the_recorded_read_and_publishes_verified_receipt(monkeypatch, tmp_path, cancel):
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException
    from tcode_agent.browser import checkpoint
    from tcode_agent.runtime import async_completion
    from tcode_agent.server import browser_live_api as api
    from tcode_agent.types import ToolResult

    scope = ("tenant", "user", "ws1", "parent")
    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.setenv("TCODE_DESKTOP_BROWSER_READY", "1")
    monkeypatch.setenv("TCODE_AGENT_REACH_CHANNELS", "reddit")
    checkpoint.save(scope, {"channel": "reddit", "channel_action": "subreddit", "channel_value": "python",
                            "channel_limit": 2, "agent_session": "child", "origin_message_id": "original"}, "browser-recover")
    monkeypatch.setattr(checkpoint, "BOOT_ID", "new-boot")
    monkeypatch.setattr(api, "_browser_resume_scope", lambda *_: (scope, SimpleNamespace(workspace_id="ws1")))
    monkeypatch.setattr(api, "_resolve_user_workdir_path", lambda *_: tmp_path)
    monkeypatch.setattr(api.ServerState, "is_running", lambda *_: False)

    async def get_session(*_, **owner):
        assert owner["workspace_id"] == "ws1"
        return SimpleNamespace(id="parent", metadata={})

    original = SimpleNamespace(id="original", role="user", content="读取 Python 版块")
    monkeypatch.setattr(api.SessionManager, "get", get_session)
    monkeypatch.setattr(api.SessionManager, "visible_messages", lambda *_: [original])
    calls = []
    records = []
    started = asyncio.Event()

    async def execute(args, context):
        calls.append(args)
        assert args["action"] == "open" and args["url"] == "https://www.reddit.com/r/python/"
        assert context.session_id == "child" and context.extra["browser_checkpoint_id"] == "browser-recover"
        if cancel:
            started.set()
            await asyncio.Future()
        return ToolResult(title="browser", output=json.dumps({"items": [{"title": "Example"}]}))

    async def record(**data):
        records.append(data)
        assert data["origin_turn_id"] == "original"
        assert data["continuation"]["result_receipt"]["validated"] is True
        return {"id": "stored"}

    monkeypatch.setattr(api, "execute_desktop_browser", execute)
    monkeypatch.setattr(async_completion, "record_completion_event", record)
    request = SimpleNamespace(headers={"content-type": "application/json"})
    async def run():
        task = asyncio.create_task(api.resume_browser_read(request, "ws1", "parent"))
        if cancel:
            await asyncio.wait_for(started.wait(), 1)
            api.cancel_browser_read(scope)
            with pytest.raises(HTTPException, match="恢复读取已取消"):
                await asyncio.wait_for(task, 1)
        else:
            assert await task == {"identifier": "browser-recover"}

    asyncio.run(run())
    assert len(records) == (0 if cancel else 1)
    assert scope not in api._resuming_reads
    with pytest.raises(HTTPException, match="没有可恢复的读取"):
        asyncio.run(api.resume_browser_read(request, "ws1", "parent"))
    assert len(calls) == 1


def test_desktop_controls_are_persistent_and_cannot_override_operator(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tcode_agent.server import internet_content_config_api as api

    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("TCODE_AGENT_REACH_CHANNELS", "v2ex")
    monkeypatch.setattr(api, "_authorize", lambda request: None)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    endpoint = "/api/config/internet-content"
    assert client.put(f"{endpoint}/github", json={"enabled": True}).status_code == 403
    assert client.put(f"{endpoint}/v2ex", json={"enabled": "false"}).status_code == 422
    assert client.put(f"{endpoint}/v2ex", json={"enabled": False}).status_code == 200
    assert reach.read_public_channel("v2ex", "read", "1000")["error"] == "CHANNEL_DISABLED"
    assert client.post(f"{endpoint}/v2ex/check", json={}).status_code == 409
    assert client.put(f"{endpoint}/v2ex", json={"enabled": True}).status_code == 200
    monkeypatch.setattr(reach, "read_public_channel", lambda *args, **kwargs: {"ok": True})
    assert client.post(f"{endpoint}/v2ex/check", json={}).status_code == 200
    row = next(row for row in client.get(endpoint).json()["channels"] if row["name"] == "v2ex")
    assert row["enabled"] and row["last_check"]["ok"] and row["last_check"]["checked_at"]


def test_transcription_canonicalizes_urls_and_requires_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.delenv("TCODE_AGENT_REACH_CHANNELS", raising=False)
    monkeypatch.delenv("TCODE_GROQ_API_KEY", raising=False)
    assert reach.read_public_channel("youtube", "transcribe", "jNQXAC9IVRw")["error"] == "NEED_SETUP"
    assert reach.read_public_channel("youtube", "transcribe", "https://evil.example/jNQXAC9IVRw")["error"] == "BAD_INPUT"
    assert reach.read_public_channel("xiaoyuzhou", "transcribe", "https://evil.example/episode/65aa0fc6b5ed5c6e535e1ab6")["error"] == "BAD_INPUT"
    assert reach.read_public_channel("xiaoyuzhou", "transcribe", "https://www.xiaoyuzhoufm.com/episode/65aa0fc6b5ed5c6e535e1ab6")["error"] == "NEED_SETUP"


def test_audio_upload_is_explicit_and_key_is_not_in_child_args_or_environment(monkeypatch, tmp_path):
    from tcode_agent.server import media_transcription as media

    monkeypatch.setattr(media.shutil, "which", lambda command: "/usr/local/bin/" + command)

    def run(argv, *, input, env, **kwargs):
        assert input == "only-stdin"
        assert all("only-stdin" not in field for field in (*argv, *env.values()))
        assert argv[-2:] == ["youtube", "https://www.youtube.com/watch?v=jNQXAC9IVRw"]
        return CompletedProcess(argv, 0, json.dumps("sample transcript"))

    monkeypatch.setattr(media.subprocess, "run", run)
    response = media.transcribe(tmp_path / "python", "youtube", "jNQXAC9IVRw", "only-stdin")
    assert response["items"][0]["data"]["kind"] == "audio_transcript"


def test_disabled_channels_are_listed_but_not_executed(monkeypatch):
    monkeypatch.setenv("TCODE_AGENT_REACH_CHANNELS", "github,v2ex")
    rows = reach.channel_catalog()["channels"]
    assert all(row["enabled"] == (row["channel"] in {"github", "v2ex"}) for row in rows)
    assert reach.read_public_channel("youtube", "read", "jNQXAC9IVRw")["error"] == "CHANNEL_DISABLED"


def test_unapproved_login_and_wrong_revision_never_launch(monkeypatch, tmp_path):
    python = tmp_path / "bin/python"
    python.parent.mkdir()
    python.touch()
    monkeypatch.setenv("TCODE_AGENT_REACH_PYTHON", str(python))

    def forbidden(*args, **kwargs):
        raise AssertionError("unapproved command was launched")

    monkeypatch.setattr(reach.subprocess, "run", forbidden)
    assert reach.read_public_channel("bilibili", "subtitle", "BV1GJ411x7h7")["error"] == "BROWSER_REQUIRED"
    assert reach.read_public_channel("boss", "search", "Python")["error"] == "BROWSER_REQUIRED"
    assert reach.read_public_channel("github", "search", "Python")["error"] == "NEED_SETUP"


def test_pins_backend_and_does_not_inherit_credentials(monkeypatch, tmp_path):
    python = tmp_path / "bin/python"
    python.parent.mkdir()
    python.touch()
    metadata = tmp_path / "lib/python3.13/site-packages/agent_reach-1.6.1.dist-info/direct_url.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"url": "https://github.com/Panniantong/Agent-Reach.git",
                                   "vcs_info": {"commit_id": reach.REVISION}}))
    monkeypatch.setenv("TCODE_AGENT_REACH_PYTHON", str(python))
    monkeypatch.setenv("GH_TOKEN", "must-not-be-inherited")

    def run(argv, *, env, **kwargs):
        assert argv[4:] == ["github", "search", "--backend", "github-api", "--timeout", "18",
                            "--limit", "5", "--", "--backend opencli"]
        assert "GH_TOKEN" not in env
        return CompletedProcess(argv, 0, json.dumps({
            "schema_version": 1, "channel": "github", "action": "search", "backend": "github-api",
            "ok": True, "items": [
                {"title": "project", "url": "https://github.com/org/repo", "raw": {"ignored": True}},
                {"title": "not GitHub", "url": "https://example.com/"},
            ],
        }).encode())

    monkeypatch.setattr(reach.subprocess, "run", run)
    result = reach.read_public_channel("github", "search", "--backend opencli")
    assert result["items"] == [{"title": "project", "url": "https://github.com/org/repo"}]
    assert result["partial"]


@pytest.mark.skipif(os.environ.get("TCODE_AGENT_REACH_LIVE_TEST") != "1", reason="requires pinned CLI and live upstream")
@pytest.mark.parametrize(("channel", "action", "value", "expected"), [
    ("github", "search", "yt-dlp", "ok"),
    ("github", "read", "yt-dlp/yt-dlp", "ok"),
    ("github", "read", "https://github.com/python/cpython/issues/100000", "ok"),
    ("github", "read", "https://github.com/yt-dlp/yt-dlp/pull/1233", "ok"),
    ("v2ex", "hot", "", "ok"),
    ("v2ex", "node", "python", "ok"),
    ("v2ex", "read", "1000", "ok"),
    ("v2ex", "user", "Livid", "ok"),
    ("bilibili", "hot", "", "ok"),
    ("bilibili", "read", "BV1GJ411x7h7", "ok"),
    ("youtube", "read", "jNQXAC9IVRw", "ok"),
    ("youtube", "search", "Python tutorial", "ok"),
    ("youtube", "subtitle", "jNQXAC9IVRw", "ok"),
    ("xueqiu", "search", "茅台", "ok"),
    ("xueqiu", "quote", "SH600519", "ok"),
    ("github", "read", "tcode-absent-check-a3d12/no-such-repo", "NOT_FOUND"),
    ("v2ex", "read", "9999999999", "NOT_FOUND"),
    ("bilibili", "read", "BV0000000000", "UPSTREAM_BROKEN"),
])
def test_live_public_actions(channel, action, value, expected):
    result = reach.read_public_channel(channel, action, value, limit=2)
    assert result.get("error", "ok") == expected
    if expected == "ok":
        assert result["items"] and result["fetched_at"]


def test_persisted_login_prompt_without_live_call_is_expired():
    from tcode_agent.browser.desktop import desktop_login_is_pending

    assert not desktop_login_is_pending({"desktop_login_id": "expired-call"})
