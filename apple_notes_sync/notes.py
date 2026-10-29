"""Read Apple Notes through osascript without ever launching Notes.

Only bulk getters are fast: any single-note Apple event costs 0.25-1 s, and
`whose` filters on notes time out and leave Notes busy for minutes. So the
listing is one JXA call made of bulk getters, and bodies come from AppleScript
ranges (`notes a thru b of folder id ...`, about 75 ms per note), which JXA
can't express.
"""

from __future__ import annotations

import html
import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

log = logging.getLogger("apple_notes_sync")

SCRIPTS = Path(__file__).parent / "scripts"
RECORD_SEP = "\x1e"
UNIT_SEP = "\x1f"
RANGE_CHUNK = 100
# A gap of up to this many unchanged notes is cheaper to fetch through than to
# split into another range call.
RANGE_GAP = 10
OSASCRIPT_TIMEOUT = 600
# Notes lists its trash as an ordinary top-level folder (only while it holds
# notes) with nothing but its localized name to tell it apart.
TRASH_FOLDERS = {"Recently Deleted"}


class NotesError(RuntimeError):
    pass


@dataclass
class Note:
    id: str
    title: str
    folder: str
    created: str
    modified: str
    locked: bool = False


@dataclass
class Listing:
    notes: Dict[str, Note] = field(default_factory=dict)
    # Folder id -> its note ids, in the folder's index order.
    folder_notes: Dict[str, List[str]] = field(default_factory=dict)
    note_folder: Dict[str, str] = field(default_factory=dict)


class NotesApp:
    def running(self) -> bool:
        return subprocess.run(["pgrep", "-xq", "Notes"]).returncode == 0

    def listing(self) -> Listing:
        raw = json.loads(_osascript(["-l", "JavaScript", str(SCRIPTS / "listing.js")]))
        return parse_listing(raw)

    def bodies(self, listing: Listing, ids: Iterable[str]) -> Iterator[Tuple[str, str]]:
        """Yield (note id, HTML body) for the wanted notes, range by range.

        Notes rejects a range whose reply is too big (-1741), so a failing range
        is split in half until single notes; a single note that still fails
        falls back to its plain text. Notes that can't be read at all are left
        out, and the caller retries them later.
        """
        wanted = set(ids)
        for folder_id, start, end in plan_ranges(listing, wanted):
            yield from self._range(listing.folder_notes[folder_id], folder_id, start, end, wanted)

    def _range(self, folder: List[str], folder_id: str, start: int, end: int, wanted: set):
        if not wanted.intersection(folder[start - 1:end]):
            return
        try:
            out = _osascript([str(SCRIPTS / "bodies.applescript"), folder_id, str(start), str(end)])
        except NotesError as exc:
            if start < end:
                mid = (start + end) // 2
                yield from self._range(folder, folder_id, start, mid, wanted)
                yield from self._range(folder, folder_id, mid + 1, end, wanted)
                return
            log.warning("body of note %d in %s failed (%s); using plain text", start, folder_id, exc)
            try:
                out = _osascript([str(SCRIPTS / "bodies.applescript"), folder_id, str(start), str(end), "plaintext"])
            except NotesError as exc2:
                log.warning("plain text of note %d in %s failed too: %s", start, folder_id, exc2)
                return
            for note_id, text in _records(out):
                if note_id in wanted:
                    yield note_id, "".join("<div>%s</div>" % (html.escape(line) or "<br>") for line in text.split("\n"))
            return
        for note_id, body in _records(out):
            if note_id in wanted:
                yield note_id, body


def parse_listing(raw: dict) -> Listing:
    listing = Listing()
    multi = len(raw["accounts"]) > 1
    for account in raw["accounts"]:
        folders = account["folders"]
        names = dict(zip(folders["ids"], folders["names"]))
        parents = dict(zip(folders["ids"], folders["parents"]))
        paths: Dict[str, str] = {}

        def path_of(folder_id: str, seen: frozenset = frozenset()) -> str:
            if folder_id in paths:
                return paths[folder_id]
            parent = parents.get(folder_id)
            parts = [names[folder_id]]
            if parent in names and parent not in seen:
                parts.insert(0, path_of(parent, seen | {folder_id}))
            elif multi:
                parts.insert(0, account["name"])
            paths[folder_id] = "/".join(parts)
            return paths[folder_id]

        trashed = set()
        for folder_id, note_ids in zip(folders["ids"], folders["notes"]):
            if names[folder_id] in TRASH_FOLDERS and parents.get(folder_id) not in names:
                trashed.update(note_ids)
                continue
            listing.folder_notes[folder_id] = list(note_ids)
            for note_id in note_ids:
                listing.note_folder[note_id] = folder_id

        notes = account["notes"]
        for note_id, title, created, modified, locked in zip(
            notes["ids"], notes["names"], notes["created"], notes["modified"], notes["locked"]
        ):
            if note_id in trashed:
                continue
            folder_id = listing.note_folder.get(note_id)
            folder = path_of(folder_id) if folder_id else (account["name"] if multi else "Unfiled")
            listing.notes[note_id] = Note(note_id, title or "", folder, created or "", modified or "", bool(locked))
    return listing


def plan_ranges(listing: Listing, wanted: Iterable[str]) -> List[tuple]:
    """Group wanted notes into (folder id, start, end) ranges, 1-based inclusive."""
    wanted = set(wanted)
    ranges = []
    for folder_id, note_ids in listing.folder_notes.items():
        indexes = [i + 1 for i, note_id in enumerate(note_ids) if note_id in wanted]
        if not indexes:
            continue
        start = prev = indexes[0]
        for index in indexes[1:] + [None]:
            if index is not None and index - prev <= RANGE_GAP + 1 and index - start < RANGE_CHUNK:
                prev = index
                continue
            ranges.append((folder_id, start, prev))
            if index is not None:
                start = prev = index
    return ranges


def _records(out: str):
    if not out:
        return
    for record in out.split(RECORD_SEP):
        note_id, sep, body = record.partition(UNIT_SEP)
        if sep:
            yield note_id, body


def _osascript(args: List[str], timeout: Optional[int] = OSASCRIPT_TIMEOUT) -> str:
    try:
        proc = subprocess.run(
            ["osascript", *args], capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise NotesError("osascript timed out after %ss: %s" % (timeout, args[-1])) from exc
    if proc.returncode != 0:
        raise NotesError("osascript failed (%d): %s" % (proc.returncode, proc.stderr.strip()))
    out = proc.stdout
    return out[:-1] if out.endswith("\n") else out
