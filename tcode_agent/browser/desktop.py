"""Private desktop browser bridge; credentials arrive over startup stdin only."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import uuid
from pathlib import Path
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from .live import browser_live_paths, safe_browser_session_id, read_browser_live_state

from ..types import ToolResult

_config: dict | None = None
_pending_login_ids: dict[str, tuple[tuple[str, str, str, str], dict]] = {}


def desktop_login_is_pending(state: dict) -> bool:
    """A persisted prompt is not proof that the original browser call still exists."""
    return str(state.get("desktop_login_id") or "") in _pending_login_ids


def pending_browser_login(tenant: str, user: str, workspace: str, parent: str) -> dict | None:
    """Recover the current prompt by owner; never return credentials or DOM."""
    scope = (tenant, user, workspace, parent)
    return next((dict(event) for owner, event in reversed(_pending_login_ids.values()) if owner == scope), None)


def configure_desktop_browser(payload: dict | None) -> None:
    global _config
    _config = payload
    _pending_login_ids.clear()
    os.environ["TCODE_DESKTOP_BROWSER_READY"] = "1" if payload else "0"


def _account_profile(tenant: str, user: str, workspace: str, channel: str) -> str:
    if not all((tenant, user, workspace, channel)):
        raise ValueError("Browser account requires a workspace owner")
    return hashlib.sha256(json.dumps([tenant, user, workspace, channel]).encode()).hexdigest()


async def manage_browser_account(tenant: str, user: str, workspace: str, channel: str, operation: str) -> dict:
    from .channel_reads import LOGIN_URLS

    if not _config or channel not in LOGIN_URLS or operation not in {"login", "logout"}:
        raise ValueError("Browser account management is unavailable")
    profile = _account_profile(tenant, user, workspace, channel)
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(_config["socket_path"]), 3)
    try:
        request = {"token": _config["secret"], "account": operation, "profile": profile, "url": LOGIN_URLS[channel]}
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        result = json.loads(await asyncio.wait_for(reader.readline(), 12))
        if result.get("ok") is not True:
            if result.get("error") == "browser_url_blocked":
                raise PermissionError("browser_url_blocked")
            raise ValueError("Browser account operation did not complete")
        return result
    finally:
        writer.close()
        await writer.wait_closed()


async def execute_desktop_browser(args: dict, context) -> ToolResult:
    if not _config:
        return ToolResult(title="browser", output="", error="Tcode 内置浏览器未就绪。不会自动切换到本地浏览器。")
    extra = context.extra or {}
    session = str(context.session_id)
    scope = tuple(str(extra.get(key) or "") for key in ("tenant_id", "user_id", "workspace_id"))
    owner = ":".join(scope) + ":" + session
    parent = str(extra.get("parent_session_id") or session)
    checkpoint_scope = (*scope, parent)
    checkpoint_id = str(extra.get("browser_checkpoint_id") or "browser-" + uuid.uuid4().hex)
    checkpoint_written = False
    requested_session = args.get("session")
    # A model-supplied session name never changes the authenticated owner.
    args = dict(args, session=safe_browser_session_id(args.get("session") or session))
    if args.get("channel"):
        channel_scope = json.dumps([owner, args["channel"], args["session"]])
        args["session"] = "channel-" + hashlib.sha256(channel_scope.encode()).hexdigest()[:40]
    workdir = Path(str(extra.get("control_workdir") or extra.get("workdir") or os.environ.get("TCODE_WORKDIR") or os.getcwd())).resolve()
    paths = browser_live_paths(workdir, args["session"])
    paths.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(_config["socket_path"], limit=12 * 1024 * 1024), 3)
    login_id = uuid.uuid4().hex
    try:
        request = {"token": _config["secret"], "owner": owner, "call_id": str(getattr(context, "call_id", "") or uuid.uuid4()), "args": args,
                   "login_confirmation": (bool(args.get("channel_read")) or os.environ.get("TCODE_DESKTOP_BROWSER_LOGIN_PREVIEW") == "1")
                   and callable(extra.get("__agent_event_emit__"))}
        if args.get("channel") and (args.get("channel_read") or args["action"] == "close") and all(scope):
            request["profile"] = _account_profile(*scope, args["channel"])
        writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
        await writer.drain()
        try:
            while True:
                line = await asyncio.wait_for(reader.readline(), 12 * 60)
                data = json.loads(line) if line else {"error": "browser_connection_lost"}
                if data.get("login_wait") is not True:
                    break
                site = str(data.get("site") or "")
                state = {"desktop_browser": True, "workspace_id": str(extra.get("workspace_id") or ""),
                         "agent_session_id": session, "status": "waiting_login", "title": site,
                         "desktop_login_id": login_id}
                event = {**state, "kind": "browser_session", "session_id": session,
                         "parent_session_id": parent, "browser_session_id": args["session"],
                         "message": f"等待登录 {site}"}
                _pending_login_ids[login_id] = ((*scope, parent), event)
                if args.get("channel_read") and not extra.get("browser_checkpoint_id") and not checkpoint_written:
                    from . import checkpoint
                    from ..runtime.async_resume import active_origin

                    value = (args.get("channel_value") or "").strip()
                    if args["channel_action"] not in {"search", "subreddit"} and value.startswith("https://"):
                        url = urlsplit(value)
                        value = urlunsplit((url.scheme, url.netloc, url.path, "", ""))
                    checkpoint.save(checkpoint_scope, {
                        "channel": args["channel"], "channel_action": args["channel_action"],
                        "channel_value": value, "channel_limit": args.get("channel_limit", 5),
                        "browser_session": safe_browser_session_id(requested_session) if requested_session else None, "agent_session": session,
                        "origin_message_id": str((active_origin.get() or {}).get("message_id") or ""),
                        "site": site,
                    }, checkpoint_id)
                    checkpoint_written = True
                paths.frame.unlink(missing_ok=True)
                temporary = paths.state.with_suffix(".tmp")
                temporary.write_text(json.dumps(state), encoding="utf-8")
                temporary.chmod(0o600)
                temporary.replace(paths.state)
                emit = extra.get("__agent_event_emit__")
                if callable(emit):
                    try:
                        await emit(event)
                    except (ConnectionError, RuntimeError):
                        # The authenticated pending endpoint can reattach the view.
                        pass
        except asyncio.TimeoutError:
            return ToolResult(
                title=f"browser {args['action']}",
                output="",
                error="桌面浏览器响应超时。当前操作已停止；如果正在接管浏览器，请先交还控制后重试。",
                metadata={
                    "browser": True,
                    "desktop_browser": True,
                    "browser_session_id": args["session"],
                    "terminal_tool_error": True,
                    "retryable": True,
                    "should_end_turn": True,
                },
            )
    finally:
        _pending_login_ids.pop(login_id, None)
        writer.close()
        await writer.wait_closed()
    screenshot = data.pop("screenshot", "")
    image_path = ""
    if screenshot:
        root = Path(str(extra.get("workdir") or workdir)).resolve() / ".tcode" / "browser" / "desktop" / session
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = root / f"{uuid.uuid4().hex}.jpg"
        target.write_bytes(base64.b64decode(screenshot, validate=True))
        target.chmod(0o600)
        image_path = str(target)
        temp_frame = paths.frame.with_suffix(".tmp")
        temp_frame.write_bytes(target.read_bytes())
        temp_frame.chmod(0o600)
        temp_frame.replace(paths.frame)
    error = str(data.pop("error", ""))
    error_code = error.split(":", 1)[0]
    if checkpoint_written and error_code != "browser_connection_lost":
        from . import checkpoint
        checkpoint.finish(checkpoint_scope, checkpoint_id)
    if args.get("channel_read"):
        data.update(channel=args["channel"], action=args.get("channel_action", "read"),
                    backend="desktop-browser", fetched_at=datetime.now(UTC).isoformat())
    user_errors = {
        "browser_document_http_error": "目标站点主文档返回 HTTP 错误，没有取得可验证的页面内容。",
        "browser_document_not_committed": "未确认目标主文档已成功加载，地址栏不能作为读取证据。",
        "browser_document_empty": "主文档没有可读取的正文或交互元素，未取得页面证据。",
        "browser_connection_lost": "原浏览器连接已失效，无法继续。请重新打开页面并按需登录。",
        "browser_login_cancelled": "已取消或超时，未继续读取受登录保护的内容。",
        "browser_login_unverified": "无法确认原页面已恢复访问，未继续执行。",
        "browser_channel_target_mismatch": "站点未到达指定读取页面，没有可验证内容。",
        "browser_channel_no_evidence": "未提取到此次动作所需的内容，不能确认读取成功。",
        "browser_url_blocked": "内置浏览器已拦截非公网地址。若站点使用了代理的 Fake-IP，请让系统 DNS 返回该站点真实的公网地址后再试。",
        "browser_login_required": "网页需要人工登录，当前操作未完成。",
        "browser_human_control_active": "你正在接管浏览器。请点击“交还给 Agent”，再让任务继续；本次结果尚未验证。",
        "browser_observation_timeout": "页面读取超时，无法确认本次操作结果。请重新观察页面，不要把先前内容当作已验证结果。",
        "browser_navigation_timeout": "网页加载超时，请检查网络或换个网址重试。",
        "stale_ref": "页面已更新，之前的元素编号已失效。请重新观察，并使用新的元素编号和页面版本；不要原样重试。",
    }
    error = user_errors.get(error_code, error)
    previous = read_browser_live_state(workdir, args["session"])
    if error:
        data.update({"url": previous.get("url", ""), "title": previous.get("title", ""), "page_revision": previous.get("frame_seq", 0),
                     "verified": False, "instruction": "任务未完成。仅报告已经观察到的事实，不得猜测搜索结果顺序或广告属性。"})
    else:
        # Keep titles and page identity before links, within the tool's text budget.
        elements = data.pop("elements", [])
        data["headings"] = [str(title)[:160] for title in data.get("headings", [])][:20]
        data["text"] = str(data.get("text", ""))[:4000]
        data["elements"] = []
        for element in elements:
            data["elements"].append(element)
            if len(json.dumps(data, ensure_ascii=False)) > 10000:
                data["elements"].pop()
                data["elements_truncated"] = True
                break
    if args["action"] == "close":
        paths.frame.unlink(missing_ok=True)
    state = {"desktop_browser": True, "workspace_id": str(extra.get("workspace_id") or ""),
        "agent_session_id": session, "status": "closed" if data.get("closed") else "paused" if error_code == "browser_human_control_active" else "error" if error else "ready",
        "url": data.get("url", ""), "title": data.get("title", ""), "action": args["action"],
        "frame_seq": data.get("page_revision", 0), "control_mode": "human" if error_code == "browser_human_control_active" else "agent", "error": error,
        "updated_at": datetime.now(UTC).isoformat()}
    temp_state = paths.state.with_suffix(".tmp")
    temp_state.write_text(json.dumps(state), encoding="utf-8")
    temp_state.chmod(0o600)
    temp_state.replace(paths.state)
    terminal_error = error_code in {"browser_human_control_active", "browser_navigation_timeout", "browser_observation_timeout",
                                   "browser_login_required", "browser_login_cancelled", "browser_login_unverified", "browser_login_pending", "browser_connection_lost",
                                   "browser_url_blocked", "browser_channel_target_mismatch", "browser_channel_no_evidence"}
    return ToolResult(title=f"browser {args['action']}", output=json.dumps(data, ensure_ascii=False), error=error or None,
        metadata={"browser": True, "desktop_browser": True, "browser_session_id": args["session"],
            "error_code": error_code, "verification_status": "UNVERIFIED" if error else "",
            "terminal_tool_error": terminal_error,
            "should_end_turn": terminal_error,
            "browser_live": True, "browser_live_frame_seq": state["frame_seq"],
            "page_revision": data.get("page_revision", 0), "url": data.get("url", ""), "title": data.get("title", ""),
            "model_image_path": image_path, "model_image_mime": "image/jpeg" if image_path else "",
            "execution_environment": {"location": "desktop_browser", "label": "Tcode 内置浏览器", "sandboxed": True,
                "isolation": "chromium_renderer", "container_isolation": False}})
