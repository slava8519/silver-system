from pathlib import Path
import tempfile
import unittest

from second_brain import project_updates as updates, storage
from second_brain.__main__ import command_line, run


class ProjectUpdatesTests(unittest.TestCase):
    def setUp(self):
        vault = tempfile.TemporaryDirectory()
        self.addCleanup(vault.cleanup)
        self.root = storage.initialize(Path(vault.name))
        self.path = storage.create(self.root, "project", "Переезд", "Решение владельца.")
        self.record = storage.read_record(self.path)
        self.id = self.record["id"]
        self.quote = "2026-11-26 Закончена проверка."
        self.note = storage.create(self.root, "note", "Протокол", "Начало.\n" + self.quote)
        self.source = {"path": self.note.relative_to(self.root).as_posix(), "quote": self.quote}
        self.source["line"] = self.note.read_text().splitlines().index(self.quote) + 1

    def save(self, field, text, **kwargs):
        fields = {"sources": [self.source], "revision": storage.read_record(self.path)["revision"]}
        return updates.write_update(self.root, self.id, field=field, text=text, **{**fields, **kwargs})

    def test_summary_event_original_dates_and_history(self):
        self.assertIsNone(updates.read_updates(self.root, self.id)["brief"])
        self.save("brief", "Проверка готова.")
        detail = self.save("event", "Закончена проверка.", date="2026-11-26")
        same = self.save("event", "Закончена проверка.", date="2026-11-26")
        self.assertEqual(same, detail)
        self.assertEqual(same["events"][0]["date"], "2026-11-26")
        self.assertEqual(self.path.read_text().splitlines()[detail["brief"]["line"]-1], "Проверка готова.")
        self.save("brief", "Готово к сдаче.")
        rows, hidden = updates.projection(storage.read_record(self.path))
        self.assertEqual({r["text"] for r in rows}, {"Готово к сдаче.", "Закончена проверка."})
        lines = self.path.read_text().splitlines()
        self.assertTrue(any("Проверка готова." in lines[n-1] for n in hidden))
        self.assertIn("Решение владельца.", lines)
        with self.assertRaises(ValueError):
            self.save("event", "Неверная дата", date="2026-11-25")
        with self.assertRaises(ValueError):
            self.save("brief", "Нет ссылки", sources=[])

    def test_reviewed_inbox_pair_preserves_owner_and_blocks_stale_updates(self):
        def apply_inbox(quote, date, summary):
            source = dict(self.source, quote=quote)
            record = storage.read_record(self.path)
            body, state = updates.inbox_update(self.root, self.path, record, source=source,
                                              date=date, text=quote, summary=summary)
            storage.edit(self.root, self.id, record["revision"], {"body": body})
            return state

        self.assertEqual(apply_inbox(self.quote, "2026-11-26", "Первая версия."), "updated")
        self.assertEqual(apply_inbox("Закончена проверка.", None, "Более старый контекст."), "older_event")
        self.assertEqual(updates.read_updates(self.root, self.id)["brief"]["text"], "Первая версия.")
        self.path.write_text(self.path.read_text().replace("\nПервая версия.\n", "\nРешение участников.\n"))
        revised = updates.read_updates(self.root, self.id)["brief"]
        self.assertTrue(revised["manual"])
        self.assertEqual(revised["sources"], [])
        self.assertEqual(apply_inbox(self.quote, "2026-11-26", "Новое предложение."), "manual_preserved")
        with self.assertRaises(storage.ConflictError):
            self.save("brief", "Новое предложение.")
        page = updates.read_updates(self.root, self.id, limit=1)
        self.assertEqual((page["total"], page["truncated"], page["brief"]["text"]), (2, True, "Решение участников."))
        self.assertIsNone(updates.read_updates(self.root, self.id, limit=1, offset=1)["events"][0]["date"])
        self.save("brief", "Исправил владелец.", actor="manual", sources=[])
        with self.assertRaises(storage.ConflictError):
            self.save("brief", "Устаревшая версия", actor="manual", sources=[], revision=self.record["revision"])

    def test_original_selected_quote_and_source_rename(self):
        from hashlib import sha256
        data = "Личная информация\n2026-11-26 Подписан акт. Ещё один частный комментарий.\n".encode()
        identifier = sha256(data).hexdigest()
        original = self.root / "Sources" / "Originals" / (identifier + ".txt")
        original.write_bytes(data)
        selected = {"path": original.relative_to(self.root).as_posix(), "line": 2, "quote": "Подписан акт."}
        result = self.save("event", "Акт подписан.", sources=[selected])
        ref = result["events"][0]["sources"][0]
        self.assertEqual(ref["href"], "/original/" + identifier + "?start=2")
        self.assertNotIn("частный", str(result))
        original.write_bytes(b"other contents")
        self.assertFalse(updates.read_updates(self.root, self.id)["events"][0]["sources"][0]["available"])
        with self.assertRaises(ValueError):
            self.save("brief", "Акт подписан.", sources=[selected])
        with self.assertRaises(ValueError):
            self.save("brief", "Нельзя.", sources=[{**selected, "path": "Sources/Originals/../../README.md"}])
        self.save("brief", "Проверка готова.")
        new = self.note.rename(self.note.with_name("Перенесённый протокол.md"))
        new.write_text(new.read_text().replace("\nНачало.", "\nНовая строка.\nНачало."))
        ref = updates.read_updates(self.root, self.id)["brief"]["sources"][0]
        self.assertEqual((ref["path"], ref["line"]), (new.relative_to(self.root).as_posix(), self.source["line"] + 1))

    def test_handwritten_summary_and_cli(self):
        self.path.write_text(self.path.read_text() + "\n## Коротко\n\nВладелец проверил.\n")
        with self.assertRaises(storage.ConflictError):
            self.save("brief", "Перепишем.")
        parser = command_line()
        prefix = ["--vault", str(self.root)]
        read = run(parser.parse_args(prefix + ["project-updates", self.id]))
        self.assertTrue(read["brief"]["manual"])
        args = ["project-update", self.id, "--revision", read["revision"], "--field", "event",
                "--text", self.quote, "--date", "2026-11-26", "--source", self.source["path"],
                "--line", str(self.source["line"]), "--quote", self.quote]
        result = run(parser.parse_args(prefix + args))
        self.assertEqual(result["events"][0]["sources"][0]["quote"], self.quote)
        self.assertTrue(result["brief"]["manual"])

    def test_example_fences_and_ambiguous_old_headings_remain_free_text(self):
        old = "Введение\n```markdown\n## Коротко\nПример.\n```\n\n## Коротко\nОдин.\n## Коротко\nДва.\n"
        self.path.write_text(self.path.read_text().replace("Решение владельца.", old))
        record = storage.read_record(self.path)
        self.assertEqual(updates.projection(record), ([], set()))
        self.assertIsNone(updates.read_updates(self.root, self.id)["brief"])
        with self.assertRaises(storage.ConflictError):
            self.save("brief", "Неоднозначная правка", actor="manual", sources=[])
        new_body, status = updates.inbox_update(self.root, self.path, record, source=self.source,
                                                text="Подтверждённое событие.", summary="Сводка.")
        self.assertEqual(status, "manual_preserved")
        self.assertIn(old, new_body)
        self.assertIn("Подтверждённое событие.", new_body)

    def test_unclosed_fenced_example_remains_at_the_end_when_writing_history(self):
        tail = "   ~~~~md\n## Коротко\nТекст примера\n~~~\n<!-- brain-project-brief-change: пример -->"
        self.path.write_text(self.path.read_text() + "\n" + tail)
        detail = self.save("brief", "Итог по источнику.")
        self.assertEqual(detail["brief"]["text"], "Итог по источнику.")
        self.assertTrue(self.path.read_text().endswith(tail))
        self.assertIn("## История сводки проекта", self.path.read_text().split(tail)[0])

    def test_explicit_acceptance_and_no_self_reference(self):
        manual = self.save("brief", "Вариант владельца.", actor="manual", sources=[])
        self.assertTrue(manual["brief"]["manual"])
        with self.assertRaises(storage.ConflictError):
            self.save("brief", "Иной вариант", accepted_manual_revision=self.record["revision"])
        accepted = self.save("brief", "Принято владельцем.", accepted_manual_revision=manual["revision"])
        self.assertTrue(accepted["brief"]["acceptedByOwner"])
        self.assertTrue(accepted["brief"]["manual"])
        with self.assertRaises(ValueError):
            self.save("brief", "Самоцитата", accepted_manual_revision=accepted["revision"],
                      sources=[{"path": accepted["path"], "line": accepted["brief"]["line"],
                                "quote": "Принято владельцем."}])
        self.assertEqual(accepted, updates.read_updates(self.root, self.id))
        approved = self.path.read_text()
        self.assertIn('"acceptedManualRevision":"' + manual['revision'], approved)

    def test_stale_identical_event_retry_and_short_cli(self):
        event = self.save("event", self.quote, date="2026-11-26")
        self.assertEqual(event, self.save("event", self.quote, date="2026-11-26", revision=self.record["revision"]))
        self.path.write_text(self.path.read_text() + "\n## Коротко\n" + "а" * 20000 + "\n")
        result = run(command_line().parse_args(["--vault", str(self.root), "project-updates", self.id]))
        self.assertEqual(len(result["brief"]["text"]), 600)
        self.assertIn("text", result["brief"]["truncatedFields"])

    def test_one_damaged_block_is_excluded_from_search_and_never_overwritten(self):
        from second_brain import search
        self.save("event", "Опубликованный фрагмент.")
        self.save("brief", "Тайная фраза, оказавшаяся в повреждённом блоке.")
        lines = self.path.read_text().splitlines(keepends=True)
        heading = next(n for n, line in enumerate(lines) if line.strip() == "## Коротко")
        marker = next(n for n in range(heading+1, len(lines)) if lines[n].startswith(updates._ENTRY))
        lines[marker] = updates._ENTRY + "{сломано} -->\n"
        self.path.write_text("".join(lines))
        read = updates.read_updates(self.root, self.id)
        self.assertIsNone(read["brief"])
        self.assertTrue(read["warnings"])
        self.assertEqual(read["events"][0]["text"], "Опубликованный фрагмент.")
        self.assertTrue(search.search(self.root, "Опубликованный"))
        self.assertEqual(search.search(self.root, "Тайная"), [])
        with self.assertRaises(storage.ConflictError):
            self.save("brief", "Перезапись", actor="manual", sources=[])
