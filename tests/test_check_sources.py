import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from second_brain import storage
from second_brain.batch import Batch
from second_brain.check_sources import check_sources
from second_brain.inbox import Inbox


class Findings:
    def __init__(self):
        self.issues = []
        self.destinations = []

    def add(self, code, path, line, message):
        self.issues.append((code, path, line, message))

    unreadable = add

    def destination(self, path, line, kind, identifier):
        self.destinations.append((kind, identifier))


class CheckSourcesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.inbox = Inbox(self.root)
        self.batch = Batch(self.root)

    def process(self, name, raw, actions=()):
        incoming = self.root / "Inbox" / name
        incoming.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        result = self.batch.apply({"path": "Inbox/" + name, "revision": digest, "actions": list(actions)})
        return digest, self.root / result["source"]["path"], self.root / result["processedPath"]

    def run_check(self, digest=None):
        report = Findings()
        counts = check_sources(self.root, report, source_id=digest)
        return report, counts

    def test_pending_questions_historical_paths_unmanaged_notes_and_pre_receipt_archives(self):
        self.process("Разбор.txt", b"owner question")
        (self.root / "Inbox" / "Разбор.txt").unlink()
        self.process("Unreadable.md", b"\xff")
        self.process("Image.png", b"\x80\0abc")
        loose = b"original before receipt"
        (self.root / "Sources/Originals" / (hashlib.sha256(loose).hexdigest() + ".md")).write_bytes(loose)
        (self.root / "Sources/Processed" / "Авторская запись.md").write_text("Просто заметка.")
        (self.root / "Sources/Originals" / "Документ.md").write_bytes(b"\xff")
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        report, counts = self.run_check()
        self.assertEqual(counts, {"receipts": 3, "originals": 4})
        self.assertEqual(report.issues, [])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_scoped_hash_check_does_not_read_unrelated_originals(self):
        selected, original, _ = self.process("Large.bin", b"x" * (2 * 1024 * 1024 + 7))
        _, changed, _ = self.process("Changed.txt", b"good")
        changed.write_text("bad")
        from second_brain import inbox
        iterdir = Path.iterdir
        def check_directory(directory):
            self.assertNotEqual(directory, self.root / "Sources/Processed")
            return iterdir(directory)
        with patch.object(inbox, "_file_digest", wraps=inbox._file_digest) as reader, \
                patch.object(Path, "iterdir", check_directory):
            report, counts = self.run_check(selected)
        self.assertEqual((report.issues, counts), ([], {"receipts": 1, "originals": 1}))
        reader.assert_called_once_with(original)
        self.assertEqual([issue[0] for issue in self.run_check()[0].issues], ["source_digest"])

    def test_missing_ambiguous_symlink_and_corrupt_journal_are_isolated(self):
        _, removed, _ = self.process("missing.md", b"first")
        removed.unlink()
        _, original, _ = self.process("double.txt", b"second")
        original.with_suffix(".md").write_bytes(original.read_bytes())
        _, pointed, _ = self.process("linked.txt", b"no symlinks")
        pointed.unlink()
        pointed.symlink_to(original)
        _, _, receipt = self.process("third.md", b"third")
        with receipt.open("a") as stream:
            stream.write('<!-- inbox-batch: {"op":"end","job":"absent"} -->\n')
        report, counts = self.run_check()
        self.assertCountEqual([issue[0] for issue in report.issues],
                              ["source_missing", "source_ambiguous", "source_unreadable", "receipt_journal"])
        self.assertEqual(counts, {"receipts": 4, "originals": 4})
        issue = next(issue for issue in report.issues if issue[0] == "receipt_journal")
        self.assertEqual((issue[1], issue[2]), (receipt, len(receipt.read_bytes().splitlines())))

    def test_recorded_quote_checked_without_returning_private_text(self):
        secret = "Сведения только для себя"
        quoted = "Хвоя ощущается первой."
        first = "Метки" + chr(0x2028) + "вторая строка" + chr(0x2028)
        action = {"kind": "context", "line": 3, "quote": quoted}
        digest, _, receipt = self.process("Наблюдение.md", (first + quoted + "\r\n" + secret).encode("utf-8-sig"),
                                          [action])
        self.assertEqual(self.run_check(digest)[0].issues, [])
        original = receipt.read_text()
        receipt.write_text(original.replace(quoted, "Несуществующая цитата"))
        report, _ = self.run_check(digest)
        self.assertEqual([issue[0] for issue in report.issues], ["receipt_quote"])
        self.assertNotIn(secret, repr(report.issues))
        self.assertNotIn("Несуществующая цитата", repr(report.issues))
        receipt.write_text(original.replace("schema_version: 1", "schema_version: 9"))
        issue, = self.run_check(digest)[0].issues
        self.assertEqual(issue[:3], ("receipt_header", receipt, 3))

    def test_committed_source_scope_requires_its_receipt(self):
        digest, _, receipt = self.process("Разбор.md", b"reviewed")
        receipt.unlink()
        issue, = self.run_check(digest)[0].issues
        self.assertEqual(issue[:3], ("receipt_missing", receipt, 0))
        self.assertEqual(self.run_check()[0].issues, [])

    def test_unfinished_batch_reports_begin_line_and_resumes_to_a_completed_question(self):
        digest, _, receipt = self.process("Дом.md", b"unassigned line")
        rows = receipt.read_text().splitlines(keepends=True)
        begin = next(i + 1 for i, row in enumerate(rows) if row.startswith('<!-- inbox-batch: {"op":"begin"'))
        receipt.write_text("".join(rows[:begin]))
        issue, = self.run_check()[0].issues
        self.assertEqual(issue[:3], ("receipt_unfinished", receipt, begin))
        pending, = self.batch.pending()
        self.assertEqual(pending["status"], "resume")
        result = self.batch.apply({"path": pending["path"], "revision": digest, "actions": []})
        self.assertEqual(result["check"]["status"], "ok")
        self.assertEqual(len(result["triage"]), 1)
        self.assertEqual(self.run_check()[0].issues, [])

    def test_header_only_or_only_person_marker_is_incomplete_until_restored(self):
        digest, _, receipt = self.process("Квитанция.md", b"source")
        saved = receipt.read_bytes()
        header = saved.split(b"\n---\n", 1)[0] + b"\n---\n"
        for tail in (b"", b"<!-- person-id: test-person -->\n"):
            with self.subTest(tail=tail):
                receipt.write_bytes(header + tail)
                issue, = self.run_check(digest)[0].issues
                self.assertEqual(issue[:3], ("receipt_empty", receipt, 1))
                self.assertEqual(self.batch.pending(), [])
        receipt.write_bytes(saved)
        self.assertEqual(self.run_check()[0].issues, [])

    def test_legacy_person_receipt_keeps_its_fact_destination_after_rename(self):
        direction = storage.read_record(storage.create(self.root, "direction", "Личное"))["id"]
        name, line = "Встреча.txt", "Нина отвечает за маршрут."
        (self.root / "Inbox" / name).write_text(line)
        digest = hashlib.sha256(line.encode()).hexdigest()
        fact = {"kind": "role", "visibility": "shared", "direction": direction, "line": 1, "text": "за маршрут"}
        result = self.inbox.process({"path": "Inbox/" + name, "revision": digest,
                                     "person": {"new": True, "name": "Нина"}, "facts": [fact]})
        receipt = self.root / result["processedPath"]
        (self.root / result["person"]["path"]).rename(self.root / "People/Путешественница.md")
        report, _ = self.run_check()
        self.assertEqual(report.issues, [])
        self.assertCountEqual([d[0] for d in report.destinations], ["person", "fact"])
        receipt.write_text("".join(s for s in receipt.read_text().splitlines(keepends=True)
                                   if not s.startswith("<!-- person-id: ")))
        report, _ = self.run_check()
        self.assertEqual(report.issues, [])
        self.assertEqual([d[0] for d in report.destinations], ["fact"])


if __name__ == "__main__":
    unittest.main()
