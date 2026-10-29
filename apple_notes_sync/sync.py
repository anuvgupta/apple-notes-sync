"""One-way sync of Apple Notes into a folder of Markdown files."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .convert import html_to_markdown
from .notes import Listing, Note, NotesError

log = logging.getLogger("apple_notes_sync")

NOTESTORE_DIR = Path.home() / "Library/Group Containers/group.com.apple.notes"
DB_FILES = ("NoteStore.sqlite", "NoteStore.sqlite-wal")
STATUS_FILE = "_Sync status.md"
MAX_PASSES = 3
ERROR_RETRY = dt.timedelta(minutes=5)
# Notes can hold a change in memory for minutes before writing its database
# (seen with a scripted delete), so even an unchanged database gets a real
# check this often.
RECHECK = dt.timedelta(minutes=5)
# Refuse to delete more than this share of known notes in one pass (when more
# than MASS_DELETE_MIN would go): Notes returning an empty or partial account
# must not wipe the copy.
MASS_DELETE_SHARE = 0.5
MASS_DELETE_MIN = 10
LOCKED_BODY = "_This note is locked in Apple Notes, so its contents aren't synced._\n"
# Basic Memory adds a permalink to each file it indexes; keeping it on rewrite
# saves it rewriting the file again.
KEPT_KEYS = {"permalink"}
UNSAFE_CHARS = re.compile(r'[\x00-\x1f/\\:*?"<>|]')


def db_signature(store: Path = NOTESTORE_DIR) -> List[Optional[List[int]]]:
    """mtime and size of the Notes database files; needs no Full Disk Access."""
    sig = []
    for name in DB_FILES:
        try:
            st = os.stat(store / name)
            sig.append([st.st_mtime_ns, st.st_size])
        except OSError:
            sig.append(None)
    return sig


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass
class State:
    signature: Optional[list] = None
    ok: bool = False
    notes_running: Optional[bool] = None
    last_sync: Optional[str] = None
    last_error: Optional[str] = None
    error_at: Optional[str] = None
    notes: Dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            return cls()
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, json.dumps(self.__dict__, indent=1, ensure_ascii=False))


class SyncError(RuntimeError):
    pass


def sync(
    notes,
    out: Path,
    state_path: Path,
    *,
    full: bool = False,
    signature: Callable[[], list] = db_signature,
    clock: Callable[[], dt.datetime] = utc_now,
) -> str:
    """Run one sync. Returns what happened: locked, unchanged, notes-closed, synced, or error."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(state_path) + ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "locked"
        return _sync_locked(notes, out, state_path, full, signature, clock)


def _sync_locked(notes, out, state_path, full, signature, clock) -> str:
    state = State.load(state_path)
    sig = signature()
    running = notes.running()

    now = clock()
    if state.ok:
        fresh = _within(state.last_sync, now, RECHECK)
    else:
        fresh = _within(state.error_at, now, ERROR_RETRY)
    unchanged = sig == state.signature and fresh
    if unchanged and not full:
        if running != state.notes_running:
            state.notes_running = running
            _finish(out, state, state_path)
        return "unchanged"

    if not running:
        # Don't record the signature: the next run with Notes open must sync.
        state.notes_running = False
        _finish(out, state, state_path)
        return "notes-closed"

    state.notes_running = True
    try:
        for _ in range(MAX_PASSES):
            complete = _sync_pass(notes, out, state, full)
            full = False
            state.signature = sig
            state.ok = complete
            state.last_sync = clock().isoformat()
            if complete:
                state.last_error = state.error_at = None
            else:
                state.last_error = "some note bodies could not be fetched; retrying"
                state.error_at = clock().isoformat()
            # Persist each pass so an interrupted multi-pass run keeps its work.
            state.save(state_path)
            new_sig = signature()
            if new_sig == sig:
                break
            sig = new_sig
    except (NotesError, SyncError) as exc:
        log.error("sync failed: %s", exc)
        # Recording the signature makes the next attempt wait for a database
        # change or ERROR_RETRY instead of retrying every run.
        state.signature = sig
        state.ok = False
        state.last_error = str(exc)
        state.error_at = clock().isoformat()
        _finish(out, state, state_path)
        return "error"
    _finish(out, state, state_path)
    return "synced"


def _sync_pass(notes, out: Path, state: State, full: bool) -> bool:
    listing: Listing = notes.listing()
    current = listing.notes
    known = state.notes

    vanished = [note_id for note_id in known if note_id not in current]
    if len(vanished) > MASS_DELETE_MIN and len(vanished) > MASS_DELETE_SHARE * len(known):
        raise SyncError(
            "listing would delete %d of %d notes; refusing (is Notes signed in and fully loaded?)"
            % (len(vanished), len(known))
        )

    changed = [
        note_id for note_id, note in current.items()
        if full or note_id not in known or _record_differs(known[note_id], note)
    ]
    # Delete first so a new note can take a vanished note's filename.
    for note_id in vanished:
        _remove(out, known.pop(note_id)["path"])

    taken = {_key(rec["path"]): note_id for note_id, rec in known.items() if note_id in current}
    pending = set(changed)
    written = moved = 0

    def apply(note_id: str, html: Optional[str]) -> None:
        nonlocal written, moved
        pending.discard(note_id)
        note = current[note_id]
        old = known.get(note_id, {}).get("path")
        path = _choose_path(out, note, old, taken)
        markdown = LOCKED_BODY if html is None else html_to_markdown(html)
        kept = _kept_lines(out / path) if _note_id_of(out / path) == note.id else []
        if _write_if_changed(out / path, _render(note, markdown, kept)):
            written += 1
        if old and old != path:
            taken.pop(_key(old), None)
            _remove(out, old)
            moved += 1
        taken[_key(path)] = note_id
        known[note_id] = {
            "path": path, "title": note.title, "folder": note.folder,
            "modified": note.modified, "locked": note.locked,
        }

    for note_id in changed:
        if current[note_id].locked:
            apply(note_id, None)
    # Bodies stream in range by range; write each as it arrives.
    for note_id, html in notes.bodies(listing, [i for i in changed if not current[i].locked]):
        if note_id in pending:
            apply(note_id, html)

    missing = pending
    if written or moved or vanished or missing:
        log.info(
            "synced: %d written, %d moved, %d deleted, %d missing, %d total",
            written, moved, len(vanished), len(missing), len(known),
        )
    return not missing


