from pathlib import Path
import tempfile
import unittest

from examples.okr_vault import _populate, generate
from examples.planning_vault import generate as generate_plans
from second_brain import storage
from second_brain.okr import KeyResults
from second_brain.planning import Planning
from second_brain.query import WorkspaceQuery


class OkrExamples(unittest.TestCase):
    def test_adding_results_preserves_existing_cards_and_plan_memberships(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = generate_plans(Path(temporary) / "Practice")
            before = {p: p.read_bytes() for p in root.rglob("*.md") if p != root / "Home.md"}
            results, plans = KeyResults(root), Planning(root)
            self.assertEqual(results.list(), [])
            self.assertFalse((root / "Plans" / "OKR").exists())
            _populate(root)
            for path, original in before.items():
                self.assertEqual(path.read_bytes(), original, path)

            expected = {
                "Время восстановления базы": ("18", "мин", "15", "Запуск Маяка"),
                "Масса дорожного набора": ("4.2", "кг", "4", "Подготовка к поездке"),
                "Стойкость кедровой ноты": ("3.5", "ч", "6", "Пробы кедровой серии"),
            }
            summaries = results.list()
            self.assertEqual({r["title"] for r in summaries}, set(expected))
            self.assertEqual(len({r["direction"] for r in summaries}), 3)
            for summary in summaries:
                with self.subTest(metric=summary["title"]):
                    card = results.show(summary["id"])
                    value, unit, target, plan_name = expected[card["title"]]
                    measured = card["measurement"]
                    self.assertEqual((measured["value"], card["unit"], card["target"]), (value, unit, target))
                    self.assertEqual(measured["date"], "2026-11-22")
                    source = measured["source"]
                    _, opened = WorkspaceQuery(root).open_citation(source)
                    self.assertEqual((opened["path"], opened["start"]), (source["path"], source["line"]))
                    line = (root / source["path"]).read_text().splitlines()[source["line"] - 1]
                    self.assertEqual(line, source["quote"])
                    task, = card["tasks"]
                    memberships = plans.plans_for_task(task["id"])
                    self.assertEqual({p["title"] for p in memberships}, {"План недели", plan_name})
                    for plan in memberships:
                        row, = [r for r in plans.view(plan["id"])["items"] if r["id"] == task["id"]]
                        self.assertEqual(row["path"], task["path"])
                    link = storage.markdown_link(card["title"], root / card["path"], root)
                    self.assertIn(link, (root / "Home.md").read_text())

    def test_generator_requires_a_new_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = generate(Path(temporary) / "Practice")
            self.assertEqual(len(KeyResults(target).list()), 3)
            with self.assertRaises(FileExistsError):
                generate(target)
