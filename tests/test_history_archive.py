from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from second_brain import history_archive as archive, project_updates, storage


class HistoryArchiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = storage.initialize(Path(directory.name))
        self.project = storage.create(self.root, "project", "Тест истории", "Рабочий текст.")
        self.id = storage.read_record(self.project)["id"]
        self.day = date(2026, 11, 28)

    def rotate(self, **kwargs):
        return archive.rotate(self.root, self.id, as_of=self.day, **kwargs)

    def add(self, text):
        with self.project.open("ab") as handle:
            handle.write(text.encode())

    def test_numeric_comparison_does_not_turn_later_identifier_into_an_html_anchor(self):
        self.assertTrue(archive._portable(self.root, "Время `<10` минут.\nЗапись `id=3`.", {}))
        self.assertFalse(archive._portable(self.root, '<span\n id="somewhere">Текст</span>', {}))
        self.assertFalse(archive._portable(self.root, '<span title="a < b" id="same">Текст</span>', {}))

    def test_quarters_protected_history_unknown_dates_fences_and_byte_roundtrip(self):
        self.add("""
## Журнал
- 2024-06-14 — первая запись
  перенос строки внутри пункта
- 30.09.26 — ровно 59 дней назад
- 2026-09-29 — ровно 60 дней назад
- 2026-09-28 — старше 60 дней
- Дату нужно подтвердить
- 2026-10-01 — третья
- 2026-10-02 — четвёртая
- 2026-11-05 — пятая
## История статусов
### 2022-12-31 — срез
- Состояние с деталями.
#### Подробности
- Небольшой риск.
### 2026-05-30 — ещё один срез
### Снимок без даты
- 2021-01-01 — пункт недатированного снимка.
## Код
```
## Журнал
- 2020-01-01 — пример
```
""")
        self.project.write_bytes(self.project.read_bytes().replace(b"\n", b"\r\n"))
        original = self.project.read_bytes()
        result = self.rotate()
        # 5 most recent bullets (plus an undated item) are protected;
        # in the status section both dated snapshots are protected.
        self.assertEqual((result["moved"], result["applied"]), (2, False))
        self.assertEqual(original, self.project.read_bytes())
        self.assertFalse((self.root/"Archive/ProjectHistory").exists())
        self.assertEqual(self.rotate(apply=result["revision"])["moved"], 2)
        updated = self.project.read_bytes()
        self.assertNotIn("первая запись".encode(), updated)
        self.assertIn("ровно 60 дней".encode(), updated)
        self.assertIn("Дату нужно подтвердить".encode(), updated)
        self.assertNotIn(b"\n", updated.replace(b"\r\n", b""))
        expected = original.decode().splitlines(keepends=True)
        for item in result["archives"]:
            data = (self.root/item["path"]).read_bytes()
            meta = json.loads(data.decode().splitlines()[0][len(archive.ORIGIN):-4])
            lines = archive.original_lines(data.decode().splitlines(keepends=True), meta)
            for span in meta["ranges"]:
                first, n = span["archiveStart"]-1, span["end"]-span["start"]+1
                self.assertEqual(lines[first:first+n], expected[span["start"]-1:span["end"]])
            self.assertEqual(item["entries"], 1)
        self.assertTrue(self.rotate(apply=result["revision"])["alreadyApplied"])
        self.assertEqual(self.rotate()["moved"], 0)

    def test_nested_status_snapshot_and_undated_text_are_owned_correctly(self):
        self.add("""
## История статусов
### 2025-03-31 — старый
Деталь 1
#### Риски
- Пункт раздела
```md
### Недействующий заголовок
```
### Следующий без даты
- 2020-02-02 — пояснение без собственной карточки
## Журнал
- 2022-12-01 — перенести по дате

Отдельный вручную написанный абзац.
""")
        result = self.rotate(min_kept=0)
        self.rotate(min_kept=0, apply=result["revision"])
        self.assertEqual(result["moved"], 2)
        self.assertIn("Отдельный вручную", self.project.read_text())
        self.assertIn("Следующий без даты", self.project.read_text())
        self.assertIn("2020-02-02", self.project.read_text())
        live = self.project.read_text()
        self.assertIn("### 2025-03-31 — старый\n[Открыть запись в архиве]", live)
        self.assertIn("#### Риски\n[Открыть запись в архиве]", live)
        self.assertNotIn("Деталь 1", live)
        self.assertNotIn("### Недействующий заголовок", live)
        self.assertEqual(self.rotate(min_kept=0)["skippedLinks"], 0)
        content = (self.root/next(i["path"] for i in result["archives"] if i["quarter"] == "2025-Q1")).read_text()
        self.assertIn("#### Риски\n", content)
        self.assertIn("- Пункт раздела", content)
        self.assertIn("### Недействующий заголовок", content)

    def test_old_native_blocks_rebase_footer_and_restore_original_citation_bytes(self):
        note = storage.create(self.root, "note", "Основание", "2025-01-17 — работу приняли.")
        quote = note.read_text().splitlines()[-1]
        line = len(note.read_text().splitlines())
        source = {"path": note.relative_to(self.root).as_posix(), "line": line, "quote": quote}
        project_updates.write_update(self.root, self.id, field="event", text="Результат приняли.",
                                     date="2025-01-17", sources=[source],
                                     revision=storage.read_record(self.project)["revision"])
        original = self.project.read_bytes()
        result = self.rotate(min_kept=0)
        self.assertEqual((result["moved"], result["skippedLinks"]), (1, 0))
        self.rotate(min_kept=0, apply=result["revision"])
        section = self.root/result["archives"][0]["path"]
        output = section.read_text()
        self.assertIn(storage.markdown_link(f"Источник, строка {line}", note, section.parent), output)
        raw = section.read_bytes().decode().splitlines(keepends=True)
        meta = json.loads(raw[0][len(archive.ORIGIN):].split(" -->")[0])
        span, = meta["ranges"]
        self.assertEqual(span["section"], "Последнее")
        self.assertEqual(len(meta["originalLines"]), 2)
        roll = archive.original_lines(raw, meta)
        archive_body = "".join(roll[span["archiveStart"]-1:span["archiveStart"] + span["end"]-span["start"]])
        full = original.decode().splitlines(keepends=True)
        self.assertEqual(archive_body, "".join(full[span["start"]-1:span["end"]]))
        self.assertEqual(hashlib.sha256(archive_body.encode()).hexdigest(), span["sha256"])
        row, = project_updates.projection({"body":output,"body_start":1})[0]
        self.assertEqual((row["text"], row["author"], row["date"], row["sources"][0]["line"]),
                         ("Результат приняли.", "ai", "2025-01-17", line))

    def test_ambiguous_markdown_links_stay_in_live_project(self):
        note = storage.create(self.root, "note", "Подтверждение", "Факт")
        self.add("""
## Журнал
- 2025-01-01 — [[Notes/Подтверждение|известная заметка]]
- 2025-01-02 — [локальная ссылка](../Notes/Подтверждение.md)
- 2025-01-03 — [[#Раздел]]
- 2025-01-04 — [[Notes/Исчезла]]
- 2025-01-05 — [ссылка][справка]
- 2025-01-06 — веб-источник [справка](https://example.org/guide)
- 2025-01-07 — запись с постоянной ссылкой ^initial-decision
- 2025-01-08 — якорь <a id="критерий"></a>
""")
        result = self.rotate(min_kept=0)
        self.assertEqual((result["moved"], result["skippedLinks"]), (2, 6))
        self.rotate(min_kept=0, apply=result["revision"])
        self.assertIn("локальная ссылка", self.project.read_text())
        self.assertTrue(note.exists())

    def test_conflicting_card_and_modified_archive_do_not_remove_working_bytes(self):
        self.add("\n## Журнал\n- 2021-01-01 — запись\n")
        result = self.rotate(min_kept=0)
        self.add("\nИсправление владельца.\n")
        with self.assertRaises(storage.ConflictError):
            self.rotate(min_kept=0, apply=result["revision"])
        result = self.rotate(min_kept=0)
        before = self.project.read_bytes()
        archive_path = self.root/result["archives"][0]["path"]
        archive_path.parent.mkdir(parents=True)
        archive_path.write_text("Ручная версия")
        with self.assertRaises(storage.ConflictError):
            self.rotate(min_kept=0, apply=result["revision"])
        self.assertEqual(self.project.read_bytes(), before)
        self.assertEqual(archive_path.read_text(), "Ручная версия")

    def test_interruption_after_archive_write_can_resume_then_detect_archive_tampering(self):
        self.add("\n## Журнал\n- 2021-01-01 — запись\n")
        before = self.project.read_bytes()
        result = self.rotate(min_kept=0)
        real_replace = storage.replace_bytes

        def fail_project(path, old, new):
            if path == self.project:
                raise OSError("interrupted")
            real_replace(path, old, new)

        with patch.object(storage, "replace_bytes", side_effect=fail_project), self.assertRaises(OSError):
            self.rotate(min_kept=0, apply=result["revision"])
        self.assertEqual(before, self.project.read_bytes())
        self.assertTrue(self.rotate(min_kept=0, apply=result["revision"])["applied"])
        path = self.root/result["archives"][0]["path"]
        path.write_text(path.read_text() + "\nВнешняя правка\n")
        with self.assertRaises(storage.ConflictError):
            self.rotate(min_kept=0, apply=result["revision"])

    def test_missing_pair_from_committed_batch_cannot_silently_drop_a_quarter(self):
        self.add("\n## Журнал\n- 2020-01-01 — решение\n- 2020-07-01 — согласование\n")
        plan = self.rotate(min_kept=0)
        self.assertEqual(len(plan["archives"]), 2)
        self.rotate(min_kept=0, apply=plan["revision"])
        lost = plan["archives"][0]["path"]
        self.project.write_text("".join(line for line in self.project.read_text().splitlines(keepends=True)
                                        if lost not in line))
        damaged = self.project.read_bytes()
        with self.assertRaisesRegex(storage.ConflictError, "часть ссылок"):
            self.rotate(min_kept=0)
        with self.assertRaisesRegex(storage.ConflictError, "часть ссылок"):
            project_updates.read_updates(self.root, self.id)
        self.assertEqual(self.project.read_bytes(), damaged)

    def test_concurrent_apply_and_later_quarter_preserve_unchanged_line_maps(self):
        self.add("\n## Журнал\n- 2020-01-01 — первое\n- 2020-07-01 — второе\n"
                 "## Данные\nПовторяется.\n## Архив истории\n## Заметки\nПовторяется.\n")
        for round_number in range(2):
            if round_number:
                # A newer operation inserts its pointers before those of the earlier one.
                self.project.write_text(self.project.read_text().replace("## Журнал\n",
                                        "## Журнал\n- 2021-04-01 — дополнение\n"))
            before = self.project.read_bytes().decode().splitlines(keepends=True)
            plan = self.rotate(min_kept=0)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [pool.submit(self.rotate, min_kept=0, apply=plan["revision"]) for _ in range(2)]
            self.assertEqual(sum(bool(r.result().get("alreadyApplied")) for r in results), 1)
            current = self.project.read_bytes().decode().splitlines(keepends=True)
            maps = []
            for part in plan["archives"]:
                history = archive.archive_reader.read_path(self.root, part["path"])
                if "retained" in history["archiveOrigin"]:
                    origin = history["archiveOrigin"]
                    self.assertEqual(origin["beforeContent"], archive.archive_reader.content_revision("".join(before)))
                    self.assertEqual(origin["afterContent"], archive.archive_reader.content_revision("".join(current)))
                    self.assertNotEqual(origin["afterContent"],
                                        archive.archive_reader.content_revision("Другой абзац\n" + "".join(current)))
                    maps.append(origin["retained"])
            self.assertEqual(len(maps), 1)
            covered = set()
            for span in maps[0]:
                start, end, at = (span[k] for k in ("start", "end", "currentStart"))
                expected = before[start-1:end]
                self.assertEqual(current[at-1:at+end-start], expected)
                self.assertEqual(hashlib.sha256("".join(expected).encode()).hexdigest(), span["sha256"])
                covered.update(range(start, end+1))
            self.assertTrue(all(n in covered for n, line in enumerate(before, 1) if line == "Повторяется.\n"))

    def test_existing_archive_section_without_final_newline_keeps_first_link_on_own_line(self):
        self.add("\n## Журнал\n- 2020-01-01 — решение\n## Архив истории")
        plan = self.rotate(min_kept=0)
        self.rotate(min_kept=0, apply=plan["revision"])
        self.assertIn("## Архив истории\n[2020-Q1](", self.project.read_text())

    def test_separately_anchored_list_keeps_all_its_items(self):
        protected = "- 2020-01-01 — первый пункт\n\n- 2020-04-01 — второй пункт\n\n^two-choices"
        self.add("\n## Журнал\n" + protected + "\n\nСледующий список.\n- 2019-01-01 — можно перенести\n")
        storage.create(self.root, "note", "Выбор", "[[Projects/Тест истории#^two-choices]]")
        plan = self.rotate(min_kept=0)
        self.assertEqual((plan["moved"], plan["skippedLinks"]), (1, 2))
        self.rotate(min_kept=0, apply=plan["revision"])
        self.assertIn(protected, self.project.read_text())

    def test_atx_and_setext_boundaries_keep_current_text_and_nested_bookmarks(self):
        for separator in ["   ## Живой статус ###", "Живой статус\n   -----", "Живой статус\n===="]:
            with self.subTest(separator=separator):
                body = ("История статусов\n----\n"
                        "   ### 2020-04-01 — снимок ###\nИсторическое значение.\n"
                        "  #### Результат C#\n- Старое подтверждение.\n"
                        "\n" + separator + "\nАктуальный текст.\n- 2021-01-03 — текущая задача.\n")
                p = storage.create(self.root, "project", "Состояние", body)
                self.id = storage.read_record(p)["id"]
                plan = self.rotate(min_kept=0)
                self.assertEqual(plan["moved"], 1)
                self.rotate(min_kept=0, apply=plan["revision"])
                archived = (self.root/plan["archives"][0]["path"]).read_text()
                current = p.read_text()
                self.assertIn(separator + "\nАктуальный текст.", current)
                self.assertNotIn("Актуальный текст", archived)
                self.assertNotIn("текущая задача", archived)
                self.assertIn("   ### 2020-04-01 — снимок ###\n[Открыть", current)
                self.assertIn("  #### Результат C#\n[Открыть", current)
                self.assertIn("#^sb-history-", current)

    def test_native_blocks_outside_structural_section_stay_in_place(self):
        base = self.project.read_bytes()
        for sep in ["  ## Другое", "Другое\n---"]:
            with self.subTest(sep=sep):
                self.project.write_bytes(base)
                project_updates.write_update(self.root, self.id, field="event", text="Завершено.",
                                             actor="manual", date="2021-02-26",
                                             revision=storage.read_record(self.project)["revision"])
                raw = self.project.read_text().replace("## Последнее\n", "## Последнее\n\n"+sep+"\n")
                self.project.write_text(raw)
                self.assertEqual(self.rotate(min_kept=0)["moved"], 0)
                self.assertEqual(raw, self.project.read_text())

    def test_non_path_project_id_remains_a_single_directory_and_can_be_read(self):
        self.project.write_text(self.project.read_text().replace(self.id, "a/b:в"))
        self.id = "a/b:в"
        self.add("\n## Журнал\n- 2022-10-01 — запись\n")
        plan = self.rotate(min_kept=0)
        self.rotate(min_kept=0, apply=plan["revision"])
        part = Path(plan["archives"][0]["path"])
        self.assertEqual(part.parent.as_posix(), "Archive/ProjectHistory/a%2Fb%3A%D0%B2")
        record = archive.archive_reader.read_path(self.root, part)
        self.assertEqual(record["project_id"], self.id)
        self.assertEqual(archive.archive_reader.by_id(self.root, record["id"])["raw"], record["raw"])

    def test_duplicate_formatted_headings_each_link_to_their_unchanged_archive_line(self):
        from urllib.parse import unquote
        import re
        title = "### 2020-01-01 — **Формула**"
        nested = "#### Показатель `раз`"
        self.add(f"\n## История статусов\n{title}\n{nested}\nПервая величина.\n"
                 f"{title}\n{nested}\nВторая величина.\n")
        before = self.project.read_bytes().decode().splitlines()
        numbered = [(i, text) for i, text in enumerate(before, 1) if text in (title, nested)]
        plan = self.rotate(min_kept=0)
        self.rotate(min_kept=0, apply=plan["revision"])
        current = self.project.read_text()
        archived = self.root / plan["archives"][0]["path"]
        raw = archived.read_text().splitlines()
        for number, title_line in numbered:
            anchor = "^sb-history-" + str(number)
            target = re.search(r"\(([^\n()]+)#" + re.escape(anchor) + r"\)", current)
            self.assertIsNotNone(target)
            self.assertEqual((self.project.parent / unquote(target[1])).resolve(), archived)
            at = raw.index(anchor)
            self.assertEqual(raw[at-2], title_line)
            origin = json.loads(raw[0][len(archive.ORIGIN):-4])
            span = next(item for item in origin["ranges"] if item["start"] <= number <= item["end"])
            self.assertEqual(raw[span["archiveStart"] + number - span["start"] - 1], title_line)
        self.assertEqual(self.rotate(min_kept=0)["moved"], 0)

    def test_missing_and_ambiguous_wiki_fragments_in_existing_markdown_stay_in_project(self):
        storage.create(self.root, "note", "Сценарий",
                       "# Сценарий\n## Этап\nПодтверждено ^result\n"
                       "### Шаг\nИтог 1\n### Шаг\nИтог 2\n"
                       "```md\n## Пример\n^hidden\n```\n")
        self.add("""
## Журнал
- 2022-01-01 — [[Notes/Сценарий#Не существует]]
- 2022-01-02 — [[Notes/Сценарий#^missing]]
- 2022-01-03 — [[Notes/Сценарий#^hidden]]
- 2022-01-04 — [[Notes/Сценарий#Шаг]]
- 2022-01-05 — [[Notes/Сценарий#Этап|К этапу]]
- 2022-01-06 — [[Notes/Сценарий#^result]]
""")
        plan = self.rotate(min_kept=0)
        self.assertEqual((plan["moved"], plan["skippedLinks"]), (2, 4))
        self.rotate(min_kept=0, apply=plan["revision"])
        for title in ("Не существует", "^missing", "^hidden", "Шаг"):
            self.assertIn("Сценарий#"+title, self.project.read_text())
