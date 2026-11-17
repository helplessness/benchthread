"""Operator-managed, read-only Agent Reach MCP. Never runs upstream setup."""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from ..atomic_storage import atomic_write_json, file_transaction
from ..browser.channel_reads import CHANNEL_READS, EXPERIMENTAL_CHANNELS

REVISION = "c70ae6da5d39bc90e09ad2ae0575ccc89f31e284"
# These explicit backends have no browser or account-import path at REVISION.
PUBLIC_ACTIONS = {
    ("github", "search"): ("github-api", True, True),
    ("github", "read"): ("github-api", True, False),
    ("v2ex", "hot"): ("v2ex-api", False, True),
    ("v2ex", "node"): ("v2ex-api", True, True),
    ("v2ex", "read"): ("v2ex-api", True, False),
    ("v2ex", "user"): ("v2ex-api", True, False),
    ("bilibili", "hot"): ("bilibili-api", False, True),
    ("bilibili", "read"): ("bilibili-api", True, False),
    ("youtube", "search"): ("yt-dlp", True, True),
    ("youtube", "read"): ("yt-dlp", True, False),
    ("youtube", "subtitle"): ("yt-dlp", True, False),
    ("youtube", "transcribe"): ("groq-whisper", True, False),
    ("xiaoyuzhou", "transcribe"): ("groq-whisper", True, False),
    ("xueqiu", "search"): ("tencent", True, True),
    ("xueqiu", "quote"): ("tencent", True, False),
}


def permitted_channels() -> set[str]:
    configured = os.environ.get("TCODE_AGENT_REACH_CHANNELS")
    supported = {channel for channel, _ in PUBLIC_ACTIONS.keys() | CHANNEL_READS}
    return supported if configured is None else supported & {name.strip() for name in configured.split(",")}


def settings_path() -> Path:
    return Path(os.environ.get("TCODE_WORKDIR") or os.getcwd()) / ".tcode" / "internet-content.json"


def read_settings() -> dict:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if (not isinstance(data, dict) or not isinstance(data.get("channels", {}), dict)
            or any(not isinstance(row, dict) for row in data.get("channels", {}).values())):
        raise ValueError("Invalid internet content settings")
    return data


def enabled_channels(settings: dict | None = None) -> set[str]:
    channels = (read_settings() if settings is None else settings).get("channels", {})
    return {name for name in permitted_channels()
            if channels.get(name, {}).get("enabled", name not in EXPERIMENTAL_CHANNELS) is True}


def write_channel_settings(channel: str, **fields) -> None:
    path = settings_path()
    with file_transaction(path):
        settings = read_settings()
        settings.setdefault("channels", {}).setdefault(channel, {}).update(fields)
        atomic_write_json(path, settings)


def channel_catalog() -> dict:
    enabled = enabled_channels()
    return {"ok": True, "channels": [
        {"channel": channel, "action": action, "enabled": channel in enabled,
         "backend": backend, "requires_value": needs_value, "accepts_limit": accepts_limit}
        for (channel, action), (backend, needs_value, accepts_limit) in PUBLIC_ACTIONS.items()
    ] + [{"channel": channel, "action": action, "enabled": channel in enabled,
          "backend": "desktop-browser", "requires_value": channel != "xueqiu", "accepts_limit": True,
          "instruction": "Delegate to agent-browser and use browser open with channel/channel_action/channel_value."}
         for channel, action in sorted(CHANNEL_READS)]}


