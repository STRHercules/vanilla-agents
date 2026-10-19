"""MCP server entry point (stdio): lets Claude use Vanilla Agents as tools. Installed by scripts/install_mcp.py. See docs/INTEGRATION.md."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vanilla_agents.mcp import serve  # noqa: E402

if __name__ == "__main__":
    serve()
