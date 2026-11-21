"""Read community agent definitions as data. Executable capabilities stay host-owned."""
import json
import re
from pathlib import Path

from ..types import AgentInfo, AgentMode
from .manifest import PluginManifestSchemaError


def read_agents(root: Path, manifest) -> list[AgentInfo]:
    agents = {}
    prefix = manifest.name.replace("-", "_").replace(".", "_") + "_"
    members = {}
    for item in manifest.agents:
        path = root / item
        for file in sorted(path.glob("*.json")) if path.is_dir() else [path]:
            if not file.resolve().is_relative_to(root) or file.stat().st_size > 128_000:
                raise PluginManifestSchemaError("invalid declarative agent file", path=file)
            role = json.loads(file.read_text(encoding="utf-8"))
            allowed = {"name", "description", "prompt", "members", "hidden", "display_name"}
            if not isinstance(role, dict) or set(role) - allowed:
                raise PluginManifestSchemaError("unsupported declarative agent fields", path=file)
            name = role.get("name")
            if not isinstance(name, str) or not name.startswith(prefix) or not re.fullmatch(r"[a-z][a-z0-9_]{1,127}", name) or name in agents:
                raise PluginManifestSchemaError("invalid or duplicate namespaced agent name", path=file)
            prompt = role.get("prompt")
            workers = role.get("members", [])
            if not isinstance(prompt, str) or not prompt.strip() or not isinstance(workers, list) or not all(isinstance(k, str) for k in workers):
                raise PluginManifestSchemaError("invalid agent prompt or members", path=file)
            members[name] = workers
            title = str(role.get("display_name") or name)
            options = {"plugin": manifest.name, "display_name": title}
            if workers:
                options["team"] = {"key": name, "display_name": title, "worker_names": workers}
            agents[name] = AgentInfo(name=name, description=str(role.get("description") or title),
                                     prompt=prompt, permission={"*": "deny"}, options=options,
                                     mode=AgentMode.MANAGER if workers else AgentMode.WORKER if role.get("hidden") else AgentMode.PRIMARY,
                                     hidden=bool(role.get("hidden", False)))
    for name, workers in members.items():
        if any(worker not in agents or members[worker] or worker == name for worker in workers):
            raise PluginManifestSchemaError("members must reference this package's leaf agents")
    for panel in manifest.panels:
        if panel.get("agent") and panel["agent"] not in agents:
            raise PluginManifestSchemaError("panel agent must belong to its package")
    return list(agents.values())
