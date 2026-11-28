from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from second_brain import inbox_archive, storage
from second_brain.batch import Batch
from second_brain.capture import Capture
from second_brain.inbox import Inbox


class InboxArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name) / "Test")
        self.batch = Batch(self.root)
        self.capture = Capture(self.root)

    def incoming(self, path, body):
        file = self.root / "Inbox" / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(body)
        return {"path": file.relative_to(self.root).as_posix(), "revision": sha256(body).hexdigest()}

    def complete(self, path, text="Учебный отчёт"):
        item = self.incoming(path, text.encode())
        result = self.batch.apply({**item, "actions": [{"kind": "context", "line": 1, "quote": text}]})
        self.assertNotIn("archiveIssue", result)
        self.assertEqual(result["check"]["status"], "ok")
        return item, result

    def test_renamed_upload_and_direct_copies_reuse_one_review(self):
        path = "Projects/Встреча.md"
        item, result = self.complete(path)
        archived = self.root / "Archive/Inbox" / path
        source = self.root / result["source"]["path"]
        self.assertFalse((self.root / item["path"]).exists())
        self.assertEqual(archived.read_bytes(), source.read_bytes())
        receipt = self.root / result["processedPath"]
        confirmed = receipt.read_bytes()
        duplicate = self.capture.save_file("Другое имя.txt", source.read_bytes())
        self.assertTrue(duplicate["alreadyProcessed"])
        self.assertEqual(duplicate["path"], result["source"]["path"])
        direct = self.incoming("Повтор.txt", source.read_bytes())
        self.assertEqual(self.batch.pending(), [])
        legacy = self.incoming(path, source.read_bytes())
        preview = inbox_archive.archive_inbox(self.root)
        self.assertEqual(preview["files"]["total"], 2)
        for copy in (direct, legacy):
            self.assertTrue((self.root / copy["path"]).is_file())
        self.assertEqual(inbox_archive.archive_inbox(self.root, apply=True)["archived"], 2)
        self.assertEqual(inbox_archive.archive_inbox(self.root, apply=True)["archived"], 0)
        self.assertEqual(confirmed, receipt.read_bytes())
        self.assertEqual(archived.read_bytes(), source.read_bytes())

    def test_different_bytes_with_same_name_are_a_new_version(self):
        first, _ = self.complete("Сводка.txt", "Один")
        fresh = self.capture.save_text("Два", "Сводка")
        self.assertNotEqual(first["revision"], fresh["revision"])
        second, _ = self.complete("Сводка.txt", "Три")
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual((self.root / "Archive/Inbox/Сводка.txt").read_text(), "Один")
        self.assertEqual((self.root / "Archive/Inbox/Versions/2/Сводка.txt").read_text(), "Три")
        self.assertEqual([r["path"] for r in self.batch.pending()], [fresh["path"]])

    def test_replaced_input_and_unanswered_questions_wait_for_resolution(self):
        item = self.incoming("Сверка.md", b"question")
        result = self.batch.apply({**item, "actions": []})
        question, = result["triage"]
        self.assertTrue((self.root / item["path"]).exists())
        self.assertEqual(inbox_archive.archive_inbox(self.root, apply=True)["archived"], 0)
        self.batch.resolve(question["id"], "Пропустить")
        self.incoming("Сверка.md", b"new document")
        self.batch.apply({**item, "triage_id": question["id"],
                          "actions": [{"kind": "context", "line": 1, "quote": "question"}]})
        self.assertEqual((self.root / item["path"]).read_bytes(), b"new document")
        self.assertEqual(Inbox(self.root).original(item["revision"])["lines"][0]["text"], "question")

    def test_attachment_keeps_link_to_reviewed_transcript(self):
        item = self.incoming("Снимок.png", b"\xff\x00\x81")
        result = self.batch.apply({**item, "actions": [{"kind": "transcript", "line": 0, "quote": "",
                                                        "text": "Строка из снимка."}]})
        self.assertEqual(self.capture.save_file("Copy.bin", b"\xff\x00\x81")["path"], result["source"]["path"])
        transcript, = self.batch.pending()
        self.assertEqual(transcript["path"], result["transcript"]["path"])
        processed = self.batch.apply({"path": transcript["path"], "revision": transcript["revision"],
                                      "actions": [{"kind": "context", "line": 3, "quote": "Строка из снимка."}]})
        label = "Расшифровка для разбора"
        receipt = self.root / result["processedPath"]
        stable = storage.markdown_link(label, self.root / processed["source"]["path"], receipt.parent)
        self.assertIn(stable, receipt.read_text())
        named = self.root / "Archive/Inbox" / f"Transcript {item['revision']}.md"
        relative_link = named.read_text().splitlines()[0].split("](")[1].rstrip(")")
        self.assertEqual((named.parent / relative_link).resolve(), self.root / result["source"]["path"])
        self.assertFalse((self.root / transcript["path"]).exists())
        self.assertEqual(Inbox(self.root).original(transcript["revision"])["attachmentId"], item["revision"])

    def test_corrupt_original_and_failed_move_never_discard_input(self):
        item = self.incoming("Сверка.txt", b"safe")
        plan = {**item, "actions": [{"kind": "context", "line": 1, "quote": "safe"}]}
        with patch.object(inbox_archive, "_move", side_effect=OSError("archive unavailable")):
            saved = self.batch.apply(plan)
        self.assertEqual(saved["archiveIssue"]["reason"], "archive unavailable")
        original = self.root / saved["source"]["path"]
        original.write_bytes(b"corrupt")
        refused = inbox_archive.archive_inbox(self.root, apply=True)
        self.assertEqual(refused["issues"]["total"], 1)
        self.assertEqual(refused["archived"], 0)
        self.assertEqual((self.root / item["path"]).read_bytes(), b"safe")
        original.write_bytes(b"safe")
        self.assertNotIn("archiveIssue", self.batch.apply(plan))
        self.assertEqual((self.root / "Archive/Inbox/Сверка.txt").read_bytes(), b"safe")


if __name__ == "__main__":
    unittest.main()
