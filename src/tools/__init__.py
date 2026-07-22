"""FastMCP tool modules. Each module exposes register(mcp, bridge)."""

from __future__ import annotations

from typing import Any, Callable

from ..bridge import FLBridge, FLBridgeError


def safe_call(bridge: FLBridge, command: str, params: dict | None = None,
              timeout: float | None = None) -> dict[str, Any]:
    """Run one bridge command and always return a JSON-friendly dict.

    Tools never raise: if the bridge is broken we return
    ``{"ok": False, "error": "...how to fix it..."}`` so Claude can explain
    the problem to the user instead of the whole server crashing.
    """
    try:
        kwargs = {} if timeout is None else {"timeout": timeout}
        result = bridge.call(command, params or {}, **kwargs)
        return {"ok": True, "result": result}
    except FLBridgeError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # last-resort guard — never crash the server
        return {"ok": False, "error": f"Unexpected error: {exc!r}"}
