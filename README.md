# fl-studio-mcp

An open-source **MCP server** that connects **Claude** (Claude Desktop or
Claude Code) to **Image-Line FL Studio**. Ask Claude in plain language to
start playback, set the tempo, gain-stage your mixer, program drum patterns,
write chord progressions into the piano roll, or audit your mix for clipping.

Built by **sorkthropic**. MIT licensed.

---

## How it works

FL Studio has no external API — only two Python environments *inside* it:

| Environment | Runs | Can touch |
|---|---|---|
| MIDI device scripts (`device_*.py`) | continuously | transport, mixer, channels, patterns, general |
| Piano roll scripts (`.pyscript`) | on demand from the piano roll menu | notes, markers, score |

So this project bridges the gap with **files + a virtual MIDI cable**:

```
Claude ──MCP──▶ fl-studio-mcp server
                   │ 1. writes  ~/.fl-studio-mcp/request.json
                   │ 2. sends MIDI CC 110 through the virtual cable ("doorbell")
                   ▼
        FL Studio device script (device_FLStudioMCP.py)
                   │ 3. reads the request, calls the FL Studio Python API
                   │ 4. writes  ~/.fl-studio-mcp/response.json
                   ▼
        fl-studio-mcp server reads the response ──▶ Claude
```

Piano roll edits are special: FL Studio only allows note editing from
on-demand piano roll scripts. Claude's note tools therefore **queue** the
notes to `~/.fl-studio-mcp/pianoroll_queue.json`, and you apply them by
running **Tools ▸ Scripts ▸ MCP PianoRoll** inside the piano roll (one click;
undo works normally).

> Note: the mailbox lives in `~/.fl-studio-mcp/` rather than `/tmp` because
> `/tmp` doesn't exist on Windows and temp paths can differ per process on
> macOS — the home folder is the one path both sides always agree on.

---

## Setup

### 1. Install this package

```bash
git clone https://github.com/alansork/fl-studio-mcp
cd fl-studio-mcp
pip install -e .        # or: uv sync
```

### 2. Create the virtual MIDI cable

**Windows — loopMIDI**
1. Install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html).
2. Open it and create a port named **FL MCP** (any name works; this one is
   auto-detected).
3. Leave loopMIDI running — the port only exists while it runs.

**macOS — nothing to do**

The server creates its own virtual port named **FL MCP** automatically the
first time it needs one. FL Studio lists it as an input while the server is
running. (If you prefer an always-on port that exists even when the server
is down, enable the **IAC Driver** in Audio MIDI Setup — Window ▸ Show MIDI
Studio ▸ double-click IAC Driver ▸ tick *Device is online* — and it will be
used instead.)

If auto-detection picks the wrong port, set the environment variable
`FL_MCP_MIDI_PORT` to (part of) the right port's name.

### 3. Install the FL Studio scripts

```bash
python3 install.py
```

This copies:
* `device_FLStudioMCP.py` → `.../FL Studio/Settings/Hardware/FLStudioMCP/`
* `MCP_PianoRoll.pyscript` → `.../FL Studio/Settings/Piano roll scripts/`

and offers to register the server in Claude Desktop's config.

### 4. Wire it up inside FL Studio

1. Restart FL Studio (it scans for scripts at startup).
2. **Options ▸ MIDI Settings**.
3. In the **Input** list, select your virtual port (FL MCP / loopMIDI / IAC).
4. Set **Controller type** to **FL Studio MCP Bridge** and click **Enable**.
5. You should see `FL Studio MCP Bridge v0.1.0 ready` in the script output
   (View ▸ Script output).

### 5. Register with Claude

**Claude Desktop** — `install.py` can do this for you, or add manually to
`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "fl-studio-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/fl-studio-mcp", "fl-studio-mcp"]
    }
  }
}
```

**Claude Code**:

```bash
claude mcp add fl-studio-mcp -- uv run --directory /path/to/fl-studio-mcp fl-studio-mcp
```

Then ask Claude: *"Run fl_check_bridge_status"* — it should report the MIDI
port and FL Studio answering the ping.

---

## Tools

### Transport (`transport_tools.py`)
| Tool | What it does |
|---|---|
| `fl_play` / `fl_stop` / `fl_pause` | Playback control |
| `fl_set_tempo(bpm)` | Set tempo (10–520 BPM) |
| `fl_set_loop_mode(mode)` | "pattern" or "song" mode |
| `fl_toggle_record` | Arm/disarm recording |
| `fl_get_transport_state` | BPM, play/record state, song position |

### Mixer (`mixer_tools.py`)
| Tool | What it does |
|---|---|
| `fl_get_mixer_summary` | Table of names, volumes, pans, mutes, colors |
| `fl_set_track_volume(track, volume)` | Fader (0–1; 0.8 = 0 dB) |
| `fl_set_track_pan(track, pan)` | Pan (-1 to 1) |
| `fl_mute_track(track, state)` | Mute/unmute |
| `fl_solo_track(track, mode)` | Solo with isolation options |
| `fl_set_track_name_color(track, name, hex)` | Label + color |
| `fl_route_track(source, target, enable)` | Bus/sidechain routing |

### Channel rack (`channel_tools.py`)
| Tool | What it does |
|---|---|
| `fl_list_channels` | Channels + their mixer assignments |
| `fl_set_channel_pitch_pan_vol(...)` | Per-channel vol/pan/pitch |
| `fl_set_step_sequencer(channel, "1000100010001000")` | Program drum steps |
| `fl_assign_channel_to_mixer(channel, track)` | Route channel → insert |
| `fl_mute_channel(channel, state)` | Mute/unmute a channel |

### Piano roll (`pianoroll_tools.py`) — *queued; apply with the MCP PianoRoll script*
| Tool | What it does |
|---|---|
| `fl_add_chord_progression("C", "major", ["I","V","vi","IV"], ...)` | Music theory → notes |
| `fl_add_notes([{pitch, start, length, velocity}, ...])` | Arbitrary notes |
| `fl_quantize_piano_roll("1/16")` | Snap notes to a grid |
| `fl_clear_piano_roll` | Remove all notes (undoable) |

### Diagnostics (`diagnostic_tools.py`)
| Tool | What it does |
|---|---|
| `fl_diagnose_mix` | Clipping / headroom / L-R imbalance report (run during playback) |
| `fl_get_project_info` | Tempo, PPQ, channel/pattern/track counts |
| `fl_check_bridge_status` | Health-check the whole bridge — run this first when debugging |

---

## Development

Run the test suite (no FL Studio or MIDI hardware needed):

```bash
python3 -m unittest discover -s tests
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No virtual MIDI cable found" | Start loopMIDI (Win) / enable IAC (macOS); check `FL_MCP_MIDI_PORT` |
| "FL Studio did not respond" | FL open? Script enabled in MIDI Settings? Right input port? |
| Notes don't appear | Piano roll tools only *queue* — run Tools ▸ Scripts ▸ MCP PianoRoll |
| `fl_diagnose_mix` shows all zeros | Peaks are only measured during playback — press play first |

## License

MIT © sorkthropic
