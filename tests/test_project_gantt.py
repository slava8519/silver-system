from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from second_brain import storage
from second_brain.cycles import Cycles
from second_brain.dependencies import Dependencies
from second_brain.gantt import Gantt
from second_brain.okr import KeyResults
from second_brain.okr_progress import Progress
from second_brain.planning import Planning
from second_brain.project_gantt import ProjectGantt
from second_brain.schedule import Schedule


class ProjectGanttTests(unittest.TestCase):
    def setUp(self):
        vault = TemporaryDirectory()
        self.addCleanup(vault.cleanup)
        self.root = storage.initialize(Path(vault.name) / "Synthetic")
        self.planning = Planning(self.root)
        self.chart = ProjectGantt(self.root)

    def card(self, kind, title, **fields):
        return storage.read_record(storage.create(self.root, kind, title, **fields))

    def test_membership_follows_the_task_project_across_plans_and_directions(self):
        projects = []
        areas = []
        works = []
        for name in ("ИТ", "Личное", "Парфюмерия"):
            area = self.card("direction", name)
            project = self.card("project", "Пилот", direction=area["id"])
            work = self.planning.create_task("Проверка", project=project["id"])
            projects.append(project["id"])
            areas.append(area["id"])
            works.append(work["id"])
            self.planning.create_task("Работа направления", direction=area["id"])
            row, = self.chart.view(project["id"])["items"]
            self.assertEqual((row["id"], row["direction"], row["directionTitle"]), (work["id"], area["id"], name))
            self.assertIsNone(row["due"])
            self.assertIsNone(row["readiness"])
        first, second = [self.planning.create_plan(name)["id"] for name in ("Неделя", "Квартал")]
        for plan in (first, second):
            for identifier in works:
                self.planning.add_task(plan, identifier)
        okr = KeyResults(self.root)
        goal = okr.create_objective("Запустить регламент", plan_id=first, direction=areas[0])
        kr = okr.create("Есть протокол", objective_id=goal["id"], unit="шт.", task_ids=[works[1]])
        okr.context_project(kr["id"], projects[0], present=True, revision=kr["revision"])
        standalone = self.planning.create_task("Приёмка", project=projects[0], kind="milestone")
        chart = self.chart.view(projects[0])
        self.assertEqual((chart["id"], chart["scope"], chart["tree"], chart["objectives"]),
                         (projects[0], "project", [], []))
        self.assertNotIn("planId", chart)
        self.assertNotIn("status", chart)
        self.assertCountEqual([r["id"] for r in chart["items"]], [works[0], standalone["id"]])
        self.assertEqual(next(r["kind"] for r in chart["items"] if r["id"] == standalone["id"]), "milestone")

    def test_live_task_dates_sources_and_project_fields_survive_moves_and_plan_closure(self):
        project = self.card("project", "Передача стенда", status="Ждём протокол", due="2026-11-30",
                            next_step="Сверить прогоны")
        task = self.planning.create_task("Прогнать сценарий", project=project["id"], due="2026-11-27")
        plan = self.planning.create_plan("Ноябрь")["id"]
        self.planning.add_task(plan, task["id"])
        cycles = Cycles(self.root)
        cycles.close(plan, cycles.preview(plan)["revision"])
        gantt = Gantt(self.root)
        gantt.edit({"id": task["id"], "actor": "manual", "field": "start", "value": "2026-11-25",
                    "revision": task["revision"]})
        dates = Schedule(self.root)
        for key, value in (("forecast", "2026-11-28"), ("completed", "2026-11-26")):
            options = {"actor": "manual", "reason": "По протоколу прогона"}
            preview = dates.preview(task["id"], key, value, **options)
            dates.apply(task["id"], key, value, preview["revision"], **options)
        line = "2026-11-26. Готовность составляет 80%."
        note = storage.create(self.root, "note", "Протокол", body=line)
        evidence = {"path": note.relative_to(self.root).as_posix(), "line": storage.read_record(note)["body_start"],
                    "quote": line}
        Progress(self.root).set_readiness(task["id"], value="80", kind="fact", date="2026-11-26", actor="ai",
                                         source=evidence, revision=self.planning.task(task["id"])["revision"])
        moved_note = note.rename(note.with_name("Сводка.md"))
        moved_note.write_text(moved_note.read_text().replace(line, "Отчёт за неделю.\n" + line))
        path = storage.find_record(self.root, project["id"])
        path = path.rename(path.with_name("Передача.md"))
        path.write_text(path.read_text().replace('status: "Ждём протокол"', '# Решение\nstatus: "Готов"'))
        moved_task = (self.root / task["path"]).rename(self.root / "Tasks" / "Прогон.md")
        chart = self.chart.view(project["id"])
        row, = chart["items"]
        self.assertEqual(row["path"], moved_task.relative_to(self.root).as_posix())
        for key, value in (("start", "2026-11-25"), ("due", "2026-11-27"), ("forecast", "2026-11-28"),
                           ("completed", "2026-11-26"), ("project", project["id"])):
            self.assertEqual(row[key], value)
            self.assertIn(value, moved_task.read_text().splitlines()[row[key + "Line"] - 1])
        self.assertEqual(row["readiness"]["value"], "80")
        self.assertEqual(row["readiness"]["source"]["path"], moved_note.relative_to(self.root).as_posix())
        self.assertEqual(row["readiness"]["source"]["line"], evidence["line"] + 1)
        self.assertEqual(gantt.detail(row["id"])["revision"], row["revision"])
        self.assertIsNone(gantt.view(plan)["items"][0]["completed"])
        project_state = chart["project"]
        for key, value in (("status", "Готов"), ("due", "2026-11-30"), ("nextStep", "Сверить прогоны")):
            source = project_state["stateSources"][key]
            self.assertEqual(project_state[key], value)
            self.assertEqual(source["path"], path.relative_to(self.root).as_posix())
            self.assertIn(value, path.read_text().splitlines()[source["line"] - 1])

    def test_external_and_missing_predecessors_keep_the_actual_link_as_evidence(self):
        project = self.card("project", "Калибровка")["id"]
        other = self.card("project", "Поставка")["id"]
        mark = self.planning.create_task("Приёмка", project=project, kind="milestone")
        internal = self.planning.create_task("Настройка", project=project)
        external = self.planning.create_task("Отгрузка", project=other, kind="milestone")
        missing = self.planning.create_task("Заявка")
        links = Dependencies(self.root)
        for predecessor in (internal, external, missing):
            links.link(mark["id"], predecessor["id"])
        (self.root / missing["path"]).unlink()
        view = self.chart.view(project)
        self.assertCountEqual([r["id"] for r in view["items"]], [mark["id"], internal["id"]])
        row = next(r for r in view["items"] if r["id"] == mark["id"])
        canonical = links.links(mark["id"])["predecessors"]
        for edge, expected in zip(row["predecessors"], canonical):
            self.assertEqual({k: v for k, v in edge.items() if k != "external"}, expected)
            self.assertIn(edge["id"], (self.root / edge["linkPath"]).read_text().splitlines()[edge["linkLine"] - 1])
        self.assertEqual([e["external"] for e in row["predecessors"]], [False, True, True])
        # Moving the task between projects in Markdown changes both live timelines.
        path = self.root / external["path"]
        path.write_text(path.read_text().replace(f'project: "{other}"', f'project: "{project}"'))
        refreshed = self.chart.view(project)
        self.assertEqual(len(refreshed["items"]), 3)
        row = next(r for r in refreshed["items"] if r["id"] == mark["id"])
        self.assertFalse(row["predecessors"][1]["external"])
        self.assertEqual(self.chart.view(other)["items"], [])

    def test_reading_an_empty_project_needs_no_index_lock_or_plan_directory(self):
        root = self.root.parent / "Read-only copy"
        project_dir = root / "Projects"
        project_dir.mkdir(parents=True)
        path = project_dir / "Проба.md"
        path.write_text("---\ntype: project\nschema_version: 1\nid: sample\ntitle: Проба\n---\n")
        original = {p: p.read_bytes() if p.is_file() else None for p in root.rglob("*")}
        view = ProjectGantt(root).view("sample")
        self.assertEqual(view["items"], [])
        self.assertEqual(view["project"]["stateSources"], {})
        self.assertEqual({p: p.read_bytes() if p.is_file() else None for p in root.rglob("*")}, original)
        with self.assertRaises(FileNotFoundError):
            ProjectGantt(root).view("unknown")
        with self.assertRaisesRegex(ValueError, "проекта"):
            self.chart.view(self.card("direction", "Дом")["id"])
