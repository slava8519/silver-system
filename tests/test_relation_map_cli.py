from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from examples.planning_vault import generate
from second_brain import storage
from second_brain.__main__ import command_line, run


class RelationMapCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = generate(Path(temporary.name) / "MapLab")
        self.parser = command_line()

    def cli(self, *arguments):
        return run(self.parser.parse_args(["--vault", str(self.root), "map", *arguments]))

    def test_search_focus_and_check_plan_source_for_three_directions(self):
        for title, theme_plan in (("Проверить восстановление базы", "Запуск Маяка"),
                                  ("Забрать дорожный набор", "Подготовка к поездке"),
                                  ("Сравнить кедровые пробы", "Пробы кедровой серии")):
            with self.subTest(title=title):
                task, = self.cli("search", title)["items"]
                groups = self.cli("focus", task["id"])["groups"]
                plans = next(group for group in groups if group["key"] == "plans")
                self.assertEqual(plans["total"], 2)
                filtered = self.cli("group", "plans", "--id", task["id"], "--match", theme_plan)
                plan, = filtered["items"]
                self.assertEqual((filtered["total"], plan["title"]), (1, theme_plan))
                ref = plan["source"]
                line = self.cli("source", ref["id"], str(ref["line"]), "--revision", ref["revision"])
                saved = (self.root / ref["path"]).read_text(encoding="utf-8").splitlines()
                self.assertEqual(line["text"], saved[ref["line"] - 1])

    def test_paged_home_alias_search_and_unassigned_card(self):
        first = self.cli("--limit", "1")
        rest = self.cli("--limit", "2", "--offset", "1")
        self.assertEqual(first["total"], 3)
        self.assertEqual(len({item["id"] for item in first["items"] + rest["items"]}), 3)
        matches = self.cli("search", "Маяк")["items"]
        self.assertIn("Переезд базы заказов", [item["title"] for item in matches])
        storage.create(self.root, "note", "Запись для разбора")
        loose = self.cli("group", "unassigned", "--match", "Запись для разбора")["items"]
        self.assertEqual([row["title"] for row in loose], ["Запись для разбора"])


if __name__ == "__main__":
    unittest.main()