def _record_differs(rec: dict, note: Note) -> bool:
    return (
        rec.get("modified") != note.modified
        or rec.get("title") != note.title
        or rec.get("folder") != note.folder
        or rec.get("locked", False) != note.locked
    )


def _choose_path(out: Path, note: Note, old: Optional[str], taken: Dict[str, str]) -> str:
    folder = "/".join(_safe_name(part) for part in note.folder.split("/"))
    stem = _safe_name(note.title) or "Untitled"
    suffix = " (%s)" % note.id.rsplit("/", 1)[-1]
    plain = "%s/%s.md" % (folder, stem)
    tagged = "%s/%s%s.md" % (folder, stem, suffix)
    if old in (plain, tagged):
        return old
    owner = taken.get(_key(plain))
    if owner == note.id:
        return plain
    if owner is None:
        # An untracked file there is someone else's, unless an interrupted run
        # (or a lost state file) left this very note behind.
        existing = _note_id_of(out / plain)
        if existing is None or existing == note.id:
            return plain
    if _note_id_of(out / tagged) not in (None, note.id):
        raise SyncError("can't place note %s: %s is taken" % (note.id, tagged))
    return tagged


def _frontmatter(path: Path) -> Optional[str]:
    """None if the file doesn't exist, else its frontmatter block ("" if none)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except (FileNotFoundError, IsADirectoryError):
        return None
    if not head.startswith("---\n"):
        return ""
    end = head.find("\n---\n", 3)
    return head[4:end] if end >= 0 else ""


def _note_id_of(path: Path) -> Optional[str]:
    """None if the file doesn't exist, else the apple_notes_id it carries ("" if none)."""
    front = _frontmatter(path)
    if front is None:
        return None
    match = re.search(r'^apple_notes_id: (".*")$', front, re.MULTILINE)
    try:
        return json.loads(match.group(1)) if match else ""
    except ValueError:
        return ""


def _kept_lines(path: Path) -> List[str]:
    """Frontmatter lines other tools added that a rewrite should keep."""
    front = _frontmatter(path) or ""
    return [line for line in front.split("\n") if line.split(":", 1)[0] in KEPT_KEYS]


def _render(note: Note, markdown: str, kept: List[str] = ()) -> str:
    meta = [
        ("title", note.title),
        ("type", "apple-note"),
        ("apple_notes_id", note.id),
        ("folder", note.folder),
        ("created", note.created),
        ("modified", note.modified),
    ]
    head = "\n".join(["%s: %s" % (k, json.dumps(v, ensure_ascii=False)) for k, v in meta] + list(kept))
    return "---\n%s\n---\n\n%s" % (head, markdown)


def _finish(out: Path, state: State, state_path: Path) -> None:
    _write_if_changed(out / STATUS_FILE, _status_note(state))
    state.save(state_path)


def _status_note(state: State) -> str:
    def local(stamp: Optional[str]) -> str:
        if not stamp:
            return "never"
        return dt.datetime.fromisoformat(stamp).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    running = {True: "yes", False: "**no**: changes are not syncing", None: "unknown"}[state.notes_running]
    return (
        "---\n"
        'title: "Apple Notes sync status"\n'
        'type: "sync-status"\n'
        "---\n\n"
        "# Apple Notes sync status\n\n"
        "**Apple Notes must stay open on this Mac for changes to sync here.** Edits made on "
        "other devices only reach this Mac while Notes is running.\n\n"
        "This folder is a read-only copy of Apple Notes. Edits made here never reach Apple Notes "
        "and are overwritten the next time the note changes.\n\n"
        "- Last sync: %s\n"
        "- Notes running at last check: %s\n"
        "- Notes synced: %d\n"
        "- Last error: %s\n"
        % (local(state.last_sync), running, len(state.notes), state.last_error or "none")
    )


def _within(stamp: Optional[str], now: dt.datetime, span: dt.timedelta) -> bool:
    return bool(stamp) and now - dt.datetime.fromisoformat(stamp) < span


def _safe_name(name: str) -> str:
    name = UNSAFE_CHARS.sub("-", name).strip().lstrip(".").strip()
    return name[:120].rstrip()


def _key(path: str) -> str:
    # APFS is case-insensitive by default.
    return path.casefold()


def _write_if_changed(path: Path, content: str) -> bool:
    try:
        if path.read_text() == content:
            return False
    except (FileNotFoundError, UnicodeDecodeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, content)
    return True


def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(".%s.tmp" % path.name)
    tmp.write_text(content)
    os.replace(tmp, path)


def _remove(out: Path, rel: str) -> None:
    path = out / rel
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    parent = path.parent
    while parent != out and out in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
