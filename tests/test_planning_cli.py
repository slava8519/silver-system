import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PlanningCLI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Vault"
        self.cli("init")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "second_brain", "--vault", str(self.root), *args],
                              capture_output=True, text=True, timeout=12)

    def cli(self, *args):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_gantt_row_shows_a_recorded_zero_and_its_origin(self):
        task = self.cli("task-create", "Проверка расписания", "--request-id", "schedule-check")
        identifier = task["id"]
        row = self.cli("gantt-row", identifier)
        self.assertIsNone(row["readiness"])
        self.cli("task-readiness", identifier, "--value", "0", "--kind", "estimate", "--actor", "manual",
                 "--owner-confirmed", "--reason", "Работы ещё не начались", "--date", "2026-08-25",
                 "--revision", row["revision"])
        reading = self.cli("gantt-row", identifier)["readiness"]
        self.assertEqual((reading["value"], reading["kind"], reading["date"]), ("0", "estimate", "2026-08-25"))

    def test_transfer_through_two_views_and_replay(self):
        direction = self.cli("create", "direction", "Музыка")
        project = self.cli("create", "project", "Осенний концерт", "--alias", "Сцена", "--direction", direction["id"])
        task = self.cli("task-create", "Проверить акустику", "--project", project["id"], "--due", "2026-11-25",
                        "--request-id", "acoustics")
        other = self.cli("task-create", "Выбрать стойку", "--direction", direction["id"])
        work, = self.cli("task-list", "--project", project["id"], "--match", "АКУСТ")["items"]
        self.assertEqual((work["id"], work["direction"]), (task["id"], direction["id"]))
        plans = [self.cli("plan-create", name) for name in ("Выпуск", "Неделя")]
        for plan in plans:
            self.cli("plan-add", plan["id"], task["id"], "--revision", plan["revision"])
        before = [(self.root / plan["path"]).read_bytes() for plan in plans]
        self.assertEqual(self.cli("plan-show", plans[0]["id"], "--task", other["id"])["total"], 0)
        preview = self.cli("task-due", task["id"], "--date", "2026-11-27", "--limit", "1")
        self.assertEqual((preview["before"], preview["after"], preview["plansTotal"], preview["truncated"]),
                         ("2026-11-25", "2026-11-27", 2, True))
        self.assertEqual(self.cli("task-history", task["id"])["total"], 0)
        cmd = ("task-due", task["id"], "--date", "2026-11-27", "--apply", preview["revision"])
        self.cli(*cmd)
        self.cli(*cmd)
        for plan, initial in zip(plans, before):
            row, = self.cli("plan-show", plan["id"], "--task", task["id"])["items"]
            self.assertEqual((row["id"], row["due"]), (task["id"], "2026-11-27"))
            lines = (self.root / row["path"]).read_text(encoding="utf-8").splitlines()
            self.assertIn(row["due"], lines[row["dueLine"] - 1])
            self.assertEqual(initial, (self.root / plan["path"]).read_bytes())
        self.assertEqual(self.cli("task-history", task["id"])["total"], 1)
        self.assertEqual(self.cli("plan-list", "--match", "НЕД")["total"], 1)
        self.assertTrue(self.cli("task-list", "--direction", direction["id"], "--limit", "1")["truncated"])
        counts = {row["path"]: row["count"] for row in self.cli("overview", "--folders", "--limit", "20")["items"]}
        self.assertEqual((counts["Tasks"], counts["Plans"]), (2, 2))

    def test_preview_does_not_overwrite_manual_date(self):
        task = self.cli("task-create", "Подать заявку", "--due", "2026-11-25")
        preview = self.cli("task-due", task["id"], "--clear")
        path = self.root / task["path"]
        path.write_text(path.read_text(encoding="utf-8").replace("2026-11-25", "2026-12-05"), encoding="utf-8")
        self.assertNotEqual(self.run_cli("task-due", task["id"], "--clear", "--apply",
                                         preview["revision"]).returncode, 0)
        self.assertEqual(self.cli("task-show", task["id"])["due"], "2026-12-05")
        self.assertEqual(self.cli("task-history", task["id"])["items"], [])

    def test_forecast_moves_in_two_plans_with_the_same_agreed_date(self):
        task = self.cli("task-create", "Сверить партии", "--due", "2025-04-14")
        identifier = task["id"]
        plans = [self.cli("plan-create", title) for title in ("Поставка", "Апрель")]
        for plan in plans:
            self.cli("plan-add", plan["id"], identifier)
        schedule = ("task-schedule", identifier)
        empty = self.cli(*schedule)
        self.assertEqual((empty["due"], empty["forecast"], empty["completed"]), ("2025-04-14", None, None))
        for expected, proposed in ((None, "2025-04-16"), ("2025-04-16", "2025-04-18")):
            cmd = (*schedule, "--forecast", proposed)
            preview = self.cli(*cmd, "--limit", "1")
            self.assertEqual((preview["before"], preview["after"]), (expected, proposed))
            self.assertEqual((preview["plansTotal"], preview["truncated"]), (2, True))
            self.assertEqual(self.cli(*schedule)["forecast"], expected)
            self.cli(*cmd, "--apply", preview["revision"])
        for plan in plans:
            row, = self.cli("plan-show", plan["id"], "--task", identifier)["items"]
            self.assertEqual((row["due"], row["forecast"], row["completed"]), ("2025-04-14", "2025-04-18", None))
        view = self.cli("gantt-row", identifier, "--limit", "1")
        self.assertEqual((view["due"], view["forecast"], view["completed"]), ("2025-04-14", "2025-04-18", None))
        line = self.cli(*schedule)["forecastLine"]
        self.assertIn("2025-04-18", (self.root / task["path"]).read_text().splitlines()[line - 1])
        history = self.cli(*schedule, "--history", "--limit", "1")
        self.assertEqual((history["total"], history["items"][0]["after"]), (2, "2025-04-18"))
        self.assertEqual(self.cli("task-history", identifier)["total"], 0)
        completion = (*schedule, "--completed", "2025-04-19", "--owner-confirmed", "--reason", "Приёмка состоялась 19 апреля.")
        self.cli(*completion, "--apply", self.cli(*completion)["revision"])
        agreement = ("task-due", identifier, "--date", "2025-04-15")
        self.cli(*agreement, "--apply", self.cli(*agreement)["revision"])
        for plan in plans:
            row, = self.cli("plan-show", plan["id"], "--task", identifier)["items"]
            self.assertEqual((row["due"], row["forecast"], row["completed"]),
                             ("2025-04-15", "2025-04-18", "2025-04-19"))
        clear = (*schedule, "--clear-forecast")
        self.cli(*clear, "--apply", self.cli(*clear)["revision"])
        dates = self.cli(*schedule)
        self.assertEqual((dates["due"], dates["forecast"], dates["completed"]), ("2025-04-15", None, "2025-04-19"))

    def test_completion_needs_its_own_evidence_and_can_be_corrected(self):
        task = self.cli("task-create", "Получить пробу")
        schedule = ("task-schedule", task["id"])
        completed = (*schedule, "--completed", "2025-04-08")
        note = self.cli("create", "note", "Приёмка", "--body", "Пробу получили 8 апреля 2025 года.")
        text = (self.root / note["path"]).read_text().splitlines()
        number = next(i for i, line in enumerate(text, 1) if "8 апреля" in line)
        before = (self.root / task["path"]).read_bytes()
        for invalid in (
            completed, (*completed, "--reason", "Возможно, готово"),
            (*completed, "--owner-confirmed"), (*completed, "--source", note["path"]),
            (*completed, "--source", note["path"], "--line", str(number), "--quote", "Отправлено"),
            (*schedule, "--completed", "9999-12-31", "--owner-confirmed", "--reason", "Прогноз на будущее."),
            (*schedule, "--history", "--apply", "wrong"),
        ):
            with self.subTest(args=invalid):
                self.assertNotEqual(self.run_cli(*invalid).returncode, 0)
                self.assertEqual((self.root / task["path"]).read_bytes(), before)
        cmd = (*completed, "--source", note["path"], "--line", str(number), "--quote", text[number - 1])
        preview = self.cli(*cmd)
        self.assertIsNone(self.cli(*schedule)["completed"])
        self.cli(*cmd, "--apply", preview["revision"])
        done = self.cli(*schedule, "--show")
        self.assertEqual((done["due"], done["forecast"], done["completed"]), (None, None, "2025-04-08"))
        undo = (*schedule, "--clear-completed", "--owner-confirmed", "--reason", "Владелец уточнил, что это другая проба.")
        preview = self.cli(*undo)
        self.cli(*undo, "--apply", preview["revision"])
        self.assertIsNone(self.cli(*schedule)["completed"])
        self.assertEqual(self.cli(*schedule, "--history")["total"], 2)
