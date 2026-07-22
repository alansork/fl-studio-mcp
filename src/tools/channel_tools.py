"""Category C — channel rack & step sequencer tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..bridge import FLBridge
from . import safe_call

MAX_STEPS = 64  # FL Studio patterns support up to 64 visible steps per row


def register(mcp: FastMCP, bridge: FLBridge) -> None:
    """Attach all channel rack tools to the FastMCP server."""

    @mcp.tool()
    def fl_list_channels() -> dict:
        """List every channel in the channel rack: index, name, volume, pan,
        mute state, and which mixer track it is routed to."""
        return safe_call(bridge, "list_channels", timeout=10.0)

    @mcp.tool()
    def fl_set_channel_pitch_pan_vol(
        channel_index: int,
        volume: float | None = None,
        pan: float | None = None,
        pitch: int | None = None,
    ) -> dict:
        """Tweak a channel's volume, pan and/or pitch. Only the parameters you
        pass are changed; the rest stay as they are.

        Args:
            channel_index: Position in the channel rack (0-based).
            volume: 0.0-1.0 (0.78 is the FL default).
            pan: -1.0 (left) to 1.0 (right).
            pitch: Pitch offset in cents (-1200 to 1200 = one octave down/up).
        """
        if channel_index < 0:
            return {"ok": False, "error": "channel_index must be >= 0"}
        if volume is not None and not (0.0 <= volume <= 1.0):
            return {"ok": False, "error": f"volume must be 0.0-1.0, got {volume}"}
        if pan is not None and not (-1.0 <= pan <= 1.0):
            return {"ok": False, "error": f"pan must be -1.0 to 1.0, got {pan}"}
        if pitch is not None and not (-1200 <= pitch <= 1200):
            return {"ok": False, "error": f"pitch must be -1200 to 1200 cents, got {pitch}"}
        if volume is None and pan is None and pitch is None:
            return {"ok": False, "error": "pass at least one of volume, pan, pitch"}
        return safe_call(bridge, "set_channel_props", {
            "channel": channel_index,
            "volume": volume,
            "pan": pan,
            "pitch": pitch,
        })

    @mcp.tool()
    def fl_set_step_sequencer(channel_index: int, step_pattern: str) -> dict:
        """Program a channel's step-sequencer row using a binary string.

        Each character is one 16th-note step: '1' = step on, '0' = step off.
        Example: "1000100010001000" is a four-on-the-floor kick over one bar.
        The pattern is written into the currently selected pattern.

        Args:
            channel_index: Channel rack row to program (0-based).
            step_pattern: String of '0'/'1', 1 to 64 characters.
        """
        if channel_index < 0:
            return {"ok": False, "error": "channel_index must be >= 0"}
        err = validate_step_pattern(step_pattern)
        if err:
            return {"ok": False, "error": err}
        return safe_call(bridge, "set_step_sequencer", {
            "channel": channel_index,
            "steps": [c == "1" for c in step_pattern],
        })

    @mcp.tool()
    def fl_assign_channel_to_mixer(channel_index: int, mixer_track: int) -> dict:
        """Route a channel's audio output to a specific mixer insert track.

        Args:
            channel_index: Channel rack row (0-based).
            mixer_track: Target mixer insert (1-125), or 0 for Master.
        """
        if channel_index < 0:
            return {"ok": False, "error": "channel_index must be >= 0"}
        if not (0 <= mixer_track <= 125):
            return {"ok": False, "error": f"mixer_track must be 0-125, got {mixer_track}"}
        return safe_call(bridge, "assign_channel_to_mixer", {
            "channel": channel_index,
            "mixer_track": mixer_track,
        })

    @mcp.tool()
    def fl_mute_channel(channel_index: int, state: bool) -> dict:
        """Mute (state=True) or unmute (state=False) a channel rack channel."""
        if channel_index < 0:
            return {"ok": False, "error": "channel_index must be >= 0"}
        return safe_call(bridge, "mute_channel",
                         {"channel": channel_index, "state": bool(state)})


def validate_step_pattern(step_pattern: str) -> str | None:
    """Check a step string. Returns an error message, or None if it's valid.

    Kept as a module-level function so the test suite can exercise it without
    a running MCP server.
    """
    if not step_pattern:
        return "step_pattern must not be empty"
    if len(step_pattern) > MAX_STEPS:
        return f"step_pattern is limited to {MAX_STEPS} steps, got {len(step_pattern)}"
    bad = set(step_pattern) - {"0", "1"}
    if bad:
        return f"step_pattern may only contain '0' and '1', found {sorted(bad)}"
    return None
