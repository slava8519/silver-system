import shutil
import tempfile
import unittest
from pathlib import Path

from second_brain.search import fragment, rebuild, search
from second_brain.storage import create, initialize, read_record


class SearchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "My notes"
        initialize(self.root)

    def add(self, kind, title, body="", aliases=(), direction=None):
        return create(self.root, kind, title, body=body, aliases=list(aliases), direction=direction)

    def test_inflected_alias_distinguishes_projects_with_similar_decisions(self):
        work = read_record(self.add("direction", "Разработка"))["id"]
        self.add("project", "Первая поставка", "Перед запуском проверяем журнал ошибок.", ["Восток"], work)
        self.add("project", "Вторая поставка", "Перед запуском проверяем журнал платежей.", ["Закат"], work)
        for alias, expected in [("Востока", "журнал ошибок"), ("Заката", "журнал платежей")]:
            hit = search(self.root, f"Какой журнал проверяем перед запуском {alias}?")[0]
            self.assertIn(expected, hit["text"])
            self.assertEqual(fragment(self.root, hit["path"], hit["start"], hit["end"]), hit["text"])
        self.assertEqual(search(self.root, "фотография"), [])

    def test_refresh_after_edits_rename_move_and_cache_loss(self):
        note = self.add("note", "Список", "Первая строка\nВторая строка", ["Указатель"])
        hit = search(self.root, "первая")[0]
        initial_id = hit["id"]
        note.write_text(note.read_text().replace("Первая строка\nВторая строка",
                                                 "Запись\n\nУникальная строка"))
        self.assertEqual(search(self.root, "первая"), [])
        renamed = note.with_name("Переименовано.md")
        note.rename(renamed)
        self.root = self.root.rename(self.root.with_name("Moved vault"))
        (self.root / ".second-brain" / "search.sqlite3").unlink()
        rebuild(self.root)
        hit = search(self.root, "Уникальная")[0]
        self.assertEqual(hit["id"], initial_id)
        self.assertEqual(hit["path"], "Notes/Переименовано.md")
        self.assertEqual(fragment(self.root, hit["path"], hit["start"], hit["end"]), "Уникальная строка")
        (self.root / hit["path"]).unlink()
        self.assertEqual(search(self.root, "Уникальная"), [])

    def test_scoped_sources_and_fragment_bounds(self):
        hidden = self.root / "Archive" / "Из архива.md"
        note = self.add("note", "Источник", "Свежая строка")
        shutil.copyfile(note, hidden)
        hidden.write_text(hidden.read_text().replace("Свежая строка", "Архивный материал"))
        (self.root / "Notes" / "Старое.md").symlink_to(hidden)
        self.assertEqual(search(self.root, "Архивный"), [])
        for bad_path in ("Archive/Из архива.md", "Notes/../Archive/Из архива.md",
                         "Notes/Старое.md", str(hidden)):
            with self.subTest(bad_path), self.assertRaises(ValueError):
                fragment(self.root, bad_path, 1, 1)
        hit = search(self.root, "Свежая")[0]
        for start, end in [(0, 1), (1, 99), (8, 5), (999, 999)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                fragment(self.root, hit["path"], start, end)

    def test_uppercase_extension_and_manually_authored_markdown(self):
        note = self.add("note", "Источник", "Синяя метка")
        note = note.rename(note.with_suffix(".MD"))
        custom = self.root / "Notes" / "Свободная заметка.md"
        custom.write_text("Текст без метаданных")
        self.assertEqual(search(self.root, "метаданных"), [])
        self.assertEqual(fragment(self.root, custom.relative_to(self.root), 1, 1), custom.read_text())
        self.assertEqual(search(self.root, "Синяя")[0]["path"], "Notes/Источник.MD")
        note.write_text(note.read_text().replace("Синяя", "Белая"))
        self.assertEqual(search(self.root, "Синяя"), [])
        hit = search(self.root, "Белая")[0]
        self.assertEqual(fragment(self.root, hit["path"], hit["start"], hit["end"]), "Белая метка")

    def test_bad_cache_rebuilt_and_alias_is_a_citable_source(self):
        self.add("direction", "Дом", aliases=["Жилище"])
        rebuild(self.root)
        (self.root / ".second-brain" / "search.sqlite3").write_bytes(b"broken index")
        hit = search(self.root, "жилище")[0]
        self.assertEqual(hit["title"], "Дом")
        line = fragment(self.root, hit["path"], hit["start"], hit["end"])
        self.assertIn("Дом", line)
        self.assertEqual(line, hit["text"])

    def test_fts_operators_and_punctuation_cannot_form_query_syntax(self):
        self.add("note", "Работа", "Портал запущен")
        self.assertEqual(search(self.root, '" OR (Портал*):!')[0]["title"], "Работа")
        self.assertEqual(search(self.root, '"+++!!'), [])
