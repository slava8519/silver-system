from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.gantt_vault import generate
from second_brain.cycles import Cycles
from second_brain.dependencies import Dependencies
from second_brain.gantt import Gantt
from second_brain.planning import Planning
from second_brain.schedule import Schedule


class CycleExamples(unittest.TestCase):
    def test_shared_work_moves_to_next_cycle_in_three_directions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = generate(Path(temporary) / "Practice")
            planning, cycles, graph = Planning(root), Cycles(root), Dependencies(root)
            week = next(p for p in planning.plans() if p["title"] == "План недели")
            tasks = {r["title"]: r for r in planning.view(week["id"])["items"]}
            trials = [
                ("Проверить восстановление базы", "Готовность к переключению", "2026-12-01"),
                ("Забрать дорожный набор", "Всё готово к выезду", "2026-12-02"),
                ("Сравнить кедровые пробы", "Выбрать образец для серии", "2026-12-01"),
            ]
            schedule = Schedule(root)
            backup_id = tasks["Подготовить резервную копию"]["id"]
            options = {"actor": "manual", "reason": "Сверено с актом."}
            with patch("second_brain.schedule.date") as calendar:
                calendar.today.return_value = date(2026, 11, 23)
                fact = schedule.preview(backup_id, "completed", "2026-11-20", **options)
                schedule.apply(backup_id, "completed", "2026-11-20", fact["revision"], **options)
            preview = cycles.preview(week["id"])
            cycles.close(week["id"], preview["revision"])
            archive = root / week["path"]
            saved = archive.read_bytes()
            next_week = planning.create_plan("Следующий цикл")
            for task, milestone, forecast in trials:
                with self.subTest(task=task):
                    for title in (task, milestone):
                        planning.add_task(next_week["id"], tasks[title]["id"])
                    key = tasks[task]["id"]
                    change = graph.preview(key, forecast)
                    for row in change["rows"]:
                        self.assertNotIn(week["id"], {p["id"] for p in row["plans"]})
                    graph.apply(key, forecast, change["revision"])

                    # Reopen both normal Gantt views after publishing the forecast.
                    old = {r["title"]: r for r in Gantt(root).view(week["id"])["items"]}
                    new = {r["title"]: r for r in Gantt(root).view(next_week["id"])["items"]}
                    for title in (task, milestone):
                        self.assertEqual(old[title]["forecast"], tasks[title]["forecast"])
                        self.assertEqual(new[title]["forecast"], forecast)
                        for field in ("id", "kind", "path", "due", "completed"):
                            self.assertEqual(new[title][field], old[title][field])
                    self.assertEqual(old["Подготовить резервную копию"]["completed"], "2026-11-20")
                    self.assertIsNone(old["Выбрать чехол для документов"]["due"])
                    self.assertEqual(archive.read_bytes(), saved)
