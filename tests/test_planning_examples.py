from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from examples.planning_vault import generate
from second_brain import storage
from second_brain.plan_dates import Deadlines
from second_brain.planning import Planning


class PlanningExamples(unittest.TestCase):
    def test_each_direction_changes_one_deadline_in_two_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = generate(Path(directory) / "Practice")
            planning, dates = Planning(root), Deadlines(root)
            tasks = {task["title"]: task for task in planning.tasks()}
            plans = {plan["title"]: plan for plan in planning.plans()}
            records = {record["title"]: record for _, record in storage.iter_records(root)}
            self.assertEqual(tasks["Проверить восстановление базы"]["project"], records["Переезд базы заказов"]["id"])
            self.assertEqual(tasks["Сравнить кедровые пробы"]["project"], records["Выпуск кедровой серии"]["id"])
            self.assertEqual(tasks["Забрать дорожный набор"]["direction"], records["Личное"]["id"])
            self.assertIsNone(tasks["Забрать дорожный набор"]["project"])
            plan_bytes = {plan["path"]: (root / plan["path"]).read_bytes() for plan in plans.values()}
            home = (root / "Home.md").read_text(encoding="utf-8")
            for plan in plans.values():
                self.assertIn(quote(plan["path"]), home)

            for title, plan_name, initial, proposed in (
                    ("Проверить восстановление базы", "Запуск Маяка", "2026-11-25", "2026-11-27"),
                    ("Забрать дорожный набор", "Подготовка к поездке", "2026-11-26", "2026-11-28"),
                    ("Сравнить кедровые пробы", "Пробы кедровой серии", "2026-11-27", "2026-11-30")):
                with self.subTest(task=title):
                    task = tasks[title]
                    preview = dates.preview(task["id"], proposed)
                    self.assertEqual((preview["before"], preview["after"]), (initial, proposed))
                    self.assertEqual({p["title"] for p in preview["plans"]}, {"План недели", plan_name})
                    self.assertEqual(planning.task(task["id"])["due"], initial)
                    dates.apply(task["id"], proposed, preview["revision"])
                    for name in ("План недели", plan_name):
                        row, = [row for row in planning.view(plans[name]["id"])["items"] if row["id"] == task["id"]]
                        self.assertEqual((row["due"], row["path"]), (proposed, task["path"]))
                    entry, = dates.history(task["id"])
                    self.assertEqual((entry["before"], entry["after"]), (initial, proposed))
            undated = tasks["Выбрать чехол для документов"]["id"]
            row, = [r for r in planning.view(plans["Подготовка к поездке"]["id"])["items"] if r["id"] == undated]
            self.assertIsNone(row["due"])
            for path, original in plan_bytes.items():
                self.assertEqual((root / path).read_bytes(), original)

    def test_fixture_publishes_only_after_all_links_are_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Practice"
            with patch.object(Planning, "add_task", side_effect=ValueError("interrupted")):
                with self.assertRaises(ValueError):
                    generate(root)
            self.assertFalse(root.exists())
            root.mkdir()
            with self.assertRaises(FileExistsError):
                generate(root)
            self.assertFalse(list(root.iterdir()))
