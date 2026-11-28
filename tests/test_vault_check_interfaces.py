from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from second_brain import inbox, storage
from second_brain.batch import Batch
from second_brain.vault_check import check_after_intake, check_vault
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

    def test_snapshot_remains_pageable_across_cli_process_and_http_after_a_repair(self):
        cache = str(self.root.parent / "cache")
        with patch.dict(os.environ, {"XDG_CACHE_HOME": cache, "LOCALAPPDATA": cache}):
            path = storage.create(self.root, "direction", "Проверка")
            copy = path.with_name("Копия.md")
            copy.write_bytes(path.read_bytes())
            content = b"private original, not in diagnostic cache"
            identifier = hashlib.sha256(content).hexdigest()
            (self.root / "Sources/Originals" / (identifier + ".txt")).write_bytes(content)
            with patch.object(inbox, "_file_digest", wraps=inbox._file_digest) as digest:
                first = self.request("/api/check?snapshot=new&limit=1")
                digest.assert_called_once()
                copy.unlink()
                token = first["snapshot"]
                self.assertTrue(datetime.fromisoformat(first["checkedAt"]).tzinfo)
                second = self.cli("check", "--snapshot", token, "--limit", "1", "--offset", "1", status=1)
                self.assertEqual(second, self.request(f"/api/check?snapshot={token}&limit=1&offset=1"))
                self.assertEqual((second["snapshot"], second["checkedAt"], second["issues"]["total"]),
                                 (token, first["checkedAt"], 2))
                self.assertNotEqual(first["issues"]["items"], second["issues"]["items"])
                fresh = self.request("/api/check?snapshot=new")
                self.assertEqual(fresh["status"], "ok")
                self.assertNotEqual(fresh["snapshot"], token)
                self.assertEqual(digest.call_count, 2)
                self.assertEqual(first, self.request(f"/api/check?snapshot={token}&limit=1"))
                self.assertEqual(digest.call_count, 2)
            for file in Path(cache).rglob("*"):
                if file.is_file():
                    self.assertNotIn(content, file.read_bytes())
                    if os.name != "nt":
                        self.assertEqual(file.stat().st_mode & 0o077, 0)

    def test_snapshot_retention_vault_identity_and_concurrent_runs(self):
        cache = str(self.root.parent / "cache")
        other = storage.initialize(self.root.parent / "Other")
        with patch.dict(os.environ, {"XDG_CACHE_HOME": cache, "LOCALAPPDATA": cache}):
            oldest = self.cli("check", "--snapshot", "new")["snapshot"]
            with self.assertRaisesRegex(ValueError, "недоступна"):
                check_vault(other, snapshot=oldest)
            other_result = check_vault(other, snapshot="new")
            with ThreadPoolExecutor(4) as executor:
                runs = list(executor.map(lambda _: check_vault(self.root, snapshot="new"), range(8)))
            self.assertEqual(len({run["snapshot"] for run in runs}), 8)
            for run in runs:
                self.assertEqual(check_vault(self.root, snapshot=run["snapshot"]), run)
            self.request("/api/check?snapshot=" + oldest, status=400)
            self.request("/api/check?snapshot=..", status=400)
            self.assertEqual(check_vault(other, snapshot=other_result["snapshot"]), other_result)

    def test_unwritable_or_in_vault_cache_still_returns_the_completed_check(self):
        path = storage.create(self.root, "direction", "Дача")
        path.with_name("Копия.md").write_bytes(path.read_bytes())
        expected = self.cli("check", "--limit", "1", status=1)
        for cache in (self.root, self.root.parent / "cache"):
            with self.subTest(cache=cache), patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache), "LOCALAPPDATA": str(cache)}):
                with patch("second_brain.check_snapshots.sqlite3.connect", side_effect=sqlite3.OperationalError()):
                    result = self.request("/api/check?snapshot=new&limit=1")
                self.assertEqual({key: result[key] for key in expected}, expected)
                self.assertIsNone(result["snapshot"])
                self.assertTrue(result["snapshotWarning"])
                self.assertTrue(datetime.fromisoformat(result["checkedAt"]).tzinfo)
        self.assertFalse((self.root / "second-brain").exists())

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

    def test_queue_remaining_from_the_form_then_read_and_finish_from_cli(self):
        direction = storage.read_record(storage.create(self.root, "direction", "Личное"))["id"]
        private = "Личное наблюдение."
        item = self.incoming(private + "\nСверить дату встречи.\nВстреча назначена.")
        fact = {"kind": "observation", "direction": direction, "visibility": "personal", "line": 1, "text": private}
        request = {**item, "person": {"new": True, "name": "Ирина"}, "facts": [fact], "remaining": "queue"}
        saved = self.request("/api/inbox/process", request)
        args = ("inbox-process", item["path"], "--revision", item["revision"], "--new-person", "Ирина",
                "--fact", "observation", direction, "personal", "1", private)
        self.assertEqual(saved, self.cli(*args, "--remaining", "queue"))
        self.assertTrue(self.request("/api/inbox")["items"][0]["remainingQueued"])
        self.assertEqual(self.cli("inbox-new")["items"][0]["remainingLines"], 2)
        source = self.cli("original", item["revision"], "--remaining", "--limit", "1")
        self.assertEqual((source["lines"], source["nextStart"]), ([{"n": 2, "text": "Сверить дату встречи."}], 3))
        self.assertNotIn(private, json.dumps(source, ensure_ascii=False))
        self.assertFalse(self.cli(*args, "--remaining", "finish")["remainingQueued"])
        self.assertEqual(self.cli("inbox-new")["total"], 0)
        self.assertEqual(self.cli("check")["status"], "ok")

    def test_damaged_manual_remainder_is_reported_by_source_and_full_checks(self):
        direction = storage.read_record(storage.create(self.root, "direction", "Личное"))["id"]
        item = self.incoming("Личная строка.\nОстаток.")
        saved = self.request("/api/inbox/process", {**item, "person": {"new": True, "name": "Ирина"},
            "facts": [{"kind": "fact", "direction": direction, "visibility": "personal", "line": 1,
                       "text": "Личная строка."}], "remaining": "queue"})
        path = self.root / saved["processedPath"]
        initial = path.read_text()
        for broken in ('"lines":[200]', '"lines":"?"'):
            with self.subTest(broken=broken):
                path.write_text(initial.replace('"lines":[2]', broken))
                for check in (check_after_intake(self.root, item["revision"]), self.cli("check", status=1)):
                    self.assertEqual(check["status"], "issues")
                    problem, = check["issues"]["items"]
                    self.assertEqual((problem["code"], problem["path"]), ("receipt_remaining", saved["processedPath"]))

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
