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
from second_brain import storage
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

    def test_full_workspace_embed_pages_and_inbox_share_the_vault_binding(self):
        identity = self.request("/api/planning-vault")[1]
        self.assertTrue(identity["workspaceEmbed"])
        binding = identity["binding"]
        query = "embed=workspace&binding=" + binding
        task = next(t for t in self.planning.tasks() if t["title"] == "Проверить восстановление базы")
        href = Gantt(self.root).detail(task["id"])["latest"][0]["sources"][0]["href"].split("#")[0]
        for page, expected in (("/", 200), ("/about.html", 200), (href, 200), ("/source/missing", 404),
                               ("/evidence/missing/missing", 404)):
            with self.subTest(page=page):
                url = page + ("&" if "?" in page else "?") + query
                status, html, headers = self.request(url)
                self.assertEqual(status, expected)
                self.assertEqual(headers.get("Content-Security-Policy"), "frame-ancestors app://obsidian.md")
                if page.startswith(("/source/", "/evidence/")):
                    self.assertIn("/obsidian-workspace.js", html)
                self.assertEqual(self.request(url.replace(binding, "other"))[0], 409)
                self.assertEqual(self.request(page)[2].get("X-Frame-Options"), "DENY")
        self.assertEqual(self.request("/workspace.html?" + query)[2].get("X-Frame-Options"), "DENY")
        content = {"title": "Работа", "text": "Из Obsidian"}
        self.assertEqual(self.request("/api/inbox/text", content, extra={"X-Brain-Vault": "wrong"})[0], 409)
        self.assertEqual(self.request("/api/inbox/text", content, extra={"X-Brain-Vault": binding})[0], 201)

    def test_work_created_in_project_is_live_without_a_period(self):
        project_path = storage.create(self.root, "project", "Длинный проект", due="2027-12-31")
        project = storage.read_record(project_path)["id"]
        scope = "/api/plan/project-gantt?id=" + project
        self.assertEqual(self.request(scope)[1]["items"], [])
        self.assertIn(project, [p["id"] for p in self.request("/api/plan/activity")[1]["projects"]])
        plans_before = self.planning.plans()
        rows = []
        for kind in ("task", "milestone"):
            payload = {"title": "Работа " + kind, "kind": kind, "projectId": project, "requestId": "year-" + kind}
            code, row, _ = self.request("/api/plan/project-work", payload)
            self.assertEqual(code, 200, row)
            self.assertEqual(self.request("/api/plan/project-work", payload)[1]["id"], row["id"])
            rows.append(row["id"])
        self.assertEqual({r["id"] for r in self.request(scope)[1]["items"]}, set(rows))
        self.assertEqual(self.planning.plans(), plans_before)
        update = {"projectId": project, "id": rows[0], "due": "2027-11-01"}
        preview = self.request("/api/plan/due/preview", update)[1]
        self.assertEqual(preview["plans"], [])
        self.assertEqual(self.request("/api/plan/due/apply", {**update, "revision": preview["revision"]})[0], 200)
        self.assertEqual(self.planning.task(rows[0])["due"], update["due"])
        outside = self.planning.create_task("Другая работа")
        self.assertEqual(self.request("/api/plan/due/preview", {**update, "id": outside["id"]})[0], 409)
        project_path.unlink()
        self.assertEqual(self.request("/api/plan/due/preview", update)[0], 409)
        self.assertEqual(self.request(scope)[0], 404)

    def test_project_membership_feedback_survives_closure_and_stale_acknowledgement(self):
        plan = self.planning.create_plan("Годовой портфель")
        pid = plan["id"]
        project = storage.read_record(storage.create(self.root, "project", "Переезд"))["id"]
        chosen = self.planning.create_task("Выбрать одно помещение", project=project)
        self.planning.create_task("Принять ремонт", project=project)
        endpoint = "/api/plan/project-membership"
        write = {"id":pid, "projectId":project, "present":True, "revision":plan["revision"]}
        self.assertEqual(self.request(endpoint, {**write, "actor":"ai"})[0], 400)
        self.assertEqual(self.request(endpoint, {**write, "present":"true"})[0], 400)
        status, included, _ = self.request(endpoint, write)
        self.assertEqual(status, 200, included)
        self.assertEqual(self.planning.view(pid)["items"], [])
        link = self.cli("activity-list", "--view", "projects", "--plan", pid)["items"][0]["planLink"]
        self.assertEqual((link["explicitlyIncluded"], link["taskCount"]), (True, 0))
        task = {"id":pid, "projectId":project, "taskId":chosen["id"], "revision":included["revision"]}
        status, added, _ = self.request("/api/plan/project-task", task)
        self.assertEqual(status, 200, added)
        self.assertEqual(self.request("/api/plan/project-task", {**task, "revision":added["revision"]})[1]["changed"], False)
        self.assertEqual([r["id"] for r in self.planning.view(pid)["items"]], [chosen["id"]])
        self.assertEqual(self.request(endpoint, {**write, "present":False})[0], 409)
        pending = self.cli("plan-project", pid, "--history", "--pending", "--limit", "1")
        self.assertTrue(pending["truncated"])
        event, = pending["items"]
        self.assertEqual((event["action"], event["actor"], event["taskId"]), ("add-task", "manual", chosen["id"]))
        self.assertIn("Выбрать одно помещение", (self.root / event["path"]).read_text().splitlines()[event["line"]-1])
        history = self.request("/api/plan/project-history?id=" + pid + "&pending=1&offset=1")[1]
        self.assertEqual(history["items"][0]["action"], "include")
        self.assertEqual(history["token"], pending["token"])
        self.assertTrue(self.request(endpoint, {**write, "revision":added["revision"], "present":False})[1]["changed"])
        self.assertEqual(self.request("/api/plan/reviewed", {"id":pid, "token":pending["token"]})[0], 409)
        queue = self.request("/api/plan/feedback?planId=" + pid)[1]["items"]
        plan_row = next(row for row in queue if row["kind"] == "plan-projects")
        self.assertEqual(plan_row["manualEntries"], 3)
        self.assertNotEqual(plan_row["token"], pending["token"])
        ai = self.cli("plan-project", pid, "--include", project, "--revision", self.planning.view(pid)["revision"])
        self.assertTrue(ai["changed"])
        self.assertEqual(self.cli("plan-project", pid, "--history")["items"][0]["actor"], "ai")
        close = self.cli("plan-close", pid)
        self.cli("plan-close", pid, "--apply", close["revision"])
        row, = self.cli("gantt-feedback", "--plan", pid)["items"]
        self.assertEqual((row["token"], row["manualEntries"]), (plan_row["token"], 3))
        file = self.root / plan["path"]
        snapshot = file.read_bytes()
        self.assertTrue(self.cli("gantt-reviewed", pid, row["token"])["changed"])
        self.assertFalse(self.cli("gantt-reviewed", pid, row["token"])["changed"])
        self.assertEqual(self.cli("gantt-feedback", "--plan", pid)["items"], [])
        self.assertEqual(file.read_bytes(), snapshot)

    def test_only_new_web_decisions_reenter_the_plan_queue(self):
        plan = self.planning.create_plan("План инвентаризации")
        pid = plan["id"]
        project = storage.read_record(storage.create(self.root, "project", "Учёт"))["id"]
        task = self.planning.create_task("Сверить остаток", project=project)
        write = {"id":pid, "projectId":project, "present":True, "revision":plan["revision"]}
        rev = self.request("/api/plan/project-membership", write)[1]["revision"]
        token = self.cli("plan-project", pid, "--history", "--pending")["token"]
        self.assertTrue(self.request("/api/plan/reviewed", {"id":pid, "token":token})[1]["changed"])
        ai = self.cli("plan-project", pid, "--task", task["id"], "--project", project, "--revision", rev)
        self.assertEqual(self.cli("plan-project", pid, "--history", "--pending")["items"], [])
        self.request("/api/plan/project-membership", {**write, "revision":ai["revision"], "present":False})
        page = self.cli("plan-project", pid, "--history", "--pending")
        self.assertEqual([(e["action"], e["actor"]) for e in page["items"]], [("exclude", "manual")])
        self.assertNotEqual(token, page["token"])
        self.assertEqual(self.request("/api/plan/reviewed", {"id":pid, "token":token})[0], 409)
        self.assertEqual(self.planning.view(pid)["items"][0]["id"], task["id"])

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
