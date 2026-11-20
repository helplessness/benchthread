import hashlib
import os
from pathlib import Path
import sys
import tempfile

import pytest

from tcode_agent.runtime.sandbox.macos_native import _publish_changes, publish_file
from tcode_agent.runtime.sandbox.project_network_exec import _publish_checkout


def test_conflict_reports_only_committed_files(tmp_path):
    project, export = tmp_path / "project", tmp_path / "export"
    project.mkdir()
    export.mkdir()
    before = {}
    after = {}
    for name in ("first", "conflict", "last"):
        (project / name).write_bytes(b"old")
        (export / name).write_bytes(b"new")
        before[name] = hashlib.sha256(b"old").hexdigest()
        after[name] = hashlib.sha256(b"new").hexdigest()
    (project / "conflict").write_bytes(b"external")
    receipt = {}
    with pytest.raises(ValueError, match="已写回 1 项"):
        _publish_changes(export, before, after,
                         lambda p,d,h: publish_file(project,p,d,expected=h),receipt,"")
    assert receipt == {"artifacts":["first"], "unpublished_artifacts":["conflict","last"]}
    assert [(project / n).read_bytes() for n in before] == [b"new",b"external",b"old"]


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin checkout publication")
def test_checkout_publication(tmp_path):
    with tempfile.TemporaryDirectory(dir=os.getenv("TCODE_TEST_SECOND_VOLUME") or tmp_path) as dest:
        parent = os.open(dest, os.O_DIRECTORY)
        try:
            payload = tmp_path / "payload"
            payload.mkdir()
            (payload / ".git").mkdir()
            (payload / ".git/HEAD").write_text("example")
            if os.getenv("TCODE_TEST_SECOND_VOLUME"):
                assert payload.stat().st_dev != os.fstat(parent).st_dev
            def cancel():
                raise ValueError("cancel")
            with pytest.raises(ValueError, match="cancel"):
                _publish_checkout(payload, parent, "repo", cancel)
            assert not list(Path(dest).iterdir())
            (Path(dest) / "repo").mkdir()
            with pytest.raises(FileExistsError):
                _publish_checkout(payload, parent, "repo", lambda: None)
            assert not list((Path(dest) / "repo").iterdir())
            _publish_checkout(payload, parent, "published", lambda: None)
            assert (Path(dest) / "published/.git/HEAD").read_text() == "example"
            assert not list(Path(dest).glob(".tcode-clone-*"))
        finally:
            os.close(parent)
