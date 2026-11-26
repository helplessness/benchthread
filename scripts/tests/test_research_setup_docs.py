"""Keep the documented opt-in research recipe runnable without private config."""
import os
from pathlib import Path
import shutil
import subprocess

from tcode_agent.research.config import load_research_settings


ROOT = Path(__file__).resolve().parents[2]


def test_documented_research_recipe_and_config_preservation(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("TCODE_RESEARCH_"):
            monkeypatch.delenv(key)
    assert not load_research_settings().enabled
    (tmp_path / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/init_local_config.sh", tmp_path / "scripts")
    shutil.copytree(ROOT / "config/templates", tmp_path / "config/templates")
    subprocess.run(["bash", str(tmp_path / "scripts/init_local_config.sh")], check=True,
                   capture_output=True)
    local = tmp_path / ".tcode-local/local.env"
    doc = (ROOT / "docs/RESEARCH-SETUP.md").read_text()
    recipe = doc.split("# research-experience-start\n", 1)[1].split("# research-experience-end", 1)[0]
    local.write_text(local.read_text() + "\n" + recipe)
    before = local.read_bytes()
    subprocess.run(["bash", str(tmp_path / "scripts/init_local_config.sh")], check=True,
                   capture_output=True)
    assert local.read_bytes() == before
    result = subprocess.run(["bash", "-c", 'set -a; source "$1"; env -0', "bash", str(local)],
                            check=True, capture_output=True)
    for entry in result.stdout.decode().split("\0"):
        if entry.startswith("TCODE_RESEARCH_"):
            key, value = entry.split("=", 1)
            monkeypatch.setenv(key, value)
    settings = load_research_settings()
    for capability in ("journal_active", "topics_active", "episodes_active", "checkpoints_active",
                       "knowledge_links_active", "resume_context_active", "research_pack_active",
                       "research_pack_import_active"):
        assert getattr(settings, capability), capability
    assert not settings.attribution_model_enabled


def test_install_hint_keeps_runtime_and_scope():
    script = (ROOT / "scripts/install_tcode.sh").read_text()
    hint = script.split("cat <<EOF", 1)[1]
    assert '--workdir "$PROJECT_ROOT/workdirs/local"' in hint
    web_hint = next(line for line in hint.splitlines() if '"$PROJECT_ROOT/scripts/start_web.sh"' in line)
    assert '--workdir "$PROJECT_ROOT/workdirs/local"' in web_hint
    assert "--full 仅增加可选 Python 依赖" in hint
