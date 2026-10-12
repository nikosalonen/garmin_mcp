"""Startup smoke tests for the packaged MCP server."""

import asyncio
import importlib.metadata
from unittest.mock import Mock

import garmin_mcp
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer


def test_main_registers_tools_and_starts_stdio(monkeypatch):
    """Run main() without real Garmin auth and stop before entering the server loop."""
    run_calls = []

    monkeypatch.delenv("GARMIN_MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("GARMIN_MCP_HOST", raising=False)
    monkeypatch.delenv("GARMIN_MCP_PORT", raising=False)
    monkeypatch.delenv("GARMIN_ENABLED_TOOLS", raising=False)
    monkeypatch.delenv("GARMIN_DISABLED_TOOLS", raising=False)
    monkeypatch.setattr(garmin_mcp, "init_api", lambda _email, _password: Mock())

    def capture_run(self, **kwargs):
        tools = asyncio.run(self.list_tools())
        run_calls.append(
            {
                "transport": kwargs.get("transport"),
                "tool_count": len(tools),
                "tool_names": [tool.name for tool in tools],
            }
        )

    monkeypatch.setattr(MCPServer, "run", capture_run)

    garmin_mcp.main()

    assert run_calls
    assert run_calls[0]["transport"] == "stdio"
    assert run_calls[0]["tool_count"] >= 10
    assert "get_devices" in run_calls[0]["tool_names"]
    assert "get_workouts" in run_calls[0]["tool_names"]


def test_main_rejects_malformed_allowlist_before_garmin_initialization(
    monkeypatch, capsys
):
    init_api = Mock()
    monkeypatch.setenv("GARMIN_ENABLED_TOOLS", ",,  ,")
    monkeypatch.setattr(garmin_mcp, "init_api", init_api)
    monkeypatch.setattr(MCPServer, "run", lambda _self, **_kwargs: None)

    with pytest.raises(SystemExit) as exc_info:
        garmin_mcp.main()

    assert exc_info.value.code == 1
    assert (
        "Invalid GARMIN_ENABLED_TOOLS: expected at least one tool name"
        in capsys.readouterr().err
    )
    init_api.assert_not_called()


def test_main_starts_server_before_garmin_login_completes(monkeypatch):
    """app.run() must not wait on Garmin login to finish (issue #255).

    slow_init_api() blocks until the test releases it. If main() still
    called init_api() synchronously before app.run(), this test would fail
    with the assertion message below instead of hanging forever, because
    the release only happens *after* main() has already returned.
    """
    import threading

    monkeypatch.delenv("GARMIN_MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("GARMIN_MCP_HOST", raising=False)
    monkeypatch.delenv("GARMIN_MCP_PORT", raising=False)

    login_release = threading.Event()

    def slow_init_api(_email, _password):
        released = login_release.wait(2)
        assert released, "login must not need to finish before app.run() is called"
        return Mock()

    monkeypatch.setattr(garmin_mcp, "init_api", slow_init_api)

    reached_run = threading.Event()

    def capture_run(self, **kwargs):
        reached_run.set()

    monkeypatch.setattr(MCPServer, "run", capture_run)

    try:
        garmin_mcp.main()
        assert reached_run.is_set()
    finally:
        login_release.set()


def _clear_server_env(monkeypatch):
    for name in (
        "GARMIN_MCP_TRANSPORT",
        "GARMIN_MCP_HOST",
        "GARMIN_MCP_PORT",
        "GARMIN_ENABLED_TOOLS",
        "GARMIN_DISABLED_TOOLS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_main_runs_stdio_without_http_options(monkeypatch):
    _clear_server_env(monkeypatch)
    monkeypatch.setattr(garmin_mcp, "init_api", lambda _email, _password: Mock())
    run_calls = []
    monkeypatch.setattr(MCPServer, "run", lambda _self, **kwargs: run_calls.append(kwargs))

    garmin_mcp.main()

    assert run_calls == [{"transport": "stdio"}]


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_main_passes_host_and_port_to_run_for_http(monkeypatch, transport):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("GARMIN_MCP_TRANSPORT", transport)
    monkeypatch.setenv("GARMIN_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("GARMIN_MCP_PORT", "9123")
    monkeypatch.setattr(garmin_mcp, "init_api", lambda _email, _password: Mock())
    run_calls = []
    monkeypatch.setattr(MCPServer, "run", lambda _self, **kwargs: run_calls.append(kwargs))

    garmin_mcp.main()

    assert run_calls == [{"transport": transport, "host": "0.0.0.0", "port": 9123}]


def test_allowlist_limits_tools_on_real_server(monkeypatch):
    _clear_server_env(monkeypatch)
    monkeypatch.setenv("GARMIN_ENABLED_TOOLS", "get_devices,get_workouts")
    monkeypatch.setattr(garmin_mcp, "init_api", lambda _email, _password: Mock())
    listed = []

    def capture_run(self, **_kwargs):
        listed.extend(tool.name for tool in asyncio.run(self.list_tools()))

    monkeypatch.setattr(MCPServer, "run", capture_run)

    garmin_mcp.main()

    assert sorted(listed) == ["get_devices", "get_workouts"]


def test_main_reports_package_version_to_clients(monkeypatch):
    """mcp 1.x always sent a version in serverInfo; MCPServer defaults to ""."""
    _clear_server_env(monkeypatch)
    monkeypatch.setattr(garmin_mcp, "init_api", lambda _email, _password: Mock())
    server_infos = []

    async def handshake(server):
        async with Client(server, mode="legacy") as client:
            return client.server_info

    def capture_run(self, **_kwargs):
        server_infos.append(asyncio.run(handshake(self)))

    monkeypatch.setattr(MCPServer, "run", capture_run)

    garmin_mcp.main()

    assert server_infos[0].name == "Garmin Connect v1.0"
    assert server_infos[0].version == importlib.metadata.version("garmin-mcp")
