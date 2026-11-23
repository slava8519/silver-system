import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from second_brain.__main__ import command_line, run


class OkrCLI(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.vault = Path(directory.name) / "Учебное хранилище"
        self.parser = command_line()
        self.cli("init")
        self.direction = self.cli("create", "direction", "ИТ")["id"]

    def cli(self, *arguments):
        return run(self.parser.parse_args(["--vault", str(self.vault), *arguments]))

    def create_kr(self, title, *options):
        return self.cli("okr-create", title, "--objective", "Быстро восстановить сервис",
                        "--unit", "мин", "--direction", self.direction, "--request-id", title, *options)

    def test_measurement_and_links_through_cli(self):
        tasks = [self.cli("task-create", title, "--direction", self.direction)
                 for title in ("Настроить копирование", "Провести учения")]
        plans = [self.cli("plan-create", title) for title in ("Проект", "Неделя")]
        for plan in plans:
            self.cli("plan-add", plan["id"], tasks[0]["id"])
        saved = self.create_kr("Время восстановления", "--target", "15", "--task", tasks[0]["id"])
        self.assertEqual(saved["tasksTotal"], 1)
        self.assertIsNone(saved["measurement"])
        identifier = saved["id"]
        linked = self.cli("okr-link", identifier, tasks[1]["id"], "--revision", saved["revision"])
        self.assertEqual(linked["tasksTotal"], 2)
        show = self.cli("okr-show", identifier, "--limit", "1")
        second = self.cli("okr-show", identifier, "--limit", "1", "--offset", "1")
        self.assertEqual((show["tasksTotal"], show["truncated"], second["truncated"]), (2, True, False))
        self.assertEqual({show["tasks"][0]["id"], second["tasks"][0]["id"]}, {task["id"] for task in tasks})
        self.assertEqual((show["unit"], show["target"]), ("мин", "15"))
        card_lines = (self.vault / show["path"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(card_lines[show["targetLine"] - 1], 'target: "15"')
        self.assertEqual({show["tasks"][0]["path"], second["tasks"][0]["path"]}, {task["path"] for task in tasks})
        for plan in plans:
            row, = self.cli("plan-show", plan["id"], "--task", tasks[0]["id"])["items"]
            self.assertEqual(row["id"], tasks[0]["id"])

        lines = ["2026-11-20 восстановление заняло 20 мин", "2026-11-22 восстановление заняло 18 мин"]
        note = self.cli("create", "note", "Протокол учений", "--body", "\n".join(lines))
        contents = (self.vault / note["path"]).read_text(encoding="utf-8").splitlines()
        version = linked["revision"]
        for index, (value, day) in enumerate((("20", "2026-11-20"), ("18", "2026-11-22"))):
            line = str(contents.index(lines[index]) + 1)
            measure = ("okr-measure", identifier, "--date", day, "--value", value,
                       "--source", note["path"], "--line", line)
            with self.assertRaises(ValueError):
                self.cli(*measure, "--revision", version, "--quote", "Такого измерения в записи нет")
            saved = self.cli(*measure, "--revision", version, "--quote", lines[index])
            version = saved["revision"]
            self.assertEqual(saved["measurement"]["source"]["quote"], lines[index])
            self.assertNotIn("history", saved)
        history = self.cli("okr-show", identifier, "--history", "--limit", "1")
        self.assertEqual((history["measurement"]["value"], history["historyTotal"], history["truncated"]),
                         ("18", 2, True))
        self.assertEqual(history["history"][0]["date"], "2026-11-22")
        self.assertEqual(self.cli("okr-show", identifier, "--history", "--limit", "1", "--offset", "1")
                         ["history"][0]["value"], "20")

    def test_find_by_objective_or_title_and_limit_json_output(self):
        for name in ("Время восстановления", "Длительность отката"):
            self.create_kr(name)
        other = self.cli("create", "direction", "Дом")
        self.assertEqual(self.cli("okr-list", "--direction", other["id"])["total"], 0)
        self.assertEqual(self.cli("okr-list", "--match", "ОТКАТА")["total"], 1)
        command = [sys.executable, "-m", "second_brain", "--vault", str(self.vault), "okr-list",
                   "--match", "СЕРВИС", "--direction", self.direction, "--limit", "1"]
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=15)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        page = json.loads(result.stdout)
        self.assertEqual((len(page["items"]), page["total"], page["truncated"]), (1, 2, True))
        self.assertNotIn("history", page["items"][0])
        self.assertIsNone(page["items"][0]["targetLine"])
        next_page = self.cli("okr-list", "--match", "СЕРВИС", "--offset", "1", "--limit", "1")
        self.assertNotEqual(page["items"][0]["id"], next_page["items"][0]["id"])
        self.assertFalse(next_page["truncated"])
