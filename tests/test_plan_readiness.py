import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from second_brain import okr_progress_cli, storage
from second_brain.cycles import Cycles
from second_brain.gantt import Gantt
from second_brain.gantt_feedback import GanttFeedback
from second_brain.okr_progress import Progress
from second_brain.plan_transaction import _journal
from second_brain.planning import Planning


class PlanReadinessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "vault")
        self.service = Progress(self.root)
        self.plans = self.service.planning
        self.plan = self.plans.create_plan("Квартал")["id"]
        self.parser = argparse.ArgumentParser()
        okr_progress_cli.add_commands(self.parser.add_subparsers(dest="command"))
        self.note = storage.create(self.root, "note", "Протокол", "Начало 2026-11-20.\nНовый этап 2026-11-24.")
        self.source = {"path": self.note.relative_to(self.root).as_posix(), "line": storage.read_record(self.note)["body_start"]}

    def page(self, *args):
        args = self.parser.parse_args(["plan-readiness", self.plan, *args])
        return okr_progress_cli.run(self.root, args)

    def task(self, title, area, *, add=True):
        direction = storage.read_record(storage.create(self.root, "direction", area))["id"]
        task = self.plans.create_task(title, direction=direction)
        if add:
            self.plans.add_task(self.plan, task["id"])
        return task

    def reading(self, task, value, *, source=None):
        return self.service.set_readiness(task["id"], value=value, kind="estimate", date="2026-11-20",
                                          revision=self.plans.task(task["id"])["revision"], actor="manual",
                                          reason="Оценка владельца", source=source)

    def test_unique_plan_members_and_targeted_history_in_three_directions(self):
        it = self.task("Запуск", "ИТ")
        personal = self.task("Поездка", "Личное", add=False)
        sample = self.task("Контроль", "Парфюмерия")
        goal = self.service.results.create_objective("Цель", plan_id=self.plan, direction=personal["direction"])
        self.service.results.create("Результат", objective_id=goal["id"], task_ids=[it["id"], personal["id"]])
        self.reading(it, "0", source=self.source)
        self.reading(personal, "50", source=self.source)
        first = self.page("--limit", "2")
        second = self.page("--limit", "2", "--offset", str(first["nextOffset"]))
        self.assertEqual([r["id"] for r in first["items"] + second["items"]], [it["id"], sample["id"], personal["id"]])
        self.assertEqual((first["planTotal"], second["nextOffset"]), (3, None))
        known, unknown = first["items"]
        self.assertEqual((known["value"], known["actor"], known["kind"]), ("0", "manual", "estimate"))
        self.assertEqual(unknown["reviewFlags"], ["unknownValue"])
        self.assertEqual(first["sourcePaths"][known["source"]["pathIndex"]], self.source["path"])
        self.assertEqual(known["source"]["line"], self.source["line"])
        line = (self.root / it["path"]).read_text().splitlines()[known["cardLine"] - 1]
        self.assertIn("0% · оценка", line)

        # The source path table is local to the returned page. One catalog scan serves all IDs.
        full = self.page()
        self.assertEqual(full["sourcePaths"], [self.source["path"]])
        calls = []
        original = Planning._read_entries

        def counted(service, kind, identifiers=None):
            calls.append(kind)
            yield from original(service, kind, identifiers)

        with patch.object(Planning, "_read_entries", counted):
            self.assertEqual(self.page("--review-on", "2026-11-28", "--candidates")["total"], 1)
        self.assertEqual(calls.count("task"), 1)

    def test_review_cutoffs_feedback_new_recorded_event_and_unavailable_source(self):
        task = self.task("Сборка", "ИТ")
        path = self.root / task["path"]
        path.write_text(path.read_text().replace("---\n", "---\nstart: 2026-11-21\nforecast: 2026-11-27\n", 1))
        self.reading(task, "0", source=self.source)
        before = self.page("--review-on", "2026-11-27")["items"][0]
        self.assertEqual(before["reviewFlags"], ["firstGanttReview"])
        feedback = GanttFeedback(self.root)
        pending = next(r for r in feedback.pending(self.plan) if r["id"] == task["id"])
        feedback.acknowledge(task["id"], pending["token"])
        gantt = Gantt(self.root)
        gantt.edit({"id": task["id"], "revision": self.plans.task(task["id"])["revision"], "field": "latest",
                    "actor": "manual", "date": "2026-11-24", "text": "Этап завершён",
                    "sources": [{**self.source, "line": self.source["line"] + 1}]})
        row = self.page("--review-on", "2026-11-28", "--candidates")["items"][0]
        self.assertEqual(set(row["reviewFlags"]), {"pastForecast", "zeroAfterWeek", "laterCitedEvent", "unreviewedGantt"})
        self.assertEqual(row["eventDate"], "2026-11-24")
        self.assertEqual(path.read_text().splitlines()[row["eventLine"] - 1], "Этап завершён")
        self.note.write_text(self.note.read_text().replace("Начало 2026-11-20.", "Цитата изменилась."))
        moved_source = self.page()["items"][0]
        self.assertTrue(moved_source["source"]["unavailable"])
        self.assertIn("unavailableSource", moved_source["reviewFlags"])
        path.write_text(path.read_text().replace("---\n", "---\ncompleted: 2026-11-28\n", 1))
        final = self.page("--review-on", "2026-11-29")["items"][0]
        self.assertTrue({"pastForecast", "zeroAfterWeek"}.isdisjoint(final["reviewFlags"]))

    def test_frozen_values_and_members_survive_removal_of_live_task(self):
        task = self.task("Отъезд", "Личное")
        self.reading(task, "50")
        close = Cycles(self.root)
        close.close(self.plan, close.preview(self.plan)["revision"])
        (self.root / task["path"]).unlink()
        with patch.object(Planning, "member_ids", side_effect=AssertionError("opening current plan membership")):
            page = self.page()
        self.assertTrue(page["historical"])
        self.assertEqual(page["items"][0]["value"], "50")
        snapshot = (self.root / page["snapshotPath"]).read_text().splitlines()[page["items"][0]["snapshotLine"] - 1]
        self.assertIn(task["id"], snapshot)
        with self.assertRaisesRegex(ValueError, "активного"):
            self.page("--review-on", "2026-11-28", "--candidates")

    def test_missing_rows_empty_pages_and_validation(self):
        task = self.task("Испытание", "Парфюмерия")
        (self.root / task["path"]).unlink()
        row, = self.page()["items"]
        self.assertTrue(row["missing"])
        self.assertEqual(row["reviewFlags"], ["missingTask"])
        self.assertEqual(self.page("--offset", "30")["items"], [])
        for options in (("--candidates",), ("--review-on", "2026-02-30")):
            with self.assertRaisesRegex(ValueError, "review-on"):
                self.page(*options)

    def test_targeted_reader_keeps_global_duplicate_detection_and_pending_generation(self):
        task = self.task("Проверка", "ИТ")
        other = self.task("Вне плана", "Личное", add=False)
        other_path = self.root / other["path"]
        duplicate = other_path.with_name("Duplicate.md")
        duplicate.write_bytes(other_path.read_bytes())
        with self.assertRaisesRegex(ValueError, "нескольких файлах"):
            self.page("--limit", "1")
        duplicate.unlink()

        self.reading(task, "50")
        path = self.root / task["path"]
        original = path.read_bytes()
        edited = original.replace(task["id"].encode(), b"pending-identity", 1)
        path.write_bytes(edited)
        _journal(self.root).write_text(json.dumps([{"path": task["path"], "before": original.decode(),
                                                    "afterHash": hashlib.sha256(edited).hexdigest()}]))
        previous = self.page()["items"][0]
        self.assertEqual((previous["id"], previous["value"]), (task["id"], "50"))
        self.assertEqual(self.plans.task(task["id"])["revision"], hashlib.sha256(original).hexdigest())
        path.write_bytes(edited + b"External edit\n")
        self.assertTrue(self.page()["items"][0]["missing"])
