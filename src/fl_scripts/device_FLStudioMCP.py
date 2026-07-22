# name=FL Studio MCP Bridge
# url=https://github.com/alansork/fl-studio-mcp
"""This script runs INSIDE FL Studio (Options > MIDI Settings > controller
type "FL Studio MCP Bridge" on the virtual MIDI port).

It is intentionally self-contained: FL Studio copies only this one file, so
it cannot import anything from the fl-studio-mcp package. It must mirror the
protocol in src/bridge/ipc_handler.py:

  * requests arrive at  ~/.fl-studio-mcp/request.json
  * a MIDI CC 110 (channel 16, value 127) says "a new request is waiting"
  * we execute the command with FL Studio's Python API modules
  * we write the outcome to ~/.fl-studio-mcp/response.json

Heavy work happens in OnIdle (FL calls it continuously on the UI thread),
NOT in the MIDI callback, to keep MIDI handling snappy.
"""

import json
import os
import time

import channels
import general
import midi
import mixer
import patterns
import transport

# ---- protocol constants (must match src/bridge/) --------------------------
BRIDGE_DIR = os.path.join(os.path.expanduser("~"), ".fl-studio-mcp")
REQUEST_FILE = os.path.join(BRIDGE_DIR, "request.json")
RESPONSE_FILE = os.path.join(BRIDGE_DIR, "response.json")
TRIGGER_CC = 110
TRIGGER_CHANNEL = 15  # 0-indexed => MIDI channel 16
SCRIPT_VERSION = "0.1.0"

# ---- state -----------------------------------------------------------------
_request_pending = False   # set by the MIDI trigger, consumed by OnIdle
_last_poll = 0.0           # fallback polling timer
_last_request_id = None    # so we never execute the same request twice
POLL_EVERY_S = 0.5         # fallback: check the mailbox even without MIDI


def OnInit():
    """Called once when FL Studio loads the script."""
    try:
        if not os.path.isdir(BRIDGE_DIR):
            os.makedirs(BRIDGE_DIR)
    except OSError:
        pass
    print("FL Studio MCP Bridge v" + SCRIPT_VERSION + " ready. Mailbox: " + BRIDGE_DIR)


def OnDeInit():
    print("FL Studio MCP Bridge unloaded.")


def OnMidiMsg(event):
    """Wake up when the MCP server rings the doorbell (CC 110, ch 16)."""
    global _request_pending
    if (
        event.midiId == midi.MIDI_CONTROLCHANGE
        and event.midiChan == TRIGGER_CHANNEL
        and event.data1 == TRIGGER_CC
    ):
        _request_pending = True
        event.handled = True  # don't let the CC leak into the project


def OnIdle():
    """FL Studio calls this continuously; we do the actual work here."""
    global _request_pending, _last_poll
    now = time.time()
    # Primary path: MIDI trigger. Fallback: poll the mailbox twice a second
    # in case a trigger was missed (e.g. port hiccup).
    if not _request_pending and (now - _last_poll) < POLL_EVERY_S:
        return
    _last_poll = now
    _request_pending = False
    _process_request_file()


# ---------------------------------------------------------------------------
# Request processing
# ---------------------------------------------------------------------------

def _process_request_file():
    global _last_request_id
    try:
        with open(REQUEST_FILE, "r") as f:
            request = json.load(f)
    except (IOError, OSError, ValueError):
        return  # no request waiting, or mid-write — try again next idle tick

    request_id = request.get("id")
    if not request_id or request_id == _last_request_id:
        return  # already handled this one
    _last_request_id = request_id

    command = request.get("command", "")
    params = request.get("params", {}) or {}

    handler = HANDLERS.get(command)
    if handler is None:
        _write_response(request_id, ok=False,
                        error="Unknown command: " + str(command))
        return
    try:
        result = handler(params)
        _write_response(request_id, ok=True, result=result)
    except Exception as exc:  # report errors back instead of dying silently
        _write_response(request_id, ok=False,
                        error=type(exc).__name__ + ": " + str(exc))


def _write_response(request_id, ok, result=None, error=None):
    """Atomically write response.json (tmp file + rename)."""
    payload = {
        "id": request_id,
        "ok": ok,
        "result": result,
        "error": error,
        "timestamp": time.time(),
    }
    tmp = RESPONSE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(payload, f)
        if os.path.exists(RESPONSE_FILE):
            os.remove(RESPONSE_FILE)  # os.replace may not exist in embedded py
        os.rename(tmp, RESPONSE_FILE)
    except (IOError, OSError) as exc:
        print("MCP Bridge: failed to write response: " + str(exc))


# ---------------------------------------------------------------------------
# Command handlers — each takes the params dict, returns a JSON-able result
# ---------------------------------------------------------------------------

