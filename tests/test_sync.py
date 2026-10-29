import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apple_notes_sync import notes as notes_mod
from apple_notes_sync.notes import Listing, Note, NotesApp, NotesError, parse_listing, plan_ranges
from apple_notes_sync.sync import STATUS_FILE, State, sync

ID = "x-coredata://STORE/ICNote/p%d"


class FakeNotes:
    def __init__(self):
        self.is_running = True
        self.notes = {}  # id -> (Note, html)
        self.listings = 0
        self.fetched = []
        self.fail_listing = False

    def add(self, n, title, folder="Notes", body=None, modified="2026-01-01T00:00:00.000Z", locked=False):
        note = Note(ID % n, title, folder, "2025-01-01T00:00:00.000Z", modified, locked)
        self.notes[note.id] = (note, body if body is not None else "<div><h1>%s</h1></div>" % title)

    def running(self):
        return self.is_running

    def listing(self):
        if self.fail_listing:
            raise NotesError("boom")
        self.listings += 1
        listing = Listing()
        for note, _ in self.notes.values():
            listing.notes[note.id] = note
            listing.folder_notes.setdefault(note.folder, []).append(note.id)
            listing.note_folder[note.id] = note.folder
        return listing

    def bodies(self, listing, ids):
        ids = list(ids)
        self.fetched.extend(ids)
        return [(i, self.notes[i][1]) for i in ids if i in self.notes]


class SyncTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name) / "out"
        self.state = Path(tmp.name) / "state" / "state.json"
        self.notes = FakeNotes()
        self.sig = [[1, 1], [1, 1]]
        self.now = dt.datetime(2026, 10, 28, 12, 0, tzinfo=dt.timezone.utc)

    def run_sync(self, **kw):
        return sync(self.notes, self.out, self.state, signature=lambda: self.sig, clock=lambda: self.now, **kw)

    def files(self):
        return sorted(str(p.relative_to(self.out)) for p in self.out.rglob("*.md") if p.name != STATUS_FILE)

    def bump(self):
        self.sig = [[self.sig[0][0] + 1, 1], self.sig[1]]

    def test_initial_sync_writes_notes_and_status(self):
        self.notes.add(1, "Idea: robots", "tech/ideas", "<div><h1>Idea: robots</h1></div><div>build one</div>")
        self.notes.add(2, "Groceries")
        self.assertEqual(self.run_sync(), "synced")
        self.assertEqual(self.files(), ["Notes/Groceries.md", "tech/ideas/Idea- robots.md"])
        text = (self.out / "tech/ideas/Idea- robots.md").read_text()
        self.assertIn('title: "Idea: robots"', text)
        self.assertIn('apple_notes_id: "x-coredata://STORE/ICNote/p1"', text)
        self.assertIn('folder: "tech/ideas"', text)
        self.assertTrue(text.endswith("# Idea: robots\nbuild one\n"))
        status = (self.out / STATUS_FILE).read_text()
        self.assertIn("must stay open", status)
        self.assertIn("Notes synced: 2", status)

    def test_unchanged_signature_skips_listing(self):
        self.notes.add(1, "A")
        self.run_sync()
        self.assertEqual(self.run_sync(), "unchanged")
        self.assertEqual(self.notes.listings, 1)

    def test_unchanged_signature_still_rechecks_after_interval(self):
        self.notes.add(1, "A")
        self.run_sync()
        del self.notes.notes[ID % 1]
        self.now += dt.timedelta(minutes=4)
        self.assertEqual(self.run_sync(), "unchanged")
        self.now += dt.timedelta(minutes=2)
        self.assertEqual(self.run_sync(), "synced")
        self.assertEqual(self.files(), [])

    def test_only_changed_notes_are_fetched(self):
        self.notes.add(1, "A")
        self.notes.add(2, "B")
        self.run_sync()
        self.notes.fetched.clear()
        self.notes.add(2, "B", body="<div>new</div>", modified="2026-02-01T00:00:00.000Z")
        self.bump()
        self.assertEqual(self.run_sync(), "synced")
        self.assertEqual(self.notes.fetched, [ID % 2])
        self.assertTrue((self.out / "Notes/B.md").read_text().endswith("new\n"))

    def test_rewrite_keeps_basic_memory_permalink(self):
        self.notes.add(1, "A")
        self.run_sync()
        path = self.out / "Notes/A.md"
        path.write_text(path.read_text().replace("\n---\n", "\npermalink: apple-notes/notes/a\n---\n", 1))
        self.notes.add(1, "A", body="<div>edited</div>", modified="2026-02-01T00:00:00.000Z")
        self.bump()
        self.run_sync()
        text = path.read_text()
        self.assertIn("permalink: apple-notes/notes/a\n---\n", text)
        self.assertTrue(text.endswith("edited\n"))

    def test_rename_and_move_replace_old_file(self):
        self.notes.add(1, "Old", "work/deep")
        self.run_sync()
        self.notes.add(1, "New", "tech")
        self.bump()
        self.run_sync()
        self.assertEqual(self.files(), ["tech/New.md"])
        self.assertFalse((self.out / "work").exists())

    def test_deleted_note_removes_file_only(self):
        self.notes.add(1, "Keep")
        self.notes.add(2, "Drop")
        self.run_sync()
        (self.out / "Notes/mine.md").write_text("not from Notes")
        del self.notes.notes[ID % 2]
        self.bump()
        self.run_sync()
        self.assertEqual(self.files(), ["Notes/Keep.md", "Notes/mine.md"])

    def test_title_collisions_get_id_suffix(self):
        self.notes.add(1, "Todo")
        self.notes.add(2, "todo")
        self.run_sync()
        self.assertEqual(self.files(), ["Notes/Todo.md", "Notes/todo (p2).md"])

    def test_existing_foreign_file_is_not_overwritten(self):
        (self.out / "Notes").mkdir(parents=True)
        (self.out / "Notes/Todo.md").write_text("mine")
        self.notes.add(1, "Todo")
        self.run_sync()
        self.assertEqual((self.out / "Notes/Todo.md").read_text(), "mine")
        self.assertIn("Notes/Todo (p1).md", self.files())

    def test_mass_delete_is_refused(self):
        for n in range(20):
            self.notes.add(n, "N%d" % n)
        self.run_sync()
        for n in range(15):
            del self.notes.notes[ID % n]
        self.bump()
        self.assertEqual(self.run_sync(), "error")
        self.assertEqual(len(self.files()), 20)
        self.assertIn("refusing", State.load(self.state).last_error)

    def test_notes_closed_writes_status_and_syncs_later(self):
        self.notes.add(1, "A")
        self.notes.is_running = False
        self.assertEqual(self.run_sync(), "notes-closed")
        self.assertEqual(self.files(), [])
        self.assertIn("**no**", (self.out / STATUS_FILE).read_text())
        self.notes.is_running = True
        self.assertEqual(self.run_sync(), "synced")
        self.assertEqual(self.files(), ["Notes/A.md"])

    def test_notes_closing_updates_status_without_listing(self):
        self.notes.add(1, "A")
        self.run_sync()
        self.notes.is_running = False
        self.assertEqual(self.run_sync(), "unchanged")
        self.assertEqual(self.notes.listings, 1)
        self.assertIn("**no**", (self.out / STATUS_FILE).read_text())

    def test_error_backs_off_until_change(self):
        self.notes.add(1, "A")
        self.notes.fail_listing = True
        self.assertEqual(self.run_sync(), "error")
        self.assertEqual(self.run_sync(), "unchanged")
        self.now += dt.timedelta(minutes=6)
        self.notes.fail_listing = False
        self.assertEqual(self.run_sync(), "synced")

    def test_change_during_sync_runs_another_pass(self):
        self.notes.add(1, "A")
        sigs = iter([[[1, 1]], [[2, 1]], [[2, 1]]])
        result = sync(self.notes, self.out, self.state, signature=lambda: next(sigs), clock=lambda: self.now)
        self.assertEqual(result, "synced")
        self.assertEqual(self.notes.listings, 2)

    def test_locked_note_gets_stub_without_fetch(self):
        self.notes.add(1, "Secret", locked=True)
        self.run_sync()
        self.assertEqual(self.notes.fetched, [])
        self.assertIn("locked", (self.out / "Notes/Secret.md").read_text())

    def test_missing_body_marks_incomplete(self):
        self.notes.add(1, "A")
        self.notes.bodies = lambda listing, ids: []
        self.assertEqual(self.run_sync(), "synced")
        state = State.load(self.state)
        self.assertFalse(state.ok)
        self.assertNotIn(ID % 1, state.notes)


    def test_lost_state_adopts_existing_files(self):
        self.notes.add(1, "A")
        self.run_sync()
        self.state.unlink()
        self.assertEqual(self.run_sync(), "synced")
        self.assertEqual(self.files(), ["Notes/A.md"])


