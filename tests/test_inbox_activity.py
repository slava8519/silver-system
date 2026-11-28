from datetime import datetime, timedelta, timezone
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from second_brain import inbox_activity as activity, storage
from second_brain.batch import Batch
from second_brain.capture import Capture
from second_brain.webserver import server


class InboxActivityTests(unittest.TestCase):
    def setUp(self):
        vault = TemporaryDirectory()
        self.addCleanup(vault.cleanup)
        self.root = storage.initialize(Path(vault.name))
        self.stamp = datetime(2026, 5, 3, 0, 10, tzinfo=timezone(timedelta(hours=3)))
        self.lookup = patch.object(activity, "_folder_dates", return_value={}).start()
        self.addCleanup(patch.stopall)

    def write(self, name, body, *, stamp=None):
        target = self.root / "Inbox" / name
        target.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        if stamp:
            activity.record_arrival(self.root, "Inbox/" + name, digest, received_at=stamp)
        return target, digest

    def test_day_kind_examples_and_source_dates_are_independent(self):
        name = "Экспорт.md"
        body = 'Транскрипция записи "Тесты за 3 марта 2026" 29 апреля 2026 г.\nДата новой задачи 1 декабря 2026 года.'
        meeting, _ = self.write(name, body.encode(), stamp=self.stamp)
        self.write("Рецепт жасмина.txt", b"recipe", stamp=self.stamp - timedelta(days=1))
        self.write("Тред о встрече.md", b"thread", stamp=self.stamp)
        self.write("Отчёт.pdf", b"report", stamp=self.stamp)
        self.write("скрин.png", b"image", stamp=self.stamp)
        self.write("Идеи.txt", "30 апреля 2026 г.".encode())
        self.write("Запасной файл.pdf", b"attachment")
        self.write("Ещё копия.txt", body.encode())  # one capture under a different name
        self.write("Расшифровка.txt", "Транскрипция записи 31 апреля 2026 г.".encode())
        page = activity.inbox_activity(self.root, limit=2)
        self.assertEqual(([i["day"] for i in page["items"]], page["truncated"]), (["2026-05-03", "2026-05-02"], True))
        self.assertEqual(page["items"][0]["kinds"], {"meeting": 1, "thread": 1, "report": 1, "image": 1})
        latest = page["latestMeeting"]
        self.assertEqual([latest[k] for k in ("day", "meetingDate", "path", "dateBasis")],
                         ["2026-05-03", "2026-04-29", meeting.relative_to(self.root).as_posix(), "capture"])
        self.assertTrue(latest["href"].startswith("#inbox?path="))
        unknown = activity.inbox_activity(self.root, offset=2)["items"][0]
        self.assertEqual((unknown["day"], unknown["kinds"]), (None, {"other": 1, "attachment": 1, "meeting": 1}))
        self.assertEqual(activity.inbox_activity(self.root, kind="thread", day="2026-05-03")["total"], 1)
        self.assertEqual(activity.inbox_activity(self.root, day="undated", limit=1)["total"], 3)

    def test_historical_receipt_after_move_carries_proven_folder_date_and_stable_original_link(self):
        target, digest = self.write("Протокол встречи.txt", b"Summary")
        self.lookup.return_value = {target: self.stamp}
        initial = activity.inbox_activity(self.root, day="2026-05-03")["items"][0]
        self.assertEqual((initial["dateBasis"], initial["dateSource"]), ("folder", "Inbox/" + target.name))
        result = Batch(self.root).apply({"path": initial["path"], "revision": digest,
                                         "actions": [{"kind": "context", "line": 1, "quote": "Summary"}]})
        self.assertFalse(target.exists())
        self.lookup.return_value.clear()
        current = activity.inbox_activity(self.root, day="2026-05-03")["items"][0]
        self.assertEqual((current["path"], current["href"], current["dateBasis"]),
                         (result["source"]["path"], "/original/" + digest, "folder"))
        # Previously archived materials need no filesystem estimates after moving machines.
        self.assertEqual(current["dateSource"], "Sources/Arrivals/" + digest + ".md")
        self.assertTrue(Capture(self.root).save_file("новое имя.txt", b"Summary")["alreadyProcessed"])
        self.assertEqual(activity.inbox_activity(self.root)["items"][0]["count"], 1)

    def test_old_receipt_ignores_date_of_later_duplicate(self):
        target, digest = self.write("Первое сообщение.txt", b"Work")
        result = Batch(self.root).apply({"path": "Inbox/" + target.name, "revision": digest,
                                        "actions": [{"kind": "context", "line": 1, "quote": "Work"}]})
        self.assertFalse((self.root / "Sources/Arrivals" / (digest + ".md")).exists())
        copy, _ = self.write("Переименованный повтор.txt", b"Work")
        self.lookup.return_value = {copy: self.stamp}
        row = activity.inbox_activity(self.root, day="undated")["items"][0]
        self.assertEqual((row["title"], row["path"]), (target.name, result["source"]["path"]))
        activity.remember_inbox_date(self.root, copy, digest)
        self.assertFalse((self.root / "Sources/Arrivals" / (digest + ".md")).exists())
        copy.rename(target)
        self.lookup.return_value = {target: datetime.now().astimezone() + timedelta(days=1)}
        self.assertEqual(activity.inbox_activity(self.root, day="undated")["total"], 1)

    def test_http_cli_and_capture_share_dates_paging_and_validation(self):
        captured = Capture(self.root).save_file("Рецепт нарцисса.txt", b"new")
        date_path = self.root / "Sources/Arrivals" / (captured["revision"] + ".md")
        before = date_path.read_bytes()
        Capture(self.root).save_file("Повтор рецепта.txt", b"new")
        self.assertEqual(before, date_path.read_bytes())
        http = server(self.root, 0, mode="work")
        thread = Thread(target=http.serve_forever, kwargs={"poll_interval": .01})
        thread.start()
        self.addCleanup(http.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(http.shutdown)
        conn = HTTPConnection("127.0.0.1", http.server_port)
        self.addCleanup(conn.close)
        conn.request("GET", "/api/inbox/arrivals?kind=recipe&limit=1")
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        page = json.load(response)
        cli = subprocess.check_output([sys.executable, "-m", "second_brain", "--vault", str(self.root),
                                       "overview", "--arrivals", "--kind", "recipe", "--limit", "1"])
        self.assertEqual(json.loads(cli), page)
        self.assertEqual(page["items"][0]["kinds"], {"recipe": 1})
        conn.request("GET", "/api/inbox/arrivals?day=2026-99-01")
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
