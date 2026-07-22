"""Virtual MIDI "doorbell" that wakes up the script inside FL Studio.

FL Studio cannot watch files on its own, but its MIDI scripts react instantly
to incoming MIDI. So after we write ``request.json`` we send one Control
Change message (CC 110, value 127, channel 16) through a virtual MIDI cable:

* Windows: a loopMIDI port (https://www.tobias-erichsen.de/software/loopmidi.html)
* macOS:   an existing IAC bus if one is online — otherwise we simply CREATE
  our own virtual port named "FL MCP" (CoreMIDI allows this), so no setup in
  Audio MIDI Setup is needed at all. FL Studio sees it in its Input list for
  as long as this server is running.

The device script inside FL Studio listens for that exact CC and then reads
and executes the request file.
"""

from __future__ import annotations

import os
import sys

import mido

# The trigger message. Channel is 0-indexed in mido, so 15 == MIDI channel 16.
TRIGGER_CC = 110
TRIGGER_CHANNEL = 15
TRIGGER_VALUE = 127

# Port-name fragments we search for, in order of preference. Users can also
# force a specific port with the FL_MCP_MIDI_PORT environment variable.
PREFERRED_PORT_FRAGMENTS = ("fl mcp", "flstudiomcp", "loopmidi", "iac")

# Name of the virtual port we create ourselves when no cable exists.
# Windows rtmidi cannot create virtual ports, hence the loopMIDI requirement.
VIRTUAL_PORT_NAME = "FL MCP"


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


def open_port():
    """Open the doorbell port.

    Prefers an existing cable (loopMIDI / IAC / anything matching
    FL_MCP_MIDI_PORT). If none exists and the OS supports it (macOS, Linux),
    creates our own virtual port instead — FL Studio then sees an input
    named "FL MCP" for as long as this server runs.
    """
    try:
        name = find_port_name()
    except MidiBridgeError:
        if sys.platform.startswith("win"):
            raise  # Windows can't create virtual ports — loopMIDI required
        try:
            return mido.open_output(VIRTUAL_PORT_NAME, virtual=True)
        except Exception as exc:
            raise MidiBridgeError(
                f"No MIDI cable found and creating a virtual port failed: {exc}"
            ) from exc
    try:
        return mido.open_output(name)
    except Exception as exc:
        raise MidiBridgeError(f"Could not open MIDI port {name!r}: {exc}") from exc


def send_trigger() -> str:
    """Send the wake-up CC to FL Studio. Returns the port name used."""
    global _cached_port
    if _cached_port is None:
        _cached_port = open_port()

    msg = mido.Message(
        "control_change",
        channel=TRIGGER_CHANNEL,
        control=TRIGGER_CC,
        value=TRIGGER_VALUE,
    )
    try:
        _cached_port.send(msg)
    except Exception:
        # The port may have died (loopMIDI closed, IAC toggled off, ...).
        # Re-open once — possibly falling back to our own virtual port —
        # and retry before giving up.
        try:
            _cached_port.close()
        except Exception:
            pass
        _cached_port = None
        _cached_port = open_port()
        try:
            _cached_port.send(msg)
        except Exception as exc:
            name = getattr(_cached_port, "name", "?")
            _cached_port = None
            raise MidiBridgeError(
                f"Failed to send MIDI trigger on {name!r}: {exc}") from exc
    return _cached_port.name
