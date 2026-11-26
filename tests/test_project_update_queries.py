from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from second_brain import brief, search, storage
from second_brain.batch import Batch
from second_brain.query import WorkspaceQuery
from second_brain.workspace import Workspace


class ProjectUpdateQueries(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Examples")
        self.batch = Batch(self.root)
        self.reader = WorkspaceQuery(self.root)
        self.workspace = Workspace(self.root)

    def project(self, direction, title, alias):
        area = storage.read_record(storage.create(self.root, "direction", direction))["id"]
        rule = self.batch.routing.set_rule(area, "Сохранять события и подтверждённую сводку.",
                                           ["event", "summary"], [])
        path = storage.create(self.root, "project", title, "Свободный текст владельца.",
                              direction=area, aliases=[alias])
        return area, rule["revision"], path

    def apply_events(self, project, entries):
        area, rule, path = project
        card = storage.read_record(path)
        incoming = self.root / "Inbox" / (card["title"] + ".txt")
        incoming.write_text("\n".join(quote for quote, _, _ in entries))
        item, = self.batch.pending()
        actions = []
        for number, (quote, summary, date) in enumerate(entries, 1):
            action = {"kind": "project_event", "project": card["id"], "direction": area,
                      "rule_revision": rule, "expected_revision": card["revision"],
                      "line": number, "quote": quote}
            if summary:
                action["summary"] = summary
            if date:
                action["date"] = date
            actions.append(action)
        receipt = self.batch.apply({"path": item["path"], "revision": item["revision"], "actions": actions})
        self.assertEqual(receipt["triage"], [])
        return card["id"]

    def test_project_updates_are_citable_in_three_directions(self):
        for direction, title, alias, evidence, summary in (
                ("ИТ-проекты", "Проверка восстановления", "Маяк", "Команда восстановила данные из резервной копии.",
                 "Восстановление из резервной копии удалось."),
                ("Личное", "Переезд", "Балкон", "Перевозчик забрал последний шкаф из спальни.",
                 "В спальне больше нет шкафов."),
                ("Парфюмерия", "Серия проб", "Кедр", "Запах кедра различим через шесть часов.",
                 "Через шесть часов сохраняется кедровая нота.")):
            with self.subTest(direction=direction):
                project = self.project(direction, title, alias)
                raw = "2026-04-02 " + evidence
                key = self.apply_events(project, [(raw, summary, "2026-04-02")])
                data = {"query": f"Где мы сейчас по проекту «{alias}» и что изменилось?", "limit": 5}
                answer = brief.answer(self.reader.ask(data), 5)
                self.assertEqual([(item["kind"], item["answer"]) for item in answer["items"]],
                                 [("brief", summary), ("event", raw)])
                self.assertEqual(answer["items"][1]["date"], "2026-04-02")
                for item in answer["items"]:
                    source = item["source"]
                    self.assertIn(item["answer"], search.fragment(self.root, source["path"], source["start"], source["end"]))
                    origin = source["origin"]
                    self.assertTrue(origin["path"].startswith("Sources/Originals/"))
                    self.assertEqual((self.root / origin["path"]).read_text().splitlines()[origin["line"]-1], raw)
                detail = self.workspace.get_record(key)
                self.assertIn("Свободный текст", detail["displayBody"])
                self.assertNotIn(summary, detail["displayBody"])
                self.assertEqual(brief.record(detail)["projectUpdates"]["brief"]["text"], summary)
                hit, = search.search(self.root, summary, record_id=key)
                self.assertEqual(hit["start"], answer["items"][0]["source"]["start"])
                by_name, = search.search(self.root, alias, record_id=key)
                self.assertEqual(by_name["start"], hit["start"])
        self.assertTrue(all("projectUpdates" not in row for row in self.workspace.overview()["records"]))

    def test_changed_brief_history_is_excluded_and_limits_are_honored(self):
        project = self.project("Сад", "Весенний сад", "Бутон")
        old = "Началась фаза цветения."
        new = "Появились всходы."
        self.apply_events(project, [("Расцвели цветы.", old, None)])
        self.assertTrue(search.search(self.root, "фаза"))
        key = self.apply_events(project, [("Сеянцы взошли.", new, None)])
        storage.create(self.root, "note", "Словарь садовника", "Бутон", aliases=["Бутон"])
        exact, = search.search(self.root, "Бутон", limit=1)
        self.assertEqual((exact["id"], exact["text"]), (key, new))
        self.assertEqual(search.search(self.root, "фаза"), [])
        self.assertEqual(self.reader.ask({"query": "Где фаза?"})["items"], [])
        items = self.reader.ask({"query": "Что изменилось по Бутону?", "limit": 1})
        self.assertEqual(items["total"], 2)
        self.assertTrue(items["truncated"])
        self.assertEqual(items["items"][0]["answer"], "Сеянцы взошли.")
        self.assertNotIn("date", items["items"][0])
        before = self.reader.ask({"query": "Коротко по Бутону"})["items"][0]
        archived = self.root / before["source"]["origin"]["path"]
        archived.rename(archived.with_suffix(".moved"))
        after = brief.answer(self.reader.ask({"query": "Коротко по Бутону"}), 1)
        self.assertFalse(after["items"][0]["source"]["origin"]["available"])
        path = project[2]
        renamed = path.rename(path.with_name("Перенесённый сад.md"))
        renamed.write_text(renamed.read_text().replace("Свободный текст", "Ещё строка.\nСвободный текст"))
        self.assertEqual(self.reader.open_citation({"id": key, "line": before["source"]["start"],
                                                   "ref": before["source"]["ref"]})[1]["start"],
                         before["source"]["start"] + 1)
        duplicate = self.project("Сувениры", "Весенний каталог", "Бутон")
        ambiguous = self.reader.ask({"query": "Коротко по Бутону"})
        self.assertEqual(ambiguous["items"], [])
        self.assertIn("нескольким", ambiguous["message"])
        unique = self.reader.ask({"query": "Коротко по Бутону", "directionIds": [duplicate[0]]})
        self.assertEqual(unique["items"], [])
        self.assertIn("пока", unique["message"])

    def test_legacy_project_gives_current_fields_with_their_yaml_sources(self):
        storage.create(self.root, "project", "Конверт", "Статус раньше был «подготовка».",
                       status="Согласован", next_step="Напечатать тираж")
        current = self.reader.ask({"query": "Где мы сейчас по Конверту и что изменилось?"})
        self.assertEqual([(item["kind"], item["answer"]) for item in current["items"]],
                         [("status", "Согласован"), ("next_step", "Напечатать тираж")])
        self.assertIn("в отдельном журнале проекта пока нет записей", current["message"])
        for item in current["items"]:
            source = item["source"]
            self.assertIn(item["kind"] + ":", search.fragment(self.root, source["path"], source["start"], source["end"]))
        explicit = self.reader.ask({"query": "Покажи сводку по Конверту"})
        self.assertEqual(explicit["items"], [])
        self.assertIn("сводка", explicit["message"])

    def test_owner_written_markdown_is_current_without_a_generated_history(self):
        path = storage.create(self.root, "project", "Мастерская",
                              "До разделов.\n\n## Коротко\nПервый абзац.\n\n"
                              "Второй абзац.\n\n## Последнее\nОткрыли двери.\n\n## Архитектура\nПлан зала.")
        record = storage.read_record(path)
        answer = self.reader.ask({"query": "Где мы сейчас по Мастерской и что изменилось?"})
        summary, event = answer["items"]
        self.assertEqual(summary["answer"], "Первый абзац.\n\nВторой абзац.")
        src = summary["source"]
        self.assertEqual(summary["answer"], search.fragment(self.root, src["path"], src["start"], src["end"]))
        self.assertEqual(event["answer"], "Открыли двери.")
        self.assertNotIn("origin", event["source"])
        detail = self.workspace.get_record(record["id"])
        self.assertEqual(detail["projectUpdates"]["brief"]["author"], "manual")
        self.assertIn("План зала.", detail["displayBody"])
        self.assertNotIn("Открыли двери.", detail["displayBody"])

    def test_partial_journal_is_marked_in_record_and_answer(self):
        project = self.project("Работа", "Открытие офиса", "Офис")
        key = self.apply_events(project, [("Подключили воду.", None, None)])
        path = project[2]
        path.write_text(path.read_text().replace("<!-- brain-project-entry: ", "<!-- brain-project-entry: broken "))
        record = self.workspace.get_record(key)
        warnings = record["projectUpdates"]["warnings"]
        self.assertEqual(brief.record(record)["projectUpdates"]["warnings"], warnings)
        answer = self.reader.ask({"query": "Что изменилось по Офису?"})
        self.assertEqual(answer["items"], [])
        self.assertIn(warnings[0], answer["message"])
        self.assertNotIn("нет записей", answer["message"])

    def test_event_dates_filter_the_whole_journal_before_pagination(self):
        project = self.project("Дом", "Ремонт 2026", "Роза")
        entries = [(day + " выполнили этап.", None, day)
                   for day in ("2026-01-04", "2026-11-01", "2026-11-25", "2026-12-10")]
        entries.append(("Событие без даты.", None, None))
        self.apply_events(project, entries)
        def ask(period, limit=5):
            return self.reader.ask({"query": "Что изменилось по проекту Ремонт 2026 " + period + "?", "limit": limit})
        scoped = ask("с 2026-11-01 по 2026-11-25", 1)
        self.assertEqual(([item["date"] for item in scoped["items"]], scoped["total"], scoped["truncated"]),
                         (["2026-11-25"], 2, True))
        self.assertEqual([i["date"] for i in ask("2026-01-04")["items"]], ["2026-01-04"])
        self.assertEqual([i["date"] for i in ask("с 2026-12-01")["items"]], ["2026-12-10"])
        self.assertEqual([i["date"] for i in ask("по 2026-10-01")["items"]], ["2026-01-04"])
        empty = ask("с 2026-06-01 по 2026-06-30")
        self.assertEqual(empty["total"], 0)
        self.assertIn("за указанный период", empty["message"])
        for invalid in ("за июль", "на этой неделе", "после 2026-01-04", "за 2026-02-30",
                        "с 2026-11-25 по 2026-11-01", "с 2026-06-01 по вчера"):
            with self.subTest(invalid=invalid):
                result = ask(invalid)
                self.assertEqual(result["items"], [])
                self.assertIn("Уточните период", result["message"])
