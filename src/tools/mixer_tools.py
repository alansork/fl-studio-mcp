"""Category B — mixer & signal-routing tools."""

from __future__ import annotations

import re

from mcp.server.fastmcp import FastMCP

from ..bridge import FLBridge
from . import safe_call

HEX_COLOR_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _hex_to_fl_color(hex_color: str) -> int:
    """Convert '#RRGGBB' into FL Studio's integer color format.

    FL Studio stores colors as 0xBBGGRR (blue in the high byte), which is the
    reverse byte order of the familiar web hex code.
    """
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def _check_track(track_index: int) -> str | None:
    """Return an error string if the mixer track index is out of range."""
    if not (0 <= track_index <= 125):
        return f"track_index must be 0-125 (0 is Master), got {track_index}"
    return None


def register(mcp: FastMCP, bridge: FLBridge) -> None:
    """Attach all mixer tools to the FastMCP server."""

    @mcp.tool()
    def fl_get_mixer_summary(only_named: bool = True) -> dict:
        """Get a summary of all mixer tracks: name, volume (0.0-1.0, 0.8 is
        0 dB), pan (-1.0 left to 1.0 right), mute state, and color.

        Args:
            only_named: If True (default), skip tracks that still have their
                default "Insert N" name — keeps the table small. Set False to
                list all 126 tracks.
        """
        # The summary walks up to 126 tracks inside FL, allow a bit more time.
        return safe_call(bridge, "get_mixer_summary", {"only_named": bool(only_named)},
                         timeout=10.0)

    @mcp.tool()
    def fl_set_track_volume(track_index: int, volume: float) -> dict:
        """Set a mixer track's fader level.

        Args:
            track_index: 0 = Master, 1-125 = inserts.
            volume: 0.0 (silent) to 1.0 (max). 0.8 is unity gain (0 dB).
        """
        if err := _check_track(track_index):
            return {"ok": False, "error": err}
        if not (0.0 <= volume <= 1.0):
            return {"ok": False, "error": f"volume must be 0.0-1.0, got {volume}"}
        return safe_call(bridge, "set_track_volume",
                         {"track": track_index, "volume": float(volume)})

    @mcp.tool()
    def fl_set_track_pan(track_index: int, pan: float) -> dict:
        """Set a mixer track's stereo pan.

        Args:
            track_index: 0 = Master, 1-125 = inserts.
            pan: -1.0 (hard left) to 1.0 (hard right), 0.0 is center.
        """
        if err := _check_track(track_index):
            return {"ok": False, "error": err}
        if not (-1.0 <= pan <= 1.0):
            return {"ok": False, "error": f"pan must be -1.0 to 1.0, got {pan}"}
        return safe_call(bridge, "set_track_pan",
                         {"track": track_index, "pan": float(pan)})

    @mcp.tool()
    def fl_mute_track(track_index: int, state: bool) -> dict:
        """Mute (state=True) or unmute (state=False) a mixer track."""
        if err := _check_track(track_index):
            return {"ok": False, "error": err}
        return safe_call(bridge, "mute_track",
                         {"track": track_index, "state": bool(state)})

    @mcp.tool()
    def fl_solo_track(track_index: int, mode: int = 0) -> dict:
        """Solo a mixer track (toggles solo on/off).

        Args:
            track_index: The track to solo.
            mode: Isolation option — 0 = plain solo toggle,
                1 = solo including the tracks it sends to,
                2 = solo including its source tracks,
                3 = both sources and destinations.
        """
        if err := _check_track(track_index):
            return {"ok": False, "error": err}
        if mode not in (0, 1, 2, 3):
            return {"ok": False, "error": f"mode must be 0-3, got {mode}"}
        return safe_call(bridge, "solo_track", {"track": track_index, "mode": mode})

    @mcp.tool()
    def fl_set_track_name_color(track_index: int, name: str, hex_color: str) -> dict:
        """Rename a mixer track and set its color.

        Args:
            track_index: The track to label.
            name: New track name, e.g. "Drum Bus".
            hex_color: Web-style color like "#FF8800".
        """
        if err := _check_track(track_index):
            return {"ok": False, "error": err}
        if not HEX_COLOR_RE.match(hex_color):
            return {"ok": False, "error": f"hex_color must look like '#RRGGBB', got {hex_color!r}"}
        return safe_call(bridge, "set_track_name_color", {
            "track": track_index,
            "name": name,
            "color": _hex_to_fl_color(hex_color),
        })

    @mcp.tool()
    def fl_route_track(source_index: int, target_index: int, enable: bool = True) -> dict:
        """Route (or un-route) audio from one mixer track into another.

        Use this for bus/submix setups or to feed a sidechain input, e.g.
        route the kick track into the compressor's sidechain track.

        Args:
            source_index: Track whose audio is sent.
            target_index: Track that receives the audio.
            enable: True to create the route, False to remove it.
        """
        for idx, label in ((source_index, "source_index"), (target_index, "target_index")):
            if not (0 <= idx <= 125):
                return {"ok": False, "error": f"{label} must be 0-125, got {idx}"}
        if source_index == target_index:
            return {"ok": False, "error": "source and target must be different tracks"}
        return safe_call(bridge, "route_track", {
            "source": source_index,
            "target": target_index,
            "enable": bool(enable),
        })
