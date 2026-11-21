import json

import pytest

from tcode_agent.runtime.async_watcher import _build_continuation_payload


@pytest.mark.parametrize("state,transfer,catalog,show", [
    ("Done", "ready", None, True),
    ("Done", "retry_wait", None, False),
    ("Done", "ready", {"total_files": 1}, False),
    ("Cancelled", "ready", None, False),
    ("Failed", "ready", None, False),
])
def test_file_preview_only_for_delivered_success(tmp_path, state, transfer, catalog, show):
    files = ["artifacts/jobs/demo/result/echo.dat"]
    payload = _build_continuation_payload(
        identifier="LOCAL-test", reg={"workdir": str(tmp_path)},
        issue={"state": state, "workspace": {"transfer_state": transfer,
               "artifact_catalog": catalog, "returned_file_preview": files}},
        result_output="{}",
    )
    assert ("已回传的工作区文件: " + json.dumps(files) in payload["message"]) == show


def test_legacy_completion_without_local_receipt_preserves_summary(tmp_path):
    payload = _build_continuation_payload(
        identifier="LOCAL-old", reg={"workdir": str(tmp_path)},
        issue={"state": "Done", "result_summary": "原任务已完成",
               "workspace": {"artifacts": ["output.txt"], "transfer_state": "ready"}},
        result_output="{}",
    )
    assert "已回传的工作区文件:" not in payload["message"]
    assert payload["can_continue"] and payload["result_summary"] == "原任务已完成"
