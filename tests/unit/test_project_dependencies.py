"""Project metadata regression tests."""

from pathlib import Path


def test_project_uses_mcp_v2_series() -> None:
    """The server is written against the MCP SDK 2.x API (MCPServer)."""
    repo_root = Path(__file__).resolve().parents[2]
    pyproject_text = (repo_root / "pyproject.toml").read_text()

    assert '"mcp>=2.3.0,<3"' in pyproject_text
