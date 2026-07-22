#!/usr/bin/env python3
"""fl-studio-mcp installer.

What it does (asking before each write):
  1. Detects your OS (Windows or macOS).
  2. Finds the FL Studio user settings folder.
  3. Copies device_FLStudioMCP.py  -> Settings/Hardware/FLStudioMCP/
  4. Copies MCP_PianoRoll.pyscript -> Settings/Piano roll scripts/
  5. Offers to register the server in Claude Desktop's config
     (claude_desktop_config.json) and prints the Claude Code command.

Run:  python3 install.py           (interactive)
      python3 install.py --yes     (accept all prompts)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DEVICE_SCRIPT = REPO_ROOT / "src" / "fl_scripts" / "device_FLStudioMCP.py"
PIANOROLL_SCRIPT = REPO_ROOT / "src" / "fl_scripts" / "MCP_PianoRoll.pyscript"

SERVER_NAME = "fl-studio-mcp"


def ask(question: str, assume_yes: bool) -> bool:
    """Yes/no prompt. --yes answers everything with yes."""
    if assume_yes:
        print(f"{question} [auto-yes]")
        return True
    reply = input(f"{question} [y/N] ").strip().lower()
    return reply in ("y", "yes")


def documents_dirs(system: str) -> list[Path]:
    """Candidate Documents folders for this OS, most reliable first.

    On Windows the real Documents folder is whatever the registry says —
    OneDrive often redirects it to ~/OneDrive/Documents, so a plain
    ~/Documents guess misses it on many machines.
    """
    candidates: list[Path] = []
    if system == "Windows":
        try:
            import winreg
            key_path = (r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                personal, _ = winreg.QueryValueEx(key, "Personal")
            candidates.append(Path(os.path.expandvars(personal)))
        except (ImportError, OSError):
            pass  # not on Windows, or the registry key is missing
        candidates.append(Path.home() / "Documents")
        candidates.append(Path.home() / "OneDrive" / "Documents")
    else:
        candidates.append(Path.home() / "Documents")
    # Drop duplicates while keeping the order.
    seen: set[Path] = set()
    return [p for p in candidates if not (p in seen or seen.add(p))]


def fl_settings_dir() -> Path:
    """Locate FL Studio's user Settings folder for this OS."""
    system = platform.system()
    if system not in ("Windows", "Darwin"):
        sys.exit(f"Unsupported OS: {system}. FL Studio runs on Windows and macOS only.")
    candidates = [d / "Image-Line" / "FL Studio" / "Settings"
                  for d in documents_dirs(system)]
    for base in candidates:
        if base.is_dir():
            return base
    tried = "\n  ".join(str(c) for c in candidates)
    sys.exit(
        f"FL Studio settings folder not found. Looked in:\n  {tried}\n"
        "Is FL Studio installed? Open it once so it creates its folders, "
        "then re-run this installer."
    )


def claude_desktop_config_path() -> Path:
    """Location of Claude Desktop's MCP config file per OS."""
    system = platform.system()
    if system == "Darwin":
        return (Path.home() / "Library" / "Application Support" / "Claude"
                / "claude_desktop_config.json")
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) \
            / "Claude" / "claude_desktop_config.json"
    sys.exit(f"Unsupported OS: {system}")


def install_fl_scripts(assume_yes: bool) -> None:
    settings = fl_settings_dir()

    hardware_dir = settings / "Hardware" / "FLStudioMCP"
    device_target = hardware_dir / "device_FLStudioMCP.py"
    if ask(f"Copy device script to {device_target}?", assume_yes):
        hardware_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DEVICE_SCRIPT, device_target)
        print(f"  ✓ installed {device_target}")

    pr_dir = settings / "Piano roll scripts"
    pr_target = pr_dir / "MCP_PianoRoll.pyscript"
    if ask(f"Copy piano roll script to {pr_target}?", assume_yes):
        pr_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PIANOROLL_SCRIPT, pr_target)
        print(f"  ✓ installed {pr_target}")


def server_entry() -> dict:
    """The MCP server entry Claude launches. Prefers uv, falls back to python."""
    if shutil.which("uv"):
        return {
            "command": "uv",
            "args": ["run", "--directory", str(REPO_ROOT), "fl-studio-mcp"],
        }
    # Claude Desktop's config only understands command/args/env (no "cwd"),
    # so point Python at the repo through PYTHONPATH instead.
    return {
        "command": sys.executable,
        "args": ["-m", "src.main"],
        "env": {"PYTHONPATH": str(REPO_ROOT)},
    }


def register_claude_desktop(assume_yes: bool) -> None:
    config_path = claude_desktop_config_path()
    if not config_path.parent.is_dir():
        print(f"Claude Desktop not found (no {config_path.parent}). Skipping.")
        return
    if not ask(f"Add '{SERVER_NAME}' to {config_path}?", assume_yes):
        return

    config: dict = {}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sys.exit(
                f"{config_path} exists but is not valid JSON — fix it by hand "
                "first so we don't clobber your other servers."
            )
    config.setdefault("mcpServers", {})[SERVER_NAME] = server_entry()
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"  ✓ registered '{SERVER_NAME}' in Claude Desktop config")
    print("  Restart Claude Desktop to pick it up.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install fl-studio-mcp")
    parser.add_argument("--yes", action="store_true",
                        help="accept every prompt automatically")
    args = parser.parse_args()

    for path in (DEVICE_SCRIPT, PIANOROLL_SCRIPT):
        if not path.is_file():
            sys.exit(f"Missing bundled script: {path} — run from the repo root.")

    print(f"fl-studio-mcp installer ({platform.system()})\n")
    install_fl_scripts(args.yes)
    register_claude_desktop(args.yes)

    entry = server_entry()
    env_flags = "".join(f"-e {k}={v} " for k, v in entry.get("env", {}).items())
    print(
        "\nFor Claude Code, register the server with:\n"
        f"  claude mcp add {SERVER_NAME} {env_flags}-- "
        f"{entry['command']} {' '.join(entry['args'])}\n"
        "\nRemaining manual steps (see README):\n"
        "  1. Create the virtual MIDI port (loopMIDI on Windows / IAC on macOS).\n"
        "  2. In FL Studio: Options > MIDI Settings > select that port under\n"
        "     Input, set controller type to 'FL Studio MCP Bridge', enable it.\n"
    )


if __name__ == "__main__":
    main()
