import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode

from examples.planning_vault import generate
from second_brain.webserver import server


class RelationMapWebTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = generate(Path(tmp.name) / "Учебное хранилище")
        self.http = server(self.root, 0, mode="work")
        worker = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.02})
        worker.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(self.http.shutdown)

    def get(self, path, **query):
        connection = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=5)
        try:
            connection.request("GET", path + ("?" + urlencode(query) if query else ""))
            response = connection.getresponse()
            return response.status, json.load(response)
        finally:
            connection.close()

    def test_browser_search_focus_and_versioned_evidence(self):
        status, results = self.get("/api/map", action="search", query="Маяк")
        self.assertEqual(status, 200)
        project = next(row for row in results["items"] if row["title"] == "Переезд базы заказов")
        status, focus = self.get("/api/map", action="focus", id=project["id"])
        self.assertEqual((status, focus["root"]), (200, str(self.root.resolve())))
        tasks = next(group for group in focus["groups"] if group["key"] == "tasks")
        work = tasks["items"][0]
        status, plans = self.get("/api/map", action="group", id=work["id"], group="plans")
        self.assertEqual((status, plans["total"]), (200, 2))
        source = plans["items"][0]["source"]
        request = {key: source[key] for key in ("id", "line", "revision")}
        status, evidence = self.get("/api/map/source", **request)
        self.assertEqual((status, evidence["path"]), (200, source["path"]))
        self.assertIn(work["id"], evidence["text"])
        path = self.root / source["path"]
        path.write_text(path.read_text(encoding="utf-8") + "\nРучная правка.\n", encoding="utf-8")
        self.assertEqual(self.get("/api/map/source", **request)[0], 409)

    def test_map_page_boundaries_and_missing_card(self):
        self.assertEqual(self.get("/api/map", limit=0)[0], 400)
        self.assertEqual(self.get("/api/map", offset=-1)[0], 400)
        self.assertEqual(self.get("/api/map", action="focus", id="missing")[0], 404)
        self.assertEqual(self.get("/api/map/source", id="missing", line="x")[0], 400)
