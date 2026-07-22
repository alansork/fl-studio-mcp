"""Virtual MIDI "doorbell" that wakes up the script inside FL Studio.

FL Studio cannot watch files on its own, but its MIDI scripts react instantly
to incoming MIDI. So after we write ``request.json`` we send one Control
Change message (CC 110, value 127, channel 16) through a virtual MIDI cable:

* Windows: a loopMIDI port (https://www.tobias-erichsen.de/software/loopmidi.html)
* macOS:   the built-in IAC Driver (enable it in Audio MIDI Setup)

The device script inside FL Studio listens for that exact CC and then reads
and executes the request file.
"""

from __future__ import annotations

import os

import mido

# The trigger message. Channel is 0-indexed in mido, so 15 == MIDI channel 16.
TRIGGER_CC = 110
TRIGGER_CHANNEL = 15
TRIGGER_VALUE = 127

# Port-name fragments we search for, in order of preference. Users can also
# force a specific port with the FL_MCP_MIDI_PORT environment variable.
PREFERRED_PORT_FRAGMENTS = ("fl mcp", "flstudiomcp", "loopmidi", "iac")


class MidiBridgeError(Exception):
    """Raised when no usable virtual MIDI port can be found or opened."""


_cached_port = None  # keep the port open between tool calls (opening is slow)


def list_output_ports() -> list[str]:
    """Return the names of every MIDI output port visible on this machine."""
    try:
        return list(mido.get_output_names())
    except Exception as exc:  # rtmidi can raise platform-specific errors
        raise MidiBridgeError(f"Could not enumerate MIDI ports: {exc}") from exc


def find_port_name() -> str:
    """Pick the virtual MIDI port that feeds FL Studio.

    Search order:
    1. Exact/partial match of the FL_MCP_MIDI_PORT environment variable.
    2. Well-known virtual-cable names (loopMIDI, IAC, or a port containing
       "FL MCP").
    """
    ports = list_output_ports()
    if not ports:
        raise MidiBridgeError(
            "No MIDI output ports found. On Windows install and start loopMIDI; "
            "on macOS enable the IAC Driver in Audio MIDI Setup."
        )

    override = os.environ.get("FL_MCP_MIDI_PORT", "").strip().lower()
    if override:
        for name in ports:
            if override in name.lower():
                return name
        raise MidiBridgeError(
            f"FL_MCP_MIDI_PORT={override!r} did not match any port. "
            f"Available ports: {ports}"
        )

    for fragment in PREFERRED_PORT_FRAGMENTS:
        for name in ports:
            if fragment in name.lower():
                return name

    raise MidiBridgeError(
        "No virtual MIDI cable found. Create a loopMIDI port (Windows) or "
        "enable the IAC Driver (macOS), then restart this server. "
        f"Ports currently visible: {ports}"
    )


def send_trigger() -> str:
    """Send the wake-up CC to FL Studio. Returns the port name used."""
    global _cached_port
    name = find_port_name()

    # Re-open the port if it changed or was never opened.
    if _cached_port is None or _cached_port.name != name:
        if _cached_port is not None:
            try:
                _cached_port.close()
            except Exception:
                pass
        try:
            _cached_port = mido.open_output(name)
        except Exception as exc:
            _cached_port = None
            raise MidiBridgeError(f"Could not open MIDI port {name!r}: {exc}") from exc

    msg = mido.Message(
        "control_change",
        channel=TRIGGER_CHANNEL,
        control=TRIGGER_CC,
        value=TRIGGER_VALUE,
    )
    try:
        _cached_port.send(msg)
    except Exception as exc:
        # The port may have died (loopMIDI closed, etc.) — drop the cache so
        # the next call re-discovers it.
        _cached_port = None
        raise MidiBridgeError(f"Failed to send MIDI trigger on {name!r}: {exc}") from exc
    return name
