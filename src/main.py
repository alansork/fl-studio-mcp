"""fl-studio-mcp server entry point.

Run with:  fl-studio-mcp        (after `pip install -e .`)
      or:  python -m src.main   (from the repo root)

Claude Desktop / Claude Code launches this over stdio; the tools then talk
to FL Studio through the MIDI + file bridge (see src/bridge/).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .bridge import FLBridge
from .tools import (
    channel_tools,
    diagnostic_tools,
    mixer_tools,
    pianoroll_tools,
    transport_tools,
)


def build_server() -> FastMCP:
    """Create the FastMCP server with every tool registered."""
    mcp = FastMCP(
        "fl-studio-mcp",
        instructions=(
            "Control Image-Line FL Studio: transport, mixer, channel rack, "
            "piano roll composition, and mix diagnostics. If tools fail, run "
            "fl_check_bridge_status first. Piano roll tools QUEUE notes — the "
            "user must run the 'MCP PianoRoll' script inside FL Studio's "
            "piano roll to apply them."
        ),
    )
    bridge = FLBridge()
    for module in (transport_tools, mixer_tools, channel_tools,
                   pianoroll_tools, diagnostic_tools):
        module.register(mcp, bridge)
    return mcp


def main() -> None:
    """Console-script entry point (stdio transport)."""
    build_server().run()


if __name__ == "__main__":
    main()
