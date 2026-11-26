import json
import subprocess
import sys
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from second_brain import project_updates, storage
from second_brain.gantt import Gantt
from second_brain.planning import Planning
from second_brain.webserver import server


class OverviewPageTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name) / "Учебный обзор")
        self.http = server(self.root, 0, mode="work")
        worker = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(self.http.shutdown)

    def get(self, path, *, expected=200, **params):
        connection = HTTPConnection("127.0.0.1", self.http.server_port, timeout=5)
        try:
            connection.request("GET", path + ("?" + urlencode(params) if params else ""))
            response = connection.getresponse()
            self.assertEqual(response.status, expected, path)
            return json.load(response) if response.headers.get_content_type() == "application/json" else response.read().decode()
        finally:
            connection.close()

    def cli(self, *args):
        result = subprocess.check_output(
            [sys.executable, "-m", "second_brain", "--vault", str(self.root), "overview", *args],
            cwd=Path(__file__).resolve().parents[1], text=True, timeout=10)
        return json.loads(result)

    def test_empty_states_leave_existing_overview_and_navigation_available(self):
        states = self.get("/api/direction-states")
        self.assertEqual((states["items"], states["total"], states["truncated"]), ([], 0, False))
        self.assertEqual(self.cli("--states"), states)
        self.assertEqual(self.get("/api/workspace")["records"], [])
        self.assertEqual(self.cli("--brief")["counts"]["direction"], 0)
        self.assertTrue(self.cli("--folders")["items"])
        shell = self.get("/")
        for route in ("overview", "inbox", "plans", "activity", "records", "people", "map"):
            with self.subTest(route=route):
                self.assertIn(f'href="#{route}"', shell)
        self.assertIn('href="/about.html"', shell)

    def test_catalog_pages_and_scoped_states_refresh_from_live_markdown(self):
        areas = [storage.read_record(storage.create(self.root, "direction", name))["id"]
                 for name in ("ИТ-проекты", "Личное", "Парфюмерия")]
        project = storage.create(self.root, "project", "Разбор заявок", direction=areas[0])
        card = storage.read_record(project)
        summary = "Правила разбора согласованы."
        event = "Владелец утвердил правила разбора."
        updated = project_updates.write_update(self.root, card["id"], actor="manual", field="brief",
                                                text=summary, revision=card["revision"])
        project_updates.write_update(self.root, card["id"], actor="manual", field="event",
                                     text=event, date="2026-11-19", revision=updated["revision"])

        seen = []
        for offset in range(3):
            page = self.get("/api/direction-states", limit=1, offset=offset)
            self.assertEqual(self.cli("--states", "--limit", "1", "--offset", str(offset)), page)
            self.assertEqual((len(page["items"]), page["total"], page["truncated"]), (1, 3, offset < 2))
            seen.append(page["items"][0]["id"])
        self.assertCountEqual(seen, areas)

        def scoped():
            response = self.get("/api/direction-states", direction=areas[0])
            self.assertEqual(self.cli("--states", "--direction", areas[0]), response)
            item, = response["items"]
            return item

        area = scoped()
        self.assertEqual((area["id"], area["projectCount"], area["taskCount"]), (areas[0], 1, 0))
        self.assertIn(summary, [brief["text"] for brief in area["briefs"]])
        self.assertEqual((area["latest"]["text"], area["latest"]["date"]), (event, "2026-11-19"))
        for entry in (*area["briefs"], area["latest"]):
            source = entry["source"]
            self.assertEqual(entry["record"]["id"], card["id"])
            self.assertTrue(source["href"])
            detail = self.get("/api/record", id=entry["record"]["id"])
            self.assertEqual(detail["path"], source["path"])
            self.assertIn(entry["text"], detail["raw"].splitlines()[source["line"] - 1])
        revised = "Владелец опубликовал правила разбора."
        project.write_text(project.read_text(encoding="utf-8").replace(summary, revised), encoding="utf-8")
        area = scoped()
        self.assertIn(revised, [brief["text"] for brief in area["briefs"]])
        self.assertEqual(area["latest"]["text"], event)
        empty, = self.get("/api/direction-states", direction=areas[2])["items"]
        self.assertEqual((empty["briefs"], empty["latest"]), ([], None))
        self.assertEqual(self.get("/api/record", id=card["id"])["projectUpdates"]["brief"]["text"], revised)

    def test_task_event_opens_its_cited_line_and_detects_an_external_correction(self):
        area = storage.read_record(storage.create(self.root, "direction", "Личное"))["id"]
        task = Planning(self.root).create_task("Подготовить документы", direction=area)
        text = "Документы переданы."
        Gantt(self.root).edit({"id": task["id"], "revision": task["revision"], "actor": "manual",
                              "field": "latest", "date": "2026-11-20", "text": text})
        state, = self.get("/api/direction-states", direction=area)["items"]
        self.assertEqual((state["taskCount"], state["latest"]["text"]), (1, text))
        url = state["latest"]["source"]["href"].split("#", 1)[0]
        document = self.get(url)
        self.assertIn('id="cited-line"', document)
        self.assertIn(text, document)
        path = self.root / task["path"]
        revised = "Документы проверены."
        path.write_text(path.read_text(encoding="utf-8").replace(text, revised), encoding="utf-8")
        self.get(url, expected=409)
        item, = self.cli("--states", "--direction", area)["items"]
        self.assertEqual(item["latest"]["text"], revised)
