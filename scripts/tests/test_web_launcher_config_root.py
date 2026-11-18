"""Regression coverage for direct Web launcher config discovery."""

import os
import sys

import pytest

from tcode_agent.server import launcher


@pytest.mark.parametrize(
    ("explicit", "source_local", "cwd_local", "source_config", "expected"),
    [
        (True, True, True, True, "explicit"),
        (False, True, True, True, "source_local"),
        (False, False, True, False, "cwd_local"),
        (False, False, False, True, "source"),
        (False, False, False, False, "workdir"),
    ],
)
def test_web_launcher_config_root(
    tmp_path, monkeypatch, explicit, source_local, cwd_local, source_config, expected
):
    source, cwd, workdir, override = (
        tmp_path / name for name in ("source", "cwd", "workdir", "explicit")
    )
    for directory in (source, cwd, workdir, override):
        directory.mkdir()
    (workdir / ".tcode-local").mkdir()
    if source_local:
        (source / ".tcode-local").mkdir()
    if cwd_local:
        (cwd / ".tcode-local").mkdir()
    if source_config:
        (source / "tcode.json").write_text("{}")
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(launcher, "source_root", source)
    monkeypatch.setattr(sys, "argv", ["tcode-server", "--workdir", str(workdir)])
    monkeypatch.setenv("TCODE_WORKDIR", str(cwd))
    monkeypatch.setenv("TCODE_CONFIG_ROOT", str(override))
    if not explicit:
        monkeypatch.delenv("TCODE_CONFIG_ROOT")
    monkeypatch.setattr(launcher, "_load_env_file", lambda *_: None)
    monkeypatch.setattr(launcher, "_configure_job_protocol", lambda *_: False)

    def check_run(_args, actual_workdir):
        assert actual_workdir == os.environ["TCODE_WORKDIR"] == str(workdir)
        assert os.environ["TCODE_CONFIG_ROOT"] == str({
            "explicit": override,
            "source_local": source / ".tcode-local",
            "cwd_local": cwd / ".tcode-local",
            "source": source,
            "workdir": workdir,
        }[expected])

    monkeypatch.setattr(launcher, "_run", check_run)
    launcher.main()
