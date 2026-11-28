import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from second_brain import history_archive, storage
from second_brain.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "Field Notebook"
        storage.initialize(self.root)
        self.space = Workspace(self.root)

    def test_revision_bookmark_follows_moved_and_retained_lines_across_two_rotations(self):
        p = storage.create(self.root, "project", "Полевые записи",
                           "## История статусов\n### 2020-01-02\nРанняя проба.\n"
                           "### 2024-01-02\nВторая проба.\n## Описание\nСостояние проекта.\n")
        card = storage.read_record(p)
        initial = p.read_text().splitlines()
        def rotate(day):
            args = {"as_of": day, "min_kept": 0}
            plan = history_archive.rotate(self.root, card["id"], **args)
            self.assertEqual(plan["moved"], 1)
            history_archive.rotate(self.root, card["id"], apply=plan["revision"], **args)
        def open_old(text):
            return self.space.get_record(card["id"], revision=card["revision"], line=initial.index(text) + 1)
        def assert_destination(text, kind):
            doc = open_old(text)
            self.assertEqual(doc["type"], kind)
            self.assertEqual(doc["raw"].splitlines()[doc["resolvedLine"] - 1], text)
        rotate("2021-01-01")
        assert_destination("Ранняя проба.", "markdown")
        assert_destination("Вторая проба.", "project")
        rotate("2026-01-01")
        assert_destination("Ранняя проба.", "markdown")
        assert_destination("Вторая проба.", "markdown")
        assert_destination("Состояние проекта.", "project")
        p.write_text(p.read_text().replace("Состояние проекта.", "Исправленная формулировка."))
        self.assertNotIn("resolvedLine", open_old("Состояние проекта."))
        assert_destination("Ранняя проба.", "markdown")
        self.assertNotIn("resolvedLine", self.space.get_record(card["id"], revision="a" * 64, line=10))

    def test_revision_bookmark_preserves_original_twins_but_rejects_ambiguous_edits(self):
        twin = "- Сохраняем одну и ту же формулировку."
        p = storage.create(self.root, "project", "Журнал",
                           f"## Журнал\n- 2020-01-01 — Один.\n{twin}\n- 2020-01-02 — Два.\n{twin}\n"
                           "- 2020-01-03 — Три.\n## Итог\nГотово.\n")
        card = storage.read_record(p)
        initial = p.read_text().splitlines()
        rows = [n for n, text in enumerate(initial, 1) if text == twin]
        plan = history_archive.rotate(self.root, card["id"], min_kept=0)
        history_archive.rotate(self.root, card["id"], min_kept=0, apply=plan["revision"])
        def destinations():
            return [self.space.get_record(card["id"], revision=card["revision"], line=n).get("resolvedLine") for n in rows]
        expected = [n for n, text in enumerate(p.read_text().splitlines(), 1) if text == twin]
        self.assertEqual(destinations(), expected)
        p.write_text(p.read_text().replace(twin, twin + "\n" + twin, 1))
        self.assertEqual(destinations(), [None, None])
        summary = "## Итог\nГотово.\n"
        p.write_text(p.read_text().replace(summary, summary + summary))
        original_title = initial.index("## Итог") + 1
        doc = self.space.get_record(card["id"], revision=card["revision"], line=original_title)
        self.assertNotIn("resolvedLine", doc)

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

    def test_folder_counts_include_visible_files_in_nested_folders(self):
        notes = self.root / "Notes"
        samples = notes / "Fieldwork" / "Samples"
        samples.mkdir(parents=True)
        card = storage.create(self.root, "note", "Полевой дневник")
        card.rename(samples / card.name)
        (samples / "photo.jpg").write_bytes(b"photo")
        (notes / "caption.txt").write_text("Описание")
        (samples / ".DS_Store").touch()
        hidden = notes / ".drafts"
        hidden.mkdir()
        storage.create(self.root, "note", "Черновик").rename(hidden / "draft.md")
        storage.create(self.root, "note", "Ещё черновик").rename(notes / ".hidden.md")
        (notes / "alias.jpg").symlink_to(samples / "photo.jpg")
        (notes / "linked-directory").symlink_to(samples, target_is_directory=True)
        (self.root / "Inbox/.gitkeep").touch()

        rows = self.space.overview()["folders"]
        counts = {row["path"]: (row["count"], row["files"]) for row in rows}
        self.assertEqual(counts["Notes/Fieldwork/Samples"], (1, 2))
        self.assertEqual(counts["Notes/Fieldwork"], (1, 2))
        self.assertEqual(counts["Notes"], (1, 3))
        self.assertEqual(counts["Inbox"], (0, 0))
        self.assertNotIn("Notes/.drafts", counts)
        self.assertNotIn("Notes/linked-directory", counts)

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
