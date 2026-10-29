# apple-notes-sync

One-way sync of Apple Notes into a folder of Markdown files, so Basic Memory (or Obsidian, or grep) can search them.

**Apple Notes must stay open.** The tool never launches Notes, and edits from other devices only reach the Mac while Notes is running. A `_Sync status.md` note at the top of the output folder says so, and shows the last sync and whether Notes was running.

## Usage

Needs only the macOS system Python (`/usr/bin/python3`, 3.9+); no dependencies.

```bash
python3 -m apple_notes_sync sync            # sync once into ~/apple-notes
python3 -m apple_notes_sync sync --full     # re-render every note
python3 -m apple_notes_sync install         # launchd agent, every 15 s
python3 -m apple_notes_sync status
python3 -m apple_notes_sync uninstall
```

`sync` and `install` take `--out DIR` and `--state FILE`. The agent runs from this checkout and logs to `~/Library/Logs/apple-notes-sync.log`. The first run asks for permission to control Notes.

## How it works

- Every 15 s the agent checks the mtime and size of `NoteStore.sqlite` and its WAL. That works without Full Disk Access, and an unchanged database means the run exits immediately.
- When it changed, one JXA call lists every note with bulk getters (ids, titles, dates, folders), and the notes whose modification date, title, or folder changed are fetched with AppleScript ranges and converted from HTML to Markdown.
- One file per note at `<folder>/<title>.md` with `title`, `type`, `apple_notes_id`, `folder`, `created`, and `modified` in frontmatter. A title clash in one folder gets the note id appended. Renamed and moved notes move their file; deleted notes delete it.
- Only files the tool wrote are ever moved or deleted, and a listing that would delete more than half the notes is refused.
- Images and attachments become `[image]` / `[attachment]` placeholders. Locked notes get a stub.

The output folder is a read-only copy: edits there never reach Apple Notes and are overwritten when the note changes.

## Why not…

- **`whose` filters** on notes time out after 120 s and leave Notes busy; single-note Apple events cost 0.25–1 s each. Only bulk getters and ranges are fast.
- **`WatchPaths`** didn't fire for Notes' in-place WAL writes while Notes was open, so the agent polls.
- **Reading `NoteStore.sqlite` directly** needs Full Disk Access; planned as an optional faster path later.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```
