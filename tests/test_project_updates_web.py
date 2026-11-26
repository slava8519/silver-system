import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from second_brain import project_updates, storage
from second_brain.webserver import server


class ProjectUpdatesWebTests(unittest.TestCase):
    def test_browser_edits_and_reads_the_same_project_as_the_chart(self):
        with TemporaryDirectory() as folder:
            root = storage.initialize(Path(folder) / "Example")
            path = storage.create(root, "project", "Дорожный комплект")
            key = storage.read_record(path)["id"]
            with server(root, 0, mode="work") as httpd:
                worker = Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
                worker.start()
                try:
                    def call(endpoint, payload=None):
                        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
                        try:
                            conn.request("GET" if payload is None else "POST", "/api/" + endpoint,
                                         None if payload is None else json.dumps(payload),
                                         {"Content-Type": "application/json"})
                            response = conn.getresponse()
                            return response.status, json.load(response)
                        finally:
                            conn.close()

                    _, record = call("record?id=" + key)
                    self.assertIsNone(record["projectUpdates"]["brief"])
                    edit = {"id": key, "text": "Набор собран, осталось забрать упаковку.", "revision": record["revision"]}
                    status, updated = call("project-brief", edit)
                    self.assertEqual(status, 200)
                    self.assertEqual((updated["brief"]["author"], updated["brief"]["text"]), ("manual", edit["text"]))
                    self.assertEqual(call("project-brief", dict(edit, text="Из другой вкладки"))[0], 409)
                    revision = updated["revision"]
                    for text in ("Заказали упаковку.", "Упаковку выдали."):
                        entry = project_updates.write_update(root, key, field="event", text=text,
                                                             actor="manual", revision=revision)
                        revision = entry["revision"]
                    for offset, expected in enumerate(("Упаковку выдали.", "Заказали упаковку.")):
                        status, page = call(f"project-updates?id={key}&limit=1&offset={offset}")
                        self.assertEqual(status, 200)
                        self.assertEqual((page["total"], page["revision"]), (2, revision))
                        self.assertEqual([e["text"] for e in page["events"]], [expected])
                    _, chart = call("plan/project-gantt?id=" + key)
                    _, card = call("record?id=" + key)
                    self.assertEqual(chart["project"]["projectUpdates"], card["projectUpdates"])
                    self.assertEqual(card["projectUpdates"]["brief"]["text"], edit["text"])
                finally:
                    httpd.shutdown()
                    worker.join()
