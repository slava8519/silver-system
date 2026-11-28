from pathlib import Path
import tempfile
import unittest

from second_brain import archive_reader, evidence, history_archive, project_updates, query, report_sources, search, storage
from second_brain.vault_check import check_vault


class ArchivedProjectLinksTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name))
        self.project = storage.create(self.root, "project", "Сад на балконе",
                                      "## История статусов\n### 2024-02-12 — до пересадки\nРаньше выбрали кашпо.\n")
        self.record = storage.read_record(self.project)
        self.relative = self.project.relative_to(self.root).as_posix()

    def move(self):
        options = dict(as_of="2026-11-28", min_kept=0)
        plan = history_archive.rotate(self.root, self.record["id"], **options)
        self.assertGreater(plan["moved"], 0)
        history_archive.rotate(self.root, self.record["id"], **options, apply=plan["revision"])
        return plan

    def test_old_citation_report_and_historical_search_follow_original_sentence(self):
        line = self.project.read_text().splitlines().index("Раньше выбрали кашпо.") + 1
        source, _ = evidence.verify_source_line(self.root, {"path": self.relative, "line": line, "quote": "кашпо"})
        report = report_sources.verify(self.root, [source])[0]
        self.move()
        resolved = evidence.resolve_source(self.root, source)
        self.assertTrue(resolved["path"].startswith("Archive/ProjectHistory/"))
        self.assertEqual(resolved["ref"], source["ref"])
        self.assertEqual(resolved["quote"], "кашпо")
        row = report_sources.resolve(self.root, report, "/report-source/example", context=True)
        self.assertTrue(row["available"])
        self.assertEqual((row["path"], row["line"]), (resolved["path"], resolved["line"]))
        _, bookmark = query.WorkspaceQuery(self.root).open_citation(source)
        self.assertEqual((bookmark["path"], bookmark["start"]), (row["path"], row["line"]))
        self.assertEqual(search.fragment(self.root, row["path"], row["line"], row["line"]), "Раньше выбрали кашпо.")
        self.assertEqual(search.search(self.root, "кашпо"), [])
        found = search.search(self.root, "кашпо", history=True)
        self.assertIn(resolved["path"], [item["path"] for item in found])
        archived = self.root / row["path"]
        archived.write_bytes(archived.read_bytes() + b"\n")
        with self.assertRaises(storage.ConflictError):
            archive_reader.read_path(self.root, archived)
        self.assertFalse(report_sources.resolve(self.root, report, "test")["available"])
        self.assertTrue(any(i["code"] == "project.archive" for i in check_vault(self.root)["issues"]["items"]))

    def test_repeating_an_update_with_its_relocated_source_is_a_noop(self):
        other = storage.create(self.root, "project", "Цветник")
        other_id = storage.read_record(other)["id"]
        n = self.project.read_text().splitlines().index("Раньше выбрали кашпо.") + 1
        reference = {"path": self.relative, "line": n, "quote": "Раньше выбрали кашпо."}
        def update(source):
            return project_updates.write_update(self.root, other_id, field="event", text="Выбрали кашпо.",
                                                revision=storage.read_record(other)["revision"], sources=[source])
        entry = update(reference)["events"][0]
        self.move()
        resolved = evidence.resolve_source(self.root, entry["sources"][0])
        before = other.read_bytes()
        self.assertEqual(update(resolved)["total"], 1)
        self.assertEqual(other.read_bytes(), before)

    def test_native_events_keep_history_paging_and_are_idempotent_after_a_move(self):
        latest = self.record
        for day, text in [("2024-01-01", "Решили проверить дренаж"), ("2026-11-26", "Принесли воду")]:
            project_updates.write_update(self.root, latest["id"], field="event", text=text,
                                         actor="manual", revision=latest["revision"], date=day)
            latest = storage.read_record(self.project)
        self.move()
        current = project_updates.read_updates(self.root, latest["id"], include_archived=False)
        self.assertEqual([e["text"] for e in current["events"]], ["Принесли воду"])
        page = project_updates.read_updates(self.root, latest["id"], 1, 1)
        event = page["events"][0]
        self.assertEqual((page["total"], event["text"], event["author"]), (2, "Решили проверить дренаж", "manual"))
        self.assertTrue(archive_reader.archive_path(event["path"]))
        self_source = {"path": event["path"], "line": event["line"], "quote": event["text"]}
        with self.assertRaisesRegex(ValueError, "независимое основание"):
            project_updates.write_update(self.root, latest["id"], field="brief", text="Новая формулировка.",
                                         revision=current["revision"], sources=[self_source])
        before = self.project.read_bytes()
        project_updates.write_update(self.root, latest["id"], field="event", text=event["text"],
                                     actor="manual", revision=current["revision"], date=event["date"])
        self.assertEqual(before, self.project.read_bytes())

    def test_identical_sentences_in_different_snapshots_keep_their_old_sources(self):
        repeated = "Показатели стабильны."
        self.project.write_text(self.project.read_text() +
                                f"{repeated}\nДополнение к старому статусу.\n\n## Рабочие замеры\n"
                                f"Утром\n{repeated}\nДнём\n{repeated}\n")
        def sources():
            return [evidence.verify_source_line(self.root, {"path": self.relative, "line": n})[0]
                    for n, text in enumerate(self.project.read_text().splitlines(), 1) if text == repeated]
        old = sources()
        reports = report_sources.verify(self.root, old)
        self.move()
        live = sources()
        self.assertEqual(len(live), 2)
        for before, report, after in zip(old, reports, [None, *live]):
            rec, fragment = query.WorkspaceQuery(self.root).open_citation(before)
            resolved = evidence.resolve_source(self.root, before)
            saved = report_sources.resolve(self.root, report, "/report-source/example", context=True)
            self.assertTrue(saved["available"])
            self.assertEqual((resolved["path"], resolved["line"]), (fragment["path"], fragment["start"]))
            self.assertEqual((saved["path"], saved["line"]), (fragment["path"], fragment["start"]))
            verified, _ = evidence.verify_source_line(self.root, before)
            rerecorded = report_sources.verify(self.root, [report])[0]
            self.assertEqual((verified["path"], verified["line"]), (fragment["path"], fragment["start"]))
            self.assertEqual((rerecorded["path"], rerecorded["line"]), (fragment["path"], fragment["start"]))
            if after:
                self.assertEqual((rec["id"], fragment["start"]), (after["id"], after["line"]))
                self.assertIn("archivePlan=", fragment["href"])
            else:
                self.assertTrue(archive_reader.archive_path(fragment["path"]))
        for current in live:
            rec, fragment = query.WorkspaceQuery(self.root).open_citation(current)
            self.assertEqual((rec["id"], fragment["start"]), (current["id"], current["line"]))

    def test_report_reaches_an_archived_line_after_the_owner_moves_folders(self):
        number = self.project.read_text().splitlines().index("Раньше выбрали кашпо.") + 1
        saved = report_sources.verify(self.root, [{"path": self.relative, "line": number}])[0]
        self.move()
        self.project.rename(self.root / "Notes" / "Новый адрес.md")
        resolved = report_sources.resolve(self.root, saved, "/report-source/test")
        self.assertTrue(resolved["available"])
        self.assertTrue(archive_reader.archive_path(resolved["path"]))

    def test_editing_a_retained_sentence_never_redirects_it_to_an_older_duplicate(self):
        repeated = "Раньше выбрали кашпо."
        self.project.write_text(self.project.read_text() + "\n## Работа сейчас\nКонтекст владельца.\n" + repeated + "\n")
        number = len(self.project.read_text().splitlines())
        saved, _ = evidence.verify_source_line(self.root, {"path": self.relative, "line": number})
        report = report_sources.verify(self.root, [saved])[0]
        self.move()
        self.project.write_text(self.project.read_text().replace(repeated, "Заменили текущее кашпо."))
        with self.assertRaises(storage.ConflictError):
            query.WorkspaceQuery(self.root).open_citation(saved)
        self.assertFalse(report_sources.resolve(self.root, report, "/report-source/test")["available"])

    def test_two_rotations_cannot_bind_a_link_to_a_duplicate_inserted_between_them(self):
        self.project.write_text(self.project.read_text() + "\n### 2025-01-01 — второй снимок\nВторой архив.\n"
                                "\n## Работа сейчас\nКонтекст владельца.\nПовтор\n")
        original = self.project.read_text().splitlines().index("Повтор") + 1
        saved, _ = evidence.verify_source_line(self.root, {"path": self.relative, "line": original})
        options = dict(as_of="2024-12-01", min_kept=0)
        first = history_archive.rotate(self.root, self.record["id"], **options)
        self.assertEqual(first["moved"], 1)
        history_archive.rotate(self.root, self.record["id"], **options, apply=first["revision"])
        lines = self.project.read_text().splitlines(keepends=True)
        number = lines.index("Повтор\n")
        at = lines.index("Второй архив.\n") + 1
        lines[at:at] = ["Вставка.\n"] * (number - at) + ["Повтор\n"]
        self.project.write_text("".join(lines))
        self.move()
        with self.assertRaises(storage.ConflictError):
            query.WorkspaceQuery(self.root).open_citation(saved)

    def test_ordinary_imported_id_can_start_with_history(self):
        self.project.write_text(self.project.read_text().replace(self.record["id"], "history:custom"))
        number = self.project.read_text().splitlines().index("Раньше выбрали кашпо.") + 1
        source, _ = evidence.verify_source_line(self.root, {"path": self.relative, "line": number})
        record, fragment = query.WorkspaceQuery(self.root).open_citation(source)
        self.assertEqual((record["id"], fragment["start"]), ("history:custom", number))

    def test_missing_archive_pointer_is_reported_at_references_only(self):
        archive = self.root / self.move()["archives"][0]["path"]
        staged = archive.with_name("2023-Q1-" + "a"*64 + ".md")
        staged.write_bytes(archive.read_bytes())
        note = self.root / "Notes" / "Ссылка.md"
        example = storage.markdown_link("Пример", staged, note.parent)
        link = storage.markdown_link("Решение", archive, note.parent)
        note.write_text(f"# Основания\n\n```md\n{example}\n```\n`{example}`\n{link}\n")
        self.assertEqual(check_vault(self.root)["status"], "ok")

        lines = self.project.read_text().splitlines(keepends=True)
        self.project.write_text("".join(line for line in lines if not line.startswith(history_archive.POINTER)))
        with self.assertRaises(FileNotFoundError):
            archive_reader.read_path(self.root, archive)
        result = check_vault(self.root)
        self.assertEqual(result["status"], "issues")
        refs = {issue["path"]: issue for issue in result["issues"]["items"]}
        self.assertEqual(set(refs), {self.relative, "Notes/Ссылка.md"})
        self.assertEqual(refs["Notes/Ссылка.md"]["line"], 7)
        for path, issue in refs.items():
            self.assertEqual(issue["code"], "project.archive")
            self.assertIn(archive.name, (self.root / path).read_text().splitlines()[issue["line"]-1])
