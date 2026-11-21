import asyncio
import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from tcode_agent.plugins import panel_runs
from tcode_agent.plugins.declarative_agents import read_agents
from tcode_agent.plugins.manifest import load_manifest, PluginManifestSchemaError
from tcode_agent.plugin_sdk.testing import contract_test_plugin


SAMPLES = Path(__file__).resolve().parents[1] / "examples/plugins"


def test_registry_first_import_registers_artifact_tools():
    subprocess.run(
        [sys.executable, "-Werror:Built-in tools could not be fully loaded", "-c",
         "from tcode_agent.plugins.registry import PluginRegistry\n"
         "from tcode_agent.tools.registry import ToolRegistry\n"
         "assert ToolRegistry.get('artifact_upload') and ToolRegistry.get('artifact_import')"],
        cwd=SAMPLES.parents[1], check=True,
    )


@pytest.fixture
def sample(tmp_path):
    return Path(shutil.copytree(SAMPLES / "evidence-assistant", tmp_path / "plugin"))


@pytest.mark.parametrize("ui,team", [(True, False), (False, True), (True, True)])
def test_package_shapes(sample, ui, team):
    path = sample / "plugin.json"
    manifest = json.loads(path.read_text())
    if not ui:
        manifest.pop("panels")
    if not team:
        manifest.pop("agents")
        manifest["panels"][0].pop("agent")
    path.write_text(json.dumps(manifest))
    assert not contract_test_plugin(sample)
    parsed = load_manifest(sample)
    assert bool(parsed.panels) == ui
    assert bool(read_agents(sample, parsed)) == team
    assert not parsed.entry


def test_panel_contract_before_load(sample):
    path = sample / "plugin.json"
    m = json.loads(path.read_text())
    m["panels"][0]["agent"] = "unrelated_agent"
    path.write_text(json.dumps(m))
    with pytest.raises(PluginManifestSchemaError, match="must belong"):
        load_manifest(sample)
    m["panels"][0]["entry"] = "missing.html"
    path.write_text(json.dumps(m))
    with pytest.raises(PluginManifestSchemaError, match="does not exist"):
        load_manifest(sample)


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_session_retry_revocation_and_restart(sample, tmp_path, monkeypatch, anyio_backend):
    manifest = load_manifest(sample)
    agents = {a.name: a for a in read_agents(sample, manifest)}
    panel = {**manifest.panels[0], "model_id":"deepseek/deepseek-v4-pro"}
    plugin = SimpleNamespace(name=manifest.name, agents=list(agents))
    config = {"name":manifest.name, "panel_grants":["team.run","document.read"]}
    manager = SimpleNamespace(workdir=str(tmp_path), _load_configured_plugins=lambda:[config])
    session = SimpleNamespace(tenant_id="t", user_id="u", id="s1", metadata={"model":"deepseek/deepseek-v4-flash"})
    reached, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def chat(**kwargs):
        calls.append(kwargs)
        reached.set()
        await release.wait()
        return SimpleNamespace(content="test evidence")
    def create_provider(**kwargs):
        assert kwargs["model_ref"] == panel["model_id"]
        return SimpleNamespace(chat=chat)

    monkeypatch.setattr(panel_runs, "create_provider", create_provider)
    monkeypatch.setattr(panel_runs.PluginRegistry, "get", lambda name:plugin)
    monkeypatch.setattr(panel_runs.BuiltinAgents, "get", agents.get)
    turn = panel_runs.PanelTurn(request_id=uuid4(), question="Compare the two groups",
        document_name="fixture.pdf", pdf=base64.b64encode((SAMPLES/"证据核对示例.pdf").read_bytes()).decode())
    results = await asyncio.gather(*(panel_runs.start(manager,session,plugin,panel,turn) for _ in range(2)))
    assert results[0]["id"] == results[1]["id"]
    await asyncio.wait_for(reached.wait(), 2)
    assert len(panel_runs.history(manager,session,plugin,panel)) == 1
    other = SimpleNamespace(tenant_id="t", user_id="u", id="s2", metadata={})
    assert panel_runs.history(manager,other,plugin,panel) == []
    with pytest.raises(HTTPException) as error:
        await panel_runs.start(manager,session,plugin,panel,turn.model_copy(update={"question":"new question"}))
    assert error.value.status_code == 409
    config["panel_grants"].remove("document.read")
    release.set()
    await asyncio.gather(*list(panel_runs._tasks.values()))
    assert len(calls) == 1
    saved = panel_runs.history(manager,session,plugin,panel)[0]
    assert saved["status"] == "interrupted"
    directory = panel_runs.record_directory(manager,session,plugin,panel)
    archived = directory / f"{turn.request_id}.json"
    record=json.loads(archived.read_text())
    record.update(status="running", boot="previous-process")
    archived.write_text(json.dumps(record))
    assert panel_runs.read_record(archived)["status"] == "interrupted"
