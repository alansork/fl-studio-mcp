"""Tests for fl-studio-mcp that run WITHOUT FL Studio or a MIDI port.

They cover the parts that must be correct for the bridge to work at all:
  * atomic JSON mailbox read/write and request/response matching
  * MIDI port selection logic
  * music theory (numerals -> MIDI notes), step patterns, grid parsing
  * mix-analysis recommendations

Run with:  python3 -m unittest discover -s tests
"""

from __future__ import annotations

import math
import unittest
from unittest import mock

from src.bridge import ipc_handler, midi_bridge
from src.tools import channel_tools, diagnostic_tools, pianoroll_tools


class TestIPCHandler(unittest.TestCase):
    """The file mailbox: writes must be atomic, ids must match."""

    def setUp(self):
        # Point every bridge file at a throwaway temp directory.
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        patches = {
            "BRIDGE_DIR": tmp,
            "REQUEST_FILE": tmp / "request.json",
            "RESPONSE_FILE": tmp / "response.json",
            "PIANOROLL_QUEUE_FILE": tmp / "pianoroll_queue.json",
            "LOCK_FILE": tmp / "bridge.lock",
        }
        self._patchers = [
            mock.patch.object(ipc_handler, name, value)
            for name, value in patches.items()
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self._tmp.cleanup()

    def test_write_request_roundtrip(self):
        request_id = ipc_handler.write_request("play", {"x": 1})
        data = ipc_handler.read_json(ipc_handler.REQUEST_FILE)
        self.assertEqual(data["id"], request_id)
        self.assertEqual(data["command"], "play")
        self.assertEqual(data["params"], {"x": 1})

    def test_request_ids_are_unique(self):
        ids = {ipc_handler.write_request("ping") for _ in range(20)}
        self.assertEqual(len(ids), 20)

    def test_wait_for_response_matches_id(self):
        request_id = ipc_handler.write_request("ping")
        # Simulate FL Studio answering.
        ipc_handler._atomic_write_json(
            ipc_handler.RESPONSE_FILE,
            {"id": request_id, "ok": True, "result": {"pong": True}},
        )
        response = ipc_handler.wait_for_response(request_id, timeout=1.0)
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"], {"pong": True})

    def test_wait_for_response_ignores_stale_id(self):
        # A response for some OTHER request must not satisfy ours.
        ipc_handler._atomic_write_json(
            ipc_handler.RESPONSE_FILE, {"id": "stale", "ok": True}
        )
        with self.assertRaises(TimeoutError):
            ipc_handler.wait_for_response("fresh", timeout=0.3)

    def test_read_json_survives_garbage(self):
        ipc_handler.ensure_bridge_dir()
        ipc_handler.REQUEST_FILE.write_text("{not valid json", encoding="utf-8")
        self.assertIsNone(ipc_handler.read_json(ipc_handler.REQUEST_FILE))

    def test_pianoroll_queue_write(self):
        ipc_handler.queue_pianoroll_action("add_notes", {"notes": [
            {"pitch": 60, "start": 0.0, "length": 1.0, "velocity": 0.8},
        ]})
        data = ipc_handler.read_json(ipc_handler.PIANOROLL_QUEUE_FILE)
        self.assertEqual(data["action"], "add_notes")
        self.assertEqual(data["payload"]["notes"][0]["pitch"], 60)


class TestMidiPortSelection(unittest.TestCase):
    """Port matching logic, with mido mocked out."""

    def test_prefers_named_fl_mcp_port(self):
        ports = ["IAC Driver Bus 1", "FL MCP Bridge", "Some Synth"]
        with mock.patch.object(midi_bridge, "list_output_ports", return_value=ports), \
             mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("FL_MCP_MIDI_PORT", None)
            self.assertEqual(midi_bridge.find_port_name(), "FL MCP Bridge")

    def test_env_override_wins(self):
        ports = ["loopMIDI Port", "My Custom Cable"]
        with mock.patch.object(midi_bridge, "list_output_ports", return_value=ports), \
             mock.patch.dict("os.environ", {"FL_MCP_MIDI_PORT": "custom"}):
            self.assertEqual(midi_bridge.find_port_name(), "My Custom Cable")

    def test_no_ports_gives_helpful_error(self):
        with mock.patch.object(midi_bridge, "list_output_ports", return_value=[]):
            with self.assertRaises(midi_bridge.MidiBridgeError) as ctx:
                midi_bridge.find_port_name()
            self.assertIn("loopMIDI", str(ctx.exception))

    def test_no_matching_port_lists_candidates(self):
        with mock.patch.object(midi_bridge, "list_output_ports",
                               return_value=["Some Synth"]), \
             mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("FL_MCP_MIDI_PORT", None)
            with self.assertRaises(midi_bridge.MidiBridgeError) as ctx:
                midi_bridge.find_port_name()
            self.assertIn("Some Synth", str(ctx.exception))


class TestMusicTheory(unittest.TestCase):
    """Roman numerals must expand to the correct MIDI notes."""

    def test_c_major_I_is_c_e_g(self):
        self.assertEqual(
            pianoroll_tools.chord_midi_notes("C", "major", "I"), [60, 64, 67]
        )

    def test_c_major_vi_is_a_minor(self):
        self.assertEqual(
            pianoroll_tools.chord_midi_notes("C", "major", "vi"), [69, 72, 76]
        )

    def test_a_minor_i(self):
        self.assertEqual(
            pianoroll_tools.chord_midi_notes("A", "minor", "i"), [69, 72, 76]
        )

    def test_dominant_seventh(self):
        # V7 in C major = G B D F
        self.assertEqual(
            pianoroll_tools.chord_midi_notes("C", "major", "V7"), [67, 71, 74, 77]
        )

    def test_major_seventh(self):
        # IVmaj7 in C major = F A C E
        self.assertEqual(
            pianoroll_tools.chord_midi_notes("C", "major", "IVmaj7"), [65, 69, 72, 76]
        )

    def test_diminished(self):
        # vii° in C major = B D F
        self.assertEqual(
            pianoroll_tools.chord_midi_notes("C", "major", "vii°"), [71, 74, 77]
        )

    def test_flat_key_names(self):
        self.assertEqual(pianoroll_tools.note_name_to_midi("Bb", 4), 70)
        self.assertEqual(pianoroll_tools.note_name_to_midi("F#", 4), 66)

    def test_bad_numeral_raises(self):
        with self.assertRaises(ValueError):
            pianoroll_tools.parse_numeral("VIII")
        with self.assertRaises(ValueError):
            pianoroll_tools.parse_numeral("Ixyz")

    def test_bad_scale_raises(self):
        with self.assertRaises(ValueError):
            pianoroll_tools.chord_midi_notes("C", "klingon", "I")

    def test_progression_timing(self):
        events = pianoroll_tools.build_progression(
            "C", "major", ["I", "V"], start_beat=0.0, length_per_chord=4.0
        )
        # Two triads = 6 notes; second chord starts at beat 4.
        self.assertEqual(len(events), 6)
        self.assertEqual({e["start"] for e in events[:3]}, {0.0})
        self.assertEqual({e["start"] for e in events[3:]}, {4.0})
        self.assertTrue(all(e["length"] == 4.0 for e in events))


class TestGridAndSteps(unittest.TestCase):
    def test_grid_parsing(self):
        self.assertEqual(pianoroll_tools.parse_grid("1/4"), 1.0)
        self.assertEqual(pianoroll_tools.parse_grid("1/8"), 0.5)
        self.assertEqual(pianoroll_tools.parse_grid("1/16"), 0.25)
        self.assertEqual(pianoroll_tools.parse_grid("1/32"), 0.125)

    def test_grid_rejects_nonsense(self):
        for bad in ("16", "1/3", "1/x", ""):
            with self.assertRaises(ValueError):
                pianoroll_tools.parse_grid(bad)

    def test_step_pattern_validation(self):
        self.assertIsNone(channel_tools.validate_step_pattern("1000100010001000"))
        self.assertIsNotNone(channel_tools.validate_step_pattern(""))
        self.assertIsNotNone(channel_tools.validate_step_pattern("10x0"))
        self.assertIsNotNone(channel_tools.validate_step_pattern("1" * 65))


class TestMixAnalysis(unittest.TestCase):
    def test_clipping_detected(self):
        result = diagnostic_tools.analyze_peaks([
            {"index": 1, "name": "Kick", "peak_left": 1.2, "peak_right": 1.1},
        ])
        self.assertEqual(len(result["clipping"]), 1)
        self.assertIn("clipping", result["recommendations"][0])

    def test_low_headroom_detected(self):
        result = diagnostic_tools.analyze_peaks([
            {"index": 2, "name": "Bass", "peak_left": 0.8, "peak_right": 0.8},
        ])
        self.assertEqual(result["clipping"], [])
        self.assertEqual(len(result["low_headroom"]), 1)

    def test_imbalance_detected(self):
        # Left is twice the right = ~6 dB apart.
        result = diagnostic_tools.analyze_peaks([
            {"index": 3, "name": "Lead", "peak_left": 0.5, "peak_right": 0.25},
        ])
        self.assertEqual(len(result["imbalanced"]), 1)
        self.assertAlmostEqual(
            result["imbalanced"][0]["lr_difference_db"],
            20 * math.log10(2), places=1,
        )

    def test_clean_mix(self):
        result = diagnostic_tools.analyze_peaks([
            {"index": 1, "name": "Kick", "peak_left": 0.5, "peak_right": 0.5},
        ])
        self.assertEqual(result["recommendations"],
                         ["No clipping, headroom, or balance issues detected."])


class TestServerBuilds(unittest.TestCase):
    """The FastMCP server must construct with all 25+ tools registered."""

    def test_all_tools_registered(self):
        from src.main import build_server
        import anyio

        server = build_server()
        tools = anyio.run(server.list_tools)
        names = {t.name for t in tools}
        expected = {
            # transport
            "fl_play", "fl_stop", "fl_pause", "fl_set_tempo", "fl_set_loop_mode",
            "fl_toggle_record", "fl_get_transport_state",
            # mixer
            "fl_get_mixer_summary", "fl_set_track_volume", "fl_set_track_pan",
            "fl_mute_track", "fl_solo_track", "fl_set_track_name_color",
            "fl_route_track",
            # channels
            "fl_list_channels", "fl_set_channel_pitch_pan_vol",
            "fl_set_step_sequencer", "fl_assign_channel_to_mixer", "fl_mute_channel",
            # piano roll
            "fl_add_chord_progression", "fl_add_notes",
            "fl_quantize_piano_roll", "fl_clear_piano_roll",
            # diagnostics
            "fl_diagnose_mix", "fl_get_project_info", "fl_check_bridge_status",
        }
        missing = expected - names
        self.assertFalse(missing, f"tools missing from server: {missing}")
        self.assertGreaterEqual(len(names), 25)


if __name__ == "__main__":
    unittest.main()
