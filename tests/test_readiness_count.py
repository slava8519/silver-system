import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from second_brain import okr_progress_cli, storage
from second_brain.gantt import Gantt
from second_brain.planning import Planning
from second_brain.refresh_planning import refresh_planning


class ReadinessCount(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = storage.initialize(Path(tmp.name))
        self.task = Planning(self.root).create_task("Проверить образцы")
        self.parser = argparse.ArgumentParser()
        okr_progress_cli.add_commands(self.parser.add_subparsers(dest="command"))

    def cli(self, *args):
        parsed = self.parser.parse_args(["task-readiness", self.task["id"], *args])
        return okr_progress_cli.run(self.root, parsed)

    def evidence(self, content):
        path = storage.create(self.root, "note", "Протокол", content)
        return ["--source", path.relative_to(self.root).as_posix(),
                "--line", str(storage.read_record(path)["body_start"])]

    def test_fact_from_count_is_replayable_readable_and_refreshes_with_the_original_quote(self):
        day = "2026-11-21"
        text = day + ". Приняты 3 из 4 образцов."
        fields = ["--kind", "fact", "--date", day, "--value", "75", "--revision", self.task["revision"],
                  "--count", "3", "4", *self.evidence(text), "--quote", text]
        first = self.cli(*fields)
        repeated = self.cli(*fields)
        self.assertEqual(first["revision"], repeated["revision"])
        self.assertEqual(self.cli()["total"], 1)
        detail = Gantt(self.root).detail(self.task["id"])
        self.assertEqual(detail["readiness"]["count"], [3, 4])
        self.assertEqual(detail["readiness"]["kind"], "fact")
        self.assertEqual(detail["readiness"]["source"]["quote"], text)
        result = refresh_planning(self.root)
        self.assertEqual(result["eventsAdded"], 1)
        self.assertEqual(result["issues"], [])
        self.assertEqual(Gantt(self.root).detail(self.task["id"])["latest"][0]["text"], text)
        self.assertEqual(refresh_planning(self.root)["changed"], 0)

    def test_slash_and_rounding_are_explicit_and_do_not_change_a_manual_decision(self):
        fields = ["--kind", "fact", "--date", "2026-11-21", "--value", "66,67",
                  "--count", "2", "3", *self.evidence("2026-11-21. Готовы 2/3 образцов.")]
        first = self.cli(*fields, "--revision", self.task["revision"])
        manual = self.cli("--kind", "estimate", "--date", "2026-11-21", "--value", "70",
                          "--actor", "manual", "--reason", "Оценка владельца", "--revision", first["revision"])
        with self.assertRaises(storage.ConflictError):
            self.cli(*fields, "--revision", manual["revision"])
        self.assertEqual(self.cli()["readiness"]["value"], "70")

    def test_rejects_unrelated_or_partial_counts_in_one_line_without_writing(self):
        task_path = self.root / self.task["path"]
        original = task_path.read_bytes()
        invalid = [
            ("Код 13/4", "3/4"), ("Код 3/40", "3/4"),
            ("Дата 3/4/2026", "3/4"), ("Дата 2026/3/4", "3/4"),
            ("Величина 0.3/4", "3/4"), ("Результат 3/4,25", "3/4"),
            ("Код -3/4", "3/4"), ("Разбито на 5 групп; сделаны 3 из 4", "5 групп"),
            ("2026-11-20. Приняты 3 из 4", "3 из 4")
        ]
        for line, quote in invalid:
            with self.subTest(line=line), self.assertRaises(ValueError):
                self.cli("--kind", "fact", "--date", "2026-11-21", "--value", "75", "--count", "3", "4",
                         *self.evidence(line), "--quote", quote, "--revision", self.task["revision"])
        self.assertEqual(task_path.read_bytes(), original)

    def test_rounding_never_makes_an_incomplete_count_zero_or_complete(self):
        for done, total, false_endpoint, precise in (("19999", "20000", "100", "99.995"),
                                                    ("1", "20001", "0", "0.005")):
            fields = ["--kind", "fact", "--date", "2026-11-21", "--revision", self.cli()["revision"],
                      "--count", done, total, *self.evidence(f"Принято {done} из {total}.")]
            with self.subTest(done=done), self.assertRaisesRegex(ValueError, "между 0 и 100"):
                self.cli(*fields, "--value", false_endpoint)
            self.assertEqual(self.cli(*fields, "--value", precise)["readiness"]["value"], precise)
        for done, value in (("0", "0"), ("4", "100")):
            result = self.cli("--kind", "fact", "--date", "2026-11-21", "--revision", self.cli()["revision"],
                              "--count", done, "4", "--value", value, *self.evidence(f"Готовы {done}/4."))
            self.assertEqual(result["readiness"]["value"], value)

    def test_count_requires_a_source_fact_and_correct_ratio_with_positive_total(self):
        source = self.evidence("2026-11-21. Приняты 3 из 4 образцов.")
        fields = ["--date", "2026-11-21", "--revision", self.task["revision"]]
        invalid = [
            ["--count", "3", "4"], ["--value", "70", "--kind", "fact", "--count", "3", "4", *source],
            ["--value", "75", "--kind", "fact", *source],
            ["--value", "75", "--kind", "estimate", "--count", "3", "4", *source],
            ["--value", "75", "--kind", "fact", "--actor", "manual", "--reason", "Осмотр", "--count", "3", "4"],
            ["--value", "0", "--kind", "fact", "--count", "0", "0", *source],
            ["--value", "0", "--kind", "fact", "--count", "-1", "4", *source],
            ["--value", "100", "--kind", "fact", "--count", "5", "4", *source]
        ]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.cli(*fields, *args)
        self.assertEqual(self.cli()["total"], 0)
