from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from second_brain import storage
from second_brain.dependencies import Dependencies
from second_brain.planning import Planning
from second_brain.schedule import Schedule


class DependencyTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.plans = Planning(self.root)
        self.links = Dependencies(self.root)
        self.dates = Schedule(self.root)
        self.origin = self.task("Проверка", due="2020-04-25", forecast="2020-04-27")
        self.mark = self.task("Готовность", kind="milestone", due="2020-04-28", forecast="2020-04-27")
        self.link(self.origin, self.mark)

    def task(self, title, *, kind="task", due=None, forecast=None, start=None, completed=None):
        item = self.plans.create_task(title, kind=kind, due=due)
        if start:
            path = self.root / item["path"]
            path.write_text(path.read_text().replace("---\n", f'---\nstart: "{start}"\n', 1))
        for field, value in (("forecast", forecast), ("completed", completed)):
            if value:
                self.set_date(item["id"], field, value)
        return item["id"]

    def set_date(self, key, field, value):
        options = {"actor": "manual", "reason": "По учебной записи"}
        preview = self.dates.preview(key, field, value, **options)
        return self.dates.apply(key, field, value, preview["revision"], **options)

    def link(self, predecessor, successor):
        return self.links.link(successor, predecessor)

    def snapshot(self):
        return {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_milestone_persists_on_shared_card_and_link_has_both_directions(self):
        parent, = self.links.links(self.mark)["predecessors"]
        child, = self.links.links(self.origin)["successors"]
        self.assertEqual((parent["id"], child["id"], child["kind"]), (self.origin, self.mark, "milestone"))
        self.assertEqual(parent["linkPath"], self.plans.task(self.mark)["path"])
        edge = (self.root / parent["linkPath"]).read_text().splitlines()[parent["linkLine"] - 1]
        self.assertIn(self.origin, edge)
        before = self.snapshot()
        self.link(self.origin, self.mark)
        self.assertEqual(self.snapshot(), before)
        target = self.root / parent["path"]
        target.rename(target.with_name("Проверка после переименования.md"))
        self.assertIn("после переименования", self.links.links(self.mark)["predecessors"][0]["path"])
        target.with_name("Проверка после переименования.md").unlink()
        self.assertTrue(self.links.links(self.mark)["predecessors"][0]["missing"])
        self.assertEqual(self.links.link(self.mark, self.origin, remove=True)["predecessors"], [])

    def test_simulation_is_read_only_updates_two_plans_and_preserves_agreements(self):
        plans = [self.plans.create_plan(title) for title in ("Запуск", "Неделя")]
        for p in plans:
            for key in (self.mark, self.origin):
                self.plans.add_task(p["id"], key)
        before = self.snapshot()
        preview = self.links.preview(self.origin, "2020-04-30")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([(r["id"], r["slackDays"]) for r in preview["rows"]], [(self.origin, -5), (self.mark, -2)])
        self.assertEqual({p["id"] for p in preview["rows"][1]["plans"]}, {p["id"] for p in plans})
        result = self.links.apply(self.origin, "2020-04-30", preview["revision"])
        self.assertEqual(result["changed"], 2)
        after = self.snapshot()
        changed = {p.relative_to(self.root).as_posix() for p in before if before[p] != after[p]}
        self.assertEqual(changed, {self.plans.task(key)["path"] for key in (self.origin, self.mark)})
        for p in plans:
            mark, work = self.plans.view(p["id"])["items"]
            self.assertEqual([r["due"] for r in (mark, work)], ["2020-04-28", "2020-04-25"])
            self.assertEqual([r["forecast"] for r in (mark, work)], ["2020-04-30"] * 2)
            self.assertIsNone(mark["completed"])
        self.assertEqual(self.dates.history(self.mark)[-1]["predecessors"], [self.origin])
        self.set_date(self.mark, "forecast", "2020-05-01")
        state = self.snapshot()
        self.assertTrue(self.links.apply(self.origin, "2020-04-30", preview["revision"])["replayed"])
        self.assertEqual(state, self.snapshot())
        with self.assertRaises(storage.ConflictError):
            self.links.apply(self.origin, "2020-04-30", preview["revision"], actor="manual")

    def test_constraints_completed_rows_unknown_durations_and_multiple_predecessors(self):
        normal = self.task("Работа с резервом", start="2020-04-29", due="2020-05-01", forecast="2020-05-04")
        unknown = self.task("Нет продолжительности", forecast="2020-05-02")
        later = self.task("Ожидает неизвестную работу", kind="milestone")
        done = self.task("Подтверждено", completed="2020-04-23", forecast="2020-04-28")
        unchanged = self.task("Ожидает результат", kind="milestone")
        other = self.task("Дальняя оценка", forecast="2020-05-06")
        joins = self.task("Все условия", kind="milestone")
        for a, b in ((self.mark, normal), (normal, unknown), (unknown, later), (self.origin, done),
                     (done, unchanged), (normal, joins), (other, joins)):
            self.link(a, b)
        preview = self.links.preview(self.origin, "2020-05-01")
        rows = {r["id"]: r for r in preview["rows"]}
        self.assertEqual((rows[normal]["status"], rows[normal]["slackDays"]), ("unchanged", -3))
        self.assertEqual((rows[unknown]["status"], rows[unknown]["slackDays"]), ("blocked", None))
        self.assertEqual(rows[later]["status"], "blocked")
        self.assertEqual((rows[done]["status"], rows[unchanged]["after"]), ("completed", "2020-04-23"))
        self.assertEqual(rows[joins]["after"], "2020-05-06")
        self.assertNotIn(other, rows)
        self.set_date(other, "forecast", None)
        with self.assertRaises(storage.ConflictError):
            self.links.apply(self.origin, "2020-05-01", preview["revision"])
        next_rows = {r["id"]: r for r in self.links.preview(self.origin, "2020-05-08")["rows"]}
        self.assertEqual(next_rows[normal]["after"], "2020-05-10")
        self.assertEqual(next_rows[joins]["status"], "blocked")
        early = self.links.preview(self.origin, "2020-04-22")
        self.assertEqual(next(r["after"] for r in early["rows"] if r["id"] == self.mark), "2020-04-27")
        with self.assertRaisesRegex(ValueError, "факт"):
            self.links.preview(done, "2020-04-25")

    def test_cycle_path_self_loop_and_competing_writers(self):
        third = self.task("Выпуск", kind="milestone")
        self.link(self.mark, third)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Цикл") as cycle:
            self.link(third, self.origin)
        for title in ("Проверка", "Готовность", "Выпуск"):
            self.assertIn(title, str(cycle.exception))
        with self.assertRaisesRegex(ValueError, "Цикл"):
            self.link(self.origin, self.origin)
        self.assertEqual(self.snapshot(), before)
        detached = self.task("Внешняя работа")
        with ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(self.link, a, b) for a, b in ((third, detached), (detached, third))]
            self.assertEqual(sum(isinstance(f.exception(), ValueError) for f in jobs), 1)

    def test_conflicting_relations_membership_or_edited_card_reject_whole_scenario(self):
        def preview():
            return self.links.preview(self.origin, "2020-04-30")["revision"]
        def verify_stale(revision):
            state = self.snapshot()
            with self.assertRaises(storage.ConflictError):
                self.links.apply(self.origin, "2020-04-30", revision)
            self.assertEqual(self.snapshot(), state)
        revision = preview()
        self.plans.add_task(self.plans.create_plan("Другой план")["id"], self.mark)
        verify_stale(revision)
        revision = preview()
        extra = self.task("Ещё веха", kind="milestone")
        self.link(self.mark, extra)
        verify_stale(revision)
        revision = preview()
        path = self.root / self.plans.task(self.mark)["path"]
        path.write_text(path.read_text() + "\nЗаметка владельца\n")
        verify_stale(revision)

    def test_two_apply_calls_commit_once_and_derived_forecast_returns_to_independent_floor(self):
        def apply():
            return self.links.apply(self.origin, "2020-04-30", revision)["replayed"]
        revision = self.links.preview(self.origin, "2020-04-30")["revision"]
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(f.result() for f in [pool.submit(apply), pool.submit(apply)]), [False, True])
        self.assertEqual(len(self.dates.history(self.mark)), 2)
        rows = self.links.preview(self.origin, "2020-04-27")["rows"]
        self.assertEqual(rows[1]["after"], "2020-04-27")
        rows = self.links.preview(self.origin, "2020-04-25")["rows"]
        self.assertEqual(rows[1]["after"], "2020-04-27")
        # Direct owner edits supersede the scenario basis without a CLI receipt.
        path = self.root / self.plans.task(self.mark)["path"]
        path.write_text(path.read_text().replace('forecast: "2020-04-30"', 'forecast: "2020-05-02"'))
        rows = self.links.preview(self.origin, "2020-04-25")["rows"]
        self.assertEqual(rows[1]["after"], "2020-05-02")

    def test_hypothesis_for_root_with_unknown_predecessor_discloses_uncertainty(self):
        self.link(self.task("Без прогноза", due="2020-04-20"), self.origin)
        result = self.links.preview(self.origin, "2020-04-29")
        self.assertIn("ещё нет даты", result["rows"][0]["reason"])

    def test_interrupted_second_write_restores_first_and_keeps_transaction_retryable(self):
        before = self.snapshot()
        revision = self.links.preview(self.origin, "2020-04-30")["revision"]
        second = self.root / self.plans.task(self.mark)["path"]
        replace = storage.replace_bytes
        def failing(path, old, new):
            if path == second:
                raise InterruptedError("Диск недоступен")
            return replace(path, old, new)
        with patch.object(storage, "replace_bytes", side_effect=failing):
            with self.assertRaises(InterruptedError):
                self.links.apply(self.origin, "2020-04-30", revision)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.links.apply(self.origin, "2020-04-30", revision)["changed"], 2)

    def test_manual_edit_during_partial_commit_is_preserved_and_requires_new_preview(self):
        revision = self.links.preview(self.origin, "2020-04-30")["revision"]
        first = self.root / self.plans.task(self.origin)["path"]
        second = self.root / self.plans.task(self.mark)["path"]
        replace = storage.replace_bytes

        def interrupt(path, old, new):
            if path == second:
                first.write_text(first.read_text() + "\nПояснение владельца\n")
                raise InterruptedError
            return replace(path, old, new)

        with patch.object(storage, "replace_bytes", side_effect=interrupt):
            with self.assertRaisesRegex(storage.ConflictError, "внешние правки"):
                self.links.apply(self.origin, "2020-04-30", revision)
        self.assertIn("Пояснение владельца", first.read_text())
        self.assertEqual(self.plans.task(self.mark)["forecast"], "2020-04-27")
        with self.assertRaisesRegex(storage.ConflictError, "часть"):
            self.links.apply(self.origin, "2020-04-30", revision)
        fresh = self.links.preview(self.origin, "2020-04-30")
        self.assertEqual(self.links.apply(self.origin, "2020-04-30", fresh["revision"])["changed"], 1)
        self.assertIn("Пояснение владельца", first.read_text())

    def test_process_death_hides_partial_update_until_next_writer_recovers_it(self):
        revision = self.links.preview(self.origin, "2020-04-30")["revision"]
        code = """
from second_brain import storage
from second_brain.dependencies import Dependencies
import os, sys
vault, task, other, revision = sys.argv[1:]
deps = Dependencies(vault)
second = deps.root / deps.planning.task(other)['path']
replace = storage.replace_bytes
def interrupted(path, old, new):
    if path == second:
        os._exit(81)
    return replace(path, old, new)
storage.replace_bytes = interrupted
deps.apply(task, '2020-04-30', revision)
"""
        run = subprocess.run([sys.executable, "-c", code, str(self.root), self.origin, self.mark, revision])
        self.assertEqual(run.returncode, 81)
        state = self.snapshot()
        self.assertEqual(self.plans.task(self.origin)["forecast"], "2020-04-27")
        self.assertEqual(self.links.preview(self.origin, "2020-04-30")["revision"], revision)
        self.assertEqual(state, self.snapshot())
        self.assertFalse(self.links.apply(self.origin, "2020-04-30", revision)["replayed"])
        self.assertFalse((self.root / ".second-brain-forecast-pending.json").exists())
        self.assertEqual(self.plans.task(self.mark)["forecast"], "2020-04-30")


if __name__ == "__main__":
    unittest.main()
