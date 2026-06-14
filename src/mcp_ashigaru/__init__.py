"""MCP Ashigaru — Kagetora's dispatchable dev-runner corps.

Bridge between Kagetora (the foreman) and the deterministic wrapper scripts that
drive Claude Code as a headless dev sub-agent on the crunchtools fleet. Runs as
the unprivileged `devrunner` sandbox on lotor and is reached by Kagetora through
the airlock gateway.

Usage:
    mcp-ashigaru-crunchtools --transport streamable-http --port 8020
    python -m mcp_ashigaru
"""

from .server import main, mcp

__version__ = "0.1.0"
__all__ = ["main", "mcp"]
