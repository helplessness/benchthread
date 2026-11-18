import logging

import pytest

from tcode_agent.server._shared import DesktopPollAccessFilter, uvicorn_log_config


@pytest.mark.parametrize(("method", "path", "status", "kept"), [
    ("GET", "/api/notifications", 200, False),
    ("GET", "/api/async-completions?unread_only=true", 200, False),
    ("GET", "/api/browser/pending-login?session_id=test", 200, False),
    ("GET", "/api/symphony/local-issues?limit=50", 200, False),
    ("GET", "/api/notifications", 401, True),
    ("GET", "/api/notifications?limit=0", 422, True),
    ("GET", "/api/async-completions", 500, True),
    ("GET", "/api/notifications", 302, True),
    ("POST", "/api/notifications/read-all", 200, True),
    ("GET", "/api/health", 200, True),
    ("GET", "/api/notifications-extra", 200, True),
])
def test_desktop_poll_access_filter(method, path, status, kept):
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %s",
                               ("127.0.0.1", method, path, "1.1", status), None)
    assert DesktopPollAccessFilter().filter(record) is kept


def test_desktop_log_config_preserves_web_and_other_handlers():
    desktop = uvicorn_log_config(desktop=True)
    web = uvicorn_log_config()
    name = "hide_successful_desktop_polling"
    assert desktop["filters"][name]["()"] is DesktopPollAccessFilter
    assert desktop["handlers"]["access"]["filters"] == [*web["handlers"]["access"]["filters"], name]
    assert desktop["handlers"]["default"] == web["handlers"]["default"]
    assert name not in web["filters"]
    assert DesktopPollAccessFilter().filter(logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "unknown", (), None))
