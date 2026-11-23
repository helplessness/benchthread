"""Observation delivery without a native driver, model call or local settings."""
import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tcode_agent.tools.builtin import computer
from tcode_agent.harness.prompt_builder import AgentPromptBuilder
from tcode_agent.providers.base import ChatMessage


@pytest.mark.parametrize("require_image", [False, True])
def test_observe_text_fallback_and_required_screenshot(monkeypatch, require_image):
    elements = [{"role": "AXButton", "label": "Save", "element_token": "fixture-token"}]
    observation = {"text_observation_available": True, "elements": elements,
                   "coordinate_contract": {"coordinate_space": "normalized"}}
    if not require_image:
        observation["model_image"] = {"path": "/synthetic/capture.png"}
    request = AsyncMock(return_value={"status": "completed_known", "result": observation})
    monkeypatch.setattr(computer, "computer_broker_client", lambda: SimpleNamespace(request=request))
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("TCODE_COMPUTER_USE_ENABLED", "1")
    monkeypatch.setattr(computer.ConfigManager, "load", lambda *_: None)
    monkeypatch.setattr(computer.ConfigManager, "get_config_copy", lambda: {})
    monkeypatch.setattr(computer, "configured_model_supports_images", lambda *_: False)
    context = SimpleNamespace(session_id="fixture", call_id="observe",
                              extra={"provider": SimpleNamespace(provider_id="local", model="text")})
    result = asyncio.run(computer.ComputerObserveTool().execute({
        "operation": "observe", "target_ref": "window_" + "a" * 32, "require_image": require_image,
    }, context))
    request.assert_awaited_once()
    assert "require_image" not in request.await_args.args[1]
    assert result.metadata["model_images"] == []
    if require_image:
        assert result.error == "screenshot_unavailable" and result.metadata["retryable"] is False
        assert "未取得所需截图" in result.output
    else:
        assert not result.error
        delivered = json.loads(result.output)
        assert delivered["elements"] == elements and delivered["image_attached_to_model_input"] is False
        assert "coordinate_contract" not in delivered and "/synthetic/capture.png" not in result.output


@pytest.mark.parametrize("capture", ["present", "deleted", "invalid"])
def test_required_capture_must_reach_actual_model_input(monkeypatch, tmp_path, capture):
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jgckAAAAASUVORK5CYII=")
    path = tmp_path / "capture.png"
    path.write_bytes(png)
    request = AsyncMock(return_value={"status": "completed_known", "result": {
        "screenshot_service_authorized": "local",
        "model_image": {"path": str(path), "mime_type": "image/png", "size": len(png)},
    }})
    monkeypatch.setattr(computer, "computer_broker_client",
                        lambda: SimpleNamespace(request=request, capture_root=str(tmp_path)))
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("TCODE_COMPUTER_USE_ENABLED", "1")
    monkeypatch.setattr(computer.ConfigManager, "load", lambda *_: None)
    monkeypatch.setattr(computer.ConfigManager, "get_config_copy", lambda: {})
    monkeypatch.setattr(computer, "configured_model_supports_images", lambda *_: True)
    context = SimpleNamespace(session_id="fixture", call_id="observe",
                              extra={"provider": SimpleNamespace(provider_id="local", model="vision")})
    result = asyncio.run(computer.ComputerObserveTool().execute({
        "operation": "observe", "target_ref": "window_" + "a" * 32, "require_image": True,
    }, context))
    assert "image_attached_to_model_input" not in json.loads(result.output)
    if capture == "deleted":
        path.unlink()
    elif capture == "invalid":
        path.write_bytes(b"not an image")

    builder = AgentPromptBuilder(SimpleNamespace())
    monkeypatch.setattr(builder, "_configured_image_input_support", lambda: True)
    tool_message = builder._tool_chat_message_for_prompt(SimpleNamespace(
        content=result.output, tool_calls=[{"tool_name": "computer_observe", "metadata": result.metadata}],
    ), tool_call_id="observe")
    original = [
        ChatMessage(role="user", content="核对截图"),
        ChatMessage(role="assistant", content="", tool_calls=[{
            "id": "observe", "function": {"name": "computer_observe", "arguments": {}},
        }]),
        tool_message,
        ChatMessage(role="user", content="内部合成提示"),
    ]
    expanded, required, missing = builder._assemble_tool_image_input(original, [tool_message])
    payload_images = [image for message in expanded for image in message.images or []]
    assert required and missing == (capture != "present")
    assert bool(payload_images) == (capture == "present")
    assert ("截图已装入" in tool_message.content) == (capture == "present")
    if payload_images:
        assert base64.b64decode(payload_images[0]["data_url"].split(",", 1)[1]) == png
    request.assert_awaited_once()
