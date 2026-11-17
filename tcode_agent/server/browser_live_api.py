"""Browser live-session WebSocket transport.

Routes retain their original paths and are registered by app.py.
"""

import asyncio
import json
import math
import os
import uuid

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from ..browser import (
    browser_live_paths,
    read_browser_live_state,
    safe_browser_session_id,
)
from ..browser import checkpoint
from ..browser.desktop import desktop_login_is_pending, execute_desktop_browser, pending_browser_login
from ..session.manager import SessionManager
from ..tools.base import ToolContext
from . import workdirs as workdir_utils
from .workspace_scope import (
    _resolve_user_workdir_path,
    _workspace_context_from_request,
    _workspace_context_from_websocket,
)
from .auth import auth_required, current_user_from_websocket, require_current_user
from .state import ServerState

router = APIRouter()
_resuming_reads: dict[tuple[str, ...], asyncio.Task] = {}


def cancel_browser_read(scope: tuple[str, ...]) -> None:
    checkpoint.finish(scope)
    if task := _resuming_reads.get(scope):
        task.cancel()


def _browser_resume_scope(request: Request, workspace_id: str, session_id: str):
    require_current_user(request)
    owner = _workspace_context_from_request(request, workspace_id=workspace_id)
    workdir_utils.assert_workspace_active(ServerState.get_workdir(), owner)
    return (owner.tenant_id, owner.user_id, owner.workspace_id, session_id), owner


@router.get("/api/browser/pending-login")
async def get_pending_browser_login(request: Request, workspace_id: str, session_id: str):
    scope, _ = _browser_resume_scope(request, workspace_id, session_id)
    saved = checkpoint.get_interrupted(scope) if os.environ.get("TCODE_DESKTOP_BROWSER_READY") == "1" else {}
    return {"pending": pending_browser_login(*scope),
            "interrupted": {"site": str(saved.get("site") or "")} if saved else None,
            "resuming": scope in _resuming_reads}


@router.post("/api/browser/resume-read")
async def resume_browser_read(request: Request, workspace_id: str, session_id: str):
    """Retry only a saved browser GET; hand verified evidence to the existing continuation system."""
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise HTTPException(415, "恢复读取要求 JSON 请求")
    if os.environ.get("TCODE_DESKTOP_BROWSER_READY") != "1":
        raise HTTPException(409, "内置浏览器未就绪")
    scope, owner = _browser_resume_scope(request, workspace_id, session_id)
    if scope in _resuming_reads or ServerState.is_running(session_id):
        raise HTTPException(409, "原任务或恢复读取正在执行")
    _resuming_reads[scope] = asyncio.current_task()
    try:
        saved = checkpoint.get_interrupted(scope)
        if not saved:
            raise HTTPException(404, "没有可恢复的读取")
        session_owner = dict(zip(("tenant_id", "user_id", "workspace_id"), scope[:3]))
        session = await SessionManager.get(session_id, **session_owner)
        if session is None:
            raise HTTPException(404, "原会话已不存在")

        from ..browser.channel_reads import channel_read
        from ..runtime.async_completion import record_completion_event, session_completion_events
        from .agent_reach_mcp import enabled_channels

        identifier = saved["identifier"]
        if any(event.get("identifier") == identifier for event in session_completion_events(session)):
            checkpoint.finish(scope, identifier)
            return {"identifier": identifier}
        channel, action, value, limit = (saved[key] for key in ("channel", "channel_action", "channel_value", "channel_limit"))
        if channel not in enabled_channels():
            raise HTTPException(409, "请先启用此渠道")
        target, evidence = channel_read(channel, action, value, limit)
        original = next((item for item in SessionManager.visible_messages(session)
                         if item.id == saved.get("origin_message_id") and str(item.role) == "user"), None)
        if original is None:
            raise HTTPException(409, "原任务上下文已不可用，未重放任何操作")

        async def emit(event):
            await ServerState.send_session_event(session_id, event)

        context = ToolContext(session_id=saved["agent_session"], message_id=original.id, agent="browser",
                              abort=asyncio.Event(), extra={**session_owner, "parent_session_id": session_id,
                              "control_workdir": str(_resolve_user_workdir_path(owner.workspace_id, owner)),
                              "browser_checkpoint_id": identifier, "__agent_event_emit__": emit})
        result = await execute_desktop_browser({"action": "open", "session": saved.get("browser_session"), "url": target,
                                                 "channel": channel, "channel_action": action, "channel_read": evidence}, context)
        if result.error:
            if result.metadata.get("error_code") == "browser_login_cancelled":
                checkpoint.finish(scope, identifier)
            raise HTTPException(409, result.error)
        output = json.loads(result.output)
        if not output.get("items") or checkpoint.get_interrupted(scope).get("identifier") != identifier:
            raise HTTPException(409, "没有可验证内容，或恢复已取消")
        event = await record_completion_event(session_id=session_id, identifier=identifier, origin_turn_id=original.id,
            task_context={"session_id": session_id, "message_id": original.id, "request": original.content},
            continuation={"target_server": "tcode-desktop", "target_tool": "browser.channel.read",
                "status": "completed", "can_continue": True, "message": "浏览器读取已恢复；可从已验证结果继续原任务。",
                "result_summary": result.output, "result_receipt": {"status": "completed", "validated": True,
                "rejected_artifact_count": 0, "browser_read": [channel, action, value]}})
        if not event:
            raise HTTPException(409, "原会话已失效，未提交结果")
        checkpoint.finish(scope, identifier)
        return {"identifier": identifier}
    except asyncio.CancelledError:
        if not checkpoint.get_interrupted(scope):
            raise HTTPException(409, "恢复读取已取消") from None
        raise
    finally:
        _resuming_reads.pop(scope, None)

