import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from second_brain import storage
from second_brain.batch import Batch
from second_brain.webserver import server


class VaultCheckInterfaces(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = storage.initialize(Path(directory.name) / "Учебный vault")
        http = server(self.root, 0, mode="work")
        self.port = http.server_port
        thread = threading.Thread(target=http.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(http.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(http.shutdown)

    def request(self, path, data=None, status=200):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST" if data is not None else "GET", path,
                         body=json.dumps(data, ensure_ascii=False).encode() if data is not None else None,
                         headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            self.assertEqual(response.status, status)
            return json.load(response)
        finally:
            conn.close()

    def cli(self, *args, data=None, status=0):
        result = subprocess.run([sys.executable, "-m", "second_brain", "--vault", str(self.root), *args],
                                input=json.dumps(data) if data is not None else None,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, status, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def incoming(self, text):
        (self.root / "Inbox" / "Источник.txt").write_text(text, encoding="utf-8")
        return {"path": "Inbox/Источник.txt", "revision": hashlib.sha256(text.encode()).hexdigest()}

    def test_full_check_cli_http_and_paging_survive_broken_catalog(self):
        self.assertEqual(self.cli("check"), self.request("/api/check"))
        for name in ("ИТ-проекты", "Личное", "Парфюмерия"):
            path = storage.create(self.root, "direction", name)
            path.with_name(name + "-копия.md").write_bytes(path.read_bytes())
        self.request("/api/workspace", status=400)
        first = self.cli("check", "--limit", "1", status=1)
        self.assertEqual(first, self.request("/api/check?limit=1"))
        self.assertGreaterEqual(first["issues"]["total"], 3)
        self.assertTrue(first["issues"]["truncated"])
        second = self.cli("check", "--limit", "1", "--offset", "1", status=1)
        self.assertEqual(second, self.request("/api/check?limit=1&offset=1"))
        self.assertNotEqual(first["issues"]["items"], second["issues"]["items"])
        self.request("/api/check?limit=500", status=400)
        self.request("/api/check?offset=-1", status=400)
        (self.root / "Sources" / "Originals").rmdir()
        self.assertEqual(self.cli("check", status=1)["status"], "incomplete")

    def test_manual_cli_and_browser_process_once_and_retry(self):
        area = storage.read_record(storage.create(self.root, "direction", "Личное"))["id"]
        quote = "Закрытая подробность встречи."
        item = self.incoming(quote)
        fact = {"kind": "fact", "direction": area, "visibility": "personal", "line": 1, "text": quote}
        payload = {**item, "person": {"new": True, "name": "Ирина"}, "facts": [fact]}
        saved = self.request("/api/inbox/process", payload)
        checked = saved["check"]
        self.assertEqual((checked["scope"], checked["status"]), ("source", "ok"))
        self.assertNotIn(quote, json.dumps(checked, ensure_ascii=False))
        repeated = self.cli("inbox-process", item["path"], "--revision", item["revision"],
                            "--new-person", "Ирина", "--fact", "fact", area, "personal", "1", quote)
        self.assertEqual(saved, repeated)
        self.assertEqual(self.cli("check")["issues"]["total"], 0)

    def test_followup_and_idempotent_batch_recheck_the_source(self):
        quote = "Перенесли встречу."
        item = self.incoming(quote)
        plan = {**item, "actions": [{"kind": "triage", "quote": quote, "line": 1,
                                    "reason": "К какому проекту относится встреча?"}]}
        saved = self.cli("inbox-apply", data=plan)
        self.assertEqual((saved["check"]["scope"], saved["check"]["status"]), ("source", "ok"))
        question, = saved["triage"]
        Batch(self.root).resolve(question["id"], "Это только контекст.")
        clarified = {**item, "triage_id": question["id"], "actions": [{"kind": "context", "quote": quote, "line": 1}]}
        answer = self.cli("inbox-apply", data=clarified)
        self.assertEqual(answer["check"]["status"], "ok")
        self.assertEqual(self.cli("inbox-apply", data=clarified), answer)
        self.assertFalse(Batch(self.root).pending())
        receipt = self.root / answer["processedPath"]
        damaged = receipt.read_text(encoding="utf-8").replace("type: processed_source", "type: note")
        receipt.write_text(damaged, encoding="utf-8")
        replay = self.cli("inbox-apply", data=clarified)
        self.assertEqual(replay["check"]["status"], "issues")
        self.assertEqual(replay["check"]["issues"]["items"][0]["path"], answer["processedPath"])
        self.assertEqual(receipt.read_text(encoding="utf-8"), damaged)

    def test_postcommit_check_problem_keeps_the_browser_receipt_and_skips_capture(self):
        check = {"status": "incomplete", "scope": "source", "checked": {"cards": 0, "receipts": 0, "originals": 0},
                 "issues": {"total": 1, "items": [], "truncated": True}}
        with patch("second_brain.vault_check.check_after_intake", return_value=check) as diagnose:
            area = storage.read_record(storage.create(self.root, "direction", "ИТ-проекты"))["id"]
            quote = "Передали документы."
            capture = self.request("/api/inbox/text", {"text": quote}, status=201)
            self.request("/api/inbox")
            diagnose.assert_not_called()
            result = self.request("/api/inbox/process", {
                "path": capture["path"], "revision": capture["revision"], "person": {"new": True, "name": "А"},
                "facts": [{"kind": "fact", "direction": area, "visibility": "shared", "line": 1, "text": quote}]})
            self.assertEqual(result["check"], check)
            diagnose.assert_called_once_with(self.root, capture["revision"])
            self.assertTrue((self.root / result["processedPath"]).is_file())
