"""File-based IPC between the MCP server and the script running inside FL Studio.

How it works (the "mailbox" model):

1. The MCP server writes a request as JSON to ``~/.fl-studio-mcp/request.json``.
2. The MCP server "rings the doorbell" by sending a MIDI message (see
   ``midi_bridge.py``). FL Studio's device script wakes up, reads the request,
   executes it with FL Studio's Python API, and writes ``response.json``.
3. The MCP server polls ``response.json`` until a response with the matching
   request id appears (or it times out).

Why a home-directory folder instead of ``/tmp``?
``/tmp`` does not exist on Windows, and on macOS each process can get a
different per-user temp dir. The user's home directory is the one path both
the MCP server and FL Studio's embedded Python resolve identically.

All writes are *atomic*: we write to a temp file and then ``os.replace`` it
into place, so a reader can never see a half-written JSON file. A `filelock`
guards against two server-side writers racing each other.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout as LockTimeout

# Single shared folder for all bridge files. The FL Studio device script
# computes this exact same path (see src/fl_scripts/device_FLStudioMCP.py).
BRIDGE_DIR = Path(os.path.expanduser("~")) / ".fl-studio-mcp"

REQUEST_FILE = BRIDGE_DIR / "request.json"
RESPONSE_FILE = BRIDGE_DIR / "response.json"
PIANOROLL_QUEUE_FILE = BRIDGE_DIR / "pianoroll_queue.json"
LOCK_FILE = BRIDGE_DIR / "bridge.lock"

# How long we wait for FL Studio to answer before giving up.
DEFAULT_TIMEOUT_S = 5.0
POLL_INTERVAL_S = 0.05


class IPCError(Exception):
    """Raised when the file queue cannot be written or read."""


def ensure_bridge_dir() -> None:
    """Create the bridge folder if it does not exist yet."""
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON so that readers can never observe a partial file.

    We write to ``<name>.tmp`` first and then rename it over the target.
    ``os.replace`` is atomic on both Windows (NTFS) and macOS/Linux.
    """
    ensure_bridge_dir()
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON file, returning None if it is missing or mid-replace."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def write_request(command: str, params: dict[str, Any] | None = None) -> str:
    """Serialize a command for FL Studio and return its unique request id."""
    request_id = uuid.uuid4().hex
    payload = {
        "id": request_id,
        "command": command,
        "params": params or {},
        "timestamp": time.time(),
    }
    try:
        # The lock only guards server-side writers (e.g. two tool calls at
        # once). FL Studio itself never holds this lock — it just reads the
        # atomically-replaced file.
        with FileLock(str(LOCK_FILE), timeout=2.0):
            _atomic_write_json(REQUEST_FILE, payload)
    except LockTimeout as exc:
        raise IPCError(
            "Could not acquire the bridge lock — another request may be stuck. "
            f"If this persists, delete {LOCK_FILE} and try again."
        ) from exc
    except OSError as exc:
        raise IPCError(f"Could not write request file {REQUEST_FILE}: {exc}") from exc
    return request_id


def wait_for_response(
    request_id: str, timeout: float = DEFAULT_TIMEOUT_S
) -> dict[str, Any]:
    """Poll response.json until FL Studio answers our specific request.

    Raises TimeoutError if FL Studio never responds — the most common causes
    are: FL Studio is closed, the device script is not loaded, or the virtual
    MIDI cable is not connected.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = read_json(RESPONSE_FILE)
        if data is not None and data.get("id") == request_id:
            return data
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(
        f"FL Studio did not respond within {timeout:.1f}s (request id {request_id})."
    )


def queue_pianoroll_action(action: str, payload: dict[str, Any]) -> None:
    """Queue an action for the Piano Roll script (MCP PianoRoll.pyscript).

    Piano roll edits cannot be done from the always-running device script —
    FL Studio only exposes note editing to on-demand Piano Roll scripts. So we
    write the action to a queue file, and the user runs the "MCP PianoRoll"
    script from the piano roll menu, which applies whatever is queued.
    """
    payload = {
        "id": uuid.uuid4().hex,
        "action": action,
        "payload": payload,
        "timestamp": time.time(),
    }
    try:
        with FileLock(str(LOCK_FILE), timeout=2.0):
            _atomic_write_json(PIANOROLL_QUEUE_FILE, payload)
    except (LockTimeout, OSError) as exc:
        raise IPCError(f"Could not write piano roll queue: {exc}") from exc