_CONTROL_ACTIONS = {
    "set_control", "click_at", "move", "drag", "scroll", "key_down", "key_up",
    "confirm_pending", "reject_pending",
}


def _write_control_request(paths, payload: dict) -> None:
    request_id = f"ui-{uuid.uuid4().hex}"
    target = paths.requests / f"{request_id}.json"
    temporary = target.with_name(f".{target.name}.tmp")
    paths.requests.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)


def _write_confirmation_decision(paths, *, confirmation_id: str, approved: bool) -> None:
    target = paths.confirmation
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(
        json.dumps({"confirmation_id": confirmation_id, "approved": approved}),
        encoding="utf-8",
    )
    os.replace(temporary, target)


@router.websocket("/ws/browser")
async def websocket_browser_live(websocket: WebSocket):
    """Stream the latest CDP screencast frame for an owned workspace session.

    Frames stay inside the authenticated TCode connection.  The browser
    container's CDP endpoint is never exposed to the client.
    """
    if auth_required():
        user = current_user_from_websocket(websocket)
        if user is None:
            await websocket.close(code=4401)
            return
        if user.tenant_status != "active":
            await websocket.close(code=4423)
            return
        websocket.state.current_user = user

    browser_session_id = str(
        websocket.query_params.get("browser_session_id") or ""
    ).strip()
    workspace_id = str(websocket.query_params.get("workspace_id") or "").strip()
    if (
        not browser_session_id
        or browser_session_id != safe_browser_session_id(browser_session_id)
        or not workspace_id
    ):
        await websocket.close(code=4400)
        return

    try:
        workspace_context = _workspace_context_from_websocket(
            websocket,
            workspace_id=workspace_id,
        )
        workdir_utils.assert_workspace_active(
            ServerState.get_workdir(),
            workspace_context,
        )
        workspace = _resolve_user_workdir_path(workspace_id, workspace_context)
        paths = browser_live_paths(workspace, browser_session_id)
    except Exception:
        await websocket.close(code=4403)
        return
    if not workspace.is_dir() or not paths.root.is_dir():
        await websocket.close(code=4404)
        return

    state = read_browser_live_state(workspace, browser_session_id)
    recorded_workspace_id = str(state.get("workspace_id") or "").strip()
    if recorded_workspace_id and recorded_workspace_id != workspace_context.workspace_id:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    last_state_signature = ""
    last_frame_seq = -1
    last_heartbeat = 0.0
    try:
        configured_max_frame_bytes = int(
            os.environ.get("TCODE_BROWSER_LIVE_MAX_FRAME_BYTES", 4 * 1024 * 1024)
        )
    except (TypeError, ValueError):
        configured_max_frame_bytes = 4 * 1024 * 1024
    max_frame_bytes = max(
        64 * 1024,
        min(configured_max_frame_bytes, 16 * 1024 * 1024),
    )
    terminal_state_seen_at: float | None = None
    try:
        while True:
            state = await asyncio.to_thread(
                read_browser_live_state,
                workspace,
                browser_session_id,
            )
            if state.get("desktop_browser") is True and state.get("status") == "waiting_login" and not desktop_login_is_pending(state):
                state = {**state, "status": "login_interrupted", "error": "原登录请求已失效，请重新打开页面。"}
            state_payload = {
                "type": "browser_state",
                "browser_session_id": browser_session_id,
                "status": str(state.get("status") or "waiting"),
                "action": str(state.get("action") or ""),
                "url": str(state.get("url") or ""),
                "title": str(state.get("title") or ""),
                "frame_seq": int(state.get("frame_seq") or 0),
                "updated_at": str(state.get("updated_at") or ""),
                "error": str(state.get("error") or ""),
                "control_mode": str(state.get("control_mode") or "agent"),
                "viewport": dict(state.get("viewport") or {}),
                "pending_confirmation": state.get("pending_confirmation"),
                "desktop_browser": state.get("desktop_browser") is True,
            }
            signature = json.dumps(state_payload, sort_keys=True, ensure_ascii=False)
            if signature != last_state_signature:
                await websocket.send_json(state_payload)
                last_state_signature = signature

            frame_seq = int(state.get("frame_seq") or 0)
            if frame_seq > last_frame_seq and paths.frame.is_file():
                frame = await asyncio.to_thread(paths.frame.read_bytes)
                if 0 < len(frame) <= max_frame_bytes:
                    await websocket.send_bytes(frame)
                    last_frame_seq = frame_seq

            now = asyncio.get_running_loop().time()
            if now - last_heartbeat >= 15:
                await websocket.send_json(
                    {
                        "type": "browser_heartbeat",
                        "browser_session_id": browser_session_id,
                    }
                )
                last_heartbeat = now
            if state_payload["status"] in {
                "closed",
                "idle_timeout",
                "stopped",
            }:
                terminal_state_seen_at = terminal_state_seen_at or now
                if now - terminal_state_seen_at >= 1.0:
                    await websocket.close(code=1000)
                    return
            else:
                terminal_state_seen_at = None
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            action = str(message.get("action") or "").strip()
            if state.get("desktop_browser") is True:
                await websocket.send_json({"type": "browser_control_error", "error": "请在 Tcode 浏览器窗口中操作；此面板展示每次动作后的页面观察。"})
                continue
            if action not in _CONTROL_ACTIONS:
                await websocket.send_json({"type": "browser_control_error", "error": "unsupported action"})
                continue
            if action in {"confirm_pending", "reject_pending"}:
                pending = state.get("pending_confirmation")
                confirmation_id = str(
                    pending.get("confirmation_id") if isinstance(pending, dict) else ""
                ).strip()
                if not confirmation_id:
                    await websocket.send_json({"type": "browser_control_error", "error": "no pending confirmation"})
                    continue
                await asyncio.to_thread(
                    _write_confirmation_decision,
                    paths,
                    confirmation_id=confirmation_id,
                    approved=action == "confirm_pending",
                )
                await websocket.send_json({"type": "browser_control_accepted", "action": action})
                continue
            payload = {
                key: message.get(key)
                for key in ("action", "mode", "x", "y", "end_x", "end_y", "delta_x", "delta_y", "key")
                if message.get(key) is not None
            }
            for key in ("x", "y", "end_x", "end_y", "delta_x", "delta_y"):
                if key in payload and (
                    not isinstance(payload[key], (int, float))
                    or isinstance(payload[key], bool)
                    or not math.isfinite(payload[key])
                    or abs(payload[key]) > 100_000
                ):
                    await websocket.send_json({"type": "browser_control_error", "error": f"invalid {key}"})
                    payload = {}
                    break
            if not payload:
                continue
            if "key" in payload and len(str(payload["key"])) > 64:
                await websocket.send_json({"type": "browser_control_error", "error": "invalid key"})
                continue
            payload["origin"] = "human"
            await asyncio.to_thread(_write_control_request, paths, payload)
            await websocket.send_json({"type": "browser_control_accepted", "action": action})
    except (WebSocketDisconnect, RuntimeError):
        return
