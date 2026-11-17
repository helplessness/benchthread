"""Config option helpers and reserved template values."""

from __future__ import annotations

import os
import re
from typing import Any


_ENV_VALUE_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

# Exact references shipped in the public templates, not provider or model-name patterns.
TEMPLATE_MODEL_REFS = frozenset({"demo/your-model-id", "demo/your-vision-model-id"})
TEMPLATE_MODEL_SETUP_HINT = "这是示例模型，请在设置中添加模型，或在 tcode.json 中填写实际模型 ID。"


def resolve_option_value(options: dict[str, Any], *names: str, default: str = "") -> str:
    """Resolve an option value, including env-backed forms."""

    for name in names:
        env_name = str(options.get(f"{name}Env") or options.get(f"{name}_env") or "").strip()
        if env_name:
            resolved = str(os.environ.get(env_name, "") or "").strip()
            if resolved:
                return resolved
            # Compatibility for existing configs that accidentally placed the
            # literal API key in apiKeyEnv instead of the environment variable name.
            if name.lower() in {"apikey", "api_key"} and env_name.lower().startswith(("sk-", "ak-")):
                return env_name

    for name in names:
        value = options.get(name)
        if value in (None, ""):
            continue
        text = str(value).strip()
        match = _ENV_VALUE_RE.match(text)
        if match:
            return str(os.environ.get(match.group(1), "") or "").strip()
        if text.lower().startswith("env:"):
            return str(os.environ.get(text[4:].strip(), "") or "").strip()
        return text

    return default
