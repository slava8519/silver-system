from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from second_brain import storage
from second_brain.inbox import Inbox
from second_brain.people import People


class InboxTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name) / "Test")
        self.inbox = Inbox(self.root)
        self.people = People(self.root)
        self.field = self.make_direction("Полевые наблюдения")
        self.book = self.make_direction("Книжный клуб")
        self.path = self.root / "Inbox" / "Встреча.md"
        self.content = ("# Встреча\r\nНина координирует наблюдения. Отдельно записан закрытый факт.\r\n"
                        "Нина выбирает книги для клуба.\r\n").encode("utf-8-sig")
        self.path.write_bytes(self.content)
        self.claims = [
            self.fact("role", self.field, 2, "координирует наблюдения"),
            self.fact("role", self.book, 3, "выбирает книги"),
            self.fact("observation", self.field, 2, "закрытый факт", "personal"),
        ]
        self.request = {"path": "Inbox/Встреча.md", "revision": hashlib.sha256(self.content).hexdigest(),
                        "person": {"name": "Нина", "new": True}, "facts": self.claims}

    def make_direction(self, title):
        path = storage.create(self.root, "direction", title)
        return storage.read_record(path)["id"]

    def fact(self, kind, direction, line, text, visibility="shared"):
        return {"kind": kind, "direction": direction, "line": line, "text": text, "visibility": visibility}

    def test_preview_and_listing_are_scoped(self):
        nested = self.root / "Inbox" / "Notes"
        nested.mkdir()
        (nested / "Outline.TXT").write_text("")
        (self.root / "Inbox" / "Archive.png").write_bytes(b"image")
        (self.root / "Inbox" / "Linked.md").symlink_to(self.path)
        (self.root / "Inbox" / "Remote").symlink_to(self.root / "Projects", target_is_directory=True)
        rows = self.inbox.list_items()
        self.assertEqual({row["path"] for row in rows}, {"Inbox/Встреча.md", "Inbox/Notes/Outline.TXT", "Inbox/Archive.png"})
        self.assertTrue(next(row for row in rows if row["path"] == "Inbox/Archive.png")["attachment"])
        self.assertTrue(all(not row["processed"] for row in rows))
        view = self.inbox.preview("Inbox/Встреча.md", start=2, limit=1)
        self.assertEqual(view["total"], 3)
        self.assertEqual(view["revision"], self.request["revision"])
        self.assertEqual(view["lines"], [{"n": 2, "text": "Нина координирует наблюдения. Отдельно записан закрытый факт."}])
        self.assertEqual(self.inbox.preview("Inbox/Notes/Outline.TXT")["lines"], [])
        for path in ("Inbox/../Home.md", "Home.md", str(self.path), "Inbox/Linked.md", "Inbox/Remote/name.md"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.inbox.preview(path)
        for start, limit in ((0, 5), (2, 81), (True, 1), (9, 2)):
            with self.subTest(start=start, limit=limit), self.assertRaises(ValueError):
                self.inbox.preview("Inbox/Встреча.md", start, limit)

    def test_idempotent_across_concurrent_requests_renames_and_extensions(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            first, again = list(workers.map(self.inbox.process, [self.request, self.request]))
        self.assertEqual(first, again)
        original = self.root / first["source"]["path"]
        self.assertEqual(original.read_bytes(), self.content)
        named = self.root / "Archive/Inbox" / self.path.name
        self.assertEqual(named.read_bytes(), self.content)
        self.assertFalse(self.path.exists())
        receipt = self.root / first["processedPath"]
        before = receipt.read_bytes()
        new_name = self.path.with_name("Перенесено.txt")
        new_name.write_bytes(named.read_bytes())
        moved = {**self.request, "path": "Inbox/" + new_name.name}
        self.assertEqual(self.inbox.process(moved), first)
        self.assertEqual(before, receipt.read_bytes())
        self.assertEqual(len(list(original.parent.iterdir())), 1)
        self.assertEqual(len(list(receipt.parent.iterdir())), 1)
        self.assertEqual(self.inbox.list_items(), [])
        new_name.write_bytes(self.content + b"\n")
        self.assertFalse(self.inbox.list_items()[0]["processed"])
        with self.assertRaises(storage.ConflictError):
            self.inbox.process(moved)
        self.assertEqual(original.read_bytes(), self.content)

    def test_bad_proposal_leaves_no_original_receipt_or_person(self):
        for change in [{"text": "придуманный фрагмент"}, {"direction": "missing"},
                       {"line": 0}, {"line": True}, {"line": 100}, {"kind": "team"},
                       {"visibility": ""}, {"text": ""}]:
            bad = {**self.request, "facts": [self.claims[0], {**self.claims[1], **change}]}
            with self.subTest(change=change), self.assertRaises((ValueError, FileNotFoundError)):
                self.inbox.process(bad)
        repeated = [self.claims[0], {**self.claims[0], "visibility": "personal"}]
        with self.assertRaises(ValueError):
            self.inbox.process({**self.request, "facts": repeated})
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.inbox.process({**self.request, "person": {"id": "unknown"}})
        for name in ("People", "Sources/Originals", "Sources/Processed"):
            self.assertEqual(list((self.root / name).iterdir()), [])

    def test_evidence_returns_only_original_permitted_quote(self):
        result = self.inbox.process(self.request)
        card = self.people.detail(result["personId"], personal=True)
        public = next(f for f in card["facts"] if f["quote"] == "координирует наблюдения")
        private = next(f for f in card["facts"] if f["visibility"] == "personal")
        excerpt = self.inbox.fact_evidence(result["personId"], public["id"])
        self.assertEqual(excerpt, {"path": result["source"]["path"], "start": 2, "end": 2,
                                   "text": "координирует наблюдения"})
        with self.assertRaises(FileNotFoundError):
            self.inbox.fact_evidence(result["personId"], private["id"])
        self.assertEqual(self.inbox.fact_evidence(result["personId"], private["id"], personal=True)["text"],
                         "закрытый факт")
        named = self.root / "Archive/Inbox" / self.path.name
        named.rename(named.with_name("Moved.txt"))
        saved = self.inbox.original(result["source"]["id"], start=2, limit=1)
        self.assertEqual(saved["revision"], self.request["revision"])
        self.assertEqual(saved["lines"], [{"n": 2, "text": self.content.decode("utf-8-sig").splitlines()[1]}])
        named.with_name("Moved.txt").rename(named)
        original = self.root / result["source"]["path"]
        original.write_bytes(original.read_bytes() + b"\n")
        with self.assertRaises(storage.ConflictError):
            self.inbox.fact_evidence(result["personId"], public["id"])
        with self.assertRaises(storage.ConflictError):
            self.inbox.process(self.request)

    def test_retry_after_interruption_and_append_second_participant(self):
        with patch.object(self.inbox, "_save_receipt", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                self.inbox.process(self.request)
        self.assertFalse(self.inbox.list_items()[0]["processed"])
        first = self.inbox.process(self.request)
        receipt = self.root / first["processedPath"]
        with receipt.open("a", encoding="utf-8") as file:
            file.write("\nКомментарий владельца.\n")
        before = receipt.read_bytes()
        second = self.inbox.process({**self.request, "person": {"new": True, "name": "Автор протокола"},
                                     "facts": [self.claims[1]]})
        self.assertNotEqual(first["personId"], second["personId"])
        self.assertEqual(second["source"], first["source"])
        self.assertEqual(second["processedPath"], first["processedPath"])
        self.assertTrue(receipt.read_bytes().startswith(before))
        self.assertEqual(receipt.read_text().count("<!-- fact-id: "), 4)
        self.assertEqual(len(self.people.detail(first["personId"], personal=True)["facts"]), 3)

    def test_manual_markdown_edit_survives_reprocessing_and_keeps_source(self):
        saved = self.inbox.process(self.request)
        person_file = self.root / saved["person"]["path"]
        before = person_file.read_text()
        before_line = "> координирует наблюдения\n"
        self.assertEqual(before.count(before_line), 1)
        person_file.write_text(before.replace(before_line, "> Проверяет записи наблюдателей.\n"))
        self.inbox.process(self.request)
        facts = self.people.detail(saved["personId"])["facts"]
        current = next(fact for fact in facts if fact["direction"] == self.field)
        self.assertEqual(current["text"], "Проверяет записи наблюдателей.")
        self.assertEqual(self.inbox.fact_evidence(saved["personId"], current["id"])["text"], "координирует наблюдения")
        self.assertIn("координирует наблюдения", [entry["text"] for entry in current["history"]])
        captured = person_file.read_bytes()
        self.inbox.process(self.request)
        self.assertEqual(person_file.read_bytes(), captured)

    def test_deleted_fact_is_not_restored_and_other_facts_can_still_be_added(self):
        saved = self.inbox.process({**self.request, "facts": self.claims[:2]})
        path = self.root / saved["person"]["path"]
        content = path.read_text()
        # The final section holds the book-club role, which the owner removed.
        path.write_text(content[:content.rindex("\n### ")])
        manual = path.read_bytes()
        receipt = self.root / saved["processedPath"]
        old_receipt = receipt.read_bytes()
        with self.assertRaises(storage.ConflictError):
            self.inbox.process({**self.request, "facts": [self.claims[2], self.claims[1]]})
        self.assertEqual(path.read_bytes(), manual)
        self.assertEqual(receipt.read_bytes(), old_receipt)
        self.inbox.process({**self.request, "facts": [self.claims[2]]})
        visible = self.people.detail(saved["personId"], personal=True)["facts"]
        self.assertEqual({f["quote"] for f in visible}, {self.claims[i]["text"] for i in (0, 2)})

    def test_deleted_person_is_not_recreated_by_retry_or_new_selection_of_passages(self):
        saved = self.inbox.process({**self.request, "facts": self.claims[:1]})
        card = self.root / saved["person"]["path"]
        card.unlink()
        receipt = self.root / saved["processedPath"]
        before = receipt.read_bytes()
        for identity in (self.request["person"], {"id": saved["personId"]}):
            for fact in self.claims[:2]:
                with self.subTest(identity=identity, quote=fact["text"]), self.assertRaises(storage.ConflictError):
                    self.inbox.process({**self.request, "person": identity, "facts": [fact]})
        self.assertEqual(list((self.root / "People").iterdir()), [])
        self.assertEqual(receipt.read_bytes(), before)
        other = {**self.request, "person": {"new": True, "name": "Второй участник"}, "facts": self.claims[:1]}
        with patch.object(self.inbox, "_save_receipt", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                self.inbox.process(other)
        completed = self.inbox.process(other)
        self.assertNotEqual(completed["personId"], saved["personId"])
        self.assertEqual(len(list((self.root / "People").glob("*.md"))), 1)
        self.assertFalse(card.exists())
        self.assertEqual(receipt.read_text().count("<!-- fact-id: "), 2)

    def test_legacy_receipt_recognizes_previous_participant_by_confirmed_fact(self):
        saved = self.inbox.process(self.request)
        receipt = self.root / saved["processedPath"]
        legacy = "".join(line for line in receipt.read_text().splitlines(keepends=True)
                         if not line.startswith("<!-- person-id: "))
        receipt.write_text(legacy)
        (self.root / saved["person"]["path"]).unlink()
        with self.assertRaises(storage.ConflictError):
            self.inbox.process({**self.request, "facts": self.claims[:1]})
        self.assertEqual(list((self.root / "People").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
