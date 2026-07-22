"""Category A — transport & playback control tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..bridge import FLBridge
from . import safe_call

MIN_BPM = 10.0
MAX_BPM = 520.0


def register(mcp: FastMCP, bridge: FLBridge) -> None:
    """Attach all transport tools to the FastMCP server."""

    @mcp.tool()
    def fl_play() -> dict:
        """Start playback in FL Studio (same as pressing the play button)."""
        return safe_call(bridge, "play")

    @mcp.tool()
    def fl_stop() -> dict:
        """Stop playback and reset the playhead to the start."""
        return safe_call(bridge, "stop")

    @mcp.tool()
    def fl_pause() -> dict:
        """Toggle pause. If playing, pauses at the current position; if paused, resumes."""
        return safe_call(bridge, "pause")

    @mcp.tool()
    def fl_set_tempo(bpm: float) -> dict:
        """Set the project tempo in BPM. Allowed range: 10.0 to 520.0.

        Args:
            bpm: New tempo, e.g. 128.0
        """
        if not (MIN_BPM <= bpm <= MAX_BPM):
            return {
                "ok": False,
                "error": f"bpm must be between {MIN_BPM} and {MAX_BPM}, got {bpm}",
            }
        return safe_call(bridge, "set_tempo", {"bpm": float(bpm)})

    @mcp.tool()
    def fl_set_loop_mode(mode: str) -> dict:
        """Switch between 'pattern' mode and 'song' mode playback.

        Args:
            mode: Either "pattern" or "song".
        """
        mode = mode.strip().lower()
        if mode not in ("pattern", "song"):
            return {"ok": False, "error": f"mode must be 'pattern' or 'song', got {mode!r}"}
        return safe_call(bridge, "set_loop_mode", {"mode": mode})

    @mcp.tool()
    def fl_toggle_record() -> dict:
        """Toggle recording arm on/off (same as pressing the record button)."""
        return safe_call(bridge, "toggle_record")

    @mcp.tool()
    def fl_get_transport_state() -> dict:
        """Get the current transport state: BPM, playing/recording status,
        loop mode, and the song position (as a bar:step:tick hint plus a
        0.0-1.0 fraction of the song)."""
        return safe_call(bridge, "get_transport_state")
