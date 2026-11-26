from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from second_brain import storage
from second_brain.cycles import Cycles
from second_brain.gantt import Gantt
from second_brain.okr import KeyResults
from second_brain.plan_tree import outline, set_parent
from second_brain.planning import Planning


class PlanTreeTests(unittest.TestCase):
    def test_parent_from_other_result_explains_hierarchy_without_joining_the_result(self):
        board = {
            "items": [{"id": "parent"}, {"id": "child", "parentId": "parent"},
                      {"id": "support", "parentId": "child"}],
            "objectives": [{"id": "goal", "title": "Goal", "results": [
                {"id": "r1", "title": "First", "taskIds": ["parent"]},
                {"id": "r2", "title": "Second", "taskIds": ["child"]},
            ]}],
        }
        branches = outline(board)
        tasks = [entry for entry in branches if entry["kind"] == "task"]
        self.assertEqual([(e["resultId"], e["id"], e["contextOnly"], e["linkedToResult"])
                          for e in tasks], [
            ("r1", "parent", False, True), ("r2", "parent", True, False),
            ("r2", "child", False, True), ("r2", "support", False, False),
        ])
        self.assertEqual(tasks[2]["parentKey"], tasks[1]["key"])
        self.assertEqual(tasks[3]["parentKey"], tasks[2]["key"])

    def test_shared_task_separate_parent_relation_and_sealed_period(self):
        with TemporaryDirectory() as temp:
            root = storage.initialize(Path(temp) / "Vault")
            area = storage.read_record(storage.create(root, "direction", "Работа"))["id"]
            p, kr = Planning(root), KeyResults(root)
            quarter = p.create_plan("Квартал")["id"]
            weekly = p.create_plan("Неделя")["id"]
            goal = kr.create_objective("Надёжный выпуск", plan_id=quarter, direction=area)
            task = p.create_task("Контрольный запуск")
            sub = p.create_task("Сверить протокол", kind="milestone")
            for plan in (quarter, weekly):
                p.add_task(plan, sub["id"])
                p.add_task(plan, task["id"])
            result_ids = [kr.create(title, objective_id=goal["id"], unit="мин", task_ids=[task["id"]])["id"]
                          for title in ("Время запуска", "Время ответа")]
            revision = p.view(quarter)["revision"]
            receipt = set_parent(p, quarter, sub["id"], task["id"], revision)
            self.assertEqual(set_parent(p, quarter, sub["id"], task["id"], revision), receipt)
            with self.assertRaises(storage.ConflictError):
                set_parent(p, quarter, sub["id"], None, revision)
            with self.assertRaisesRegex(ValueError, "замыкает"):
                set_parent(p, quarter, task["id"], sub["id"], receipt["revision"])
            self.assertFalse(any(r.get("parentId") for r in p.view(weekly)["items"]))
            board = kr.board(quarter)
            self.assertIn(sub["id"], board["gaps"]["unlinkedTaskIds"])
            chart = Gantt(root).view(quarter)
            self.assertEqual(len(chart["items"]), 2)
            for key in result_ids:
                parent, child = [e for e in chart["tree"] if e["resultId"] == key and e["kind"] == "task"]
                self.assertEqual((child["level"], child["parentKey"]), (3, parent["key"]))
            cycle = Cycles(root)
            cycle.close(quarter, cycle.preview(quarter)["revision"])
            self.assertEqual(chart["tree"], Gantt(root).view(quarter)["tree"])
            with self.assertRaises(storage.ConflictError):
                set_parent(p, quarter, sub["id"], None, receipt["revision"])
