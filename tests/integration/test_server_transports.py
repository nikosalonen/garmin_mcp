"""Start the real server process over stdio and HTTP and talk to it.

These never reach Garmin: all GARMIN* variables are removed and
GARMINTOKENS points at an empty directory, so the background login gives
up with "OAuth tokens not found" without a network call.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

SRC_DIR = Path(__file__).resolve().parents[2] / "src"

# "legacy" is the initialize handshake today's clients (Claude Desktop) use;
# "auto" lets mcp 2 pick the newest protocol the server offers.
CLIENT_MODES = ["legacy", "auto"]
NEWEST_PROTOCOL = "2026-07-28"


def _assert_protocol(client, mode):
    if mode == "legacy":
        assert client.protocol_version != NEWEST_PROTOCOL
    else:
        assert client.protocol_version == NEWEST_PROTOCOL


def _server_env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GARMIN")}
    env["GARMINTOKENS"] = str(tmp_path / "tokens")
    env["GARMINTOKENS_BASE64"] = str(tmp_path / "tokens_base64")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC_DIR), env.get("PYTHONPATH")]))
    env.update(extra)
    return env


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.timeout(60)
@pytest.mark.parametrize("mode", CLIENT_MODES)
async def test_stdio_server_lists_tools_and_reads_a_resource(tmp_path, mode):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "garmin_mcp"],
        env=_server_env(tmp_path),
        cwd=str(tmp_path),
    )

    async with Client(params, mode=mode) as client:
        _assert_protocol(client, mode)
        tools = await client.list_tools()
        resource = await client.read_resource("workout://templates/simple-run")

    names = {tool.name for tool in tools.tools}
    assert {"get_devices", "get_workouts"} <= names
    assert json.loads(resource.contents[0].text)["workoutName"] == "Simple Run"


@pytest.fixture
def http_server(tmp_path):
    port = _free_port()
    log_path = tmp_path / "server.log"
    env = _server_env(
        tmp_path,
        GARMIN_MCP_TRANSPORT="streamable-http",
        GARMIN_MCP_PORT=str(port),
    )
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "garmin_mcp"],
            env=env,
            cwd=tmp_path,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(f"{base_url}/healthz", timeout=1) as response:
                    health = response.read()
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    pytest.fail(f"server did not start:\n{log_path.read_text()}")
                time.sleep(0.2)
        yield base_url, health
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.timeout(60)
@pytest.mark.parametrize("mode", CLIENT_MODES)
async def test_streamable_http_serves_healthz_and_tools(http_server, mode):
    base_url, health = http_server

    async with Client(f"{base_url}/mcp", mode=mode) as client:
        _assert_protocol(client, mode)
        tools = await client.list_tools()

    assert health == b"ok"
    assert "get_devices" in {tool.name for tool in tools.tools}
