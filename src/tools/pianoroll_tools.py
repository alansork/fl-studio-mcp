"""Category D — piano roll & composition tools.

Important architectural note: FL Studio only allows note editing from
*Piano Roll scripts* (run on demand from the piano roll menu), not from the
always-running MIDI device script. So these tools QUEUE actions into
``~/.fl-studio-mcp/pianoroll_queue.json`` — the user then opens the piano
roll of the target channel and runs Tools > Scripts > "MCP PianoRoll",
which applies whatever is queued. Every tool response reminds the user of
that step.

The music theory (keys, scales, Roman numerals -> MIDI notes) lives here in
plain Python so it is fully unit-testable without FL Studio.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..bridge import FLBridge, FLBridgeError

RUN_SCRIPT_HINT = (
    "Notes are queued. In FL Studio, open the piano roll for the target "
    "channel and run: Piano roll menu > Tools > Scripts > 'MCP PianoRoll' "
    "to apply them."
)

# ---------------------------------------------------------------------------
# Music theory helpers (pure functions — no FL Studio needed)
# ---------------------------------------------------------------------------

# Semitone offset of each note name from C.
NOTE_TO_SEMITONE = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
    "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10,
    "B": 11,
}

# Scale intervals in semitones from the root, one entry per scale degree.
SCALES = {
    "major":          [0, 2, 4, 5, 7, 9, 11],
    "minor":          [0, 2, 3, 5, 7, 8, 10],  # natural minor
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "dorian":         [0, 2, 3, 5, 7, 9, 10],
    "phrygian":       [0, 1, 3, 5, 7, 8, 10],
    "lydian":         [0, 2, 4, 6, 7, 9, 11],
    "mixolydian":     [0, 2, 4, 5, 7, 9, 10],
    "locrian":        [0, 1, 3, 5, 6, 8, 10],
}

ROMAN_TO_DEGREE = {"I": 0, "II": 1, "III": 2, "IV": 3, "V": 4, "VI": 5, "VII": 6}

# Chord shapes as semitone offsets from the chord root.
TRIADS = {
    "major":      [0, 4, 7],
    "minor":      [0, 3, 7],
    "diminished": [0, 3, 6],
    "augmented":  [0, 4, 8],
}


def note_name_to_midi(name: str, octave: int = 4) -> int:
    """Turn a note name like 'C' or 'F#' into a MIDI number. C4 = 60."""
    key = name.strip().upper()
    if key not in NOTE_TO_SEMITONE:
        raise ValueError(f"Unknown note name {name!r}. Use C, C#, Db, D ... B.")
    return 12 * (octave + 1) + NOTE_TO_SEMITONE[key]


def parse_numeral(numeral: str) -> tuple[int, str, str | None]:
    """Split a Roman numeral chord symbol into (degree, quality, seventh).

    Examples:
        "I"     -> (0, "major", None)
        "vi"    -> (5, "minor", None)
        "vii°"  -> (6, "diminished", None)
        "V7"    -> (4, "major", "minor7")   # dominant 7th
        "IVmaj7"-> (3, "major", "major7")
    """
    s = numeral.strip()
    # Pull the leading Roman letters (I, V, i, v combinations).
    core = ""
    for ch in s:
        if ch in "IViv":
            core += ch
        else:
            break
    suffix = s[len(core):]
    if not core or core.upper() not in ROMAN_TO_DEGREE:
        raise ValueError(f"Cannot parse Roman numeral {numeral!r}")
    degree = ROMAN_TO_DEGREE[core.upper()]

    # Uppercase numeral = major chord, lowercase = minor chord.
    quality = "major" if core[0].isupper() else "minor"
    seventh: str | None = None

    rest = suffix
    if rest.startswith(("°", "o", "dim")):
        quality = "diminished"
        rest = rest.lstrip("°o").removeprefix("dim").removeprefix("im")
    elif rest.startswith(("+", "aug")):
        quality = "augmented"
        rest = rest.lstrip("+").removeprefix("aug")

    if rest.lower().startswith("maj7"):
        seventh = "major7"
    elif rest.startswith("7"):
        # Plain 7 = minor seventh interval (dominant on a major triad,
        # m7 on a minor triad, half-diminished feel on a dim triad).
        seventh = "minor7"
    elif rest:
        raise ValueError(f"Unknown chord suffix {rest!r} in {numeral!r}")
    return degree, quality, seventh


def chord_midi_notes(key: str, scale: str, numeral: str, octave: int = 4) -> list[int]:
    """Return the MIDI note numbers for one Roman-numeral chord in a key."""
    scale_key = scale.strip().lower().replace(" ", "_")
    if scale_key not in SCALES:
        raise ValueError(f"Unknown scale {scale!r}. Options: {sorted(SCALES)}")
    intervals = SCALES[scale_key]
    degree, quality, seventh = parse_numeral(numeral)

    root = note_name_to_midi(key, octave) + intervals[degree]
    notes = [root + off for off in TRIADS[quality]]
    if seventh == "minor7":
        notes.append(root + 10)
    elif seventh == "major7":
        notes.append(root + 11)
    return notes


def build_progression(
    key: str,
    scale: str,
    numerals: list[str],
    start_beat: float,
    length_per_chord: float,
    octave: int = 4,
    velocity: float = 0.8,
) -> list[dict]:
    """Expand a chord progression into individual note events.

    Returns note dicts of the shape the piano roll script consumes:
    {"pitch": 60, "start": 0.0, "length": 4.0, "velocity": 0.8}
    (start/length in beats).
    """
    events: list[dict] = []
    beat = float(start_beat)
    for numeral in numerals:
        for pitch in chord_midi_notes(key, scale, numeral, octave):
            events.append({
                "pitch": pitch,
                "start": beat,
                "length": float(length_per_chord),
                "velocity": float(velocity),
            })
        beat += float(length_per_chord)
    return events


def parse_grid(grid: str) -> float:
    """Turn a grid label like '1/16' into a step size in beats.

    In FL Studio one beat is a quarter note, so '1/4' -> 1.0 beat,
    '1/8' -> 0.5, '1/16' -> 0.25, '1/32' -> 0.125.
    """
    s = grid.strip()
    if not s.startswith("1/"):
        raise ValueError(f"grid must look like '1/16', got {grid!r}")
    try:
        denom = int(s[2:])
    except ValueError as exc:
        raise ValueError(f"grid must look like '1/16', got {grid!r}") from exc
    if denom not in (1, 2, 4, 8, 16, 32, 64):
        raise ValueError(f"grid denominator must be a power of 2 up to 64, got {denom}")
    return 4.0 / denom


# ---------------------------------------------------------------------------
# FastMCP tools
# ---------------------------------------------------------------------------

def register(mcp: FastMCP, bridge: FLBridge) -> None:
    """Attach all piano roll tools to the FastMCP server."""

    @mcp.tool()
    def fl_add_chord_progression(
        key: str,
        scale: str,
        numerals: list[str],
        start_beat: float = 0.0,
        length_per_chord: float = 4.0,
        octave: int = 4,
        velocity: float = 0.8,
    ) -> dict:
        """Build a chord progression from music theory and queue it for the
        piano roll. Example: key="C", scale="major",
        numerals=["I", "V", "vi", "IV"] produces C, G, Am, F.

        Args:
            key: Root note of the key, e.g. "C", "F#", "Bb".
            scale: One of major, minor, harmonic_minor, dorian, phrygian,
                lydian, mixolydian, locrian.
            numerals: Roman numerals — uppercase = major (I, IV, V),
                lowercase = minor (ii, vi), suffixes: ° or dim, +, 7, maj7.
            start_beat: Beat where the first chord starts (0 = pattern start).
            length_per_chord: Beats per chord (4.0 = one bar in 4/4).
            octave: Octave of the chord roots (4 puts C at middle C, MIDI 60).
            velocity: Note velocity 0.0-1.0.
        """
        try:
            notes = build_progression(
                key, scale, numerals, start_beat, length_per_chord, octave, velocity
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            bridge.queue_pianoroll("add_notes", {"notes": notes})
        except FLBridgeError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "queued_notes": len(notes),
            "chords": numerals,
            "next_step": RUN_SCRIPT_HINT,
        }

    @mcp.tool()
    def fl_add_notes(notes: list[dict]) -> dict:
        """Queue an arbitrary list of notes for the piano roll.

        Args:
            notes: List of note objects like
                {"pitch": 60, "start": 0.0, "length": 1.0, "velocity": 0.8}
                where pitch is MIDI 0-127, start/length are in beats, and
                velocity is 0.0-1.0 (defaults to 0.8 if omitted).
        """
        cleaned: list[dict] = []
        for i, n in enumerate(notes):
            try:
                pitch = int(n["pitch"])
                start = float(n["start"])
                length = float(n["length"])
                velocity = float(n.get("velocity", 0.8))
            except (KeyError, TypeError, ValueError) as exc:
                return {"ok": False, "error": f"note {i} is malformed ({exc!r}); "
                        "need pitch, start, length, optional velocity"}
            if not (0 <= pitch <= 127):
                return {"ok": False, "error": f"note {i}: pitch must be 0-127, got {pitch}"}
            if start < 0 or length <= 0:
                return {"ok": False, "error": f"note {i}: start must be >= 0 and length > 0"}
            if not (0.0 <= velocity <= 1.0):
                return {"ok": False, "error": f"note {i}: velocity must be 0.0-1.0"}
            cleaned.append({"pitch": pitch, "start": start,
                            "length": length, "velocity": velocity})
        if not cleaned:
            return {"ok": False, "error": "notes list is empty"}
        try:
            bridge.queue_pianoroll("add_notes", {"notes": cleaned})
        except FLBridgeError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "queued_notes": len(cleaned), "next_step": RUN_SCRIPT_HINT}

    @mcp.tool()
    def fl_quantize_piano_roll(grid: str = "1/16") -> dict:
        """Queue a quantize action: snap all note start times in the active
        piano roll to the given grid.

        Args:
            grid: Grid size — "1/4", "1/8", "1/16" (default) or "1/32".
        """
        try:
            step_beats = parse_grid(grid)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            bridge.queue_pianoroll("quantize", {"step_beats": step_beats})
        except FLBridgeError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "grid": grid, "next_step": RUN_SCRIPT_HINT}

    @mcp.tool()
    def fl_clear_piano_roll() -> dict:
        """Queue a clear action: remove ALL notes from the active piano roll.
        The user still has to run the MCP PianoRoll script to apply it, and
        Ctrl+Z inside FL Studio undoes it, so this is recoverable."""
        try:
            bridge.queue_pianoroll("clear", {})
        except FLBridgeError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "next_step": RUN_SCRIPT_HINT}