class BodiesTest(unittest.TestCase):
    def test_failing_ranges_split_and_fall_back_to_plain_text(self):
        folder = ["n%d" % i for i in range(1, 9)]
        listing = Listing(folder_notes={"f": folder})
        calls = []

        def fake(args, timeout=None):
            _, _, start, end = args[:4]
            start, end = int(start), int(end)
            plain = len(args) > 4
            calls.append((start, end, plain))
            if end - start >= 2 or (start == 5 and not plain):
                raise NotesError("-1741")
            return "\x1e".join("n%d\x1f%s" % (i, "line 1\nline 2" if plain else "<div>%d</div>" % i)
                                for i in range(start, end + 1))

        with mock.patch.object(notes_mod, "_osascript", fake):
            got = dict(NotesApp().bodies(listing, ["n1", "n2", "n5", "n6", "n7"]))
        self.assertEqual(sorted(got), ["n1", "n2", "n5", "n6", "n7"])
        self.assertEqual(got["n1"], "<div>1</div>")
        self.assertEqual(got["n5"], "<div>line 1</div><div>line 2</div>")
        # Halves with no wanted notes (n3-n4) are never fetched.
        self.assertNotIn((3, 4, False), calls)


class ListingTest(unittest.TestCase):
    def test_parse_listing_builds_folder_paths(self):
        raw = {"accounts": [{
            "id": "acct", "name": "iCloud",
            "folders": {
                "ids": ["f1", "f2"], "names": ["tech", "ideas"],
                "parents": ["x/ICAccount/p1", "f1"], "notes": [["n1"], ["n2", "n3"]],
            },
            "notes": {
                "ids": ["n1", "n2", "n3"], "names": ["a", "b", None],
                "created": ["c"] * 3, "modified": ["m"] * 3, "locked": [False, True, False],
            },
        }]}
        listing = parse_listing(raw)
        self.assertEqual(listing.notes["n1"].folder, "tech")
        self.assertEqual(listing.notes["n2"].folder, "tech/ideas")
        self.assertTrue(listing.notes["n2"].locked)
        self.assertEqual(listing.notes["n3"].title, "")
        self.assertEqual(listing.folder_notes["f2"], ["n2", "n3"])

    def test_recently_deleted_is_excluded(self):
        raw = {"accounts": [{
            "id": "acct", "name": "iCloud",
            "folders": {
                "ids": ["f1", "f2", "f3"], "names": ["Notes", "Recently Deleted", "Recently Deleted"],
                "parents": ["x/ICAccount/p1", "x/ICAccount/p1", "f1"], "notes": [["n1"], ["n2"], ["n3"]],
            },
            "notes": {
                "ids": ["n1", "n2", "n3"], "names": ["a", "b", "c"],
                "created": ["c"] * 3, "modified": ["m"] * 3, "locked": [False] * 3,
            },
        }]}
        listing = parse_listing(raw)
        self.assertEqual(sorted(listing.notes), ["n1", "n3"])
        self.assertEqual(listing.notes["n3"].folder, "Notes/Recently Deleted")
        self.assertNotIn("f2", listing.folder_notes)

    def test_plan_ranges_clusters_and_chunks(self):
        listing = Listing(folder_notes={"f": ["n%d" % i for i in range(1, 301)]})
        wanted = {"n1", "n3", "n40", "n100", "n101", "n250"}
        self.assertEqual(
            plan_ranges(listing, wanted),
            [("f", 1, 3), ("f", 40, 40), ("f", 100, 101), ("f", 250, 250)],
        )
        every = {"n%d" % i for i in range(1, 251)}
        self.assertEqual(plan_ranges(listing, every), [("f", 1, 100), ("f", 101, 200), ("f", 201, 250)])


if __name__ == "__main__":
    unittest.main()