def _current_bpm():
    """Current tempo in plain BPM.

    Real FL builds return the raw project value from getCurrentTempo, which
    is BPM * 1000 (e.g. 140 BPM -> 140000). Valid tempos are 10-522 BPM, so
    anything >= 1000 must be the raw form and gets divided down.
    """
    bpm = mixer.getCurrentTempo()
    if bpm and bpm >= 1000:
        bpm = bpm / 1000.0
    return bpm


def _h_ping(params):
    return {
        "script_version": SCRIPT_VERSION,
        "fl_version": getattr(general, "getVersion", lambda: 0)(),
        "time": time.time(),
    }


# ---- transport -------------------------------------------------------------

def _h_play(params):
    if not transport.isPlaying():
        transport.start()
    return {"playing": bool(transport.isPlaying())}


def _h_stop(params):
    transport.stop()
    return {"playing": bool(transport.isPlaying())}


def _h_pause(params):
    # transport.start() toggles play/pause in FL Studio.
    transport.start()
    return {"playing": bool(transport.isPlaying())}


def _h_set_tempo(params):
    bpm = float(params["bpm"])
    # There is no direct tempo setter; the documented way is a REC event.
    # REC_Tempo expects BPM * 1000 as an int.
    general.processRECEvent(
        midi.REC_Tempo,
        int(bpm * 1000),
        midi.REC_Control | midi.REC_UpdateControl,
    )
    return {"bpm": bpm}


def _h_set_loop_mode(params):
    want_song = params.get("mode") == "song"
    # getLoopMode: 0 = pattern, 1 = song. setLoopMode() toggles.
    if bool(transport.getLoopMode()) != want_song:
        transport.setLoopMode()
    return {"mode": "song" if transport.getLoopMode() else "pattern"}


def _h_toggle_record(params):
    transport.record()
    return {"recording": bool(transport.isRecording())}


def _h_get_transport_state(params):
    return {
        "bpm": _current_bpm(),
        "playing": bool(transport.isPlaying()),
        "recording": bool(transport.isRecording()),
        "loop_mode": "song" if transport.getLoopMode() else "pattern",
        "song_position_fraction": transport.getSongPos(),
        "song_position_hint": transport.getSongPosHint(),
    }


# ---- mixer -----------------------------------------------------------------

def _h_get_mixer_summary(params):
    only_named = bool(params.get("only_named", True))
    tracks = []
    count = min(mixer.trackCount(), 126)
    for i in range(count):
        name = mixer.getTrackName(i)
        # Default names look like "Insert 1" / "Master"; skip untouched
        # inserts when only_named is set (Master always included).
        if only_named and i != 0 and name.startswith("Insert "):
            continue
        tracks.append({
            "index": i,
            "name": name,
            "volume": round(mixer.getTrackVolume(i), 4),
            "pan": round(mixer.getTrackPan(i), 4),
            "muted": bool(mixer.isTrackMuted(i)),
            "color": mixer.getTrackColor(i),
        })
    return {"track_count": count, "tracks": tracks}


def _h_set_track_volume(params):
    track = int(params["track"])
    mixer.setTrackVolume(track, float(params["volume"]))
    return {"track": track, "volume": mixer.getTrackVolume(track)}


def _h_set_track_pan(params):
    track = int(params["track"])
    mixer.setTrackPan(track, float(params["pan"]))
    return {"track": track, "pan": mixer.getTrackPan(track)}


def _h_mute_track(params):
    track = int(params["track"])
    want_muted = bool(params["state"])
    if bool(mixer.isTrackMuted(track)) != want_muted:
        mixer.muteTrack(track)  # toggles
    return {"track": track, "muted": bool(mixer.isTrackMuted(track))}


def _h_solo_track(params):
    track = int(params["track"])
    mode = int(params.get("mode", 0))
    # Newer FL builds accept solo modes; fall back to the plain toggle.
    solo_flags = {
        0: None,
        1: getattr(midi, "fxSoloModeWithDestTracks", None),
        2: getattr(midi, "fxSoloModeWithSourceTracks", None),
        3: (getattr(midi, "fxSoloModeWithDestTracks", 0)
            | getattr(midi, "fxSoloModeWithSourceTracks", 0)) or None,
    }.get(mode)
    if solo_flags is None:
        mixer.soloTrack(track)
    else:
        mixer.soloTrack(track, -1, solo_flags)
    return {"track": track, "soloed": bool(mixer.isTrackSolo(track))}


def _h_set_track_name_color(params):
    track = int(params["track"])
    mixer.setTrackName(track, str(params["name"]))
    mixer.setTrackColor(track, int(params["color"]))
    return {"track": track, "name": mixer.getTrackName(track)}


