import asyncio
import json
import sys

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tcode_agent.server.mcp_config_api import router


def test_missing_fastmcp_only_affects_authorized_connection_probe(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"demo": {"url": "https://unused.invalid/mcp"}}))
    monkeypatch.setenv("TCODE_MCP_CONFIG", str(path))
    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED", "0")
    monkeypatch.setenv("TCODE_DESKTOP_PAUSE_REMOTE_SERVICES", "0")
    monkeypatch.setenv("TCODE_ENTERPRISE_MODE", "1")
    monkeypatch.setitem(sys.modules, "fastmcp", None)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.post("/api/mcp/config/demo/test").status_code == 401
        monkeypatch.setenv("TCODE_ENTERPRISE_MODE", "0")
        assert client.post("/api/mcp/config/absent/test").status_code == 404
        result = client.post("/api/mcp/config/demo/test")
        assert result.status_code == 503
        assert "未安装 fastmcp，未执行连接测试" in result.json()["detail"]


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_installed_fastmcp_probes_without_executing_tools(tmp_path, monkeypatch, transport):
    fastmcp = pytest.importorskip("fastmcp")
    from fastmcp.utilities.tests import run_server_async

    server = fastmcp.FastMCP("probe-fixture")
    calls = []

    @server.tool()
    def echo(text: str) -> str:
        calls.append(text)
        return text

    path = tmp_path / "mcp.json"
    monkeypatch.setenv("TCODE_MCP_CONFIG", str(path))
    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.setenv("TCODE_DESKTOP_MANAGED", "0")
    monkeypatch.setenv("TCODE_DESKTOP_PAUSE_REMOTE_SERVICES", "0")
    monkeypatch.setenv("TCODE_ENTERPRISE_MODE", "0")
    app = FastAPI()
    app.include_router(router)

    async def probe():
        async with run_server_async(server, transport=transport) as url:
            path.write_text(json.dumps({"demo": {"url": url, "transport": transport}}))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/mcp/config/demo/test")
                assert response.status_code == 200
                assert response.json() == {"tools": 1}
                assert not calls

    asyncio.run(probe())
