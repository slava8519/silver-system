from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from second_brain import storage
from second_brain.cycles import Cycles
from second_brain.gantt import Gantt
from second_brain.gantt_feedback import GanttFeedback
from second_brain.gantt_groups import GanttGroups


class GanttGroupTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Test")
        self.service = GanttGroups(self.root)
        self.plans = self.service.planning
        self.period = self.plans.create_plan("IV квартал")["id"]
        self.other = self.plans.create_plan("I квартал")["id"]
        area = storage.read_record(storage.create(self.root, "direction", "ИТ"))
        self.goal = self.service.okr.create_objective("Готовность", plan_id=self.period, direction=area["id"])
        self.kr = self.service.okr.create("Проверка пройдена", objective_id=self.goal["id"])
        path = storage.create(self.root, "note", "Протокол", "2026-11-26. Проверили перенос и согласовали сроки.")
        self.source = {"path": path.relative_to(self.root).as_posix(), "line": storage.read_record(path)["body_start"],
                       "quote": "2026-11-26. Проверили перенос и согласовали сроки."}

    def edit(self, kind, identifier, text, *, field="brief", actor="manual", **options):
        data = {"revision": self.service.detail(self.period, kind, identifier)["revision"],
                "text": text, "field": field, "actor": actor, **options}
        return self.service.edit(self.period, kind, identifier, data)

    def test_independent_canonical_cards_journal_and_evidence(self):
        task = self.plans.create_task("Работа")
        self.plans.add_task(self.period, task["id"])
        for kind, identifier in [("plan", self.period), ("objective", self.goal["id"]), ("key_result", self.kr["id"])]:
            with self.subTest(kind=kind):
                initial = self.service.detail(self.period, kind, identifier)
                self.assertEqual(initial["brief"]["text"], "")
                self.assertFalse(initial["readOnly"])
                self.edit(kind, identifier, "Положение " + kind)
                for index in range(7):
                    event = self.edit(kind, identifier, str(index), field="latest", actor="ai", date="2026-11-26", sources=[self.source])
                self.assertEqual(event["brief"]["text"], "Положение " + kind)
                self.assertEqual(event["latestTotal"], 7)
                self.assertEqual(len(event["latest"]), 5)
                more = self.service.detail(self.period, kind, identifier, limit=2, offset=5)
                self.assertEqual([item["text"] for item in more["latest"]], ["1", "0"])
                self.assertFalse(more["truncated"])
                item = more["latest"][0]
                corrected = self.edit(kind, identifier, "Перенесли сверку.", field="latest", entryId=item["id"])
                history = self.service.history(self.period, kind, identifier, limit=1)
                self.assertEqual((history["total"], history["items"][0]["before"], history["items"][0]["after"]),
                                 (9, "1", "Перенесли сверку."))
                self.assertTrue(history["truncated"])
                corrected_event = next(entry for entry in self.service.detail(self.period, kind, identifier, limit=20)["latest"]
                                       if entry["id"] == item["id"])
                self.assertEqual(corrected_event["sources"], item["sources"])
                self.assertTrue(corrected_event["manual"])
                lines = (self.root / corrected["path"]).read_text().splitlines()
                self.assertEqual(lines[corrected["brief"]["line"] - 1], "Положение " + kind)
                self.assertEqual(lines[corrected_event["line"] - 1], "Перенесли сверку.")
                with self.assertRaises(FileNotFoundError):
                    self.service.detail(self.other, kind, identifier)
        self.assertEqual(Gantt(self.root).detail(task["id"])["brief"]["text"], "")

    def test_optimistic_write_native_markdown_ai_guard_and_feedback(self):
        goal = self.goal["id"]
        initial = self.service.detail(self.period, "objective", goal)
        self.edit("objective", goal, "Вручную согласовано")
        with self.assertRaises(storage.ConflictError):
            self.service.edit(self.period, "objective", goal, {"actor": "manual", "field": "brief", "text": "Поздняя форма",
                              "revision": initial["revision"]})
        path = self.root / initial["path"]
        path.write_bytes(path.read_bytes().replace("Вручную согласовано\n".encode(), "Исправлено в Obsidian\n".encode()))
        self.assertTrue(self.service.detail(self.period, "objective", goal)["brief"]["manual"])
        with self.assertRaises(storage.ConflictError):
            self.edit("objective", goal, "Новая формулировка", actor="ai", sources=[self.source])
        with self.assertRaises(ValueError):
            self.edit("objective", goal, "Инициатива начата", field="latest", actor="ai", date="2026-11-27", sources=[self.source])
        with self.assertRaises(ValueError):
            self.edit("objective", goal, "Инициатива начата", field="latest", actor="ai")
        with self.assertRaises(ValueError):
            self.edit("objective", goal, "<!-- plan-members -->")
        feedback = GanttFeedback(self.root)
        unread = next(item for item in feedback.pending(self.period) if item["id"] == goal)
        self.edit("objective", goal, "Новая ручная правка")
        with self.assertRaises(storage.ConflictError):
            feedback.acknowledge(goal, unread["token"])
        unread = next(item for item in feedback.pending(self.period) if item["id"] == goal)
        self.assertTrue(feedback.acknowledge(goal, unread["token"])["changed"])
        self.assertFalse(feedback.acknowledge(goal, unread["token"])["changed"])
        self.assertFalse(any(item["id"] == goal for item in feedback.pending(self.period)))

    def test_closed_snapshots_survive_live_edits_and_missing_cards(self):
        cycles = Cycles(self.root)
        plan_brief = self.edit("plan", self.period, "Итог периода")
        self.edit("objective", self.goal["id"], "Принят результат")
        first = cycles.preview(self.period)
        self.edit("key_result", self.kr["id"], "Проверка подтверждена", field="latest", actor="ai",
                  sources=[self.source], date="2026-11-26")
        with self.assertRaises(storage.ConflictError):
            cycles.close(self.period, first["revision"])
        second = cycles.preview(self.period)
        self.assertNotIn("groupJournals", second)
        cycles.close(self.period, second["revision"])
        (self.root / self.kr["path"]).unlink()
        path = self.root / plan_brief["path"]
        path.write_bytes(path.read_bytes().replace("Итог периода\n".encode(), "Сегодня иначе\n".encode(), 1))
        self.assertTrue(self.service.detail(self.period, "plan", self.period)["readOnly"])
        self.assertEqual(self.service.detail(self.period, "plan", self.period)["brief"]["text"], "Итог периода")
        self.assertEqual(self.service.detail(self.period, "key_result", self.kr["id"])["latest"][0]["date"], "2026-11-26")
        self.assertEqual(self.service.history(self.period, "key_result", self.kr["id"])["total"], 1)
        with self.assertRaises(storage.ConflictError):
            self.edit("objective", self.goal["id"], "После закрытия")

    def test_group_storage_is_lazy_per_plan_and_pinned_in_snapshot(self):
        task = self.plans.create_task("Отдельная задача")
        for period in (self.period, self.other):
            self.plans.add_task(period, task["id"])
        for key in ("project/", "direction/", "outside"):
            info = self.service.detail(self.period, "group", key)
            self.assertFalse(info["stored"])
            self.assertFalse((self.root / info["path"]).exists())
        with self.assertRaises(FileNotFoundError):
            self.service.detail(self.other, "group", "outside")
        for period in (self.period, self.other):
            current = self.service.detail(period, "group", "project/")
            changed = self.service.edit(period, "group", "project/",
                                        {"revision": current["revision"], "actor": "manual", "field": "brief", "text": period})
            self.assertEqual(changed["id"], "project/")
            self.assertTrue(changed["stored"])
            self.assertTrue((self.root / changed["path"]).is_file())
        feedback = GanttFeedback(self.root)
        first = next(row for row in feedback.pending(self.period) if row["id"] == "project/")
        second = next(row for row in feedback.pending(self.other) if row["id"] == "project/")
        self.assertNotEqual(first["token"], second["token"])
        feedback.acknowledge("project/", first["token"])
        self.assertTrue(any(row["token"] == second["token"] for row in feedback.pending(self.other)))
        self.assertFalse(any(row["token"] == first["token"] for row in feedback.pending(self.period)))
        cycles = Cycles(self.root)
        cycles.close(self.period, cycles.preview(self.period)["revision"])
        self.assertEqual(self.service.detail(self.period, "group", "project/")["brief"]["text"], self.period)
        self.assertTrue(self.service.detail(self.period, "group", "direction/")["readOnly"])
        self.assertEqual(self.service.detail(self.period, "group", "outside")["latest"], [])

    def test_manual_page_filters_before_slicing_and_reports_native_deletions(self):
        from second_brain.gantt import _entries
        kind, identifier = "objective", self.goal["id"]
        ai_brief = "Перенос согласован. • Дальше: сверить сроки."
        self.edit(kind, identifier, ai_brief, actor="ai", sources=[self.source])
        before = self.service.detail(self.period, kind, identifier)["revision"]
        with self.assertRaises(ValueError):
            self.edit(kind, identifier, "Текст без полей", actor="ai", sources=[self.source])
        self.assertEqual(before, self.service.detail(self.period, kind, identifier)["revision"])
        event_one = self.edit(kind, identifier, "Вручную раз", field="latest")["latest"][0]
        self.edit(kind, identifier, "Вручную два", field="latest")
        for n in range(8):
            self.edit(kind, identifier, str(n), field="latest", actor="ai", date="2026-11-26", sources=[self.source])
        self.edit(kind, identifier, "Без даты", field="latest", actor="ai", date=None, sources=[self.source])
        with self.assertRaises(ValueError):
            self.edit(kind, identifier, "Когда-то", field="latest", actor="ai", sources=[self.source])
        page = self.service.detail(self.period, kind, identifier, manual=True, limit=1, offset=1)
        self.assertEqual(page["latestTotal"], 2)
        self.assertEqual(page["latest"][0]["id"], event_one["id"])
        self.assertIsNone(page["brief"])
        path = self.root / page["path"]
        lines = path.read_text().splitlines(keepends=True)
        item = next(row for row in _entries(lines, "latest") if row[0]["id"] == event_one["id"])
        del lines[item[1]:item[2]]
        path.write_text("".join(lines))
        protected = self.service.detail(self.period, kind, identifier, manual=True)
        deleted = next(row for row in protected["latest"] if row["id"] == event_one["id"])
        self.assertTrue(deleted["deleted"])
        self.assertIsNone(protected["updated"])
        self.assertNotIn(event_one["id"], {row["id"] for row in self.service.detail(self.period, kind, identifier, limit=20)["latest"]})
