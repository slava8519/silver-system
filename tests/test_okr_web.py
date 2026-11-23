import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from second_brain import storage
from second_brain.cycles import Cycles
from second_brain.okr import KeyResults
from second_brain.planning import Planning
from second_brain.webserver import server


class KeyResultWebTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = storage.initialize(Path(tmp.name) / "Vault")
        self.service = KeyResults(self.root)
        self.httpd = server(self.root, port=0, mode="work")
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.httpd.shutdown)

    def get(self, path, status=200):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            text = response.read().decode()
            self.assertEqual(response.status, status, text)
            return json.loads(text) if "json" in response.getheader("Content-Type") else text
        finally:
            conn.close()

    def test_empty_vault_and_result_without_a_measurement(self):
        self.assertEqual(self.get("/api/plan/okrs")["items"], [])
        self.assertFalse((self.root / "Plans" / "OKR").exists())
        direction = storage.read_record(storage.create(self.root, "direction", "Дом"))["id"]
        card = self.service.create("Масса", objective="Собрать багаж", direction=direction, unit="кг")
        row, = self.get("/api/plan/okrs")["items"]
        self.assertEqual((row["id"], row["directionTitle"], row["measurement"]), (card["id"], "Дом", None))
        self.assertEqual(self.get("/api/plan/okr?id=" + card["id"])["tasks"], [])
        self.get("/api/plan/okr?id=missing", 404)

    def test_sourced_measurement_links_to_one_task_in_live_and_closed_plans(self):
        direction = storage.read_record(storage.create(self.root, "direction", "Работа"))["id"]
        planning = Planning(self.root)
        task = planning.create_task("Проверить стенд", direction=direction)
        active = planning.create_plan("Запуск")
        closed = planning.create_plan("Прошлая неделя")
        for plan in (active, closed):
            planning.add_task(plan["id"], task["id"])
        cycles = Cycles(self.root)
        cycles.close(closed["id"], cycles.preview(closed["id"])["revision"])
        card = self.service.create("Время восстановления", objective="Ускорить запуск", unit="мин",
                                   direction=direction, task_ids=[task["id"]], target="15")
        quote = "2026-11-22. На стенде 18 мин."
        note = storage.create(self.root, "note", "Протокол", quote)
        self.service.measure(card["id"], value="18", date="2026-11-22", revision=card["revision"],
                             source={"path": note.relative_to(self.root).as_posix(), "line": storage.read_record(note)["body_start"]})
        task_path = self.root / task["path"]
        renamed = task_path.with_name("Стенд.md")
        task_path.rename(renamed)
        note.rename(note.with_name("Протокол после переноса.md"))

        detail = self.get("/api/plan/okr?id=" + card["id"])
        self.assertEqual((detail["directionTitle"], detail["target"]), ("Работа", "15"))
        source = detail["measurement"]["source"]
        self.assertIn(quote, self.get(source["href"].split("#")[0]))
        row, = detail["tasks"]
        self.assertEqual(row["path"], renamed.relative_to(self.root).as_posix())
        self.assertEqual({(p["id"], p["status"]) for p in row["plans"]},
                         {(active["id"], "active"), (closed["id"], "closed")})
        for plan in row["plans"]:
            work, = self.get("/api/plan?id=" + plan["id"])["items"]
            self.assertEqual(work["id"], row["id"])
        new_note = note.with_name("Протокол после переноса.md")
        new_note.write_text(new_note.read_text().replace("18 мин", "20 мин"))
        unavailable = self.get("/api/plan/okr?id=" + card["id"])["measurement"]["source"]
        self.assertTrue(unavailable["unavailable"])
        self.assertEqual(unavailable["quote"], quote)
        self.assertTrue(self.get("/api/plan/okrs")["items"][0]["measurement"]["sourceUnavailable"])
        renamed.unlink()
        missing, = self.get("/api/plan/okr?id=" + card["id"])["tasks"]
        self.assertTrue(missing["missing"])
        self.assertIn(closed["id"], [p["id"] for p in missing["plans"]])
