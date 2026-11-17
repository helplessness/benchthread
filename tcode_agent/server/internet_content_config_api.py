"""Instance administrator controls for admitted internet content channels."""
import os
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StrictBool

from . import agent_reach_mcp as reach
from ..browser.channel_reads import LOGIN_URLS
from ..browser.desktop import manage_browser_account
from . import workdirs
from .state import ServerState
from .websearch_config_api import _authorize
from .workspace_scope import _workspace_context_from_request

router = APIRouter(prefix="/api/config/internet-content")
CHECKS = {
    "github": ("read", "python/cpython"),
    "v2ex": ("read", "1000"),
    "bilibili": ("read", "BV1GJ411x7h7"),
    "youtube": ("read", "jNQXAC9IVRw"),
    "xueqiu": ("search", "茅台"),
}


class ChannelSwitch(BaseModel):
    enabled: StrictBool


def _managed(request: Request) -> None:
    _authorize(request)
    if os.environ.get("TCODE_DESKTOP_MANAGED") != "1":
        raise HTTPException(403, "此设置仅适用于桌面端")


def _public() -> dict:
    try:
        settings = reach.read_settings()
        permitted = reach.permitted_channels()
        enabled = reach.enabled_channels(settings)
        return {"configured": bool(os.environ.get("TCODE_AGENT_REACH_PYTHON")), "channels": [
            {"name": channel, "permitted": channel in permitted, "enabled": channel in enabled,
             "public_check": channel in CHECKS,
             "account": channel in LOGIN_URLS,
             "actions": list(dict.fromkeys(action for name, action in
                 [*reach.PUBLIC_ACTIONS, *sorted(reach.CHANNEL_READS)] if name == channel)),
             "last_check": settings.get("channels", {}).get(channel, {}).get("last_check")}
            for channel in dict.fromkeys(name for name, _ in [*reach.PUBLIC_ACTIONS, *sorted(reach.CHANNEL_READS)])
        ], "transcription_configured": bool(os.environ.get("TCODE_GROQ_API_KEY", "").strip())}
    except (ValueError, OSError, AttributeError):
        raise HTTPException(409, "无法读取互联网渠道配置") from None


@router.get("")
def get_settings(request: Request):
    _managed(request)
    return _public()


@router.put("/{channel}")
def set_channel(request: Request, channel: str, payload: ChannelSwitch):
    _managed(request)
    if channel not in reach.permitted_channels():
        raise HTTPException(403, "此渠道未获运行配置允许")
    try:
        reach.write_channel_settings(channel, enabled=payload.enabled, last_check=None)
    except (ValueError, OSError, AttributeError):
        raise HTTPException(409, "无法保存互联网渠道配置") from None
    return _public()


@router.post("/{channel}/check")
def check_channel(request: Request, channel: str):
    _managed(request)
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise HTTPException(415, "渠道检查要求 JSON 请求")
    if channel not in CHECKS:
        raise HTTPException(404, "未知渠道")
    if channel not in {row["name"] for row in _public()["channels"] if row["enabled"]}:
        raise HTTPException(409, "请先启用渠道")
    action, value = CHECKS[channel]
    result = reach.read_public_channel(channel, action, value, limit=1)
    try:
        reach.write_channel_settings(channel, last_check={
            "ok": result["ok"], "error": result.get("error"), "action": action,
            "checked_at": datetime.now(UTC).isoformat(),
        })
    except (ValueError, OSError, AttributeError):
        raise HTTPException(409, "无法保存渠道检查结果") from None
    return _public()


@router.post("/{channel}/account/{operation}")
async def channel_account(request: Request, workspace_id: str, channel: str, operation: str):
    _managed(request)
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise HTTPException(415, "账号操作要求 JSON 请求")
    if channel not in LOGIN_URLS or operation not in {"login", "logout"}:
        raise HTTPException(404, "此渠道不支持浏览器账号管理")
    if operation == "login" and channel not in {row["name"] for row in _public()["channels"] if row["enabled"]}:
        raise HTTPException(409, "请先启用渠道")
    owner = _workspace_context_from_request(request, workspace_id=workspace_id)
    workdirs.assert_workspace_active(ServerState.get_workdir(), owner)
    try:
        return await manage_browser_account(owner.tenant_id, owner.user_id, owner.workspace_id, channel, operation)
    except PermissionError:
        raise HTTPException(409, "登录网址解析到非公网地址，已拦截。若使用 Fake-IP 代理，请让该站点返回真实 IP 后重试。") from None
    except (ValueError, OSError, TimeoutError):
        raise HTTPException(409, "浏览器账号操作未完成；请关闭不用的浏览器标签后重试。") from None
