"""Single read-only browser checkpoint per owned chat; never stores cookies or inputs to forms."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from ..atomic_storage import atomic_write_json, file_transaction

BOOT_ID = uuid.uuid4().hex


def _path(scope: tuple[str, ...]) -> Path:
    key = hashlib.sha256(json.dumps(scope).encode()).hexdigest()
    return Path(os.environ["TCODE_WORKDIR"]) / ".tcode" / "browser-checkpoints" / (key + ".json")


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save(scope: tuple[str, ...], operation: dict, identifier: str) -> None:
    if not all(scope) or not os.environ.get("TCODE_WORKDIR"):
        return
    path = _path(scope)
    with file_transaction(path):
        atomic_write_json(path, {"identifier": identifier, "boot": BOOT_ID, "paused_at": time.time(), **operation})


def get_interrupted(scope: tuple[str, ...]) -> dict:
    if not all(scope) or not os.environ.get("TCODE_WORKDIR"):
        return {}
    value = _load(_path(scope))
    paused_at = value.get("paused_at")
    if not isinstance(paused_at, (int, float)) or value.get("boot") == BOOT_ID or not 0 <= time.time() - paused_at <= 86_400:
        return {}
    return value


def finish(scope: tuple[str, ...], identifier: str = "") -> None:
    if not all(scope) or not os.environ.get("TCODE_WORKDIR"):
        return
    path = _path(scope)
    if not path.exists():
        return
    with file_transaction(path):
        if not identifier or _load(path).get("identifier") == identifier:
            path.unlink(missing_ok=True)
