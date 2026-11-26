from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from second_brain import storage
from second_brain.__main__ import command_line, run
from second_brain.cycles import Cycles
from second_brain.okr import KeyResults
from second_brain.okr_progress import Progress, _weighted
from second_brain.planning import Planning


class ResultRelations(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.plans = Planning(self.root)
        self.okr = KeyResults(self.root)
        self.weights = Progress(self.root)
        self.area = self.card("direction", "Работа")
        self.period = self.plans.create_plan("Сезон")
        self.goal = self.okr.create_objective("Выпустить серию", plan_id=self.period["id"], direction=self.area)
        self.parser = command_line()

    def card(self, kind, title, **options):
        return storage.read_record(storage.create(self.root, kind, title, **options))["id"]

    def cli(self, *args):
        return run(self.parser.parse_args(["--vault", str(self.root), *args]))

    def create(self, title="Готовая серия", tasks=()):
        return self.okr.create(title, objective_id=self.goal["id"], unit="%", task_ids=tasks)

    def context(self, kr, project, present):
        return self.okr.context_project(kr["id"], project, present=present, revision=kr["revision"])

    def test_empty_project_never_adds_its_tasks_and_snapshot_preserves_project_at_closure(self):
        own_project = self.card("project", "Типография", direction=self.area)
        family = self.card("direction", "Личное")
        other_project = self.card("project", "Переезд", direction=family)
        task = self.plans.create_task("Упаковать коробки", project=other_project)
        kr = self.create()
        initial = self.root / kr["path"]
        original = initial.read_bytes()
        own = self.context(kr, own_project, True)
        self.assertEqual(own, self.context(kr, own_project, True))
        with self.assertRaises(storage.ConflictError):
            self.context(kr, other_project, True)
        with self.assertRaises(ValueError):
            self.context(own, self.area, True)
        linked = self.context(own, other_project, True)
        self.assertEqual(self.okr.board(self.period["id"])["items"], [])
        self.assertEqual(self.plans.plans_for_task(task["id"]), [])
        self.assertTrue(original in initial.read_bytes())
        project = storage.find_record(self.root, own_project)
        renamed = project.rename(project.with_name("Проект в новой папке.md"))
        value = self.okr.show(kr["id"])
        self.assertEqual(self.root / value["contextProjects"][0]["path"], renamed)
        renamed.unlink()
        value = self.okr.show(kr["id"])
        self.assertEqual(value["contextProjects"][0], {"id": own_project, "path": None, "title": None, "missing": True})
        updated = self.context(value, own_project, False)
        self.assertEqual(updated["contextProjects"], linked["contextProjects"][1:])
        self.assertEqual(updated, self.context(value, own_project, False))
        cycles = Cycles(self.root)
        pid = self.period["id"]
        cycles.close(pid, cycles.preview(pid)["revision"])
        before = self.okr.board(pid)
        self.assertEqual(before["objectives"][0]["results"][0]["contextProjects"], updated["contextProjects"])
        storage.find_record(self.root, other_project).unlink()
        self.assertEqual(self.okr.board(pid), before)
        with self.assertRaises(storage.ConflictError):
            self.context(updated, other_project, False)

    def test_unlink_removes_only_its_edge_and_preserves_weight_decision_and_readiness(self):
        task = self.plans.create_task("Подготовить пробу")
        free = self.plans.create_task("Проверить шкаф")
        other_plan = self.plans.create_plan("Неделя")
        self.plans.add_task(other_plan["id"], task["id"])
        one = self.create(tasks=[task["id"], free["id"]])
        two = self.create("Контроль", tasks=[task["id"]])
        version = self.weights.set_weight(one["id"], task_id=task["id"], weight=".5", revision=one["revision"])["revision"]
        self.weights.set_weight(two["id"], task_id=task["id"], weight="1", revision=two["revision"])
        self.weights.set_readiness(task["id"], value=0, kind="fact", date="2026-11-24", actor="manual",
                                   reason="Ещё не начинали", revision=task["revision"])
        task_file = self.root / task["path"]
        original = task_file.read_bytes()
        with self.assertRaises(storage.ConflictError):
            self.okr.unlink(one["id"], task["id"], one["revision"])
        with patch.object(storage, "replace_bytes", side_effect=OSError("не опубликовано")):
            with self.assertRaises(OSError):
                self.okr.unlink(one["id"], task["id"], version)
        still = self.okr.show(one["id"])
        self.assertEqual(still["taskWeights"][task["id"]], "0.5")
        removed = self.okr.unlink(one["id"], task["id"], version)
        self.assertEqual(removed["tasks"], [still["tasks"][1]])
        self.assertEqual(removed["taskWeights"], {})
        self.assertEqual(self.okr.unlink(one["id"], task["id"], version), removed)
        self.assertEqual(self.okr.show(two["id"])["taskWeights"], {task["id"]: "1"})
        self.assertEqual({p["id"] for p in self.plans.plans_for_task(task["id"])}, {self.period["id"], other_plan["id"]})
        self.assertEqual(task_file.read_bytes(), original)
        renewed = self.okr.link(one["id"], task["id"], removed["revision"])
        self.assertNotIn(task["id"], renewed["taskWeights"])
        self.assertIn("вес 0.5", (self.root / one["path"]).read_text())
        task_file.unlink()
        cleared = self.okr.unlink(one["id"], task["id"], renewed["revision"])
        self.assertFalse(any(row["id"] == task["id"] for row in cleared["tasks"]))

    def test_manual_annotations_and_orphaned_weight_on_relink(self):
        kr = self.create()
        t = self.plans.create_task("Сверка")
        kr = self.okr.link(kr["id"], t["id"], kr["revision"])
        self.weights.set_weight(kr["id"], task_id=t["id"], weight=0, revision=kr["revision"])
        path = self.root / kr["path"]
        raw = path.read_text()
        member = next(l for l in raw.splitlines() if storage.id_marker("task", t["id"]) in l)
        # The owner removed a link in Obsidian but kept their prose and the old weight.
        path.write_bytes(raw.replace(member, "- Решение владельца: отдельно проверить образец.").replace("\n", "\r\n").encode())
        project = self.card("project", "Ароматы", direction=self.area)
        current = self.okr.show(kr["id"])
        with ThreadPoolExecutor(2) as pool:
            choices = [pool.submit(self.context, current, project, True),
                       pool.submit(self.okr.link, kr["id"], t["id"], current["revision"])]
        self.assertEqual(sum(isinstance(r.exception(), storage.ConflictError) for r in choices), 1)
        current = self.okr.show(kr["id"])
        current = self.okr.link(kr["id"], t["id"], current["revision"])
        current = self.context(current, project, True)
        body = path.read_bytes()
        self.assertNotIn(t["id"], current["taskWeights"])
        self.assertNotIn(b"\n", body.replace(b"\r\n", b""))
        note = "личный комментарий"
        marker = storage.id_marker("project", project)
        path.write_bytes(body.replace(marker.encode(), (marker + " " + note).encode()))
        self.context(self.okr.show(kr["id"]), project, False)
        self.assertIn(note.encode(), path.read_bytes())
        self.assertIn("Решение владельца".encode(), path.read_bytes())

    def test_cli_projection_and_closed_task_unlink(self):
        task = self.plans.create_task("Сверить")
        kr = self.create(tasks=[task["id"]])
        version = kr["revision"]
        ids = [self.card("project", n) for n in ("Свет", "Кедр")]
        for project in ids:
            saved = self.cli("okr-context", kr["id"], project, "--add", "--revision", version)
            version = saved["revision"]
        page = self.cli("okr-show", kr["id"], "--projects", "--limit", "1", "--offset", "1")
        self.assertEqual((page["total"], page["truncated"], page["items"][0]["id"]), (2, False, ids[1]))
        saved = self.cli("okr-context", kr["id"], ids[0], "--remove", "--revision", version)
        clean = self.cli("okr-unlink", kr["id"], task["id"], "--revision", saved["revision"])
        self.assertEqual((clean["tasksTotal"], clean["projectsTotal"]), (0, 1))
        self.okr.link(kr["id"], task["id"], clean["revision"])
        cycles = Cycles(self.root)
        pid = self.period["id"]
        cycles.close(pid, cycles.preview(pid)["revision"])
        now = self.okr.show(kr["id"])
        with self.assertRaises(storage.ConflictError):
            self.cli("okr-unlink", kr["id"], task["id"], "--revision", now["revision"])

    def test_unknown_and_explicit_zero_weights_and_readings(self):
        base = [("first", "0", False)]
        weights = {"first": "1"}
        for weight, value, result, missing_weight, missing_value in (
            (None, None, None, True, True), (None, "0", None, True, False),
            ("0", None, "0", False, False), ("0", "0", "0", False, False),
        ):
            with self.subTest(weight=weight, reading=value):
                known = _weighted(base + [("second", value, False)], {**weights, "second": weight}, "formula")
                self.assertEqual(known["value"], result)
                self.assertEqual(known["missingWeights"], ["second"] if missing_weight else [])
                self.assertEqual(known["missingValues"], ["second"] if missing_value else [])
