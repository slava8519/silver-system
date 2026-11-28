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
        self.root = Path(tmp.name).resolve() / "My notes"
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

    def test_fragment_keeps_unicode_line_coordinates_and_stops_after_requested_line(self):
        path = self.root / "Notes" / "Разметка.md"
        text = "Один\r\nДва\u2028Три\n\nПять"
        path.write_bytes(text.encode("utf-8"))
        self.assertEqual(fragment(self.root, "Notes/Разметка.md", 2, 4), "Два\nТри\n")
        with self.assertRaisesRegex(ValueError, "только 5 строк"):
            fragment(self.root, "Notes/Разметка.md", 2, 6)
        path.write_bytes(b"first\n" + b"next\n" * 10000 + b"\xff")
        self.assertEqual(fragment(self.root, "Notes/Разметка.md", 1, 1), "first")

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

    def test_incremental_refresh_preserves_unrelated_rows_and_rolls_back_failure(self):
        from sqlite3 import connect
        from unittest.mock import patch
        from second_brain import storage

        stable = self.add("note", "Справочник", "Постоянное основание.")
        changed = self.add("note", "Выводы", "Первая редакция.")
        rebuild(self.root)
        db_path = self.root / ".second-brain" / "search.sqlite3"
        identity = db_path.stat().st_ino
        with connect(db_path) as db:
            old_signature = db.execute("SELECT signature FROM metadata").fetchone()[0]
        before = changed.read_bytes()
        changed.write_bytes(before.replace(b"schema_version: 1", b"schema_version: 7"))
        with self.assertRaisesRegex(ValueError, "Неподдерживаемая"):
            search(self.root, "справочник")
        with connect(db_path) as db:
            self.assertEqual(db.execute("SELECT signature FROM metadata").fetchone()[0], old_signature)
            self.assertEqual(db.execute("SELECT count(*) FROM lines WHERE text='Первая редакция.'").fetchone()[0], 1)
        changed.write_bytes(before.replace("Первая".encode(), "Вторая".encode()))
        with patch.object(storage, "read_record", wraps=storage.read_record) as reads:
            hit, = search(self.root, "Вторая")
        self.assertEqual([call.args[0] for call in reads.call_args_list], [changed])
        self.assertEqual(hit["text"], "Вторая редакция.")
        self.assertEqual(search(self.root, "Первая"), [])
        self.assertEqual(search(self.root, "Постоянное")[0]["path"], stable.relative_to(self.root).as_posix())
        self.assertEqual(identity, db_path.stat().st_ino)

    def test_warm_ask_reads_only_sources_returned_and_checks_all_matches(self):
        from unittest.mock import patch
        from second_brain import storage
        from second_brain.query import WorkspaceQuery

        area = read_record(self.add("direction", "Системы"))["id"]
        project = self.add("project", "Стабилизация", "Договорились сверить аудит.", direction=area)
        for i in range(12):
            self.add("note", f"Запись {i}", f"Последняя партия компаса принята, номер {i}.", direction=area)
        self.add("note", "Другая тема", "Термин встречается только в чужом вопросе.")
        rebuild(self.root)
        reader = WorkspaceQuery(self.root)
        with patch.object(storage, "read_record", wraps=storage.read_record) as reads:
            answer = reader.ask({"query": "Что известно про компас?", "limit": 1})
        self.assertEqual((len(answer["items"]), answer["total"], answer["truncated"]), (1, 12, True))
        hit, = answer["items"]
        self.assertEqual([c.args[0] for c in reads.call_args_list], [self.root / hit["source"]["path"]])
        with patch.object(storage, "read_record", wraps=storage.read_record) as reads:
            answer = reader.ask({"query": "Какой срок у проекта «Стабилизация»?", "directionIds": [area]})
        self.assertEqual(answer["items"], [])
        self.assertEqual([c.args[0] for c in reads.call_args_list], [project])

    def test_parallel_refresh_after_move_returns_new_path_and_never_changes_markdown(self):
        from concurrent.futures import ThreadPoolExecutor
        from second_brain import storage

        path = self.add("note", "Замер", "Уникальный отсчёт.")
        identifier = read_record(path)["id"]
        rebuild(self.root)
        moved = self.root / "Projects" / "Перенос.md"
        path.rename(moved)
        expected = moved.read_bytes()
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda _: search(self.root, "отсчёт"), range(4)))
        self.assertEqual([rows[0]["path"] for rows in results], ["Projects/Перенос.md"] * 4)
        self.assertEqual(storage.find_record(self.root, identifier), moved)
        self.assertEqual(moved.read_bytes(), expected)
        moved.with_name("Дубликат.md").write_bytes(expected)
        with self.assertRaisesRegex(ValueError, "нескольких файлах"):
            storage.find_record(self.root, identifier)

    def test_empty_card_title_is_searchable_but_cannot_answer_a_body_question(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        from second_brain.query import WorkspaceQuery

        self.add("note", "Выбор кедра")
        hit, = search(self.root, "кедр")
        self.assertIn("Выбор кедра", hit["text"])
        reader = WorkspaceQuery(self.root)
        question = {"query": "Где кедр?"}
        with patch("second_brain.query.search_index.optional", return_value=nullcontext(None)):
            expected = reader.ask(question)
        self.assertEqual(expected["items"], [])
        self.assertEqual(reader.ask(question), expected)

    def test_person_fact_made_private_leaves_search_and_cached_terms(self):
        from second_brain.people import People

        direction = read_record(self.add("direction", "Студия"))["id"]
        people = People(self.root)
        source = {"id": "a" * 64, "path": "Sources/Originals/" + "a" * 64 + ".md"}
        person = people.resolve({"new": True, "name": "Мира"}, source["id"])
        text = "uniquetermforvisibility"
        people.apply(person, source, [{"kind": "fact", "direction": direction, "visibility": "shared",
                                       "line": 1, "text": text}])
        self.assertEqual(search(self.root, text)[0]["text"], text)
        path = self.root / person["path"]
        path.write_text(path.read_text().replace('"visibility":"shared"', '"visibility":"personal"'))
        self.assertEqual(search(self.root, text), [])
        self.assertNotIn(text.encode(), (self.root / ".second-brain/search.sqlite3").read_bytes())

    def test_ask_recovers_when_only_the_postings_table_is_lost(self):
        from contextlib import closing
        import sqlite3
        from second_brain.query import WorkspaceQuery

        self.add("note", "Цвета", "Карандаш лежит в ящике.")
        reader = WorkspaceQuery(self.root)
        question = {"query": "Где карандаш?"}
        expected = reader.ask(question)
        with closing(sqlite3.connect(self.root / ".second-brain/search.sqlite3")) as db:
            db.execute("DROP TABLE fragments")
            db.commit()
        self.assertEqual(reader.ask(question), expected)
        self.assertEqual(search(self.root, "карандаш")[0]["text"], expected["items"][0]["answer"])

    def test_unicode_word_boundaries_keep_lexical_search_and_markdown_answer(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        from second_brain.query import WorkspaceQuery

        self.add("note", "Справочник", "Сведения о İstanbul.")
        reader = WorkspaceQuery(self.root)
        question = {"query": "Что известно про stanbul?"}
        with patch("second_brain.query.search_index.optional", return_value=nullcontext(None)):
            direct = reader.ask(question)
        self.assertEqual(direct["items"][0]["answer"], "Сведения о İstanbul.")
        self.assertEqual(reader.ask(question), direct)
        self.assertEqual(search(self.root, "Istanbul")[0]["text"], "Сведения о İstanbul.")
        self.assertEqual(search(self.root, "stanbul"), [])
