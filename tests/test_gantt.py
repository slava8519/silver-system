from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from second_brain import storage
from second_brain.gantt import Gantt
from second_brain.plan_dates import Deadlines
from second_brain.planning import Planning
from second_brain.query import WorkspaceQuery


class GanttTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = storage.initialize(Path(tmp.name) / "Vault")
        self.plans = Planning(self.root)
        self.gantt = Gantt(self.root)
        self.task = self.plans.create_task("Подготовить пробу")
        self.path = self.root / self.task["path"]
        self.note = storage.create(self.root, "note", "Протокол", body="Решили сравнить образцы. 20.11.2026\n")
        self.record = storage.read_record(self.note)
        self.source = {"path": self.note.relative_to(self.root).as_posix(), "line": self.record["body_start"],
                       "quote": "сравнить образцы. 20.11.2026"}

    def detail(self):
        return self.gantt.detail(self.task["id"])

    def edit(self, field="brief", actor="ai", **kwargs):
        return self.gantt.edit({"id": self.task["id"], "revision": self.detail()["revision"], "field": field,
                                "actor": actor, **kwargs})

    def test_three_directions_one_task_per_two_plans_and_start_optional(self):
        week = self.plans.create_plan("Неделя")
        for direction, project in (("ИТ", "Маяк"), ("Личное", None), ("Парфюмерия", "Серия")):
            area = storage.read_record(storage.create(self.root, "direction", direction))
            options = {"direction": area["id"]}
            if project:
                card = storage.read_record(storage.create(self.root, "project", project, direction=area["id"]))
                options = {"project": card["id"]}
            task = self.plans.create_task("Проверить " + direction, due="2026-12-10", **options)
            plan = self.plans.create_plan(direction)
            for target in (week, plan):
                self.plans.add_task(target["id"], task["id"])
            base = self.gantt.view(plan["id"])["items"][0]
            self.assertEqual((base["projectTitle"], base["directionTitle"], base["start"]), (project, direction, None))
            with_start = self.gantt.edit({"id": task["id"], "revision": task["revision"], "actor": "manual",
                                         "field": "start", "value": "2026-11-20"})
            first = self.gantt.edit({"id": task["id"], "revision": with_start["revision"], "actor": "ai",
                                     "field": "brief", "text": direction, "sources": [self.source]})
            due = Deadlines(self.root)
            proposal = due.preview(task["id"], "2026-12-12")
            due.apply(task["id"], proposal["after"], proposal["revision"])
            self.assertEqual(self.gantt.detail(task["id"])["brief"], first["brief"])
            with self.assertRaises(ValueError):
                due.preview(task["id"], "2026-11-19")
            for target in (week, plan):
                row = next(r for r in self.gantt.view(target["id"])["items"] if r["id"] == task["id"])
                self.assertEqual((row["start"], row["due"], row["title"]),
                                 ("2026-11-20", "2026-12-12", task["title"]))
                self.assertNotIn("brief", row)
                self.assertNotIn("сравнить образцы", (self.root / target["path"]).read_text())

    def test_traceable_manual_and_ai_corrections_date_and_source_moves(self):
        summary = self.edit(text="Сравним образцы.", sources=[self.source])["brief"]
        self.assertFalse(summary["manual"])
        self.assertEqual(summary["author"], "ai")
        ref = summary["sources"][0]
        self.assertTrue(ref["href"].startswith("/source/"))
        original = self.edit("latest", text="Сравнение согласовано.", date="2026-11-20", sources=[self.source])
        event = original["latest"][0]
        second = self.edit("latest", text="Сравнение согласовано.", date="2026-11-20", sources=[self.source])
        self.assertEqual(original["revision"], second["revision"])
        manual = self.edit("latest", "manual", entryId=event["id"], text="Сверяем два образца.")
        revised = manual["latest"][0]
        self.assertEqual((revised["date"], revised["author"], revised["id"]),
                         ("2026-11-20", "manual", event["id"]))
        change = manual["history"][0]
        self.assertEqual((change["before"], change["after"], change["beforeAuthor"]),
                         (event["text"], revised["text"], "ai"))
        self.assertEqual(revised["sources"], [ref])
        with self.assertRaises(storage.ConflictError):
            self.edit("latest", entryId=event["id"], text="Пересчёт.", sources=[self.source])
        self.assertEqual(manual["revision"], self.detail()["revision"])
        self.assertEqual(manual["revision"], self.edit("latest", "manual", entryId=event["id"],
                                                      text=revised["text"])["revision"])
        moved = self.note.rename(self.note.with_name("Перенесено.md"))
        moved.write_text(moved.read_text().replace("Решили сравнить", "Первая строка.\nРешили сравнить"))
        _, resolved = WorkspaceQuery(self.root).open_citation(ref)
        self.assertEqual(resolved["path"], moved.relative_to(self.root).as_posix())
        self.assertEqual(resolved["start"], ref["line"] + 1)
        manual_after_move = self.edit("latest", "manual", entryId=event["id"], text="Сверяем по отдельности.")
        self.assertEqual(manual_after_move["latest"][0]["sources"], [ref])
        moved.write_text(moved.read_text().replace("Решили сравнить образцы.", "Контроль отменён."))
        with self.assertRaises(storage.ConflictError):
            WorkspaceQuery(self.root).open_citation(ref)
        self.assertEqual(self.detail()["brief"]["sources"][0]["quote"], self.source["quote"])

    def test_stale_revision_and_native_edit_and_deletion_protect_owner(self):
        first = self.edit(text="Вывод ИИ", sources=[self.source])
        original = self.path.read_text()
        self.path.write_text(original.replace("\nВывод ИИ\n", "\nВладелец поправил вывод.\n"))
        stale = {"id": self.task["id"], "revision": first["revision"], "field": "brief", "actor": "manual",
                 "text": "Устарело"}
        with self.assertRaises(storage.ConflictError):
            self.gantt.edit(stale)
        native = self.detail()["brief"]
        self.assertEqual((native["text"], native["updated"], native["manual"]),
                         ("Владелец поправил вывод.", None, True))
        before = self.path.read_bytes()
        with self.assertRaises(storage.ConflictError):
            self.edit(text="Автоматическое обновление", sources=[self.source])
        self.assertEqual(self.path.read_bytes(), before)
        revised = self.edit(actor="manual", text="Уточнение")
        self.assertEqual(revised["history"][0]["before"], native["text"])
        latest = self.edit("latest", text="Событие", sources=[self.source])["latest"][0]
        data = self.path.read_text()
        # Simulate removing the entire entry, including the markers, in a Markdown editor.
        self.path.write_text(data[:data.index("## Последнее")].replace(
            data[data.index("## Коротко"):data.index("## История Ганта")], ""))
        removed = self.gantt.detail(self.task["id"], all_entries=True)
        self.assertTrue(removed["brief"]["deleted"])
        self.assertEqual((removed["latest"][0]["id"], removed["latest"][0]["deleted"]), (latest["id"], True))
        self.assertEqual(removed["latestTotal"], 0)
        with self.assertRaises(storage.ConflictError):
            self.edit(text="Вернул вывод", sources=[self.source])
        with self.assertRaises(storage.ConflictError):
            self.edit("latest", text="Событие", sources=[self.source])

    def test_owner_accepts_specific_ai_revision_without_losing_attribution_or_protection(self):
        for field in ("brief", "latest"):
            with self.subTest(field=field):
                manual = self.edit(field, "manual", text="Формулировка владельца")
                changes = {"id": self.task["id"], "revision": manual["revision"], "field": field, "actor": "ai",
                           "text": "Согласованное уточнение", "sources": [self.source]}
                if field == "latest":
                    changes["entryId"] = manual["latest"][0]["id"]
                with self.assertRaises(storage.ConflictError):
                    self.gantt.edit({**changes, "acceptedByOwner": True})
                with self.assertRaises(ValueError):
                    self.gantt.edit({**changes, "sources": []}, accepted_manual_revision=manual["revision"])
                interim = self.edit("latest", "manual", text="Изменение в соседнем поле " + field)
                with self.assertRaises(storage.ConflictError):
                    self.gantt.edit({**changes, "revision": interim["revision"]},
                                    accepted_manual_revision=manual["revision"])
                self.assertEqual(self.detail()["revision"], interim["revision"])
                changes["revision"] = interim["revision"]
                result = self.gantt.edit(changes, accepted_manual_revision=interim["revision"])
                saved = result["brief"] if field == "brief" else next(
                    e for e in result["latest"] if e["id"] == changes["entryId"])
                self.assertEqual((saved["author"], saved["acceptedByOwner"], saved["manual"]), ("ai", True, True))
                self.assertEqual(saved["text"], changes["text"])
                change = result["history"][0]
                self.assertEqual((change["before"], change["beforeAuthor"], change["beforeManual"]),
                                 ("Формулировка владельца", "manual", True))
                self.assertEqual((change["actor"], change["acceptedByOwner"], change["acceptedManualRevision"]),
                                 ("ai", True, interim["revision"]))
                with self.assertRaises(storage.ConflictError):
                    self.gantt.edit({**changes, "text": "Без согласия", "revision": result["revision"]})
                # Subsequent direct edits to the visible text are attributed to their human editor.
                live = self.path.read_text().replace("\nСогласованное уточнение\n", "\nУточнено вручную\n")
                self.path.write_text(live)
                detail = self.detail()
                edited = detail["brief"] if field == "brief" else next(
                    e for e in detail["latest"] if e["id"] == changes["entryId"])
                self.assertEqual((edited["author"], edited["manual"]), ("manual", True))
                self.assertFalse(edited.get("acceptedByOwner", False))

    def test_concurrent_editors_and_corruption_of_another_section(self):
        initial = self.detail()["revision"]

        def edit_owner(text):
            try:
                self.gantt.edit({"id": self.task["id"], "revision": initial, "field": "brief",
                                 "actor": "manual", "text": text})
                return "saved"
            except storage.ConflictError:
                return "conflict"

        with ThreadPoolExecutor(2) as pool:
            self.assertCountEqual(pool.map(edit_owner, ("Первый", "Второй")), ("saved", "conflict"))
        self.edit("latest", "manual", text="Этап", date="2026-11-18")
        data = self.path.read_text().replace("\n2026-11-18\n", "\n2026-11-17\n")
        self.path.write_text(data)
        self.assertEqual(self.detail()["latest"][0]["date"], "2026-11-17")
        self.path.write_text(data.replace("<!-- /brain-gantt -->", "", 1))
        broken = self.path.read_bytes()
        with self.assertRaises(storage.ConflictError):
            self.gantt.edit({"id": self.task["id"], "revision": self.plans.task(self.task["id"])["revision"],
                             "field": "latest", "actor": "manual", "text": "Любая другая правка"})
        self.assertEqual(self.path.read_bytes(), broken)

    def test_bounded_latest_and_history_and_freeform_markdown(self):
        self.path.write_text(self.path.read_text() + "\n## Коротко\n\nМой абзац.\n\n## Прочее\nЛичное поле.\n")
        with self.assertRaises(storage.ConflictError):
            self.edit(text="Вывод", sources=[self.source])
        self.assertEqual(self.detail()["brief"]["text"], "Мой абзац.")
        changed = self.edit(actor="manual", text="")
        self.assertTrue(changed["brief"]["manual"])
        self.assertEqual(changed["brief"]["text"], "")
        for i in range(13):
            last = self.edit("latest", "manual", text=f"Шаг {i}")
        self.assertEqual((last["latestTotal"], len(last["latest"]), last["historyTotal"], len(last["history"])),
                         (13, 12, 14, 5))
        self.assertEqual(last["latest"][0]["text"], "Шаг 12")
        self.assertIsNone(last["latest"][0]["date"])
        self.assertEqual(len(self.gantt.detail(self.task["id"], all_entries=True)["latest"]), 13)
        self.assertIn("## Прочее\nЛичное поле.", self.path.read_text())

    def test_invalid_source_and_corrupt_boundaries_leave_task_untouched(self):
        raw = self.path.read_bytes()
        for bad in ([], [{**self.source, "quote": "Выдумка"}], [{**self.source, "line": 9999}],
                    [{**self.source, "line": 1}], [{**self.source, "path": self.note.as_posix()}],
                    [{**self.source, "path": "Notes/../private/secret.md"}],
                    [{**self.source, "path": "People/Кто-то.md"}], [{**self.source, "path": "Notes/bad.md"}]):
            with self.subTest(bad=bad), self.assertRaises((ValueError, FileNotFoundError)):
                self.edit(text="Вывод", sources=bad)
        fake = self.note.with_name("Symlink.md")
        fake.symlink_to(self.note)
        with self.assertRaisesRegex(ValueError, "Символическая"):
            self.edit(text="Вывод", sources=[{**self.source, "path": "Notes/Symlink.md"}])
        self.assertEqual(self.path.read_bytes(), raw)
        self.edit(text="Записали.", sources=[self.source])
        broken = self.path.read_bytes().replace(b"<!-- /brain-gantt -->", b"")
        self.path.write_bytes(broken)
        with self.assertRaises(storage.ConflictError):
            self.edit(text="Другое.", sources=[self.source])
        self.assertEqual(self.path.read_bytes(), broken)

    def test_fenced_examples_stay_byte_identical_during_row_and_history_edits(self):
        example = ("````markdown\r\n## Коротко\r\n```\r\n## Последнее\r\n"
                   "<!-- brain-gantt: not JSON -->\r\n<!-- brain-gantt-change: пример -->\r\n````\r\n").encode()
        tail = "  ~~~md\r\n## Коротко\r\n<!-- brain-gantt-change: пример без конца -->".encode()
        original = self.path.read_bytes().replace(b"\n", b"\r\n")
        self.path.write_bytes(original + example + tail)
        self.assertEqual((self.detail()["brief"]["text"], self.detail()["historyTotal"]), ("", 0))
        self.edit(actor="manual", text="Сводка владельца.")
        result = self.edit("latest", text="Проверили.", sources=[self.source])
        self.assertEqual((result["brief"]["text"], result["latest"][0]["text"], result["historyTotal"]),
                         ("Сводка владельца.", "Проверили.", 2))
        self.assertIn(example, self.path.read_bytes())
        self.assertTrue(self.path.read_bytes().endswith(tail))

    def test_native_text_on_both_sides_of_fenced_example_can_be_replaced(self):
        example = "~~~md\n## Пример\n<!-- /brain-gantt -->\n~~~\n"
        self.path.write_text(self.path.read_text() + "\n## Коротко\nДо.\n" + example + "После.\n")
        self.assertEqual(self.detail()["brief"]["text"], "До.\nПосле.")
        self.edit(actor="manual", text="Сводка владельца.")
        self.assertIn(example, self.path.read_text())
        self.assertEqual(self.detail()["brief"]["text"], "Сводка владельца.")

    def test_example_inserted_into_managed_text_survives_native_and_editor_changes(self):
        self.edit(text="Сверка.", sources=[self.source])
        example = "```md\n## Последнее\n<!-- brain-gantt-references -->\n<!-- /brain-gantt -->\n```\n"
        self.path.write_text(self.path.read_text().replace("\nСверка.\n", "\nСверка.\n" + example).rstrip("\n"))
        self.assertTrue(self.detail()["brief"]["manual"])
        result = self.edit(actor="manual", text="Уточнено.")
        self.assertIn(example, self.path.read_text())
        self.assertEqual(result["brief"]["text"], "Уточнено.")
        self.assertEqual(result["historyTotal"], 2)
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Закройте"):
            self.edit(actor="manual", text="```md\nПример")
        self.assertEqual(self.path.read_bytes(), original)

    def test_ai_date_requires_dated_quote_and_preserves_explicit_owner_date(self):
        undated = {**self.source, "quote": "сравнить образцы"}
        before = self.path.read_bytes()
        for source, day in ((undated, "2026-11-20"), (self.source, "2026-11-21")):
            with self.subTest(source=source, day=day), self.assertRaisesRegex(ValueError, "Дата события"):
                self.edit("latest", text="Событие", date=day, sources=[source])
        self.assertEqual(self.path.read_bytes(), before)
        no_date = self.edit("latest", text="Событие", sources=[undated])
        self.assertIsNone(no_date["latest"][0]["date"])
        dated = self.edit("latest", text="Датированное событие", date="2026-11-20", sources=[undated, self.source])
        self.assertEqual(dated["latest"][0]["date"], "2026-11-20")
        manual = self.edit("latest", "manual", text="Самостоятельная сверка.", date="2026-11-25")
        event = manual["latest"][0]
        correction = {"id": self.task["id"], "revision": manual["revision"], "actor": "ai", "field": "latest",
                      "entryId": event["id"], "text": "Согласованная формулировка.", "sources": [undated]}
        with self.assertRaisesRegex(ValueError, "Дата события"):
            self.gantt.edit({**correction, "date": "2026-11-26"}, accepted_manual_revision=manual["revision"])
        edited = self.gantt.edit(correction, accepted_manual_revision=manual["revision"])
        self.assertEqual(edited["latest"][0]["date"], "2026-11-25")
