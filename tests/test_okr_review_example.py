import tempfile
import unittest
from pathlib import Path

from examples.okr_review_vault import generate
from second_brain import storage
from second_brain.okr import KeyResults


class ReviewExampleTests(unittest.TestCase):
    def test_season_goal_has_dated_work_two_project_contexts_and_a_second_open_result(self):
        with tempfile.TemporaryDirectory() as work:
            root, plan_id, result_id = generate(Path(work) / "Quarter")
            service = KeyResults(root)
            board = service.board(plan_id)
            goal = next(g for g in board["objectives"] if any(r["id"] == result_id for r in g["results"]))
            self.assertEqual(len(goal["results"]), 2)
            card = next(r for r in goal["results"] if r["id"] == result_id)
            sibling = next(r for r in goal["results"] if r["id"] != result_id)
            self.assertEqual((card["weight"], card["progress"]["value"]), ("0.3", "50.5"))
            self.assertEqual((sibling["weight"], sibling["progress"]["value"]), ("0.7", None))
            self.assertEqual((goal["progress"]["weightSum"], goal["progress"]["value"]), ("1", None))

            items = {r["id"]: r for r in board["items"]}
            tasks = [items[t] for t in card["taskIds"]]
            self.assertEqual(len(tasks), 5)
            self.assertTrue(all(t["start"] and t["due"] and t["forecast"] for t in tasks))
            self.assertEqual({t["project"] for t in tasks}, {p["id"] for p in card["contextProjects"]})
            self.assertEqual(len(card["contextProjects"]), 2)
            milestone, = [t for t in items.values() if t.get("parentId") == tasks[0]["id"] and t["kind"] == "milestone"]
            self.assertEqual(milestone["forecast"], "2027-01-10")

            numeric, warehouse = card["criteria"]
            self.assertEqual((numeric["baseline"]["value"], numeric["operator"], numeric["target"]), ("11", "<=", "5"))
            self.assertIsNone(card["measurement"])
            self.assertIsNone(warehouse["measurement"])
            note, record = next((p, r) for p, r in storage.iter_records(root)
                                if r["title"] == "Учебный протокол для ввода нового замера")
            source = {"path": note.relative_to(root).as_posix(), "line": record["body_start"]}
            updated = service.observe_criterion(result_id, numeric["id"], value=4, date="2026-11-24",
                                                source=source, revision=card["revision"])
            self.assertIsNone(updated["criteriaMet"])
            updated = service.observe_criterion(result_id, warehouse["id"], value=True, date="2026-11-24",
                                                source={**source, "line": source["line"] + 1},
                                                revision=updated["revision"])
            self.assertTrue(updated["criteriaMet"])
            with self.assertRaises(FileExistsError):
                generate(root)


if __name__ == "__main__":
    unittest.main()
