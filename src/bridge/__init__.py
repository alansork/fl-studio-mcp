"""The bridge package: everything needed to talk to FL Studio.

``FLBridge`` is the one object the MCP tools use. It combines:
  * the file mailbox (ipc_handler)  — carries the actual request/response data
  * the MIDI doorbell (midi_bridge) — tells FL Studio "you have mail"
"""

from __future__ import annotations

from typing import Any

from . import ipc_handler, midi_bridge
from .ipc_handler import IPCError
from .midi_bridge import MidiBridgeError


class FLBridgeError(Exception):
    """A single friendly error type the MCP tools can catch and report."""


class FLBridge:
    """Sends one command to FL Studio and returns its result.

    Every FastMCP tool ends up calling ``bridge.call("some_command", {...})``.
    If anything in the chain is broken (no MIDI cable, FL closed, script not
    loaded) this raises FLBridgeError with a message that tells the user how
    to fix it.
    """

    def call(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        timeout: float = ipc_handler.DEFAULT_TIMEOUT_S,
    ) -> Any:
        # 1. Drop the request into the mailbox.
        try:
            request_id = ipc_handler.write_request(command, params)
        except IPCError as exc:
            raise FLBridgeError(str(exc)) from exc

        # 2. Ring the doorbell.
        try:
            midi_bridge.send_trigger()
        except MidiBridgeError as exc:
            raise FLBridgeError(
                f"MIDI bridge problem: {exc}\n"
                "Checklist: (1) virtual MIDI port exists (loopMIDI / IAC), "
                "(2) FL Studio is running, (3) the 'FL Studio MCP Bridge' "
                "script is assigned to that port in FL Studio's MIDI settings."
            ) from exc

        # 3. Wait for the answer.
        try:
            response = ipc_handler.wait_for_response(request_id, timeout=timeout)
        except TimeoutError as exc:
            raise FLBridgeError(
                f"{exc}\n"
                "FL Studio received no request or could not answer. Checklist: "
                "(1) FL Studio is open, (2) Options > MIDI Settings shows the "
                "virtual port with controller type 'FL Studio MCP Bridge' and "
                "the input enabled, (3) the port numbers match."
            ) from exc

        if not response.get("ok", False):
            raise FLBridgeError(
                f"FL Studio reported an error for '{command}': "
                f"{response.get('error', 'unknown error')}"
            )
        return response.get("result")

    def queue_pianoroll(self, action: str, payload: dict[str, Any]) -> None:
        """Queue a note-editing action for the Piano Roll script."""
        try:
            ipc_handler.queue_pianoroll_action(action, payload)
        except IPCError as exc:
            raise FLBridgeError(str(exc)) from exc


__all__ = ["FLBridge", "FLBridgeError", "ipc_handler", "midi_bridge"]
