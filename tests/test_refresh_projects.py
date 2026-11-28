from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from second_brain import history_archive, project_updates, storage
from second_brain.batch import Batch
from second_brain.refresh_projects import refresh_projects


class RefreshProjectsTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name))
        self.area = storage.read_record(storage.create(self.root, "direction", "Печать"))["id"]
        self.batch = Batch(self.root)

    def card(self, title, body="", **fields):
        path = storage.create(self.root, "project", title, body, direction=self.area, **fields)
        return storage.read_record(path)["id"], path

    def run_refresh(self):
        with storage._write_lock(self.root):
            return refresh_projects(self.root)

    def test_a_fresh_decision_after_rotation_cites_the_current_project(self):
        decision = "Решили печатать карты на матовой бумаге."
        key, path = self.card("Тираж", f"## История статусов\n### 2024-01-10 — образцы\n{decision}\n\n"
                                       f"## Сегодня\n{decision}\n")
        options = dict(as_of="2026-11-28", min_kept=0)
        plan = history_archive.rotate(self.root, key, **options)
        history_archive.rotate(self.root, key, apply=plan["revision"], **options)
        self.assertEqual(self.run_refresh()["eventsAdded"], 1)
        event = project_updates.read_updates(self.root, key)["events"][0]
        self.assertEqual(event["sources"][0]["path"], path.relative_to(self.root).as_posix())
        self.assertEqual(event["sources"][0]["archivePlan"], plan["revision"])

    def test_canonical_state_and_project_decisions_are_citable_and_repeatable(self):
        key, path = self.card("Тираж", "Решили печатать карты на матовой бумаге.\n\nУтвердили обложку.\n", status="Готов")
        storage.create(self.root, "note", "Посторонний протокол", "Утвердили другое решение.", direction=self.area)
        result = self.run_refresh()
        self.assertEqual((result["changed"], result["eventsAdded"], result["summariesUpdated"], result["issues"]),
                         (1, 2, 1, []))
        shown = project_updates.read_updates(self.root, key)
        self.assertEqual(shown["total"], 2)
        self.assertEqual(shown["brief"]["text"], "Статус — Готов")
        self.assertTrue(all(e["sources"][0]["available"] for e in [*shown["events"], shown["brief"]]))
        before = path.read_bytes()
        self.assertEqual(self.run_refresh()["changed"], 0)
        self.assertEqual(path.read_bytes(), before)
        storage.edit(self.root, key, shown["revision"], {"status": "Отправлен"})
        self.assertEqual(self.run_refresh()["summariesUpdated"], 1)
        self.assertIn("Отправлен", project_updates.read_updates(self.root, key)["brief"]["text"])

    def test_processed_field_only_publishes_approved_value_and_respects_permission(self):
        key, path = self.card("Набор")
        rule = self.batch.routing.set_rule(self.area, "Веди сроки.", ["next_step"], [])
        original = self.root / "Inbox" / "Встреча.md"
        quote = "Следующий шаг — собрать образцы. Личное слово из переписки."
        original.write_text(quote)
        item, = self.batch.pending()
        action = {"kind": "project", "project": key, "direction": self.area, "field": "next_step",
                  "value": "собрать образцы", "line": 1, "quote": quote, "expected_revision": storage.read_record(path)["revision"],
                  "rule_revision": rule["revision"]}
        self.batch.apply({"path": item["path"], "revision": item["revision"], "actions": [action]})
        before = path.read_bytes()
        self.assertEqual(self.run_refresh()["changed"], 0)
        self.assertEqual(path.read_bytes(), before)
        self.batch.routing.set_rule(self.area, "Веди историю.", ["event", "summary"], [], rule["revision"])
        result = self.run_refresh()
        self.assertEqual((result["eventsAdded"], result["summariesUpdated"], result["issues"]), (1, 1, []))
        updates = project_updates.read_updates(self.root, key)
        source = updates["events"][0]["sources"][0]
        self.assertEqual((source["quote"], source["available"]), ("собрать образцы", True))
        self.assertNotIn("Личное слово", path.read_text())
        self.assertEqual(self.run_refresh()["changed"], 0)

    def test_owner_text_and_independent_note_are_preserved(self):
        key, path = self.card("Принтер", "## Коротко\n\nЖдём решения владельца.\n", status="Доставлен")
        storage.create(self.root, "note", "Принтер", "Решили установить красный лоток.", direction=self.area)
        self.card("Пустой")
        before = path.read_bytes()
        result = self.run_refresh()
        self.assertEqual((result["examined"], result["changed"], result["manualPreserved"], result["issues"]),
                         (2, 0, 1, []))
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(project_updates.read_updates(self.root, key)["total"], 0)

    def test_tentative_examples_and_duplicates_do_not_block_an_independent_project(self):
        _, first = self.card("Дубль", "Решили заказать тираж.")
        copy = first.with_name("Копия.md")
        original = first.read_bytes()
        copy.write_bytes(original)
        key, path = self.card("Настоящий", "Решили бы перенести тираж.\n\n## Предложения\n"
                              "Утвердили бюджет.\n### Смета\nСогласовали цены.\n"
                              "## Пример\nРешили нанять курьера.\n## Итоги\n"
                              "Решили заказать пробный тираж.\n```md\nРешили изменить макет.\n```\n")
        result = self.run_refresh()
        self.assertEqual((result["examined"], result["changed"], result["eventsAdded"]), (3, 1, 1))
        self.assertEqual({i["path"] for i in result["issues"]}, {p.relative_to(self.root).as_posix() for p in [first, copy]})
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual(copy.read_bytes(), original)
        changed = path.rename(path.with_name("Тираж мастерской.md"))
        changed.write_text(changed.read_text().replace("## Итоги", "## Решения команды\n\nПредисловие."))
        event, = project_updates.read_updates(self.root, key)["events"]
        self.assertEqual(event["sources"][0]["quote"], "Решили заказать пробный тираж.")
        self.assertTrue(event["sources"][0]["available"])
        self.assertEqual(event["sources"][0]["path"], changed.relative_to(self.root).as_posix())

    def test_refresh_protects_manual_deletion_and_advances_unambiguous_dated_ai_summary(self):
        key, path = self.card("Палитра", status="Согласован план")
        rule = self.batch.routing.rules()[0]
        def incoming(date, fact, **extra):
            quote = date + ". " + fact
            (self.root / "Inbox" / (date + ".md")).write_text(quote)
            item, = self.batch.pending()
            action = {"kind": "project_event", "direction": self.area, "project": key, "quote": quote, "line": 1,
                      "rule_revision": rule["revision"], "expected_revision": storage.read_record(path)["revision"],
                      "date": date, "text": fact, **extra}
            self.batch.apply({"path": item["path"], "revision": item["revision"], "actions": [action]})
        incoming("2026-11-20", "Заказан пробный оттиск.", summary="Ожидаем пробный оттиск.")
        incoming("2026-11-22", "Пробный оттиск получен.")
        result = self.run_refresh()
        self.assertEqual((result["eventsAdded"], result["summariesUpdated"], result["issues"]), (0, 1, []))
        current = project_updates.read_updates(self.root, key)
        self.assertEqual(current["brief"]["text"], "Пробный оттиск получен.")
        self.assertTrue(current["brief"]["sources"][0]["available"])
        self.assertEqual(self.run_refresh()["changed"], 0)

        manual = project_updates.write_update(self.root, key, field="brief", text="Принято. Ждём сверки.",
                                             actor="manual", revision=current["revision"])
        source = current["brief"]["sources"][0]
        agreed = project_updates.write_update(self.root, key, field="brief", text="Согласованная ручная сводка.",
                                             sources=[source], revision=manual["revision"],
                                             accepted_manual_revision=manual["revision"])
        self.assertTrue(agreed["brief"]["acceptedByOwner"])
        incoming("2026-11-23", "Краски доставлены.")
        self.assertEqual(self.run_refresh()["manualPreserved"], 1)
        self.assertEqual(project_updates.read_updates(self.root, key)["brief"]["text"], "Согласованная ручная сводка.")

        raw = path.read_text()
        start = raw.index("## Коротко\n")
        end = raw.index("<!-- /brain-project-section -->", start) + len("<!-- /brain-project-section -->")
        path.write_text(raw[:start] + raw[end:])
        before = path.read_bytes()
        result = self.run_refresh()
        self.assertEqual((result["manualPreserved"], result["issues"], result["changed"]), (1, [], 0))
        self.assertEqual(path.read_bytes(), before)

    def test_recorded_agreement_and_explicit_project_link_but_not_a_name_or_two_recipients(self):
        key, path = self.card("Кабинет", "Для первого запуска согласован вход по одноразовой ссылке.\n"
                              "Для следующего запуска не согласован срок.\n"
                              "Для повторного запуска будет согласован другой порядок.\n"
                              "> Для примера согласован новый цвет.\n")
        other_key, other = self.card("Поддержка")
        note = storage.create(self.root, "note", "Совместная встреча",
                              "Решили проверить [Кабинет](../Projects/Кабинет.md) на стенде.\n"
                              "Решили проверить проект Поддержка с консультантом.\n"
                              "Решили созвониться по [Кабинету](../Projects/Кабинет.md) и "
                              "[Поддержке](../Projects/Поддержка.md).\n", direction=self.area)
        result = self.run_refresh()
        self.assertEqual((result["eventsAdded"], result["changed"]), (2, 1))
        self.assertEqual(result["example"], {"id": key, "path": path.relative_to(self.root).as_posix()})
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(project_updates.read_updates(self.root, other_key)["total"], 0)
        rows = project_updates.read_updates(self.root, key)["events"]
        self.assertEqual({e["text"] for e in rows}, {"«Решили проверить Кабинет на стенде.»",
                         "«Для первого запуска согласован вход по одноразовой ссылке.»"})
        self.assertTrue(all(e["sources"][0]["available"] for e in rows))
        renamed = note.rename(note.with_name("Протокол.md"))
        before = path.read_bytes()
        self.assertEqual(self.run_refresh()["changed"], 0)
        self.assertEqual(path.read_bytes(), before)
        rows = project_updates.read_updates(self.root, key)["events"]
        self.assertTrue(any(e["sources"][0]["available"] and e["sources"][0]["path"]
                            == renamed.relative_to(self.root).as_posix() for e in rows))
        moved = path.rename(path.with_name("Кабинет поддержки.md"))
        renamed.write_text(renamed.read_text().replace("Кабинет.md", "Кабинет%20поддержки.md"))
        self.assertEqual(self.run_refresh()["eventsAdded"], 0)
        self.assertEqual(project_updates.read_updates(self.root, key)["total"], 2)

    def test_unique_wikilink_targets_and_confirmed_negative_decision(self):
        key, _ = self.card("Тираж", "Решение: пока не принято.\nРешение: возможно сменим шрифт.\nРешили не переносить выпуск.")
        other, _ = self.card("Архив")
        storage.create(self.root, "note", "Архив", "Решили изучить смету.", direction=self.area)
        storage.create(self.root, "note", "Совет", "Решили проверить [[Projects/Тираж|журнал]] в пятницу.\n"
                       "Утвердили цену для [[Тираж]].\nРешили изменить [[Архив]].", direction=self.area)
        result = self.run_refresh()
        self.assertEqual((result["changed"], result["eventsAdded"]), (1, 3))
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(project_updates.read_updates(self.root, other)["total"], 0)
        result = project_updates.read_updates(self.root, key)
        self.assertEqual({e["text"] for e in result["events"]}, {"«Решили проверить журнал в пятницу.»",
                         "«Утвердили цену для Тираж.»", "«Решили не переносить выпуск.»"})
        self.assertEqual(self.run_refresh()["changed"], 0)

    def test_first_refresh_keeps_markdown_source_labels_on_the_original_line(self):
        _, path = self.card("Поставка", "Цель — выпустить карту.\n\nДля первого запуска согласован вход по ссылке.")
        original = path.read_bytes().replace(b"schema_version: 1", b"schema_version: 1 # owner setting")
        path.write_bytes(original.replace(b"\n", b"\r\n"))
        fact = "Для первого запуска согласован вход по ссылке."
        number = path.read_text().splitlines().index(fact) + 1
        result = self.run_refresh()
        self.assertEqual((result["eventsAdded"], result["summariesUpdated"], result["issues"]), (1, 1, []))
        current = path.read_bytes()
        lines = current.decode().splitlines()
        self.assertEqual(lines[number - 1], fact)
        footers = [line for line in lines if line.startswith("[Источник, строка ")]
        self.assertEqual(len(footers), 2)
        self.assertTrue(all(line.startswith(f"[Источник, строка {number}]") for line in footers))
        self.assertIn(b"schema_version: 1 # owner setting", current)
        self.assertNotIn(b"\n", current.replace(b"\r\n", b""))
        self.assertEqual(self.run_refresh()["changed"], 0)
        self.assertEqual(path.read_bytes(), current)

        # An existing vault can still have the first version's individual YAML comment lines.
        row = next(line for line in lines if line.startswith("schema_version:"))
        original_field, *marks = row.split("  # project-refresh-")
        legacy = current.replace(row.encode(), original_field.encode()).replace(
            b"\r\n---\r\n", b"\r\n" + b"".join(b"# project-refresh-" + mark.encode() + b"\r\n" for mark in marks)
            + b"---\r\n", 1)
        path.write_bytes(legacy)
        self.assertEqual(self.run_refresh()["changed"], 0)
        self.assertEqual(path.read_bytes(), legacy)