def read_public_channel(channel: str, action: str, value: str = "", limit: int = 5) -> dict:
    """Read allowlisted public GitHub, V2EX, Bilibili, YouTube and stock data; no browser or login."""
    def failure(code: str) -> dict:
        return {"ok": False, "channel": channel, "action": action, "error": code}

    spec = PUBLIC_ACTIONS.get((channel, action))
    browser_action = (channel, action) in CHANNEL_READS
    if not spec and not browser_action:
        return failure("ACTION_NOT_ALLOWED")
    try:
        enabled = enabled_channels()
    except (OSError, ValueError, AttributeError):
        return failure("NEED_SETUP")
    if channel not in enabled:
        return failure("CHANNEL_DISABLED")
    if browser_action:
        return {**failure("BROWSER_REQUIRED"),
                "instruction": "Delegate to agent-browser for the existing TCode browser; do not use upstream browser or cookies."}
    backend, needs_value, accepts_limit = spec
    value = value.strip()
    if bool(value) != needs_value or len(value) > 512 or any(ord(c) < 32 for c in value) or not 1 <= limit <= 20:
        return failure("BAD_INPUT")
    if channel == "bilibili" and action == "read":
        # Do not let the CLI follow a short-link redirect.
        match = re.fullmatch(r"(?:https://www\.bilibili\.com/video/)?(BV[0-9A-Za-z]{10})/?", value)
        if not match:
            return failure("BAD_INPUT")
        value = match[1]
    if channel == "youtube" and action != "search":
        match = re.fullmatch(r"(?:https://(?:www\.youtube\.com/watch\?v=|youtu\.be/))?([\w-]{11})", value, re.ASCII)
        if not match:
            return failure("BAD_INPUT")
        value = match[1]
    if channel == "xiaoyuzhou":
        match = re.fullmatch(r"https://www\.xiaoyuzhoufm\.com/episode/([a-f0-9]{24})(?:\?[^#\s]*)?", value)
        if not match:
            return failure("BAD_INPUT")
        value = match[1]
    transcription_key = os.environ.get("TCODE_GROQ_API_KEY", "").strip() if action == "transcribe" else ""
    if action == "transcribe" and not transcription_key:
        return failure("NEED_SETUP")
    configured = Path(os.environ.get("TCODE_AGENT_REACH_PYTHON", ""))
    if not configured.is_absolute() or not configured.is_file():
        return failure("NEED_SETUP")
    install_metadata = list(configured.parent.parent.glob("lib/python*/site-packages/agent_reach-*.dist-info/direct_url.json"))
    try:
        origin = json.loads(install_metadata[0].read_text()) if len(install_metadata) == 1 else {}
    except (OSError, ValueError):
        return failure("NEED_SETUP")
    if origin.get("url") != "https://github.com/Panniantong/Agent-Reach.git" or origin.get("vcs_info", {}).get("commit_id") != REVISION:
        return failure("NEED_SETUP")
    if action == "transcribe":
        from .media_transcription import transcribe

        return transcribe(configured, channel, value, transcription_key)
    argv = [str(configured), "-I", "-m", "agent_reach.cli", channel, action, "--backend", backend, "--timeout", "18"]
    if accepts_limit:
        argv += ["--limit", str(limit)]
    if value:
        argv += ["--", value]
    try:
        with TemporaryDirectory(prefix="tcode-reach-") as isolated:
            isolated = str(Path(isolated).resolve())
            # Child-only environment: do not inherit user credentials, proxy or CLI config.
            child_env = {"HOME": isolated, "USERPROFILE": isolated, "PATH": os.defpath, "TMPDIR": isolated}
            run = subprocess.run(argv, cwd=isolated, env=child_env, capture_output=True, timeout=23, check=False)
        if len(run.stdout) > 2 * 1024 * 1024:
            return failure("RESULT_TOO_LARGE")
        response = json.loads(run.stdout)
    except subprocess.TimeoutExpired:
        return failure("TIMEOUT")
    except (OSError, ValueError):
        return failure("UPSTREAM_BROKEN")
    if not isinstance(response, dict) or response.get("schema_version") != 1 or (response.get("channel"), response.get("action")) != (channel, action):
        return failure("INVALID_RESULT")
    if response.get("ok") is not True:
        error = response.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        return failure(code if code in {"BAD_INPUT", "NOT_FOUND", "NEED_LOGIN", "NEED_SETUP", "RATE_LIMITED", "PLATFORM_BLOCKED", "TIMEOUT"} else "UPSTREAM_BROKEN")
    if run.returncode or response.get("backend") != backend or not isinstance(response.get("items"), list):
        return failure("INVALID_RESULT")
    items = []
    for item in response["items"][:limit]:
        if not isinstance(item, dict):
            continue
        result = {field: item[field][:12000] for field in ("title", "url", "author", "published_at", "text") if isinstance(item.get(field), str)}
        if not result.get("url") or not result.get("title") and not result.get("text"):
            continue
        try:
            url = urlsplit(result["url"])
        except ValueError:
            continue
        domain = {"github":"github.com", "v2ex":"v2ex.com", "bilibili":"bilibili.com",
                  "youtube":"youtube.com", "xueqiu":"xueqiu.com"}[channel]
        if url.scheme != "https" or url.username or url.password or url.hostname not in {domain, "www." + domain}:
            continue
        items.append(result)
        if (channel, action) == ("youtube", "subtitle"):
            raw = item.get("raw")
            language = raw.get("lang") if isinstance(raw, dict) else None
            result["data"] = {"kind": "platform_caption", "caption_origin": "unspecified"}
            if isinstance(language, str):
                result["data"]["language"] = language[:40]
        if channel == "xueqiu" and isinstance(item.get("raw"), dict):
            result["data"] = {key: value[:100] for key, value in item["raw"].items()
                              if key in {"symbol", "name", "current", "last_close", "open", "chg",
                                         "percent", "high", "low", "time", "market", "type"} and isinstance(value, str)}
    if not items:
        return failure("NO_VERIFIABLE_CONTENT")
    return {"ok": True, "channel": channel, "action": action, "backend": backend,
            "fetched_at": datetime.now(UTC).isoformat(), "items": items,
            "partial": len(items) < len(response["items"]) or any(len(item.get("text", "")) == 12000 for item in items)}


if __name__ == "__main__":
    from fastmcp import FastMCP

    server = FastMCP("TCode Agent Reach — public read only")
    server.tool(read_public_channel)
    server.run()
