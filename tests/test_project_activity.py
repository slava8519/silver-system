import tempfile
import unittest
from pathlib import Path

from examples.project_activity_vault import generate
from second_brain import cycles, storage
from second_brain.__main__ import command_line, run
from second_brain.okr import KeyResults
from second_brain.project_activity import ProjectActivity


class ProjectActivityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.root = storage.initialize(self.folder / "Sandbox")
        self.parser = command_line()
        self.results = KeyResults(self.root)
        self.planning = self.results.planning

    def cli(self, *args, root=None):
        return run(self.parser.parse_args(["--vault", str(root or self.root), *args]))

    def card(self, kind, title, **fields):
        return storage.read_record(storage.create(self.root, kind, title, **fields))["id"]

    def test_annual_project_charts_tasks_outside_okr_and_attribution_to_three_periods(self):
        root, project_id, annual, quarter = generate(self.folder / "Workshop")
        catalog = ProjectActivity(root).catalog()
        project = next(p for p in catalog["projects"] if p["id"] == project_id)
        self.assertEqual((project["taskCount"], project["okrTaskCount"]), (4, 2))
        self.assertEqual((project["start"], project["end"]), ("2026-02-02", "2026-12-18"))
        self.assertEqual(project["projectStart"], "2026-01-12")
        links = {p["id"]: p for p in project["plans"]}
        self.assertTrue(links[annual]["explicitlyIncluded"])
        self.assertFalse(links[quarter]["explicitlyIncluded"])
        self.assertEqual((links[annual]["taskCount"], links[annual]["okrTaskCount"]), (4, 0))
        self.assertEqual((links[quarter]["taskCount"], links[quarter]["okrTaskCount"]), (2, 2))
        self.assertEqual(links[quarter]["contextResultCount"], 1)
        self.assertEqual([p["taskCount"] for p in links.values() if p["status"] == "closed"], [1])
        idea = next(p for p in catalog["projects"] if p["title"] == "Таблички для выставочных полок")
        self.assertEqual((idea["taskCount"], idea["plans"], idea["start"]), (0, [], None))
        chart = self.cli("project-gantt", project_id, "--limit", "2", root=root)
        self.assertEqual((chart["total"], len(chart["items"]), chart["truncated"]), (4, 2, True))
        second = self.cli("project-gantt", project_id, "--limit", "2", "--offset", "2", root=root)
        self.assertEqual(len({r["id"] for r in chart["items"] + second["items"]}), 4)
        page = self.cli("activity-list", "--view", "plans", "--project", project_id, "--match", "IV", root=root)
        self.assertEqual((page["total"], page["items"][0]["id"]), (1, quarter))
        self.assertEqual(page["items"][0]["projectLink"]["taskCount"], 2)
        self.assertNotIn("projects", page["items"][0])
        with self.assertRaises(FileExistsError):
            generate(root)

    def test_project_start_can_precede_any_tasks_and_can_be_cleared_without_changing_due(self):
        create = ("create", "project", "Съёмка", "--project-start", "2027-01-08", "--due", "2027-12-09",
                  "--request-id", "shoot")
        project = self.cli(*create)["id"]
        self.assertEqual(self.cli(*create)["id"], project)
        detail = self.cli("show", project, "--brief")
        graph = self.cli("project-gantt", project)
        self.assertEqual((graph["total"], graph["project"]["projectStart"]), (0, "2027-01-08"))
        row, = ProjectActivity(self.root).catalog()["projects"]
        self.assertEqual((row["projectStart"], row["start"], row["end"]), ("2027-01-08", None, None))
        ref = detail["fieldSources"]["projectStart"]
        self.assertEqual(ref, graph["project"]["stateSources"]["projectStart"])
        self.assertEqual(ref, row["stateSources"]["projectStart"])
        path = self.root / ref["path"]
        self.assertIn("2027-01-08", path.read_text().splitlines()[ref["line"] - 1])
        self.assertEqual(self.cli("show", project)["project_start"], "2027-01-08")
        for option, value in (("--project-start", "2027-02-29"), ("--project-start", "2028-01-01"),
                              ("--due", "2026-12-31"), ("--project-start", "в январе")):
            before = path.read_bytes()
            with self.subTest(option=option, value=value), self.assertRaises(ValueError):
                self.cli("update", project, "--revision", detail["revision"], option, value)
            self.assertEqual(before, path.read_bytes())
        self.cli("update", project, "--revision", detail["revision"], "--clear-project-start")
        cleared = self.cli("show", project, "--brief")
        self.assertEqual((cleared["projectStart"], cleared["due"]), (None, "2027-12-09"))
        self.assertNotIn("projectStart", cleared["fieldSources"])
        self.assertNotIn("project_start:", path.read_text())
        with self.assertRaises(ValueError):
            self.cli("create", "project", "Обратный срок", "--project-start", "2027-03-01", "--due", "2027-02-01")
        with self.assertRaises(ValueError):
            self.cli("create", "note", "Личное", "--project-start", "2027-03-01")

    def test_legacy_text_due_and_manual_fields_survive_unrelated_edits(self):
        project = self.card("project", "Старая карточка", due="после переезда")
        original = self.cli("show", project, "--brief")
        self.assertIsNone(original["projectStart"])
        self.cli("update", project, "--revision", original["revision"], "--project-start", "2027-05-10")
        detail = self.cli("show", project, "--brief")
        self.assertEqual((detail["projectStart"], detail["due"]), ("2027-05-10", "после переезда"))
        path = self.root / detail["path"]
        legacy = path.read_text().replace('due: "после переезда"', 'due: "2027-04-01"  # вручную')
        path.write_text(legacy)
        detail = self.cli("show", project, "--brief")
        self.cli("update", project, "--revision", detail["revision"], "--status", "Открыт")
        self.assertIn('due: "2027-04-01"  # вручную', path.read_text())
        self.assertEqual(self.cli("show", project, "--brief")["projectStart"], "2027-05-10")

    def test_context_only_project_deduplication_orphan_results_and_no_implied_membership(self):
        a = self.card("direction", "Исследования")
        owned = self.card("project", "Стенд", direction=a)
        context = self.card("project", "Словарь", direction=a)
        no_link = self.card("project", "Новая тема", direction=a)
        task = self.planning.create_task("Опыт", project=owned)
        extra = self.planning.create_task("Проверка", project=owned, due="2026-12-01")
        independent = self.planning.create_task("Вне проекта", direction=a)
        plan = self.planning.create_plan("Период")["id"]
        self.planning.add_task(plan, task["id"])
        self.planning.add_task(plan, independent["id"])
        goal = self.results.create_objective("Проверяемые условия", plan_id=plan, direction=a)["id"]
        for name in ("Первое условие", "Второе условие"):
            kr = self.results.create(name, unit="шт", objective_id=goal, task_ids=[task["id"]])
            self.results.context_project(kr["id"], context, present=True, revision=kr["revision"])
        legacy = self.results.create("КР без периода", unit="шт", direction=a, objective="Проверяемые условия",
                                     task_ids=[extra["id"]])
        result = ProjectActivity(self.root).catalog()
        period, = result["plans"]
        self.assertEqual((period["taskCount"], period["objectiveCount"], period["resultCount"]), (2, 1, 2))
        self.assertEqual(set(p["id"] for p in period["projects"]), {context, owned})
        links = {p["id"]: p for p in period["projects"]}
        self.assertEqual((links[owned]["taskCount"], links[owned]["okrTaskCount"]), (1, 1))
        self.assertEqual((links[context]["taskCount"], links[context]["contextResultCount"]), (0, 2))
        projects = {p["id"]: p for p in result["projects"]}
        self.assertEqual(projects[no_link]["plans"], [])
        row, = result["unassignedResults"]
        self.assertEqual((row["id"], row["missingObjective"], row["projectIds"]), (legacy["id"], False, [owned]))
        self.assertEqual(self.cli("activity-list", "--view", "unassigned", "--project", context)["total"], 0)
        self.assertEqual(self.cli("project-gantt", context)["total"], 0)

    def test_retrospective_keeps_project_before_task_moves_and_preserves_unknown_old_counts(self):
        area = self.card("direction", "Мастерская")
        before = self.card("project", "Первый зал", direction=area)
        after = self.card("project", "Второй зал", direction=area)
        task = self.planning.create_task("Перенести станцию", project=before, due="2026-10-09")
        period = self.planning.create_plan("Осень")["id"]
        self.planning.add_task(period, task["id"])
        service = cycles.Cycles(self.root)
        service.close(period, service.preview(period)["revision"])
        path, raw, record, header = self.planning._find("plan", period)
        meta = cycles.closed_metadata(record)
        original = storage.comment_json(meta)
        rows = [{k: v for k, v in row.items() if k != "snapshotLine"} for row in cycles.frozen_rows(record, meta)]
        meta.pop("okr")
        new_header = storage._patch_header(header, record, {cycles.CYCLE_SEAL: cycles._digest([meta, rows])})
        path.write_bytes(new_header + record["body"].replace(original, storage.comment_json(meta)).encode())
        task_path = self.root / task["path"]
        task_path.write_text(task_path.read_text().replace(before, after).replace("2026-10-09", "2027-02-07"))
        snapshot = path.read_bytes()
        catalog = ProjectActivity(self.root).catalog()
        plan, = catalog["plans"]
        relation, = plan["projects"]
        self.assertEqual((plan["status"], plan["end"], relation["id"]), ("closed", "2026-10-09", before))
        self.assertEqual((plan["hierarchyRecorded"], plan["resultCount"], relation["okrTaskCount"]), (False, None, None))
        projects = {p["id"]: p for p in catalog["projects"]}
        self.assertEqual((projects[before]["taskCount"], len(projects[before]["plans"])), (0, 1))
        self.assertEqual((projects[after]["taskCount"], projects[after]["end"], projects[after]["plans"]), (1, "2027-02-07", []))
        self.assertEqual(snapshot, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
