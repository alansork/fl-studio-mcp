"""Checks on the two scripts that run INSIDE FL Studio.

Those scripts import FL-only modules (channels, mixer, flpianoroll, ...) so
we can never run them here — but we CAN parse them. These tests catch the
mistakes that would otherwise only show up inside FL Studio:

  * a syntax error in either script
  * a command the MCP server sends that the device script has no handler for
  * a piano roll action the server queues that the pyscript can't apply
  * the two sides drifting apart on the protocol constants (CC number,
    MIDI channel, mailbox file names)
"""

import ast
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "src" / "fl_scripts"
TOOLS = REPO / "src" / "tools"

DEVICE_SCRIPT = SCRIPTS / "device_FLStudioMCP.py"
PIANOROLL_SCRIPT = SCRIPTS / "MCP_PianoRoll.pyscript"


def _dict_string_keys(source: str, dict_name: str) -> set:
    """Parse a script and return the string keys of a top-level dict like
    HANDLERS = {"play": _h_play, ...}."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == dict_name
                    for t in node.targets)
            and isinstance(node.value, ast.Dict)
        ):
            return {k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    raise AssertionError(f"dict {dict_name} not found")


class TestScriptsCompile(unittest.TestCase):
    """A syntax error here would only ever surface inside FL Studio."""

    def test_device_script_compiles(self):
        compile(DEVICE_SCRIPT.read_text(), str(DEVICE_SCRIPT), "exec")

    def test_pianoroll_script_compiles(self):
        compile(PIANOROLL_SCRIPT.read_text(), str(PIANOROLL_SCRIPT), "exec")


class TestProtocolConsistency(unittest.TestCase):
    """The server side and the in-FL side must speak the same protocol."""

    def test_every_server_command_has_a_device_handler(self):
        sent = set()
        for tool_file in TOOLS.glob("*.py"):
            sent |= set(re.findall(r'safe_call\(bridge,\s*"([a-z_]+)"',
                                   tool_file.read_text()))
        self.assertTrue(sent, "no commands found in tool sources")
        handled = _dict_string_keys(DEVICE_SCRIPT.read_text(), "HANDLERS")
        missing = sent - handled
        self.assertFalse(
            missing,
            f"tools send commands the device script can't handle: {missing}")

    def test_every_queued_action_has_a_pyscript_handler(self):
        queued = set(re.findall(r'queue_pianoroll\("([a-z_]+)"',
                                (TOOLS / "pianoroll_tools.py").read_text()))
        self.assertTrue(queued, "no queued actions found in pianoroll tools")
        handled = _dict_string_keys(PIANOROLL_SCRIPT.read_text(), "ACTIONS")
        missing = queued - handled
        self.assertFalse(
            missing,
            f"tools queue actions the pyscript can't apply: {missing}")

    def test_midi_trigger_constants_match(self):
        from src.bridge import midi_bridge

        device_src = DEVICE_SCRIPT.read_text()
        cc = int(re.search(r"^TRIGGER_CC = (\d+)", device_src, re.M).group(1))
        chan = int(re.search(r"^TRIGGER_CHANNEL = (\d+)", device_src, re.M).group(1))
        self.assertEqual(cc, midi_bridge.TRIGGER_CC)
        self.assertEqual(chan, midi_bridge.TRIGGER_CHANNEL)

    def test_mailbox_file_names_match(self):
        from src.bridge import ipc_handler

        device_src = DEVICE_SCRIPT.read_text()
        self.assertIn('"request.json"', device_src)
        self.assertIn('"response.json"', device_src)
        self.assertEqual(ipc_handler.REQUEST_FILE.name, "request.json")
        self.assertEqual(ipc_handler.RESPONSE_FILE.name, "response.json")
        self.assertIn('"pianoroll_queue.json"',
                      PIANOROLL_SCRIPT.read_text())
        self.assertEqual(ipc_handler.PIANOROLL_QUEUE_FILE.name,
                         "pianoroll_queue.json")


class TestResearchedApiConventions(unittest.TestCase):
    """Pin the FL Studio API facts we verified against the official docs
    (il-group.github.io/FL-Studio-API-Stubs), so a future edit can't quietly
    reintroduce the old mistakes."""

    def test_channel_pitch_uses_cents_mode(self):
        # setChannelPitch mode 1 = cents; mode 0 = pitch-bend-range fraction.
        # The fl_set_channel_pitch_pan_vol tool promises cents.
        self.assertIn('setChannelPitch(ch, int(params["pitch"]), 1)',
                      DEVICE_SCRIPT.read_text())

    def test_tempo_reads_are_normalized(self):
        # getCurrentTempo returns raw BPM*1000 in real FL builds; every bpm
        # value we report must go through the _current_bpm() normalizer.
        device_src = DEVICE_SCRIPT.read_text()
        self.assertNotIn("getCurrentTempo(True)", device_src)
        self.assertEqual(device_src.count("_current_bpm()"), 3,
                         "expected the definition + two call sites")


if __name__ == "__main__":
    unittest.main()
