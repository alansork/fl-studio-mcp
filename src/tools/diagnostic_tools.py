"""Category E — mix diagnostics & bridge health tools."""

from __future__ import annotations

import math

from mcp.server.fastmcp import FastMCP

from ..bridge import FLBridge, midi_bridge
from . import safe_call

# Peak levels below this (linear, where 1.0 = 0 dBFS) count as "low headroom
# warning" territory: louder than -3 dBFS but not clipping yet.
LOW_HEADROOM_LINEAR = 0.707  # ≈ -3 dBFS
# Left/right peak difference (in dB) beyond which we flag an imbalance.
IMBALANCE_DB = 3.0


def _to_db(linear: float) -> float:
    """Convert a linear peak (1.0 = 0 dBFS) to decibels."""
    if linear <= 0.0:
        return -math.inf
    return 20.0 * math.log10(linear)


def analyze_peaks(tracks: list[dict]) -> dict:
    """Turn raw per-track peak readings into structured recommendations.

    ``tracks`` is a list of {"index", "name", "peak_left", "peak_right"}
    with linear peak values (1.0 = 0 dBFS). Pure function so the test suite
    can exercise it without FL Studio.
    """
    clipping, low_headroom, imbalanced = [], [], []
    for t in tracks:
        left = float(t.get("peak_left", 0.0))
        right = float(t.get("peak_right", 0.0))
        peak = max(left, right)
        label = f"{t.get('index')}: {t.get('name', '?')}"
        if peak >= 1.0:
            clipping.append({"track": label, "peak_db": round(_to_db(peak), 2)})
        elif peak >= LOW_HEADROOM_LINEAR:
            low_headroom.append({"track": label, "peak_db": round(_to_db(peak), 2)})
        if left > 0 and right > 0:
            diff = abs(_to_db(left) - _to_db(right))
            if diff >= IMBALANCE_DB:
                imbalanced.append({"track": label, "lr_difference_db": round(diff, 2)})

    recommendations: list[str] = []
    for c in clipping:
        recommendations.append(
            f"Track {c['track']} is clipping ({c['peak_db']} dBFS) — pull the "
            "fader down or add a limiter."
        )
    for h in low_headroom:
        recommendations.append(
            f"Track {h['track']} has under 3 dB of headroom ({h['peak_db']} dBFS) — "
            "consider lowering it before adding more elements."
        )
    for i in imbalanced:
        recommendations.append(
            f"Track {i['track']} is {i['lr_difference_db']} dB louder on one side — "
            "check its pan or a stereo plugin."
        )
    if not recommendations:
        recommendations.append("No clipping, headroom, or balance issues detected.")
    return {
        "clipping": clipping,
        "low_headroom": low_headroom,
        "imbalanced": imbalanced,
        "recommendations": recommendations,
    }


def register(mcp: FastMCP, bridge: FLBridge) -> None:
    """Attach all diagnostic tools to the FastMCP server."""

    @mcp.tool()
    def fl_diagnose_mix() -> dict:
        """Scan peak levels on all active mixer tracks and report clipping,
        low headroom, and left/right imbalances with recommendations.

        Note: FL Studio only reports peaks while audio is PLAYING — start
        playback (fl_play) over the loudest section first, then run this.
        """
        raw = safe_call(bridge, "get_track_peaks", timeout=10.0)
        if not raw.get("ok"):
            return raw
        tracks = raw["result"].get("tracks", [])
        analysis = analyze_peaks(tracks)
        analysis["ok"] = True
        if all(t.get("peak_left", 0) == 0 and t.get("peak_right", 0) == 0
               for t in tracks):
            analysis["note"] = ("All peaks are zero — is playback running? "
                                "Peaks are only measured during playback.")
        return analysis

    @mcp.tool()
    def fl_get_project_info() -> dict:
        """Get an overview of the project: tempo, channel count, mixer track
        count, pattern count, and timebase (PPQ)."""
        return safe_call(bridge, "get_project_info")

    @mcp.tool()
    def fl_check_bridge_status() -> dict:
        """Health-check the Claude <-> FL Studio bridge: lists MIDI ports,
        which port is selected, and whether FL Studio answers a ping.
        Run this first if any other tool is failing."""
        status: dict = {"ok": True}
        try:
            status["midi_ports"] = midi_bridge.list_output_ports()
            status["selected_port"] = midi_bridge.find_port_name()
        except midi_bridge.MidiBridgeError as exc:
            return {"ok": False, "stage": "midi", "error": str(exc)}
        ping = safe_call(bridge, "ping", timeout=3.0)
        status["fl_studio_responding"] = ping.get("ok", False)
        if ping.get("ok"):
            status["fl_studio"] = ping["result"]
        else:
            status["ok"] = False
            status["error"] = ping.get("error")
        return status
