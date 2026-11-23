from pathlib import Path
import tempfile
import unittest

from examples.gantt_vault import generate, seed_milestone_practice
from second_brain.dependencies import Dependencies
from second_brain.gantt import Gantt
from second_brain.gantt_feedback import GanttFeedback
from second_brain.planning import Planning


def markdown_snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*.md")}


class MilestoneExamples(unittest.TestCase):
    def test_three_shared_milestones_and_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            root = generate(Path(folder) / "Practice")
            planning, graph = Planning(root), Dependencies(root)
            tasks = {row["title"]: row for row in planning.tasks()}
            original = markdown_snapshot(root)
            seed_milestone_practice(root)
            self.assertEqual(markdown_snapshot(root), original)
            cases = [
                ("Проверить восстановление базы", "Готовность к переключению", "Запуск Маяка", "2026-11-30", -1),
                ("Забрать дорожный набор", "Всё готово к выезду", "Подготовка к поездке", "2026-11-30", 0),
                ("Сравнить кедровые пробы", "Выбрать образец для серии", "Пробы кедровой серии", "2026-12-01", -3),
            ]
            for before, title, plan, proposed, days in cases:
                with self.subTest(title=title):
                    task, milestone = tasks[before], tasks[title]
                    self.assertEqual((milestone["kind"], milestone["project"], milestone["direction"]),
                                     ("milestone", task["project"], task["direction"]))
                    parents = planning.plans_for_task(milestone["id"])
                    self.assertEqual({p["title"] for p in parents}, {plan, "План недели"})
                    self.assertEqual([r["id"] for r in graph.links(milestone["id"])["predecessors"]], [task["id"]])
                    snap = markdown_snapshot(root)
                    preview = graph.preview(task["id"], proposed)
                    rows = {r["id"]: r for r in preview["rows"]}
                    self.assertEqual(set(rows), {task["id"], milestone["id"]})
                    self.assertEqual(rows[milestone["id"]]["slackDays"], days)
                    self.assertEqual(markdown_snapshot(root), snap)
                    graph.apply(task["id"], proposed, preview["revision"])
                    after = markdown_snapshot(root)
                    self.assertEqual({p for p in after if after[p] != snap.get(p)}, {task["path"], milestone["path"]})
                    for parent in parents:
                        reread = {r["id"]: r for r in planning.view(parent["id"])["items"]}
                        for card in (task, milestone):
                            self.assertEqual((reread[card["id"]]["due"], reread[card["id"]]["forecast"],
                                              reread[card["id"]]["completed"]), (card["due"], proposed, None))

    def test_milestone_edits_return_to_agent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = generate(Path(folder) / "Practice")
            planning, graph, feedback = Planning(root), Dependencies(root), GanttFeedback(root)
            tasks = {t["title"]: t for t in planning.tasks()}
            milestone = tasks["Всё готово к выезду"]
            pred = tasks["Забрать дорожный набор"]
            row = next(r for r in feedback.pending() if r["id"] == milestone["id"])
            self.assertEqual((row["kind"], row["predecessorCount"]), ("milestone", 1))
            feedback.acknowledge(row["id"], row["token"])
            graph.link(milestone["id"], pred["id"], remove=True, actor="manual")
            edited = next(r for r in feedback.pending() if r["id"] == milestone["id"])
            self.assertNotEqual(row["token"], edited["token"])
            self.assertEqual(edited["predecessorCount"], 0)
            graph.link(milestone["id"], pred["id"], actor="manual")
            restored = next(r for r in feedback.pending() if r["id"] == milestone["id"])
            self.assertNotEqual(restored["token"], row["token"])
            with self.assertRaisesRegex(ValueError, "вехи"):
                Gantt(root).edit({"id": milestone["id"], "revision": planning.task(milestone["id"])["revision"],
                                  "actor": "manual", "field": "start", "value": "2026-11-20"})
