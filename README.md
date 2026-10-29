# apple-notes-sync

One-way sync of Apple Notes into a folder of Markdown files, so Basic Memory can index them for full-text and semantic search.

## Plan

- Poll every 5–15 minutes via a launchd job.
- Export only notes modified since the last run (AppleScript/JXA, no Full Disk Access needed) and convert their HTML bodies to Markdown.
- Name each file by its Apple Notes ID and keep the title in frontmatter, so renames update in place and deletions can be detected.
- Let Basic Memory's file watcher pick up the changes.

The synced folder is read-only from this side: edits there never flow back to Apple Notes. Locked notes are skipped.
