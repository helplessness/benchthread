import logging

import pytest

from tcode_agent.server._shared import PollAccessFilter, uvicorn_log_config


@pytest.mark.parametrize(("method", "path", "status", "web_kept", "desktop_kept"), [
    ("GET", "/api/notifications", 200, False, False),
    ("GET", "/api/async-completions?unread_only=true", 200, False, False),
    ("GET", "/api/browser/pending-login?session_id=test", 200, True, False),
    ("GET", "/api/symphony/local-issues?limit=50", 200, True, False),
    ("GET", "/api/notifications", 401, True, True),
    ("GET", "/api/notifications?limit=0", 422, True, True),
    ("GET", "/api/async-completions", 500, True, True),
    ("GET", "/api/notifications", 302, True, True),
    ("POST", "/api/notifications/read-all", 200, True, True),
    ("GET", "/api/health", 200, True, True),
    ("GET", "/api/notifications-extra", 200, True, True),
])
def test_poll_access_filter(method, path, status, web_kept, desktop_kept):
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %s",
                               ("127.0.0.1", method, path, "1.1", status), None)
    assert PollAccessFilter().filter(record) is web_kept
    assert PollAccessFilter(desktop=True).filter(record) is desktop_kept


def test_log_config_enables_poll_filter_only_on_the_access_handler():
    desktop = uvicorn_log_config(desktop=True)
    web = uvicorn_log_config()
    name = "hide_successful_polling"
    for config, is_desktop in ((web, False), (desktop, True)):
        assert config["filters"][name] == {"()": PollAccessFilter, "desktop": is_desktop}
        assert name in config["handlers"]["access"]["filters"]
        assert name not in config["handlers"]["default"].get("filters", [])
    assert desktop["handlers"]["default"] == web["handlers"]["default"]
    assert PollAccessFilter().filter(logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "unknown", (), None))
