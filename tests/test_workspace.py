import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from second_brain import storage
from second_brain.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "Field Notebook"
        storage.initialize(self.root)
        self.space = Workspace(self.root)

    def test_open_and_overview_read_an_existing_vault_without_bootstrapping(self):
        missing = self.root.parent / "No Vault"
        with self.assertRaises(ValueError):
            Workspace(missing)
        self.assertFalse(missing.exists())
        (self.root / "Fieldwork" / "Maps").mkdir(parents=True)
        (self.root / ".obsidian").mkdir()
        before = {p.relative_to(self.root) for p in self.root.rglob("*")}
        overview = self.space.overview()
        self.assertEqual(overview["records"], [])
        self.assertEqual(before, {p.relative_to(self.root) for p in self.root.rglob("*")})
        folders = {f["path"]: f["count"] for f in overview["folders"]}
        self.assertIn("Sources/Originals", folders)
        self.assertEqual(folders["Fieldwork/Maps"], 0)
        self.assertNotIn(".obsidian", folders)

    def test_state_evidence_tracks_manual_edits_and_the_current_path(self):
        direction = self.space.create_record({"type": "direction", "title": "Мастерская"})
        project = self.space.create_record({"type": "project", "title": "Собрать стеллаж",
                                            "direction": direction["id"], "status": "Ждём размеры",
                                            "nextStep": "Измерить проём"})
        self.assertIsNone(project["due"])
        self.assertNotIn("due", project["fieldSources"])
        old = self.root / project["path"]
        nested = old.parent / "Joinery"
        nested.mkdir()
        new = old.rename(nested / "Чертёж.md")
        text = new.read_text().replace('status: "Ждём размеры"', '# личная правка\nstatus: "Можно пилить"')
        new.write_text(text)
        rows = self.space.overview()
        row, = (r for r in rows["records"] if r["id"] == project["id"])
        self.assertEqual(row["status"], "Можно пилить")
        field = row["fieldSources"]["status"]
        self.assertEqual(field["path"], "Projects/Joinery/Чертёж.md")
        self.assertEqual((self.root / field["path"]).read_text().splitlines()[field["line"] - 1],
                         'status: "Можно пилить"')
        self.assertEqual(dict((f["path"], f["count"]) for f in rows["folders"])["Projects/Joinery"], 1)
        self.assertEqual(self.space.get_record(project["id"])["raw"], text)

    def test_patch_preserves_id_links_comments_custom_yaml_and_the_complete_body(self):
        old_area = self.space.create_record({"type": "direction", "title": "Клуб"})
        new_area = self.space.create_record({"type": "direction", "title": "Музей"})
        card = self.space.create_record({"type": "project", "title": "Фонотека", "body": "Строка 1\nСтрока 2",
                                         "direction": old_area["id"]})
        path = self.root / card["path"]
        raw = path.read_text().replace('title: "Фонотека"', "title: 'Фонотека' # заголовок").replace(
            "aliases: []", 'aliases:\n  - Катушка # прежнее имя\ncustom:\n  разрешение: вручную\n  метки: [1, 3]')
        path.write_bytes(raw.replace("\n", "\r\n").encode())
        old_body = self.space.get_record(card["id"])["body"]
        revision = self.space.get_record(card["id"])["revision"]
        updated = self.space.edit_record({"id": card["id"], "revision": revision, "title": "Каталог",
                                          "aliases": ["Записи"], "direction": new_area["id"], "due": "после сверки"})
        self.assertEqual(updated["id"], card["id"])
        self.assertEqual(updated["path"], card["path"])
        self.assertEqual(updated["direction"], new_area["id"])
        self.assertEqual(updated["aliases"], ["Записи"])
        self.assertEqual(updated["body"], old_body)
        self.assertIn("# заголовок\r\n", updated["raw"])
        self.assertIn("# прежнее имя\r\n", updated["raw"])
        self.assertIn("custom:\r\n  разрешение: вручную\r\n  метки: [1, 3]\r\n", updated["raw"])
        with self.assertRaises(storage.ConflictError):
            self.space.edit_record({"id": card["id"], "revision": revision, "title": "Устарело"})
        cleared = self.space.edit_record({"id": card["id"], "revision": updated["revision"],
                                          "direction": None, "due": None})
        self.assertEqual((cleared["direction"], cleared["due"]), (None, None))
        self.assertNotIn("due:", cleared["raw"])
        self.assertNotIn("direction:", cleared["raw"])

    def test_retried_create_returns_the_same_record_after_a_manual_rename_and_edit(self):
        data = {"type": "note", "title": "Компас", "body": "Проверено на маршруте", "requestId": "sample-write"}
        barrier = threading.Barrier(2)

        def create_once(_):
            barrier.wait()
            return self.space.create_record(data)["id"]

        with ThreadPoolExecutor(2) as pool:
            ids = list(pool.map(create_once, range(2)))
        self.assertEqual(ids[0], ids[1])
        path = storage.find_record(self.root, ids[0])
        path = path.rename(path.with_name("Список вещей.md"))
        with path.open("a", encoding="utf-8") as writer:
            writer.write("\nПравка пользователя.")
        replay = Workspace(self.root).create_record(data)
        self.assertEqual(replay["id"], ids[0])
        self.assertIn("Правка пользователя.", replay["body"])
        with self.assertRaises(storage.ConflictError):
            self.space.create_record({**data, "title": "Другой запрос"})
        self.assertEqual(len(self.space.overview()["records"]), 1)

    def test_complex_project_fields_are_preserved_on_assignment_and_clear(self):
        project = self.space.create_record({"type": "project", "title": "Проверка"})
        path = self.root / project["path"]
        for field, yaml, value in (("status", "status: {stage: review, owner: Anna}", "done"),
                                   ("nextStep", "next_step:\n- ручная запись", "обсудить"),
                                   ("projectStart", "project_start: [начало, весна]", "2026-04-01")):
            raw = project["raw"].replace("aliases: []", "aliases: []\n" + yaml)
            path.write_text(raw)
            current = self.space.get_record(project["id"])
            self.assertIsNone(current[field])
            for replacement in (value, None):
                with self.subTest(field=field, replacement=replacement), self.assertRaisesRegex(ValueError, "Markdown"):
                    self.space.edit_record({"id": project["id"], "revision": current["revision"], field: replacement})
                self.assertEqual(path.read_bytes(), raw.encode())
            saved = self.space.edit_record({"id": project["id"], "revision": current["revision"], "body": "Уточнение"})
            self.assertIn(yaml, saved["raw"])

    def test_invalid_edits_preserve_the_original_file(self):
        card = self.space.create_record({"type": "note", "title": "Афиша"})
        for edit in ({"direction": card["id"]}, {"title": "  "}, {"aliases": "алиас"},
                     {"nextStep": {"value": "вложено"}}, {"type": "project"}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.space.edit_record({"id": card["id"], "revision": card["revision"], **edit})
        self.assertEqual(self.space.get_record(card["id"])["raw"], card["raw"])
        path = self.root / card["path"]
        path.write_text(path.read_text().replace("aliases: []",
                        "aliases: null # очищено\ndirection: ~\nstatus:\n  external: confirmed"))
        manual = self.space.get_record(card["id"])
        self.assertEqual(manual["aliases"], [])
        self.assertIsNone(manual["direction"])
        self.assertIsNone(manual["status"])
        self.assertNotIn("status", manual["fieldSources"])
        changed = self.space.edit_record({"id": manual["id"], "revision": manual["revision"], "title": "Весенняя афиша"})
        self.assertIn("status:\n  external: confirmed", changed["raw"])
        with self.assertRaises(ValueError):
            self.space.edit_record({"id": changed["id"], "revision": changed["revision"], "status": "Готово"})


if __name__ == "__main__":
    unittest.main()
