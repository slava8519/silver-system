import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from second_brain import storage
from second_brain.__main__ import command_line, run
from second_brain.webserver import server


class CycleInterfaces(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "Vault"
        storage.initialize(self.root)
        self.parser = command_line()
        self.httpd = server(self.root, 0, mode="work")
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.httpd.shutdown)
        self.plan = self.cli("plan-create", "Цикл")["id"]
        self.task = self.cli("task-create", "Сверить образцы", "--due", "2026-11-27")
        self.cli("plan-add", self.plan, self.task["id"])

    def cli(self, *args):
        return run(self.parser.parse_args(["--vault", str(self.root), *args]))

    def http(self, path, data=None, status=200):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            connection.request("GET" if data is None else "POST", "/api/plan" + path,
                               body=None if data is None else json.dumps(data),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.status, status, result)
            return result
        finally:
            connection.close()

    def test_archived_gantt_links_to_its_saved_weekly_review(self):
        preview = self.cli("plan-close", self.plan)
        self.cli("plan-close", self.plan, "--apply", preview["revision"])
        plan_url = "?id=" + self.plan
        self.assertEqual(self.http(plan_url)["weeklyReviews"], [])
        report = self.cli("weekly-review", self.plan, "--save")
        renamed = (self.root / report["path"]).with_name("Мой выпуск.md")
        (self.root / report["path"]).rename(renamed)
        issues = self.http(plan_url)["weeklyReviews"]
        self.assertEqual([(issue["id"], issue["path"]) for issue in issues],
                         [(report["id"], renamed.relative_to(self.root).as_posix())])
        other = self.cli("plan-create", "Другой цикл")["id"]
        self.cli("plan-close", other, "--apply", self.cli("plan-close", other)["revision"])
        self.assertEqual(self.http("?id=" + other)["weeklyReviews"], [])

    def test_close_through_http_read_snapshot_and_live_card_through_cli(self):
        m = self.cli("task-create", "Дегустация", "--kind", "milestone")
        self.cli("plan-add", self.plan, m["id"])
        preview = self.cli("plan-close", self.plan, "--limit", "1")
        self.assertEqual((preview["total"], preview["truncated"], len(preview["items"])), (2, True, 1))
        next_page = self.cli("plan-close", self.plan, "--offset", "1", "--limit", "1")
        self.assertEqual((next_page["revision"], next_page["items"][0]["id"]), (preview["revision"], m["id"]))
        close = {"id": self.plan, "revision": preview["revision"]}
        closed = self.http("/close/apply", close)
        self.assertEqual(closed["status"], "closed")
        self.assertTrue(self.http("/close/apply", close)["replayed"])
        active = self.cli("plan-create", "Следующий цикл")["id"]
        self.cli("plan-add", active, self.task["id"])
        path = self.root / self.task["path"]
        renamed = path.with_name("Слепая сверка.md")
        path.rename(renamed)
        forecast = ("task-schedule", self.task["id"], "--forecast", "2026-12-01")
        self.cli(*forecast, "--apply", self.cli(*forecast)["revision"])
        historical = self.cli("plan-show", self.plan, "--task", self.task["id"])
        live = self.cli("plan-show", active, "--task", self.task["id"])
        self.assertEqual((live["status"], live["items"][0]["forecast"]), ("active", "2026-12-01"))
        self.assertEqual((historical["items"][0]["forecast"], historical["items"][0]["due"]),
                         (None, "2026-11-27"))
        self.assertGreater(historical["items"][0]["snapshotLine"], 0)
        self.assertEqual(self.http("?id=" + self.plan)["items"][0]["path"], self.task["path"])
        self.assertEqual(self.cli("task-schedule", self.task["id"])["forecast"], "2026-12-01")
        current = self.http("/task-location?id=" + self.task["id"])
        self.assertEqual(current["path"], renamed.relative_to(self.root).as_posix())
        self.assertEqual([plan["id"] for plan in current["plans"]], [active])

    def test_stale_row_editor_and_milestone_creation_after_close(self):
        active = self.cli("plan-create", "Работа продолжается")["id"]
        self.cli("plan-add", active, self.task["id"])
        card = self.http("/task?id=" + self.task["id"])
        pending = {"planId": self.plan, "id": self.task["id"], "revision": card["revision"],
                   "field": "brief", "text": "Вторая сверка"}
        preview = self.http("/close/preview", {"id": self.plan})
        self.cli("plan-close", self.plan, "--apply", preview["revision"])
        files = {p: p.read_bytes() for p in self.root.rglob("*.md")}
        self.http("/task/edit", pending, 409)
        self.http("/milestone", {"planId": self.plan, "title": "Старая форма", "requestId": "closed"}, 409)
        self.assertEqual(files, {p: p.read_bytes() for p in self.root.rglob("*.md")})
        self.http("/task/edit", {**pending, "planId": active})
        self.assertEqual(self.http("/task?id=" + self.task["id"])["brief"]["text"], "Вторая сверка")

    def test_stale_closure_revision_never_closes_or_replaces_snapshot(self):
        preview = self.http("/close/preview", {"id": self.plan})
        more = self.cli("task-create", "Уточнение")
        self.cli("plan-add", self.plan, more["id"])
        self.http("/close/apply", {"id": self.plan, "revision": preview["revision"]}, 409)
        self.http("/close/apply", {"id": self.plan}, 400)
        self.http("/close/preview", {"id": self.plan, "revision": preview["revision"]}, 400)
        self.assertEqual(self.cli("plan-list", "--match", "Цикл")["items"][0]["status"], "active")
        self.http("/close/apply", {"id": self.plan, "revision": self.cli("plan-close", self.plan)["revision"]})
        closed = self.cli("plan-show", self.plan)
        self.http("/close/apply", {"id": self.plan, "revision": preview["revision"]}, 409)
        self.assertEqual(self.cli("plan-show", self.plan), closed)