def _h_route_track(params):
    source = int(params["source"])
    target = int(params["target"])
    enable = 1 if params.get("enable", True) else 0
    mixer.setRouteTo(source, target, enable)
    mixer.afterRoutingChanged()  # required for FL to pick up the change
    return {"source": source, "target": target, "routed": bool(enable)}


def _h_get_track_peaks(params):
    tracks = []
    count = min(mixer.trackCount(), 126)
    for i in range(count):
        name = mixer.getTrackName(i)
        if i != 0 and name.startswith("Insert "):
            continue  # only report tracks the user actually uses (+ Master)
        tracks.append({
            "index": i,
            "name": name,
            "peak_left": mixer.getTrackPeaks(i, 0),
            "peak_right": mixer.getTrackPeaks(i, 1),
        })
    return {"tracks": tracks}


# ---- channel rack ----------------------------------------------------------

def _h_list_channels(params):
    result = []
    for i in range(channels.channelCount()):
        result.append({
            "index": i,
            "name": channels.getChannelName(i),
            "volume": round(channels.getChannelVolume(i), 4),
            "pan": round(channels.getChannelPan(i), 4),
            "muted": bool(channels.isChannelMuted(i)),
            "mixer_track": channels.getTargetFxTrack(i),
        })
    return {"channel_count": channels.channelCount(), "channels": result}


def _check_channel(index):
    if index < 0 or index >= channels.channelCount():
        raise ValueError("channel " + str(index) + " out of range (0-"
                         + str(channels.channelCount() - 1) + ")")


def _h_set_channel_props(params):
    ch = int(params["channel"])
    _check_channel(ch)
    if params.get("volume") is not None:
        channels.setChannelVolume(ch, float(params["volume"]))
    if params.get("pan") is not None:
        channels.setChannelPan(ch, float(params["pan"]))
    if params.get("pitch") is not None:
        # Mode 1 = pitch in cents. (Mode 0 would be a fraction of the
        # channel's pitch-bend range, NOT cents — the tool promises cents.)
        channels.setChannelPitch(ch, int(params["pitch"]), 1)
    return {
        "channel": ch,
        "volume": channels.getChannelVolume(ch),
        "pan": channels.getChannelPan(ch),
    }


def _h_set_step_sequencer(params):
    ch = int(params["channel"])
    _check_channel(ch)
    steps = params["steps"]  # list of booleans
    for pos, on in enumerate(steps):
        channels.setGridBit(ch, pos, 1 if on else 0)
    return {"channel": ch, "steps_written": len(steps),
            "pattern": patterns.patternNumber()}


def _h_assign_channel_to_mixer(params):
    ch = int(params["channel"])
    _check_channel(ch)
    target = int(params["mixer_track"])
    setter = getattr(channels, "setTargetFxTrack", None)
    if setter is None:
        raise RuntimeError(
            "This FL Studio version does not expose setTargetFxTrack; "
            "route the channel manually (channel settings > TRACK)."
        )
    setter(ch, target)
    return {"channel": ch, "mixer_track": channels.getTargetFxTrack(ch)}


def _h_mute_channel(params):
    ch = int(params["channel"])
    _check_channel(ch)
    want_muted = bool(params["state"])
    if bool(channels.isChannelMuted(ch)) != want_muted:
        channels.muteChannel(ch)  # toggles
    return {"channel": ch, "muted": bool(channels.isChannelMuted(ch))}


# ---- project info ----------------------------------------------------------

def _h_get_project_info(params):
    return {
        "bpm": _current_bpm(),
        "ppq": general.getRecPPQ(),
        "channel_count": channels.channelCount(),
        "mixer_track_count": mixer.trackCount(),
        "pattern_count": patterns.patternCount(),
        "current_pattern": patterns.patternNumber(),
    }


HANDLERS = {
    "ping": _h_ping,
    # transport
    "play": _h_play,
    "stop": _h_stop,
    "pause": _h_pause,
    "set_tempo": _h_set_tempo,
    "set_loop_mode": _h_set_loop_mode,
    "toggle_record": _h_toggle_record,
    "get_transport_state": _h_get_transport_state,
    # mixer
    "get_mixer_summary": _h_get_mixer_summary,
    "set_track_volume": _h_set_track_volume,
    "set_track_pan": _h_set_track_pan,
    "mute_track": _h_mute_track,
    "solo_track": _h_solo_track,
    "set_track_name_color": _h_set_track_name_color,
    "route_track": _h_route_track,
    "get_track_peaks": _h_get_track_peaks,
    # channels
    "list_channels": _h_list_channels,
    "set_channel_props": _h_set_channel_props,
    "set_step_sequencer": _h_set_step_sequencer,
    "assign_channel_to_mixer": _h_assign_channel_to_mixer,
    "mute_channel": _h_mute_channel,
    # project
    "get_project_info": _h_get_project_info,
}
