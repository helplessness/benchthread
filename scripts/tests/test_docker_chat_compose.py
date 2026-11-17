"""Verify the basic-chat Compose boundaries without a running Docker Engine."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_chat_compose_is_local_and_persistent(tmp_path):
    docker = shutil.which("docker")
    if not docker or subprocess.run([docker, "compose", "version"], capture_output=True).returncode:
        pytest.skip("Docker Compose CLI is not installed")
    (tmp_path / "config").mkdir()
    (tmp_path / "settings.env").write_text("TCODE_BOOTSTRAP_PASSWORD='not-a-real-secret'\n", encoding="utf-8")
    result = subprocess.run(
        [docker, "compose", "--env-file", os.devnull, "-f", str(ROOT / "compose.yaml"),
         "-p", "benchthread-compose-check", "config", "--format", "json"],
        env={**os.environ, "TCODE_DOCKER_SETUP_DIR": str(tmp_path), "TCODE_DOCKER_PORT": "18080"},
        check=True, capture_output=True, text=True, timeout=20,
    )
    config = json.loads(result.stdout)
    assert set(config["services"]) == {"web"}
    web = config["services"]["web"]
    assert len(web["ports"]) == 1
    port = web["ports"][0]
    assert (port["host_ip"], str(port["published"]), port["target"]) == ("127.0.0.1", "18080", 8080)
    assert web["build"]["args"]["TCODE_INSTALL_LARK_CLI"] == "0"
    assert web["environment"]["TCODE_ENTERPRISE_MODE"] == "1"
    assert web["environment"]["TCODE_MANAGED_JOB_PROTOCOL"] == "0"
    assert web["environment"]["TCODE_SANDBOX_ENABLED"] == "false"
    assert web["environment"]["TCODE_ASYNC_WATCHER_ENABLED"] == "0"
    volumes = {item["target"]: item for item in web["volumes"]}
    assert set(volumes) == {"/workspace", "/config"}
    assert volumes["/workspace"]["type"] == "volume" and volumes["/workspace"]["source"] == "workspace"
    assert volumes["/config"]["type"] == "bind" and volumes["/config"]["read_only"]
