from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from second_brain import storage
from second_brain.cycles import Cycles
from second_brain.gantt import Gantt
from second_brain.okr_progress import Progress
from second_brain.planning import Planning
from second_brain.refresh_planning import refresh_planning
from second_brain.schedule import Schedule


class RefreshPlanningTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name))
        self.planning = Planning(self.root)
        self.gantt = Gantt(self.root)

    def source(self, title, text):
        path = storage.create(self.root, "note", title, text)
        return {"path": path.relative_to(self.root).as_posix(), "line": storage.read_record(path)["body_start"],
                "quote": text}

    def edit(self, task, field, text, source=None, **fields):
        return self.gantt.edit({"id": task, "revision": self.gantt.detail(task)["revision"], "field": field,
                                "actor": "ai" if source else "manual", "text": text,
                                "sources": [source] if source else [], **fields})

    def test_measured_and_completed_events_live_in_one_task_across_plans(self):
        project = storage.read_record(storage.create(self.root, "project", "Мастерская"))["id"]
        task = self.planning.create_task("Сдача рабочего места", project=project)
        active = self.planning.create_plan("Неделя")
        old = self.planning.create_plan("Прошлая неделя")
        for plan in (active, old):
            self.planning.add_task(plan["id"], task["id"])
        cycles = Cycles(self.root)
        cycles.close(old["id"], cycles.preview(old["id"])["revision"])
        frozen = (self.root / old["path"]).read_bytes()
        plan_before = (self.root / active["path"]).read_bytes()

        measured = self.source("Замер", "2025-05-10. Готовность рабочего места составляет 40%.")
        progress = Progress(self.root)
        progress.set_readiness(task["id"], value="40", kind="fact", date="2025-05-10", actor="ai",
                               source=measured, revision=task["revision"])
        completed = self.source("Акт", "2025-05-12. Рабочее место принято, опись подписана.")
        schedule = Schedule(self.root)
        data = {"actor": "ai", "sources": [completed]}
        proposal = schedule.preview(task["id"], "completed", "2025-05-12", **data)
        schedule.apply(task["id"], "completed", "2025-05-12", proposal["revision"], **data)
        report = refresh_planning(self.root)
        self.assertEqual((report["examined"], report["plans"], report["closedPlans"], report["issues"]), (1, 2, 1, []))
        self.assertEqual((report["changed"], report["eventsAdded"], report["summariesUpdated"]), (1, 2, 1))
        self.assertEqual(report["example"], {"id": task["id"], "path": task["path"]})
        row = self.gantt.detail(task["id"])
        self.assertEqual(row["brief"]["text"], completed["quote"])
        self.assertEqual(row["brief"]["sources"][0]["path"], completed["path"])
        self.assertEqual([r["date"] for r in row["latest"]], ["2025-05-12", "2025-05-10"])
        self.assertEqual((self.root / active["path"]).read_bytes(), plan_before)
        self.assertEqual((self.root / old["path"]).read_bytes(), frozen)
        repeated = refresh_planning(self.root)
        self.assertEqual(repeated["changed"], 0)
        self.assertNotIn("example", repeated)
        self.assertEqual(self.gantt.detail(task["id"])["revision"], row["revision"])

    def test_same_evidence_preserves_specific_ai_summary_and_only_strictly_newer_replaces_it(self):
        task = self.planning.create_task("Доставка")["id"]
        first = self.source("Комплектация", "2025-03-03. Для отправки подготовлены все коробки.")
        self.edit(task, "brief", "Груз укомплектован. Следующий шаг — погрузка.", first)
        self.edit(task, "latest", first["quote"], first, date="2025-03-03")
        self.assertEqual(refresh_planning(self.root)["changed"], 0)
        previous = self.source("Ранний протокол", "2025-03-01. Согласованы размеры кузова.")
        self.edit(task, "latest", previous["quote"], previous, date="2025-03-01")
        self.assertEqual(refresh_planning(self.root)["changed"], 0)
        last = self.source("Склад", "2025-03-05. Перевозчик забрал все коробки.")
        self.edit(task, "latest", "Коробки переданы перевозчику.", last, date="2025-03-05")
        self.assertEqual(refresh_planning(self.root)["summariesUpdated"], 1)
        self.assertEqual(self.gantt.detail(task)["brief"]["text"], "Коробки переданы перевозчику.")
        self.assertEqual(refresh_planning(self.root)["changed"], 0)

    def test_raw_manual_summary_and_removed_event_are_protected(self):
        task = self.planning.create_task("Сверка образца", kind="milestone")
        source = self.source("Контроль", "2025-02-01. Контроль образца завершён.")
        opts = {"actor": "manual", "sources": [source]}
        dates = Schedule(self.root)
        proposal = dates.preview(task["id"], "completed", "2025-02-01", **opts)
        dates.apply(task["id"], "completed", "2025-02-01", proposal["revision"], **opts)
        self.edit(task["id"], "brief", "Записано владельцем.")
        first = refresh_planning(self.root)
        self.assertEqual((first["manualSummaries"], first["eventsAdded"], first["summariesUpdated"]), (1, 1, 0))
        path = self.root / task["path"]
        body = path.read_text()
        start = body.index("## Последнее")
        end = body.find("\n## ", start+1)
        path.write_text(body[:start] + (body[end:] if end != -1 else ""))
        second = refresh_planning(self.root)
        self.assertEqual((second["changed"], second["issues"]), (0, []))
        self.assertEqual(self.gantt.detail(task["id"])["latest"], [])

    def test_missing_evidence_reports_its_task_and_other_cards_still_refresh(self):
        broken = self.planning.create_task("Проверить цех")
        valid = self.planning.create_task("Проверить коробки")
        for task in (broken, valid):
            ref = self.source(task["title"], "2025-04-01. Проверка выполнена.")
            self.edit(task["id"], "latest", "Проверка выполнена.", ref, date="2025-04-01")
            if task == broken:
                (self.root / ref["path"]).write_text((self.root / ref["path"]).read_text().replace("выполнена.", "перенесена."))
        report = refresh_planning(self.root)
        self.assertEqual((report["examined"], report["changed"], report["summariesUpdated"]), (2, 1, 1))
        self.assertEqual([item["path"] for item in report["issues"]], [broken["path"]])
        self.assertFalse(self.gantt.detail(broken["id"])["brief"]["text"])

    def test_estimate_and_unrelated_notes_do_not_become_facts(self):
        task = self.planning.create_task("Закрыть сверку")
        ref = self.source("Оценка", "2025-01-15. Ожидается готовность 75%.")
        Progress(self.root).set_readiness(task["id"], value=75, kind="estimate", date="2025-01-15",
                                          source=ref, actor="ai", revision=task["revision"])
        self.source("Похожая задача", "Другой проект завершил сверку.")
        result = refresh_planning(self.root)
        self.assertEqual((result["changed"], result["eventsAdded"], result["issues"]), (0, 0, []))

    def test_withdrawn_completion_is_history_and_cannot_supply_new_brief(self):
        task = self.planning.create_task("Подписать акт")
        task_id = task["id"]
        source = self.source("Акт", "2025-03-04. Акт подписан.")
        dates = Schedule(self.root)
        options = {"actor": "manual", "sources": [source]}
        preview = dates.preview(task_id, "completed", "2025-03-04", **options)
        dates.apply(task_id, "completed", "2025-03-04", preview["revision"], **options)
        self.edit(task_id, "latest", source["quote"], source, date="2025-03-04")
        correction = {"actor": "manual", "reason": "Оказалось, что акт относился к другой работе."}
        preview = dates.preview(task_id, "completed", None, **correction)
        dates.apply(task_id, "completed", None, preview["revision"], **correction)
        self.assertEqual(refresh_planning(self.root)["changed"], 0)
        row = self.gantt.detail(task_id)
        self.assertEqual(len(row["latest"]), 1)
        self.assertFalse(row["brief"]["text"])
        self.edit(task_id, "brief", "Акт подписан.", source)
        report = refresh_planning(self.root)
        self.assertEqual(report["changed"], 0)
        self.assertIn("уточнённому", report["issues"][0]["reason"])

    def test_missing_live_plan_member_does_not_modify_membership(self):
        task = self.planning.create_task("Перемещённая карточка")
        plan = self.planning.create_plan("Цикл")
        self.planning.add_task(plan["id"], task["id"])
        (self.root / task["path"]).unlink()
        original = (self.root / plan["path"]).read_bytes()
        report = refresh_planning(self.root)
        self.assertEqual((report["plans"], report["examined"], report["changed"]), (1, 0, 0))
        self.assertEqual(report["issues"][0]["path"], plan["path"])
        self.assertIn(task["id"], report["issues"][0]["reason"])
        self.assertEqual((self.root / plan["path"]).read_bytes(), original)

    def test_one_ambiguous_project_keeps_other_plans_diagnostics(self):
        project = storage.create(self.root, "project", "Отгрузка")
        identifier = storage.read_record(project)["id"]
        linked = self.planning.create_task("Передать", project=identifier)
        missing = self.planning.create_task("Проверить")
        delivery = self.planning.create_plan("Поставка")
        checks = self.planning.create_plan("Проверки")
        self.planning.add_task(delivery["id"], linked["id"])
        self.planning.add_task(checks["id"], missing["id"])
        (self.root / missing["path"]).unlink()
        project.with_name("Копия.md").write_bytes(project.read_bytes())

        result = refresh_planning(self.root)
        problems = {issue["path"]: issue["reason"] for issue in result["issues"]}
        self.assertIn(identifier, problems[delivery["path"]])
        self.assertIn(missing["id"], problems[checks["path"]])
