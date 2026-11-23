import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from second_brain import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "vault with spaces"
        storage.initialize(self.root)

    def test_initialize_preserves_existing_files_byte_for_byte(self):
        home = self.root / "Home.md"
        home.write_bytes("Моя главная\r\n".encode())
        note = self.root / "Notes" / "legacy.md"
        note.write_bytes(b"# My old note\n")
        plain = self.root / "Notes" / "log.md"
        plain.write_bytes(b"---\nMy old notebook\n")
        storage.initialize(self.root)
        self.assertEqual(home.read_bytes(), "Моя главная\r\n".encode())
        self.assertEqual(note.read_bytes(), b"# My old note\n")
        self.assertEqual(list(storage.iter_records(self.root)), [])

    def test_direction_links_and_ids_survive_renaming(self):
        direction = storage.create(self.root, "direction", "Городская история")
        direction_id = storage.read_record(direction)["id"]
        note = storage.create(self.root, "note", "Архив", "Опись 4", ["Карта"], direction_id)
        project = storage.create(self.root, "project", "Закончить каталог", direction=direction_id)
        for path in [note, project]:
            self.assertEqual(storage.read_record(path)["direction"], direction_id)
        moved = direction.rename(direction.with_name("Архивные поиски.md"))
        self.assertEqual(storage.find_record(self.root, direction_id), moved)
        note_id = storage.read_record(note)["id"]
        with self.assertRaises(ValueError):
            storage.create(self.root, "note", "Другая", direction=note_id)
        with self.assertRaises(FileNotFoundError):
            storage.create(self.root, "project", "Без связи", direction="unknown")

    def test_file_names_and_duplicate_titles(self):
        path = storage.create(self.root, "note", "../Подбор: весна/лето", "Оригинал")
        record = storage.read_record(path)
        self.assertEqual(record["title"], "../Подбор: весна/лето")
        self.assertEqual(path.parent, self.root / "Notes")
        another = storage.create(self.root, "note", record["title"], "Другая запись")
        self.assertNotEqual(another, path)
        self.assertNotEqual(storage.read_record(another)["id"], record["id"])
        self.assertEqual(path.read_bytes().splitlines()[-1].decode(), "Оригинал")

    def test_update_preserves_header_and_body_line_reference(self):
        path = storage.create(self.root, "note", "Название", "старое\nтело")
        old = path.read_bytes().replace(b"aliases: []", (
            "custom:\n  template: manual # keep this\naliases:\n  - 'Карта'\n  - \"Схема\""
        ).encode()).replace(b"\n", b"\r\n")
        path.write_bytes(old)
        record = storage.read_record(path)
        self.assertEqual(record["aliases"], ["Карта", "Схема"])
        self.assertEqual(old.splitlines()[record["body_start"] - 1].decode(), "старое")
        header = old[:-len(record["body"].encode())]
        storage.update(self.root, record["id"], "новое тело", record["revision"])
        self.assertEqual(path.read_bytes(), header + "новое тело".encode())

    def test_manual_yaml_aliases_and_titles(self):
        path = storage.create(self.root, "note", "Весна: пробы")
        initial = path.read_text(encoding="utf-8").replace(
            'title: "Весна: пробы"', "title: 'Весна: пробы' # карточка"
        )
        for field, expected in (
            ("[Кедр, «Лес»] # варианты", ["Кедр", "«Лес»"]),
            ('''["Сад, весной", 'O''Keeffe'] # цитаты''', ["Сад, весной", "O'Keeffe"]),
            ("# варианты\n  - Кедр  # главный\n  - «Лес»", ["Кедр", "«Лес»"]),
        ):
            with self.subTest(field=field):
                path.write_text(initial.replace("aliases: []", "aliases: " + field), encoding="utf-8")
                record = storage.read_record(path)
                self.assertEqual(record["title"], "Весна: пробы")
                self.assertEqual(record["aliases"], expected)

    def test_intervening_manual_edit_is_preserved(self):
        path = storage.create(self.root, "project", "Ремонт", "До")
        record = storage.read_record(path)
        manual = path.read_bytes() + " ручной правки".encode()
        path.write_bytes(manual)
        with self.assertRaises(storage.ConflictError):
            storage.update(self.root, record["id"], "Другое", record["revision"])
        self.assertEqual(path.read_bytes(), manual)

    def test_failed_commit_keeps_original_record(self):
        path = storage.create(self.root, "note", "Оригинал", "Сохранить")
        record = storage.read_record(path)
        before = path.read_bytes()
        with patch.object(storage.os, "replace", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                storage.update(self.root, record["id"], "Изменить", record["revision"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_only_one_writer_can_commit_the_same_revision(self):
        path = storage.create(self.root, "note", "Общее")
        record = storage.read_record(path)

        def write(body):
            try:
                storage.update(self.root, record["id"], body, record["revision"])
                return body
            except storage.ConflictError:
                return None

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(write, ["Первое", "Второе"]))
        self.assertEqual(results.count(None), 1)
        self.assertIn(storage.read_record(path)["body"], results)

    def test_symlinks_do_not_redirect_reads_or_writes(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        note = storage.create(self.root, "note", "Запись")
        note_id = storage.read_record(note)["id"]
        moved = note.rename(outside / "Outside.md")
        note.symlink_to(moved)
        with self.assertRaises(FileNotFoundError):
            storage.find_record(self.root, note_id)
        with self.assertRaises(ValueError):
            storage.create(self.root, "note", "Запись")
        (self.root / "Projects").rmdir()
        (self.root / "Projects").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            storage.create(self.root, "project", "Снаружи")
        self.assertEqual(set(outside.iterdir()), {moved})


if __name__ == "__main__":
    unittest.main()
