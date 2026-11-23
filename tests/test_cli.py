import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
SCENARIOS = json.loads((PROJECT / "examples" / "scenarios.json").read_text(encoding="utf-8"))


class VaultWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name) / "Three directions"

    def cli(self, *args, expect_success=True):
        result = subprocess.run(
            [sys.executable, "-m", "second_brain", "--vault", str(self.vault), *args],
            cwd=PROJECT, capture_output=True, text=True, timeout=20,
        )
        if expect_success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result.stdout

    def assert_sources(self):
        for scenario in SCENARIOS:
            for record in scenario["records"]:
                with self.subTest(record=record["title"]):
                    hits = json.loads(self.cli("search", record["question"]))
                    self.assertTrue(hits)
                    hit = hits[0]
                    self.assertEqual(hit["title"], record["title"])
                    self.assertIn(record["evidence"], hit["text"])
                    source = self.cli("read", hit["path"], str(hit["start"]), str(hit["end"]))
                    self.assertIn(record["evidence"], source)

    def test_three_directions_share_one_portable_vault(self):
        self.cli("examples")
        self.assert_sources()
        rows = json.loads(self.cli("list"))
        for scenario in SCENARIOS:
            direction, = (row for row in rows if row["type"] == "direction"
                          and row["title"] == scenario["direction"])
            members = json.loads(self.cli("list", "--direction", direction["id"]))
            expected = {(rec["type"], rec["title"]) for rec in scenario["records"]}
            self.assertEqual({(item["type"], item["title"]) for item in members}, expected)
        shutil.rmtree(self.vault / ".second-brain")
        self.vault = self.vault.rename(self.vault.with_name("Moved vault"))
        self.cli("rebuild")
        self.assert_sources()
        self.assertEqual(json.loads(self.cli("list")), rows)

    def test_admin_commands_reassign_project_and_keep_unedited_fields(self):
        self.cli("init")
        music = json.loads(self.cli("create", "direction", "Музыка"))
        field = json.loads(self.cli("create", "direction", "Экспедиции"))
        project = json.loads(self.cli("create", "project", "Записать квартет", "--direction", music["id"],
                                      "--alias", "Этюд", "--body", "Согласовали состав квартета.",
                                      "--status", "Назначена репетиция", "--next-step", "Выбрать студию."))
        read = json.loads(self.cli("show", project["id"]))
        self.cli("update", project["id"], "--revision", read["revision"], "--direction", field["id"],
                 "--title", "Концерт в поле", "--alias", "Триоль", "--due", "2026-12-10")
        info = json.loads(self.cli("overview"))
        current, = [r for r in info["records"] if r["id"] == project["id"]]
        self.assertEqual((current["direction"], current["path"]), (field["id"], project["path"]))
        self.assertEqual((current["title"], current["aliases"]), ("Концерт в поле", ["Триоль"]))
        self.assertEqual((current["status"], current["nextStep"]), ("Назначена репетиция", "Выбрать студию."))
        source = current["fieldSources"]["due"]
        self.assertIn("2026-12-10", self.cli("read", source["path"], str(source["line"]), str(source["line"])))
        question = "Что решили по Триоли?"
        self.assertEqual(json.loads(self.cli("ask", question, "--direction", music["id"]))["items"], [])
        answer, = json.loads(self.cli("ask", question, "--direction", field["id"]))["items"]
        self.assertEqual(answer["answer"], "Согласовали состав квартета.")
        self.cli("update", project["id"], "--revision", read["revision"], "--detach", expect_success=False)

    def test_user_can_add_a_direction_and_keep_manual_edits(self):
        self.cli("examples")
        originals = {p: p.read_bytes() for p in self.vault.rglob("*.md")}
        area = json.loads(self.cli("create", "direction", "Астрономия"))
        created = json.loads(self.cli("create", "note", "Настройка телескопа",
                                     "--direction", area["id"], "--body", "Начинаю с Луны."))
        read = json.loads(self.cli("show", created["id"]))
        self.cli("update", created["id"], "--revision", read["revision"], "--body", "Использую лунный фильтр.")
        read = json.loads(self.cli("show", created["id"]))
        file = self.vault / created["path"]
        with file.open("a", encoding="utf-8") as writer:
            writer.write("\nПроверено вручную.\n")
        manual = file.read_bytes()
        self.cli("update", created["id"], "--revision", read["revision"], "--body", "Устаревшая правка.",
                 expect_success=False)
        self.cli("init")
        self.cli("examples", expect_success=False)
        self.assertEqual(file.read_bytes(), manual)
        hits = json.loads(self.cli("search", "лунный фильтр"))
        self.assertEqual(hits[0]["id"], created["id"])
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
