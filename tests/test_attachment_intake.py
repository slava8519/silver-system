from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from second_brain import storage
from second_brain.batch import Batch
from second_brain.inbox import Inbox
from second_brain.routing import Routing


class AttachmentIntake(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = storage.initialize(Path(directory.name) / "Practice")
        self.inbox = Inbox(self.root)
        self.batch = Batch(self.root)

    def add(self, name, data):
        path = self.root / "Inbox" / name
        path.write_bytes(data)
        return {"path": path.relative_to(self.root).as_posix(), "revision": hashlib.sha256(data).hexdigest()}

    @staticmethod
    def transcript(item, text):
        return {**item, "actions": [{"kind": "transcript", "line": 0, "quote": "", "text": text}]}

    def test_visual_transcript_then_project_update_cites_the_archived_source(self):
        excerpt = "Проект «Тетрадь» получил статус «готов»."
        drawing = ('<svg xmlns="http://www.w3.org/2000/svg"><text x="8" y="28">' + excerpt + '</text></svg>').encode()
        item = self.add("Сообщение.svg", drawing)
        row, = self.batch.pending()
        self.assertEqual((row["status"], row["size"]), ("attachment", len(drawing)))
        self.assertTrue(self.inbox.list_items()[0]["attachment"])
        with self.assertRaises(ValueError):
            self.inbox.preview(item["path"])
        plan = self.transcript(item, excerpt)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, repeated = pool.map(self.batch.apply, [plan, plan])
        self.assertEqual(first, repeated)
        self.assertEqual(first["applied"], [])
        (self.root / item["path"]).unlink()
        raw, data = self.inbox.attachment(item["revision"])
        self.assertEqual((data, raw.suffix), (drawing, ".svg"))
        with self.assertRaises(ValueError):
            self.inbox.original(item["revision"])
        derivative, = self.batch.pending()
        self.assertEqual(derivative["path"], first["transcript"]["path"])
        written = self.inbox.preview(derivative["path"])
        target = written["lines"][0]["text"].split("](")[1].rstrip(")")
        self.assertEqual(((self.root / derivative["path"]).parent / target).resolve(), raw)
        self.assertEqual(written["lines"][2], {"n": 3, "text": excerpt})
        direction = storage.read_record(storage.create(self.root, "direction", "Издание"))["id"]
        rule = Routing(self.root).set_rule(direction, "Записывать согласованный статус.", ["status"], [])
        record = storage.read_record(storage.create(self.root, "project", "Тетрадь", direction=direction))
        review = self.batch.apply({"path": derivative["path"], "revision": derivative["revision"], "actions": [{
            "kind": "project", "field": "status", "value": "готов", "line": 3, "quote": excerpt,
            "direction": direction, "rule_revision": rule["revision"], "project": record["id"],
            "expected_revision": record["revision"]}]})
        self.assertEqual([s["value"] for s in review["applied"]], ["готов"])
        archived_transcript = self.root / review["source"]["path"]
        self.assertEqual((archived_transcript.parent / target).resolve(), raw)
        self.assertEqual(self.batch.pending(), [])
        self.assertEqual(self.batch.triage(), [])

    def test_unsupported_file_can_wait_for_clarification_then_resume_from_original(self):
        data = b"\x00\xff\xff\x11"
        item = self.add("Вложение", data)
        self.add("Вторая копия.odd", data)
        self.assertEqual(len(self.batch.pending()), 1)
        question, = self.batch.apply({**item, "actions": []})["triage"]
        self.assertEqual((question["status"], question["line"]), ("open", 0))
        self.assertEqual(self.inbox.attachment(item["revision"])[0].suffix, ".bin")
        (self.root / item["path"]).unlink()
        self.assertEqual(self.batch.pending(), [])
        self.batch.resolve(question["id"], "Материал для справки, можно пропустить.")
        ready, = self.batch.pending()
        self.assertTrue(ready["attachment"])
        self.batch.apply({**item, "triage_id": ready["triage_id"],
                          "actions": [{"kind": "context", "quote": "", "line": 0}]})
        self.assertEqual(self.batch.triage(), [])
        self.assertEqual(self.inbox.attachment(item["revision"])[1], data)

    def test_modified_or_nontext_data_cannot_yield_verified_text_or_overwrite_a_transcript(self):
        item = self.add("Экран.png", b"old bytes")
        changed = self.add("Экран.png", b"new bytes")
        with self.assertRaises(storage.ConflictError):
            self.batch.apply(self.transcript(item, "устаревшая расшифровка"))
        forged = {"kind": "context", "line": 1, "quote": "new bytes"}
        with self.assertRaises(ValueError):
            self.batch.apply({**changed, "actions": [forged]})
        self.assertFalse(list((self.root / "Sources/Originals").iterdir()))
        folder = self.root / "Inbox/Transcripts"
        folder.mkdir()
        occupied = folder / (changed["revision"] + ".md")
        occupied.write_text("Ручная запись.", encoding="utf-8")
        result = self.batch.apply(self.transcript(changed, "Читаемое слово"))
        self.assertEqual(len(result["triage"]), 1)
        self.assertEqual(occupied.read_text(encoding="utf-8"), "Ручная запись.")
        raw, _ = self.inbox.attachment(changed["revision"])
        raw.write_bytes(b"changed archive")
        with self.assertRaises(storage.ConflictError):
            self.inbox.attachment(changed["revision"])
        self.assertEqual(self.batch.triage()[0]["status"], "damaged")

    def test_interrupted_transcription_replays_even_after_attachment_is_moved(self):
        item = self.add("Фрагмент.webp", b"webp-data")
        plan = self.transcript(item, "Видимая надпись")
        append = self.batch._append
        def stop_after_file(path, digest, event, label=""):
            if event["op"] == "step":
                raise InterruptedError()
            return append(path, digest, event, label)
        with patch.object(self.batch, "_append", side_effect=stop_after_file):
            with self.assertRaises(InterruptedError):
                self.batch.apply(plan)
        (self.root / item["path"]).unlink()
        resumed = self.batch.apply({**item, "actions": []})
        self.assertTrue((self.root / resumed["transcript"]["path"]).is_file())
        self.assertEqual(len(list((self.root / "Inbox/Transcripts").iterdir())), 1)
        self.assertEqual(len(self.batch.pending()), 1)
