import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from second_brain import storage
from second_brain.__main__ import command_line, run
from second_brain.webserver import server


class DependencyInterfaces(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "Vault"
        storage.initialize(self.root)
        self.parser = command_line()
        self.httpd = server(self.root, 0, mode="work")
        worker = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(self.httpd.shutdown)

    def cli(self, *args):
        return run(self.parser.parse_args(["--vault", str(self.root), *args]))

    def request(self, path, body=None, status=200):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            conn.request("GET" if body is None else "POST", "/api/plan" + path,
                         body=None if body is None else json.dumps(body),
                         headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, status, payload)
            return payload
        finally:
            conn.close()

    def test_shared_milestone_scenario_and_bounded_cli(self):
        plans = [self.cli("plan-create", title) for title in ("Поставка", "Неделя")]
        work = self.cli("task-create", "Получить партию", "--due", "2026-11-25")
        form = {"planId": plans[0]["id"], "title": "Приёмка", "due": "2026-11-29", "requestId": "accept"}
        milestone = self.request("/milestone", form)
        self.assertEqual(self.request("/milestone", form)["id"], milestone["id"])
        self.assertEqual(milestone["kind"], "milestone")
        self.cli("plan-add", plans[1]["id"], milestone["id"])
        for plan in plans:
            self.cli("plan-add", plan["id"], work["id"])
        graph = self.cli("task-dependency", milestone["id"], "--add", work["id"])
        self.cli("task-dependency", milestone["id"], "--add", work["id"])
        self.assertEqual([r["id"] for r in graph["predecessors"]], [work["id"]])
        self.assertEqual(self.request("/dependencies?id=" + work["id"])["successors"][0]["kind"], "milestone")
        plans_before = {self.root / p["path"]: (self.root / p["path"]).read_bytes() for p in plans}
        files_before = {f: f.read_bytes() for f in self.root.rglob("*.md")}
        scenario = {"id": work["id"], "forecast": "2026-11-30"}
        preview = self.request("/scenario/preview", scenario)
        limited = self.cli("task-scenario", work["id"], "--forecast", scenario["forecast"], "--limit", "1")
        self.assertEqual((limited["truncated"], limited["rowsTotal"], len(limited["rows"])), (True, 2, 1))
        after = {r["id"]: (r["after"], r["slackDays"]) for r in preview["rows"]}
        self.assertEqual(after, {work["id"]: ("2026-11-30", -5), milestone["id"]: ("2026-11-30", -1)})
        self.assertEqual({f: f.read_bytes() for f in self.root.rglob("*.md")}, files_before)
        self.request("/scenario/apply", {**scenario, "revision": preview["revision"]})
        for plan in plans:
            for card in (work, milestone):
                row, = self.cli("plan-show", plan["id"], "--task", card["id"])["items"]
                self.assertEqual((row["due"], row["forecast"], row["completed"], row["kind"]),
                                 (card["due"], scenario["forecast"], None, card["kind"]))
        self.assertEqual({f: f.read_bytes() for f in plans_before}, plans_before)
        self.assertEqual(self.cli("task-schedule", milestone["id"], "--history")["items"][0]["actor"], "manual")
        self.request("/dependency", {"id": milestone["id"], "predecessor": work["id"], "action": "remove"})
        self.request("/dependency", {"id": milestone["id"], "predecessor": work["id"], "action": "remove"})
        self.assertEqual(self.cli("task-dependency", work["id"])["successorsTotal"], 0)
        history = self.cli("task-dependency", milestone["id"], "--history")
        self.assertEqual([(h["action"], h["actor"]) for h in history["items"]], [("remove", "manual"), ("add", "ai")])
        for change in history["items"]:
            self.assertEqual((change["predecessor"], change["predecessorTitle"], change["path"]),
                             (work["id"], work["title"], milestone["path"]))
            line = (self.root / change["path"]).read_text().splitlines()[change["line"] - 1]
            self.assertIn(work["title"], line)

    def test_direction_cycle_and_request_errors(self):
        ids = [self.cli("task-create", title, "--kind", "milestone")["id"] for title in ("Первая", "Вторая")]
        self.request("/dependency", {"id": ids[1], "predecessor": ids[0], "action": "add"})
        error = self.request("/dependency", {"id": ids[0], "predecessor": ids[1], "action": "add"}, status=400)
        self.assertIn("цикл", error["error"].lower())
        self.assertEqual(self.cli("task-dependency", ids[0])["predecessors"], [])
        self.assertEqual(self.cli("task-dependency", ids[0], "--history")["total"], 0)
        page = self.request("/tasks?limit=1")
        self.assertEqual((page["truncated"], page["total"], page["items"][0]["kind"]), (True, 2, "milestone"))
        self.request("/tasks?limit=21", status=400)
        self.request("/scenario/preview", {"id": ids[0], "forecast": None}, status=400)
        self.request("/scenario/preview", {"id": ids[0], "forecast": "2026-11-30", "actor": "ai"}, status=400)
        self.request("/milestone", {"planId": "missing", "title": "Ещё", "requestId": "third"}, status=404)
        self.assertEqual(self.cli("task-list")["total"], 2)

    def test_stale_web_scenario_and_cli_apply_with_many_plans(self):
        work, milestone = [self.cli("task-create", title, "--kind", kind)["id"]
                           for title, kind in (("Проверить цвет", "task"), ("Цвет согласован", "milestone"))]
        self.cli("task-dependency", milestone, "--add", work)
        payload = {"id": work, "forecast": "2026-12-04"}
        old = self.request("/scenario/preview", payload)
        for day in range(6):
            plan = self.cli("plan-create", str(day))
            self.cli("plan-add", plan["id"], milestone)
        self.request("/scenario/apply", {**payload, "revision": old["revision"]}, status=409)
        self.assertIsNone(self.cli("task-schedule", work)["forecast"])
        command = ("task-scenario", work, "--forecast", payload["forecast"])
        preview = self.cli(*command)
        milestone_row, = (r for r in preview["rows"] if r["id"] == milestone)
        self.assertEqual((milestone_row["plansTruncated"], len(milestone_row["plans"]), milestone_row["plansTotal"]),
                         (True, 5, 6))
        next_page = self.cli(*command, "--plans-offset", "5")
        last, = (r for r in next_page["rows"] if r["id"] == milestone)
        self.assertEqual((len(last["plans"]), last["plansTruncated"], next_page["revision"]), (1, False, preview["revision"]))
        self.cli(*command, "--apply", preview["revision"])
        self.assertTrue(self.cli(*command, "--apply", preview["revision"])["replayed"])
        actual = self.request("/task?id=" + milestone)
        self.assertEqual((actual["forecast"], actual["completed"], actual["scheduleHistory"][0]["actor"]),
                         (payload["forecast"], None, "ai"))
