from __future__ import annotations

import argparse
import logging
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .notes import NotesApp
from .sync import State, sync

LABEL = "com.anuvgupta.apple-notes-sync"
DEFAULT_OUT = Path.home() / "apple-notes"
DEFAULT_STATE = Path.home() / "Library/Application Support/apple-notes-sync/state.json"
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
LOG = Path.home() / "Library/Logs/apple-notes-sync.log"
REPO = Path(__file__).resolve().parent.parent
PYTHON = "/usr/bin/python3"
INTERVAL = 15


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="apple-notes-sync", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="sync once")
    p_sync.add_argument("--full", action="store_true", help="rewrite every note")
    p_install = sub.add_parser("install", help="install and start the launchd agent")
    sub.add_parser("uninstall", help="stop and remove the launchd agent")
    sub.add_parser("status", help="show the last sync state")
    for p in (p_sync, p_install):
        p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder (default: %(default)s)")
    for p in (p_sync, p_install, sub.choices["status"]):
        p.add_argument("--state", type=Path, default=DEFAULT_STATE, help="state file (default: %(default)s)")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)

    if args.command == "sync":
        result = sync(NotesApp(), args.out.expanduser(), args.state.expanduser(), full=args.full)
        if sys.stderr.isatty():
            print(result, file=sys.stderr)
        return 1 if result == "error" else 0
    if args.command == "install":
        return install(args.out.expanduser(), args.state.expanduser())
    if args.command == "uninstall":
        return uninstall()
    return status(args.state.expanduser())


def install(out: Path, state: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            PYTHON, "-m", "apple_notes_sync", "sync", "--out", str(out), "--state", str(state),
        ],
        "WorkingDirectory": str(REPO),
        "StartInterval": INTERVAL,
        "ThrottleInterval": 5,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }
    _bootout()
    with open(PLIST, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "bootstrap", _domain(), str(PLIST)], check=True)
    print("installed %s\n  out: %s\n  log: %s" % (PLIST, out, LOG))
    return 0


def uninstall() -> int:
    _bootout()
    if PLIST.exists():
        PLIST.unlink()
    print("uninstalled %s" % LABEL)
    return 0


def status(state_path: Path) -> int:
    state = State.load(state_path)
    print("last sync:     %s" % (state.last_sync or "never"))
    print("ok:            %s" % state.ok)
    print("notes running: %s" % state.notes_running)
    print("notes synced:  %d" % len(state.notes))
    print("last error:    %s" % (state.last_error or "none"))
    loaded = subprocess.run(["launchctl", "print", "%s/%s" % (_domain(), LABEL)], capture_output=True).returncode == 0
    print("agent loaded:  %s" % loaded)
    return 0


def _domain() -> str:
    return "gui/%d" % os.getuid()


def _bootout() -> None:
    subprocess.run(["launchctl", "bootout", "%s/%s" % (_domain(), LABEL)], capture_output=True)
