import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from second_brain import history_archive, project_updates, search, search_index, storage
from second_brain.query import WorkspaceQuery


class HistorySearchTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.reader = WorkspaceQuery(self.root)

    def cli(self, *args):
        command = [sys.executable, "-m", "second_brain", "--vault", str(self.root), *args]
        return json.loads(subprocess.check_output(command, text=True))

    def test_current_and_historical_status_use_separate_sources_even_without_index(self):
        for title, area in (("Релиз", "Работа"), ("Переезд", "Личное"), ("Формула", "Парфюмерия")):
            direction = storage.read_record(storage.create(self.root, "direction", area))["id"]
            file = storage.create(self.root, "project", title, direction=direction, status="утверждён",
                                  body="## История статусов\n### Снимок\nНоситель — прежний сапфир.\n\n"
                                       "## Текущий статус\nНоситель — новый янтарь.\n"
                                       "```md\n## История статусов\n```\nМатериал обсидиан.\n"
                                       "## Журнал\n- Отправлен берилл.\n")
            identifier = storage.read_record(file)["id"]
            for archived, wanted in ((False, "янтарь"), (True, "сапфир")):
                request = {"query": "Какой носитель?", "history": archived, "directionIds": [direction], "limit": 1}
                for idx in (False, True):
                    if idx:
                        search.rebuild(self.root)
                    item, = self.reader.ask(request)["items"]
                    self.assertIn(wanted, item["answer"])
                    src = item["source"]
                    self.assertEqual(file.read_text().splitlines()[src["start"]-1], item["answer"])
                    self.assertEqual(self.reader.open_citation({"id": identifier, "line": src["start"],
                                                               "ref": src["ref"]})[1]["start"], src["start"])
                self.assertIn(wanted, search.search(self.root, "носитель", record_id=identifier, history=archived)[0]["text"])
            self.assertEqual(search.search(self.root, "берилл", record_id=identifier), [])
            self.assertTrue(search.search(self.root, "обсидиан", record_id=identifier))
            self.assertEqual(search.search(self.root, "обсидиан", record_id=identifier, history=True), [])
            self.assertIn("утверждён", search.search(self.root, "утверждён", record_id=identifier)[0]["text"])
        self.assertFalse(self.cli("search", "сапфир"))
        self.assertTrue(self.cli("search", "сапфир", "--history"))
        self.assertTrue(self.cli("ask", "Где сапфир?", "--history", "--brief")["items"])
        note = storage.create(self.root, "note", "Учебник", body="## История парфюмерии\nРецептура помандера.")
        self.assertEqual(search.search(self.root, "помандер")[0]["path"], note.relative_to(self.root).as_posix())

    def test_old_summary_is_citable_only_as_history_and_cache_upgrades(self):
        path = storage.create(self.root, "project", "Склад")
        card = storage.read_record(path)
        first = project_updates.write_update(self.root, card["id"], field="brief", text="Выбран циркон.",
                                             revision=card["revision"], actor="manual")
        search.rebuild(self.root)
        with closing(sqlite3.connect(self.root / ".second-brain/search.sqlite3")) as db:
            db.execute("PRAGMA user_version=6")
        project_updates.write_update(self.root, card["id"], field="brief", text="Выбран опал.",
                                     revision=first["revision"], actor="manual")
        self.assertFalse(search.search(self.root, "циркон"))
        hit, = search.search(self.root, "циркон", history=True)
        self.assertEqual(hit["text"], "Выбран циркон.")
        self.assertIn(hit["text"], search.fragment(self.root, hit["path"], hit["start"], hit["end"]))
        item, = self.reader.ask({"query": "Где циркон?", "history": True})["items"]
        src = item["source"]
        _, opened = self.reader.open_citation({"id": card["id"], "line": src["start"], "ref": src["ref"]})
        self.assertEqual(opened["start"], hit["start"])
        with closing(sqlite3.connect(self.root / ".second-brain/search.sqlite3")) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], search_index._VERSION)

    def test_setext_sections_and_fenced_or_commented_examples(self):
        path = storage.create(self.root, "note", "Рецепт",
                              body="Архив\n-----\nЗаменён малахит.\n\n"
                                   "Решения\n=======\nУтверждён селенит.\n\n"
                                   "<!--\n## История статусов\n-->\nДобавлен авантюрин.\n\n"
                                   "```\n## Архив\n```\nНайден нефрит.")
        for word, archived in (("малахит", True), ("селенит", False), ("авантюрин", False), ("нефрит", False)):
            with self.subTest(word=word):
                self.assertEqual(len(search.search(self.root, word, history=archived)), 1)
                self.assertEqual(search.search(self.root, word, history=not archived), [])
                item, = self.reader.ask({"query": f"Где {word}?", "history": archived})["items"]
                self.assertIn(word, path.read_text().splitlines()[item["source"]["start"]-1])

    def test_quarterly_archive_is_searchable_only_while_owned_and_tracks_edits(self):
        area = storage.read_record(storage.create(self.root, "direction", "Лаборатория"))["id"]
        path = storage.create(self.root, "project", "Пуск", body="## Журнал\n- 2025-12-01 Осмотрен кианит.\n",
                              direction=area)
        card = storage.read_record(path)
        search.rebuild(self.root)
        options = {"as_of": "2026-11-28", "keep_days": 60, "min_kept": 0}
        preview = history_archive.rotate(self.root, card["id"], **options)
        applied = history_archive.rotate(self.root, card["id"], apply=preview["revision"], **options)
        archived = applied["archives"][0]["path"]
        self.assertFalse(search.search(self.root, "кианит"))
        hit, = search.search(self.root, "кианит", history=True, record_id=card["id"])
        self.assertEqual(hit["path"], archived)
        self.assertEqual(search.fragment(self.root, archived, hit["start"], hit["end"]), hit["text"])
        with patch.object(storage, "read_record", wraps=storage.read_record) as reads:
            self.assertTrue(search.search(self.root, "кианит", history=True))
            self.assertFalse(reads.called)
        path.write_text(path.read_text() + "\n## Текущая работа\nУтверждён формат.\n")
        with patch("second_brain.archive_reader.read_path", side_effect=AssertionError("cold part reindexed")):
            self.assertTrue(search.search(self.root, "формат"))
            self.assertEqual(search.search(self.root, "кианит", history=True), [hit])
        request = {"query": "Где кианит?", "directionIds": [area], "history": True}
        answer, = self.reader.ask(request)["items"]
        self.assertEqual(answer["source"]["path"], archived)
        self.assertEqual(answer["id"], hit["id"])
        (self.root / ".second-brain/search.sqlite3").unlink()
        self.assertEqual(self.reader.ask(request)["items"][0]["source"], answer["source"])
        self.assertFalse((self.root / ".second-brain/search.sqlite3").exists())
        search.rebuild(self.root)
        current = storage.read_record(path)
        storage.edit(self.root, card["id"], current["revision"], {"title": "Картотека", "aliases": ["Нувоит"]})
        self.assertTrue(any(row["path"] == archived for row in search.search(self.root, "нувоит", history=True)))
        path.write_text(path.read_text().replace("<!-- brain-history-archive: ", "<!-- former-archive: "))
        self.assertFalse(search.search(self.root, "кианит", history=True))
