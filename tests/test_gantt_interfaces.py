from datetime import date
import hashlib
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from examples.gantt_vault import generate
from second_brain.gantt import Gantt
from second_brain.gantt_feedback import GanttFeedback
from second_brain.planning import Planning
from second_brain.webserver import server


class GanttInterfaces(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = generate(Path(temp.name) / "Учебные планы")
        self.planning = Planning(self.root)
        self.plan = next(p for p in self.planning.plans() if p["title"] == "План недели")
        self.httpd = server(self.root, 0, mode="work")
        worker = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02})
        worker.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(self.httpd.shutdown)

    def request(self, path, data=None, *, extra=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            conn.request("GET" if data is None else "POST", path,
                         body=json.dumps(data) if data is not None else None,
                         headers={"Content-Type": "application/json", **(extra or {})})
            reply = conn.getresponse()
            raw = reply.read()
            value = json.loads(raw) if "application/json" in reply.getheader("Content-Type") else raw.decode()
            return reply.status, value, dict(reply.getheaders())
        finally:
            conn.close()

    def cli(self, *args, data=None, success=True):
        result = subprocess.run([sys.executable, "-m", "second_brain", "--vault", str(self.root), *args],
                                input=json.dumps(data) if data is not None else None, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return json.loads(result.stdout) if success else result.stderr

    def test_three_directions_browser_to_cli_and_back(self):
        status, plan, _ = self.request("/api/plan?id=" + self.plan["id"])
        self.assertEqual(status, 200)
        self.assertTrue(any(t.get("due") is None for t in plan["items"]))
        for title in ("Проверить восстановление базы", "Забрать дорожный набор", "Сравнить кедровые пробы"):
            with self.subTest(title=title):
                row = next(t for t in plan["items"] if t["title"] == title)
                key = row["id"]
                status, card, _ = self.request("/api/plan/task?id=" + key)
                self.assertEqual(status, 200)
                original = card["latest"][0]
                source = original["sources"][0]
                self.assertIn(source["quote"], self.request(source["href"].split("#")[0])[1])
                edit = {"id": key, "revision": card["revision"], "field": "latest",
                        "entryId": original["id"], "text": title + ". Уточнение владельца."}
                self.assertEqual(self.request("/api/plan/task/edit", edit)[0], 200)
                self.assertEqual(self.request("/api/plan/task/edit", edit)[0], 409)
                seen = self.cli("gantt-row", key, "--manual")
                self.assertEqual(seen["latest"][0]["text"], edit["text"])
                self.assertTrue(seen["latest"][0]["manual"])
                self.cli("gantt-edit", data={**edit, "revision": seen["revision"], "sources": [source]}, success=False)
                self.cli("gantt-edit", data={"id": key, "revision": seen["revision"], "field": "brief",
                                            "text": source["quote"], "sources": [source]})
                reread = self.request("/api/plan/task?id=" + key)[1]
                self.assertEqual(reread["brief"]["text"], source["quote"])
                self.assertEqual(reread["latest"][0]["text"], edit["text"])
                self.assertEqual(reread["brief"]["author"], "ai")
                self.assertIn(edit["text"], (self.root / row["path"]).read_text())
                if title == "Проверить восстановление базы":
                    accepted = {**edit, "revision": reread["revision"], "text": source["quote"], "sources": [source]}
                    self.cli("gantt-edit", "--accept-manual", reread["revision"], data=accepted)
                    event = self.request("/api/plan/task?id=" + key)[1]["latest"][0]
                    self.assertEqual((event["author"], event["acceptedByOwner"], event["manual"]), ("ai", True, True))

                preview = self.request("/api/plan/due/preview", {"id": key, "due": "2026-12-01"})[1]
                self.assertEqual(len(preview["plans"]), 2)
                saved = self.request("/api/plan/due/apply", {"id": key, "due": preview["after"],
                                                           "revision": preview["revision"]})
                self.assertEqual(saved[0], 200)
                for parent in preview["plans"]:
                    items = self.request("/api/plan?id=" + parent["id"])[1]["items"]
                    self.assertEqual(next(t for t in items if t["id"] == key)["due"], "2026-12-01")

    def test_review_reappears_after_native_edit_and_survives_ai_only_edits(self):
        feedback = GanttFeedback(self.root)
        for row in list(feedback.pending()):
            feedback.acknowledge(row["id"], row["token"])
        task = next(t for t in self.planning.tasks() if t["title"] == "Проверить восстановление базы")
        key = task["id"]
        card = Gantt(self.root).detail(key)
        file = self.root / task["path"]
        file.write_text(file.read_text().replace("\n" + card["brief"]["text"] + "\n", "\nСверил владелец.\n", 1))
        queue = self.cli("gantt-feedback", "--plan", self.plan["id"])
        self.assertEqual(queue["total"], 1)
        token = queue["items"][0]["token"]
        file.write_text(file.read_text().replace("Сверил владелец.", "Уточнил владелец.", 1))
        self.cli("gantt-reviewed", key, token, success=False)
        row, = self.cli("gantt-feedback")["items"]
        self.assertEqual((row["manualEntries"], row["firstReview"]), (1, False))
        self.assertTrue(self.cli("gantt-reviewed", key, row["token"])["changed"])
        self.assertFalse(self.cli("gantt-reviewed", key, row["token"])["changed"])
        card = Gantt(self.root).detail(key)
        event = card["latest"][0]
        self.cli("gantt-edit", data={"id": key, "revision": card["revision"], "field": "latest",
                                    "entryId": event["id"], "text": "Копия подготовлена.",
                                    "sources": event["sources"]})
        self.assertEqual(self.cli("gantt-feedback")["items"], [])

    def test_vault_binding_embed_and_mutation_guards(self):
        identity = self.request("/api/planning-vault")[1]
        token = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()
        self.assertEqual(identity["binding"], token)
        self.assertEqual(self.request("/?embed=1")[0], 409)
        status, _, headers = self.request("/?embed=1&binding=" + token)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Security-Policy"), "frame-ancestors app://obsidian.md")
        self.assertEqual(self.request("/")[2].get("X-Frame-Options"), "DENY")
        self.assertEqual(self.request("/api/plan/task/edit", {}, extra={"X-Brain-Vault": "other"})[0], 409)
        self.assertEqual(self.request("/api/plans", extra={"Origin": "https://example.org"})[0], 403)
        self.assertEqual(self.request("/api/plan/task/edit", {"actor": "ai"})[0], 400)
        self.assertEqual(self.request("/api/plan/due/preview", {})[0], 400)
        task = next(t for t in self.planning.tasks() if t.get("start"))
        self.assertEqual(self.request("/api/plan/due/preview", {"id": task["id"], "due": "2026-11-01"})[0], 400)
        self.assertEqual(self.request("/api/plan/due/preview", {"id": task["id"], "due": None})[0], 200)

    def test_older_events_page_matches_visible_card_and_revision(self):
        task = next(t for t in self.planning.tasks() if t["title"] == "Проверить восстановление базы")
        gantt = Gantt(self.root)
        revision = task["revision"]
        for n in range(13):
            row = gantt.edit({"id": task["id"], "revision": revision, "actor": "manual",
                              "field": "latest", "text": f"Запись {n}", "date": f"2026-10-{n+1:02}"})
            revision = row["revision"]
        url = "/api/plan/events?id=" + task["id"]
        first = self.request(url)[1]
        second = self.request(url + "&offset=12")[1]
        self.assertEqual(first["items"], self.request("/api/plan/task?id=" + task["id"])[1]["latest"])
        self.assertTrue(first["truncated"])
        self.assertEqual(second["revision"], revision)
        self.assertEqual([e["text"] for e in second["items"]], ["Запись 1", "Запись 0"])
        self.assertEqual((second["total"], second["truncated"]), (14, False))

    def test_agreement_forecast_and_completion_from_http_and_markdown(self):
        def field_values(row):
            return row["due"], row["forecast"], row["completed"]
        day = min(date(2026, 11, 22), date.today()).isoformat()
        tasks = {t["title"]: t for t in self.planning.tasks()}
        feedback = GanttFeedback(self.root)
        for row in list(feedback.pending()):
            feedback.acknowledge(row["id"], row["token"])
        for title, predicted in (("Проверить восстановление базы", "2026-11-27"),
                                 ("Забрать дорожный набор", "2026-11-28"),
                                 ("Сравнить кедровые пробы", "2026-11-26")):
            with self.subTest(title=title):
                task = tasks[title]
                key = task["id"]
                parents = self.planning.plans_for_task(key)
                self.assertEqual(len(parents), 2)
                plan_files = {self.root / p["path"]: (self.root / p["path"]).read_bytes() for p in parents}
                url = "/api/plan/schedule/"
                edit = {"id": key, "field": "forecast", "value": predicted, "reason": "Новая оценка владельца"}
                status, preview, _ = self.request(url + "preview", edit)
                self.assertEqual(status, 200)
                self.assertEqual((preview["before"], preview["after"]), (task["forecast"], predicted))
                self.assertEqual(field_values(self.planning.task(key)), field_values(task))
                self.assertEqual(self.request(url + "apply", {**edit, "revision": preview["revision"]})[0], 200)
                self.assertEqual(self.request(url + "preview", {"id": key, "field": "completed", "value": day})[0], 400)
                # A separately confirmed result can also precede the planned start.
                fact = {"id": key, "field": "completed", "value": day, "reason": "Владелец сверил результат и дату"}
                checked = self.request(url + "preview", fact)[1]
                saved = self.request(url + "apply", {**fact, "revision": checked["revision"]})
                self.assertEqual(saved[0], 200)
                for p in parents:
                    rows = self.request("/api/plan?id=" + p["id"])[1]["items"]
                    self.assertEqual(field_values(next(r for r in rows if r["id"] == key)),
                                     (task["due"], predicted, fact["value"]))
                self.assertEqual(field_values(self.cli("gantt-row", key)), (task["due"], predicted, fact["value"]))
                detail = self.request("/api/plan/task?id=" + key)[1]
                self.assertEqual([c["field"] for c in detail["scheduleHistory"][:2]], ["completed", "forecast"])
                self.assertEqual(detail["scheduleHistory"][0]["actor"], "manual")
                for file, original in plan_files.items():
                    self.assertEqual(file.read_bytes(), original)

                change = next(r for r in feedback.pending() if r["id"] == key)
                self.assertEqual(field_values(change), field_values(detail))
                feedback.acknowledge(key, change["token"])
                file = self.root / task["path"]
                file.write_text(file.read_text().replace('forecast: "' + predicted + '"', 'forecast: "2026-12-04"', 1))
                native = next(r for r in feedback.pending() if r["id"] == key)
                self.assertEqual(native["forecast"], "2026-12-04")
                self.assertNotEqual(native["token"], change["token"])
