import json

import pytest

from tcode_agent.runtime.async_watcher import _build_continuation_payload
from tcode_agent.presentation.job_results import MAX_TEXT, async_tool_text
from tcode_agent.server.session_presenter import _public_frontend_message_metadata
from tcode_agent.types import Message


@pytest.mark.parametrize("target, expected", [
    ("plain", "plain"),
    ({"text": "  exact\n"}, "  exact\n"),
    ({"structuredContent": {"result": "translation"}}, "translation"),
    ({"content": [{"type": "text", "text": "one"}, {"type": "image", "data": "ignored"},
                  {"type": "text", "text": "<two>"}]}, "one\n<two>"),
    ({"output_bundle": {"text": "private metadata"}}, ""),
    ({"isError": True, "text": "not a successful result"}, ""),
])
def test_text_projection_and_legacy_session(target, expected):
    receipt = {"target_result": target, "status": "completed", "job_id": "test"}
    envelope = {"content": [{"type": "text", "text": json.dumps(receipt)}]}
    message = Message(role="assistant", presentation="async_completion",
                      content="legacy\n结果摘要: " + json.dumps(envelope) + "\n下一步: 继续")
    assert async_tool_text(receipt) == expected
    assert _public_frontend_message_metadata(message).get("tool_result_text", "") == expected
    message.presentation = None
    assert "tool_result_text" not in _public_frontend_message_metadata(message)


def test_long_text_is_independent_of_truncated_chat_summary(tmp_path):
    text = "原文" * 1500 + "末尾"
    raw = json.dumps({"status": "completed", "job_id": "transcript", "target_result": {"text": text}})
    for state in ["Done", "Failed", "Cancelled"]:
        payload = _build_continuation_payload(identifier="LOCAL-text", reg={"workdir": str(tmp_path)},
            issue={"state": state, "result_summary": raw}, result_output=raw)
        assert payload["tool_result_text"] == (text if state == "Done" else "")
    assert "显示长度已达上限" in async_tool_text({"target_result": "a" * (MAX_TEXT + 1)})
    assert not async_tool_text({"content": 3})


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
