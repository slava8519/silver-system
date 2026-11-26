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

    def request(self, path, status=200, *, data=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            conn.request("GET" if data is None else "POST", path,
                         body=None if data is None else json.dumps(data), headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            text = response.read().decode()
            self.assertEqual(response.status, status, text)
            return json.loads(text) if "json" in response.getheader("Content-Type") else text
        finally:
            conn.close()

    def test_empty_vault_and_result_without_a_measurement(self):
        self.assertEqual(self.request("/api/plan/okrs")["items"], [])
        self.assertFalse((self.root / "Plans" / "OKR").exists())
        direction = storage.read_record(storage.create(self.root, "direction", "Дом"))["id"]
        card = self.service.create("Масса", objective="Собрать багаж", direction=direction, unit="кг")
        row, = self.request("/api/plan/okrs")["items"]
        self.assertEqual((row["id"], row["directionTitle"], row["measurement"]), (card["id"], "Дом", None))
        self.assertEqual(self.request("/api/plan/okr?id=" + card["id"])["tasks"], [])
        self.request("/api/plan/okr?id=missing", 404)

    def test_record_criteria_from_source_picker_and_view_weighted_work_in_closed_period(self):
        area = storage.read_record(storage.create(self.root, "direction", "Работа"))["id"]
        planning = Planning(self.root)
        plan = planning.create_plan("Период")["id"]
        goal = self.service.create_objective("Подготовить запуск", plan_id=plan, direction=area)
        task = planning.create_task("Уточнить сценарии")
        result = self.service.create("Покрытие", unit="%", objective_id=goal["id"], task_ids=[task["id"]])
        result_id = result["id"]
        note = storage.create(self.root, "note", "Контрольный прогон",
                              "2026-10-01. Вне покрытия 11%.\n2026-11-24. Вне покрытия 4%.\n2026-11-24. Склад участвует: да.")
        source, = self.request("/api/plan/okr-sources")["items"]
        evidence = self.request("/api/plan/okr-source-lines?id=" + source["id"])["items"]
        initial, actual, confirmation = [{**r, "id": source["id"], "path": source["path"]} for r in evidence]
        self.request("/api/plan/okr-source-lines?id=" + area, 400)
        def post(action, card, **kwargs):
            return self.request("/api/plan/okr/" + action, data={"id": card["id"], "revision": card["revision"], **kwargs})
        result = post("criterion", result, kind="numeric", primary=True, operator="<=", target=5,
                      title="Доля вне покрытия", requestId="coverage", baseline={"value":11,"date":"2026-10-01","source":initial})
        number = result["criteria"][0]["id"]
        result = post("criterion", result, kind="boolean", title="Склад участвует", requestId="warehouse")
        check = result["criteria"][1]["id"]
        result = post("weight", result, weight="1")
        self.assertIsNone(self.service.show(result_id)["measurement"])
        result = post("weight", result, taskId=task["id"], weight="1")
        post("readiness", task, kind="estimate", value="50.5", date="2026-11-23", reason="Ручная оценка")
        read = self.request("/api/plan/okr-board?id=" + plan)
        self.assertEqual(read["progress"]["value"], "50.5")
        live_chart = self.request("/api/plan?id=" + plan)
        self.assertEqual(live_chart["progress"], read["progress"])
        self.assertTrue(live_chart["progress"]["hasEstimates"])
        self.assertIsNone(read["objectives"][0]["results"][0]["criteriaMet"])
        self.assertEqual(self.request("/api/plan/okr-periods")["items"][0]["results"], 1)
        result = post("observe", self.service.show(result_id), criterionId=number, value=4, date="2026-11-24", source=actual)
        self.assertIsNone(result["criteriaMet"])
        result = post("observe", result, criterionId=check, value=True, date="2026-11-24", source=confirmation)
        self.assertTrue(result["criteriaMet"])
        result = post("criterion-target", result, criterionId=number, operator="<=", target=3)
        self.assertFalse(result["criteriaMet"])
        cycle = Cycles(self.root)
        cycle.close(plan, cycle.preview(plan)["revision"])
        post("readiness", planning.task(task["id"]), kind="fact", value="100", date="2026-11-25",
             reason="Подтверждено после закрытия")
        note.write_text(note.read_text().replace("Вне покрытия 4%.", "Вне покрытия 9%."))
        self.assertTrue(self.service.show(result_id)["measurement"]["source"]["unavailable"])
        old = self.request("/api/plan/okr-board?id=" + plan)
        self.assertEqual(old["progress"]["value"], "50.5")
        self.assertEqual(self.request("/api/plan?id=" + plan)["progress"], live_chart["progress"])
        self.assertEqual(self.request("/api/plan/task?id=" + task["id"])["readiness"]["value"], "100")
        self.assertFalse(old["objectives"][0]["results"][0]["criteriaMet"])
        self.assertEqual(old["objectives"][0]["results"][0]["criteria"][0]["measurement"]["value"], "4")

    def test_browser_creates_quarter_and_its_gantt_includes_linked_work_across_projects(self):
        direction = storage.read_record(storage.create(self.root, "direction", "Работа"))["id"]
        planning = Planning(self.root)
        tasks = []
        for name in ("Сервер", "Клиент"):
            project = storage.read_record(storage.create(self.root, "project", name, direction=direction))
            tasks.append(planning.create_task("Приёмка", project=project["id"]))
        a, b = [task["id"] for task in tasks]
        create = {"title": "IV квартал", "requestId": "q4"}
        quarter = self.request("/api/plan/create", data=create)["id"]
        self.assertEqual(self.request("/api/plan/create", data=create)["id"], quarter)
        planning.add_task(quarter, a)
        obj = self.request("/api/plan/objective/create", data={
            "title": "Надёжно выпускать обновления", "planId": quarter, "direction": direction, "requestId": "goal"})
        kr = self.request("/api/plan/okr/create", data={
            "objectiveId": obj["id"], "title": "Успешные выпуски", "unit": "%", "target": "99",
            "taskIds": [a], "requestId": "kr"})
        self.request("/api/plan/okr/link", data={"id": kr["id"], "taskId": b, "revision": kr["revision"]})
        options = self.request("/api/plan/okr-options")
        self.assertEqual({t["projectTitle"] for t in options["tasks"]}, {"Сервер", "Клиент"})
        self.assertEqual(options["objectives"][0]["planId"], quarter)
        board = self.request("/api/plan/okr-board?id=" + quarter)
        self.assertEqual(board["objectives"][0]["results"][0]["taskIds"], [a, b])
        self.assertEqual(board["objectives"][0]["directionTitle"], "Работа")
        gantt = self.request("/api/plan?id=" + quarter)
        self.assertEqual([r["id"] for r in gantt["items"]], [a, b])
        self.assertEqual(gantt["objectives"][0]["results"][0]["id"], kr["id"])
        # An implicitly included task is editable from the common Gantt and the
        # date lives on the same card in both its quarterly and project plans.
        project_plan = planning.create_plan("Клиент")
        planning.add_task(project_plan["id"], b)
        edit = {"id": b, "planId": quarter, "due": "2027-01-09"}
        preview = self.request("/api/plan/due/preview", data=edit)
        self.request("/api/plan/due/apply", data={**edit, "revision": preview["revision"]})
        self.assertEqual(self.request("/api/plan?id=" + project_plan["id"])["items"][0]["due"], edit["due"])
        old = self.service.create("Время отклика", objective="Старая цель", direction=direction, unit="с")
        attach = {"id": old["id"], "objectiveId": obj["id"], "revision": old["revision"]}
        assigned = self.request("/api/plan/okr/assign", data=attach)
        self.assertEqual(assigned["id"], old["id"])
        self.assertEqual(assigned["planId"], quarter)
        self.request("/api/plan/okr/create", 400, data={"title": "Пропущена цель", "requestId": "missing"})
        self.request("/api/plan/okr-board?id=unknown", 404)

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

        detail = self.request("/api/plan/okr?id=" + card["id"])
        self.assertEqual((detail["directionTitle"], detail["target"]), ("Работа", "15"))
        source = detail["measurement"]["source"]
        self.assertIn(quote, self.request(source["href"].split("#")[0]))
        row, = detail["tasks"]
        self.assertEqual(row["path"], renamed.relative_to(self.root).as_posix())
        self.assertEqual({(p["id"], p["status"]) for p in row["plans"]},
                         {(active["id"], "active"), (closed["id"], "closed")})
        for plan in row["plans"]:
            work, = self.request("/api/plan?id=" + plan["id"])["items"]
            self.assertEqual(work["id"], row["id"])
        new_note = note.with_name("Протокол после переноса.md")
        new_note.write_text(new_note.read_text().replace("18 мин", "20 мин"))
        unavailable = self.request("/api/plan/okr?id=" + card["id"])["measurement"]["source"]
        self.assertTrue(unavailable["unavailable"])
        self.assertEqual(unavailable["quote"], quote)
        self.assertTrue(self.request("/api/plan/okrs")["items"][0]["measurement"]["sourceUnavailable"])
        renamed.unlink()
        missing, = self.request("/api/plan/okr?id=" + card["id"])["tasks"]
        self.assertTrue(missing["missing"])
        self.assertIn(closed["id"], [p["id"] for p in missing["plans"]])
