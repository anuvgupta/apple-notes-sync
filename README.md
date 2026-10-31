# apple-notes-sync

One-way sync of Apple Notes into a folder of Markdown files, so Basic Memory (or Obsidian, or grep) can search them.

**Apple Notes must stay open.** The tool never launches Notes, and edits from other devices only reach the Mac while Notes is running. A `_Sync status.md` note at the top of the output folder says so, and shows the last sync and whether Notes was running.

## Setup

Needs macOS with Apple Notes and the system Python (`/usr/bin/python3`, 3.9+, from the Command Line Tools: `xcode-select --install`). No other dependencies.

1. Clone the repo somewhere it will stay; the agent runs from this checkout, so moving or deleting it stops the sync.

   ```bash
   git clone https://github.com/anuvgupta/apple-notes-sync.git ~/apple-notes-sync
   cd ~/apple-notes-sync
   ```

2. Open Apple Notes and leave it open.
3. Run the first sync by hand. It copies every note (about 3 minutes for 2,300 notes). macOS asks for permission to let your terminal control Notes; click **Allow**.

   ```bash
   /usr/bin/python3 -m apple_notes_sync sync
   ```

4. Install the background agent, which then syncs every 15 seconds. Its first run may ask for the same permission again; click **Allow**. Check on it with `status` or the log.

   ```bash
   /usr/bin/python3 -m apple_notes_sync install
   ```

Notes land in `~/apple-notes` by default; pass `--out DIR` to both commands to put them elsewhere. Keep that folder out of git repos you push, since it holds a copy of every note.

## Searching with Basic Memory

The synced folder is plain Markdown, so grep or Obsidian work on it as-is. To get meaning-based search from Claude ("find my notes about game ideas"), add it to [Basic Memory](https://github.com/basicmachines-co/basic-memory), which indexes Markdown for full-text and semantic search and exposes it to Claude over MCP.

1. Add Basic Memory to Claude Code (it runs through [uv](https://docs.astral.sh/uv/)):

   ```bash
   claude mcp add basic-memory -- uvx basic-memory mcp
   ```

   For Claude Desktop, add the same command (`uvx basic-memory mcp`) as an MCP server in its settings.

2. After the first sync has finished, register the folder as its own project:

   ```bash
   uvx basic-memory project add apple-notes ~/apple-notes
   ```

   This indexes and embeds every note, which takes several minutes for a large library. Adding the project while the first sync is still writing leaves later files unindexed; if that happens, run `uvx basic-memory reindex -p apple-notes`. After that, Basic Memory's watcher picks up changes within about 30 seconds.

3. Ask Claude to search your Apple Notes, for example "search my Apple Notes for tech ideas about home automation". Name Apple Notes or the `apple-notes` project, since Basic Memory searches its default project otherwise. From a terminal:

   ```bash
   uvx basic-memory tool search-notes "home automation ideas" --project apple-notes
   ```

Basic Memory adds a `permalink:` line to each file's frontmatter; the sync keeps it when it rewrites a note.

## Usage

```bash
python3 -m apple_notes_sync sync            # sync once into ~/apple-notes
python3 -m apple_notes_sync sync --full     # re-render every note
python3 -m apple_notes_sync install         # launchd agent, every 15 s
python3 -m apple_notes_sync status
python3 -m apple_notes_sync uninstall
```

`sync` and `install` take `--out DIR` and `--state FILE`. The agent logs to `~/Library/Logs/apple-notes-sync.log`.

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

## License

MIT
