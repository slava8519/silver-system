from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from second_brain import reports, storage
from second_brain.query import WorkspaceQuery, _line_ref, _state_sources
from second_brain.vault_check import check_vault
from second_brain.cycles import Cycles
from second_brain.planning import Planning
from second_brain.weekly_report import weekly_review


class Reports(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.archive = self.root / "Reports"
        self.form = {"title": "Итог пробы", "audience": "Парфюмер", "guidance": "Укажите условия.",
                     "body": "## Наблюдение"}
        self.payload = {"title": "Ольховая проба", "template_id": "free-report", "template_version": 1,
                        "body": "На блоттере различим запах ольхи.", "request_id": "alder"}
        self.calendar = {"title": "Совет дома", "template_id": "meeting-agenda", "cadence": "weekly",
                         "start": "2026-11-02", "request_id": "home"}

    def issue(self, **kwargs):
        return reports.save_issue(self.root, **{**self.payload, **kwargs})

    def series(self, **kwargs):
        return reports.create_series(self.root, **{**self.calendar, **kwargs})

    def period(self, series, start, end, **kwargs):
        return self.issue(series_id=series["id"], template_id=series["templateId"], start=start, end=end, **kwargs)

    @contextmanager
    def unchanged(self):
        def snapshot():
            return {p: p.read_bytes() for p in self.root.rglob("*.md") if not p.is_symlink()}
        before = snapshot()
        yield
        self.assertEqual(snapshot(), before)

    def test_discovery_is_read_only_and_pages_are_compact(self):
        with self.unchanged():
            page = reports.list_templates(self.root, limit=1)
            self.assertEqual((page["total"], page["nextOffset"], page["truncated"]),
                             (len(reports.BUILTIN_TEMPLATES), 1, True))
            self.assertNotIn("body", page["items"][0])
            self.assertNotIn("guidance", page["items"][0])
            template = reports.get_template(self.root, "team-digest")
            self.assertEqual(template["history"], [{"version": 1, "title": template["title"]}])
            self.assertIsNone(template["path"])
            self.assertFalse((self.root / reports.TEMPLATES).exists())
            self.assertEqual(reports.list_issues(self.root)["total"], 0)
            self.assertEqual(reports.list_series(self.root)["total"], 0)

    def test_builtin_version_is_frozen_across_program_updates(self):
        original = reports.get_template(self.root, "free-report")
        saved = self.issue(template_revision=original["revision"])
        previous = reports.get_template(self.root, "free-report", 1)
        path = self.root / previous["path"]
        self.assertEqual(original["revision"], previous["revision"])
        with patch.dict(reports.BUILTIN_TEMPLATES, {"free-report": {**self.form, "body": "## Новая структура"}}):
            self.assertEqual(reports.get_template(self.root, "free-report", 1), previous)
            self.assertFalse(reports.get_issue(self.root, saved["id"])["templateChanged"])
        path.write_text(path.read_text() + "\nРучная правка вне редактора.\n")
        reread = reports.get_issue(self.root, saved["id"])
        self.assertTrue(reread["templateChanged"])
        self.assertEqual(reread["templateRevision"], previous["revision"])
        with self.unchanged(), self.assertRaises(storage.ConflictError):
            self.issue(request_id="different", template_revision=previous["revision"])
        path.unlink()
        self.assertTrue(reports.get_issue(self.root, saved["id"])["templateChanged"])
        self.assertTrue(self.issue()["replayed"])

    def test_historical_issue_without_period_keeps_its_release_date_and_series_calendar(self):
        series = self.series()
        for token, day in (("newer", "2026-10-26"), ("older", "2026-10-19")):
            issue = self.issue(request_id=token, template_id=series["templateId"])
            path = self.root / issue["path"]
            original = path.read_text()
            path.write_text(original.replace("created_at:", 'release_date: "' + day + '"\n'
                                             + 'series_id: "' + series["id"] + '"\ncreated_at:', 1))
        rows = reports.list_issues(self.root, series_id=series["id"])["items"]
        self.assertEqual([r["releaseDate"] for r in rows], ["2026-10-26", "2026-10-19"])
        self.assertTrue(all(r["start"] is None and r["end"] is None for r in rows))
        self.assertEqual(reports.get_series(self.root, series["id"])["nextStart"], series["start"])

    def test_custom_template_history_retry_and_concurrent_changes(self):
        first = reports.save_template(self.root, **self.form, request_id="journal")
        self.assertEqual(reports.save_template(self.root, **self.form, request_id="journal"), first)
        with self.assertRaises(storage.ConflictError):
            reports.save_template(self.root, **{**self.form, "body": "Другое"}, request_id="journal")
        before = (self.root / first["path"]).read_bytes()
        def revise(number):
            try:
                return reports.save_template(self.root, **{**self.form, "body": "## Проба " + str(number)},
                                             id=first["id"], revision=first["revision"])
            except storage.ConflictError:
                return None
        with ThreadPoolExecutor(2) as pool:
            attempts = list(pool.map(revise, (1, 2)))
        current, = [item for item in attempts if item]
        self.assertEqual([v["version"] for v in current["history"]], [2, 1])
        self.assertEqual((self.root / first["path"]).read_bytes(), before)
        self.assertEqual(reports.get_template(self.root, first["id"], 1)["body"], self.form["body"])
        replay = reports.save_template(self.root, **{**self.form, "body": current["body"]}, id=first["id"],
                                       revision=first["revision"])
        self.assertEqual(replay, current)
        saved = self.issue(template_id=first["id"], template_version=1)
        self.assertEqual(saved["templateVersion"], 1)
        self.assertIn("v1.md", (self.root / saved["path"]).read_text())

    def test_original_draft_and_separate_evidence_with_rename_line_move_and_change(self):
        sentence = "Ольха раскрылась через час."
        note = storage.create(self.root, "note", "Наблюдение [1]", sentence)
        source = {"path": note.relative_to(self.root).as_posix(), "line": storage.read_record(note)["body_start"],
                  "claim": "Время раскрытия", "quote": "через час"}
        issue = self.issue(sources=[source])
        self.assertEqual(issue["body"], self.payload["body"])
        self.assertEqual(issue["sources"][0]["claim"], source["claim"])
        self.assertTrue(issue["sources"][0]["available"])
        markdown = unquote((self.root / issue["path"]).read_text())
        self.assertIn("../" + source["path"], markdown)
        self.assertIn("Время раскрытия", markdown)
        moved = note.rename(note.with_name("Новое имя.md"))
        moved.write_text(moved.read_text().replace(sentence, "\n" + sentence))
        proof = reports.read_issue_source(self.root, issue["id"], 0)
        self.assertTrue(proof["available"])
        self.assertEqual(proof["line"], source["line"] + 1)
        self.assertEqual(proof["path"], moved.relative_to(self.root).as_posix())
        self.assertTrue(any(line["text"] == sentence and line["n"] == proof["line"] for line in proof["lines"]))
        moved.write_text(moved.read_text().replace("через час", "через два часа"))
        proof = reports.read_issue_source(self.root, issue["id"], 0)
        self.assertFalse(proof["available"])
        self.assertEqual((proof["quote"], proof["lines"]), ("через час", []))

    def test_unmanaged_markdown_with_duplicates_uses_only_unambiguous_moves(self):
        folder = self.root / "Sources/Processed"
        proof = folder / "Наблюдения.md"
        proof.write_text("Контроль\nПроверено\n")
        issue = self.issue(sources=[{"path": proof.relative_to(self.root).as_posix(), "line": 2}])
        proof.write_text("Контроль\n\nПроверено\nПроверено\n")
        self.assertFalse(reports.read_issue_source(self.root, issue["id"], 0)["available"])
        proof.write_text("Контроль\n\nПроверено\n")
        self.assertEqual(reports.read_issue_source(self.root, issue["id"], 0)["line"], 3)

    def test_archived_text_and_markdown_are_bound_to_the_hash_of_the_whole_original(self):
        for ext in ("txt", "md"):
            with self.subTest(ext=ext):
                raw = ("---\nid: original-card\n---\nИтог подтверждён.\nПротокол " + ext + "\n").encode()
                digest = sha256(raw).hexdigest()
                relative = f"Sources/Originals/{digest}.{ext}"
                path = self.root / relative
                path.write_bytes(raw)
                source = {"path": relative, "line": 4, "claim": "Итог"}
                if ext == "md":
                    source["id"] = "original-card"
                issue = self.issue(sources=[source], request_id=ext)
                proof = reports.read_issue_source(self.root, issue["id"], 0)
                self.assertEqual((proof["available"], proof["id"], proof["quote"], proof["claim"]),
                                 (True, digest, "Итог подтверждён.", "Итог"))
                if ext == "md":
                    # Old reports used the ID in the original Markdown frontmatter.
                    issue_path = self.root / issue["path"]
                    issue_path.write_text(issue_path.read_text().replace('"id":"' + digest + '"', '"id":"original-card"'))
                    self.assertTrue(reports.read_issue_source(self.root, issue["id"], 0)["available"])
                # Even an unchanged cited line cannot authenticate a changed original.
                path.write_bytes(raw + b"extra\n")
                proof = reports.read_issue_source(self.root, issue["id"], 0)
                self.assertEqual((proof["available"], proof["lines"], proof["quote"]), (False, [], "Итог подтверждён."))
                with self.assertRaises(storage.ConflictError):
                    self.issue(sources=[source], request_id="changed-" + ext)
                path.write_bytes(raw)
                self.assertTrue(reports.read_issue_source(self.root, issue["id"], 0)["available"])

    def test_report_source_follows_card_across_record_folders_and_rejects_ambiguous_move(self):
        note = storage.create(self.root, "note", "Основание", "Получено три образца.")
        source = {"path": note.relative_to(self.root).as_posix(), "line": storage.read_record(note)["body_start"]}
        issue = self.issue(sources=[source])
        moved = note.rename(self.root / "Projects/Протокол.md")
        moved.write_text(moved.read_text().replace("Получено", "\nПолучено"))
        proof = reports.read_issue_source(self.root, issue["id"], 0)
        self.assertTrue(proof["available"])
        self.assertEqual((proof["path"], proof["line"]), ("Projects/Протокол.md", source["line"] + 1))
        (self.root / "Directions/Копия.md").write_bytes(moved.read_bytes())
        self.assertFalse(reports.read_issue_source(self.root, issue["id"], 0)["available"])

    def test_project_field_citations_follow_the_field_and_never_old_values_in_prose(self):
        project = storage.create(self.root, "project", "Переезд", status="Готов",
                                 due="2026-11-30", next_step="Подписать", body="Журнал\n")
        project.write_text(project.read_text().replace("status:", 'owner: "Ирина"\nstatus:'))
        record = storage.read_record(project, include_raw=True)
        fields = [{"path": s["path"], "line": s["start"], "quote": s["lines"][0]["text"], "ref": s["ref"]}
                  for _, s in _state_sources(self.root, project, record)]
        issue = self.issue(sources=fields)
        self.assertEqual([s["ref"] for s in issue["sources"]], [s["ref"] for s in fields])
        owner, = WorkspaceQuery(self.root).ask({"query": "Кто владелец проекта «Переезд»?"})["items"]
        self.assertEqual(owner["answer"], "Ирина")
        self.assertIn(owner["source"]["ref"], [s["ref"] for s in issue["sources"] if s["scope"] == "field"])
        old = self.issue(sources=fields, request_id="legacy")
        old_path = self.root / old["path"]
        old_lines = old_path.read_text().splitlines()
        for n, text in enumerate(old_lines):
            if text.startswith(reports._SOURCE):
                stored = json.loads(text[len(reports._SOURCE):-4])
                stored.pop("scope")
                stored["ref"] = _line_ref(stored["quote"])
                old_lines[n] = reports._SOURCE + storage.comment_json(stored) + " -->"
        old_path.write_text("\n".join(old_lines) + "\n")
        project = project.rename(project.with_name("Переезд базы.md"))
        raw = project.read_text().replace("---\n", "---\n\n", 1)
        project.write_text(raw)
        for edition in (issue, old):
            shown = reports.get_issue(self.root, edition["id"])["sources"]
            self.assertTrue(all(s["available"] for s in shown))
            self.assertEqual([s["line"] for s in shown], [s["line"] + 1 for s in fields])
        archive = "\n".join(s["quote"] for s in fields)
        for s in fields:
            raw = raw.replace(s["quote"], s["quote"].partition(":")[0] + ': "Изменено"')
        project.write_text(raw + archive + "\n")
        for edition in (issue, old):
            for n, source in enumerate(fields):
                shown = reports.read_issue_source(self.root, edition["id"], n)
                self.assertEqual((shown["available"], shown["quote"], shown["lines"]), (False, source["quote"], []))
        # A separately cited history line still works, even with field-like Markdown.
        historical = {"path": project.relative_to(self.root).as_posix(), "line": len(raw.splitlines()) + 1}
        prose = self.issue(sources=[historical], request_id="history-line")
        project.write_text(raw + "\n" + archive + "\n")
        shown = reports.read_issue_source(self.root, prose["id"], 0)
        self.assertEqual((shown["available"], shown["line"]), (True, historical["line"] + 1))

        # Legacy evidence did not distinguish a field from matching Markdown prose.
        # A renamed or damaged type must not let that old citation bind to archived state.
        for changed_type in ('type: "note"', 'type: "unknown"'):
            project.write_text((raw + "\n" + archive + "\n").replace('type: "project"', changed_type))
            for edition in (issue, old):
                for n in range(len(fields)):
                    self.assertFalse(reports.read_issue_source(self.root, edition["id"], n)["available"])
            self.assertTrue(reports.read_issue_source(self.root, prose["id"], 0)["available"])

    def test_fenced_report_markers_are_authored_text_and_only_actual_appendix_is_parsed(self):
        proof = {"path": "Home.md", "line": 1}
        sample = "\n".join((reports._FOOTER, "", "Шаблон выпуска — [Демо](v1.md)",
                            reports._SOURCE + "пример -->", reports._END))
        for fence in ("````md", "~~~md"):
            for close in ("", "\n" + fence.rstrip("md")):
                body = "Формат записи\n\n" + fence + "\n" + sample + close
                with self.subTest(fence=fence, closed=bool(close)):
                    saved = self.issue(body=body, sources=[proof], request_id=None)
                    shown = reports.get_issue(self.root, saved["id"])
                    self.assertEqual(shown["body"], body)
                    self.assertEqual([s["path"] for s in shown["sources"]], ["Home.md"])
                    self.assertEqual(check_vault(self.root)["status"], "ok")
        with self.assertRaisesRegex(ValueError, "служебная разметка"):
            self.issue(body="Раздел вне примера\n" + reports._END, request_id=None)

    def test_replay_after_manual_issue_edit_title_collision_and_source_loss(self):
        (self.archive / "Ольховая проба.md").write_text("Свободная заметка\n")
        proof = self.root / "Home.md"
        sources = [{"path": "Home.md", "line": 1}]
        def publish(_):
            return self.issue(sources=sources)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(publish, (1, 2)))
        self.assertEqual(sorted(r["replayed"] for r in results), [False, True])
        self.assertEqual(results[0]["id"], results[1]["id"])
        path = self.root / results[0]["path"]
        self.assertNotEqual(path.name, "Ольховая проба.md")
        path = path.rename(self.archive / "Обсудить.md")
        with path.open("a") as stream:
            stream.write("\nПроверено владельцем.\n")
        proof.unlink()
        with self.unchanged():
            issue = self.issue(sources=sources)
            self.assertIn("Проверено владельцем.", issue["body"])
            self.assertTrue(issue["replayed"])
            self.assertEqual(issue["path"], "Reports/Обсудить.md")
            with self.assertRaises(storage.ConflictError):
                self.issue(body="Другая проба.", sources=sources)

    def test_calendar_anchor_on_the_last_day_and_leap_date(self):
        cases = (("monthly", "2024-01-31", "2024-02-28", "2024-02-29", "2024-03-30"),
                 ("quarterly", "2024-01-31", "2024-04-29", "2024-04-30", "2024-07-30"),
                 ("yearly", "2024-02-29", "2025-02-27", "2025-02-28", "2026-02-27"),
                 ("biweekly", "2026-11-02", "2026-11-15", "2026-11-16", "2026-11-29"),
                 ("daily", "2026-11-02", "2026-11-02", "2026-11-03", "2026-11-03"))
        for cadence, start, end, later, next_end in cases:
            with self.subTest(cadence=cadence):
                card = self.series(cadence=cadence, start=start, request_id=cadence)
                self.assertEqual((card["nextStart"], card["nextEnd"]), (start, end))
                self.period(card, start, end)
                again = reports.get_series(self.root, card["id"])
                self.assertEqual((again["nextStart"], again["nextEnd"]), (later, next_end))

    def test_evidence_cutoff_is_independent_from_period_and_creation_time(self):
        series = self.series(start="2026-11-16")
        issue = self.period(series, "2026-11-16", "2026-11-22", as_of="2026-11-23")
        self.assertEqual((issue["start"], issue["end"], issue["asOf"]), ("2026-11-16", "2026-11-22", "2026-11-23"))
        self.assertTrue(self.period(series, "2026-11-16", "2026-11-22", as_of="2026-11-23")["replayed"])
        with self.assertRaises(storage.ConflictError):
            self.period(series, "2026-11-16", "2026-11-22", as_of="2026-11-24")
        older = self.issue(start="2026-11-16", end="2026-11-22", as_of="2026-11-15")
        undated = self.issue(request_id="undated")
        self.assertIsNone(undated["asOf"])
        self.assertNotIn("as_of:", (self.root / undated["path"]).read_text())
        # The fingerprint must still match issues written before as_of was available.
        original_hash = reports._hash([self.payload["title"], self.payload["body"], "free-report", 1, [], None, None, None])
        self.assertIn(original_hash, (self.root / undated["path"]).read_text())
        self.assertTrue(self.issue(request_id="undated")["replayed"])
        with self.assertRaises(ValueError):
            self.issue(as_of="2026-02-29")
        cutoffs = {r["id"]: r["asOf"] for r in reports.list_issues(self.root)["items"]}
        self.assertEqual(cutoffs, {issue["id"]: "2026-11-23", older["id"]: "2026-11-15", undated["id"]: None})

    def test_catchup_keeps_first_gap_and_due_filter_precedes_paging(self):
        self.series(title="А. Позже", start="2027-01-01", request_id="late")
        card = self.series(title="Б. Раньше")
        self.period(card, "2026-11-09", "2026-11-15")
        self.assertEqual(reports.get_series(self.root, card["id"])["nextStart"], "2026-11-02")
        due = reports.list_series(self.root, due_on="2026-11-08", limit=1)
        self.assertEqual([item["id"] for item in due["items"]], [card["id"]])
        self.period(card, "2026-11-02", "2026-11-08")
        self.assertEqual(reports.get_series(self.root, card["id"])["nextStart"], "2026-11-16")
        self.assertEqual(reports.list_series(self.root, due_on="2026-11-15")["total"], 0)
        self.assertEqual(reports.list_issues(self.root, series_id=card["id"])["total"], 2)
        with self.unchanged():
            with self.assertRaises(storage.ConflictError):
                self.period(card, "2026-11-02", "2026-11-08", body="Другой итог.")
            with self.assertRaises(ValueError):
                self.period(card, "2026-11-17", "2026-11-23")
            with self.assertRaises(ValueError):
                self.period(card, "2026-11-16", "2026-11-16")
            with self.assertRaises(ValueError):
                self.issue(series_id=card["id"], start="2026-11-16", end="2026-11-22")
            with self.assertRaises(storage.ConflictError):
                self.series(title="Другое название")

    def test_sources_and_templates_checked_before_writing(self):
        (self.root / "Notes" / "link.md").symlink_to(self.root.parent / "outside.md")
        with self.unchanged():
            for path in ("../outside.md", str(self.root / "Home.md"), "Notes/../Home.md", ".obsidian/demo.md",
                         "C:/demo.md", "Notes/link.md"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.issue(sources=[{"path": path, "line": 1}])
            for source in ({"path": "Home.md", "line": 0}, {"path": "Home.md", "line": 1, "quote": "Вымысел"}):
                with self.assertRaises(ValueError):
                    self.issue(sources=[source])
            for body in ("## Итог\n\n### Детали", reports.get_template(self.root, "free-report")["body"]):
                with self.assertRaises(ValueError):
                    self.issue(body=body)
            with self.assertRaises(FileNotFoundError):
                self.issue(template_version=20)
            with self.assertRaises(ValueError):
                self.issue(start="2026-13-01", end="2027-01-01")
            with self.assertRaises(ValueError):
                reports.get_template(self.root, "../../elsewhere")

    def test_legacy_weekly_issue_keeps_its_own_text_and_closure_metadata(self):
        planning = Planning(self.root)
        plan = planning.create_plan("Контрольная неделя")
        cycles = Cycles(self.root)
        cycles.close(plan["id"], cycles.preview(plan["id"])["revision"])
        legacy = weekly_review(self.root, plan["id"], save=True)
        self.issue()
        with self.unchanged():
            items = reports.list_issues(self.root)["items"]
            self.assertEqual({r["kind"] for r in items}, {"report_issue", "weekly_review"})
            issue = reports.get_issue(self.root, legacy["id"])
            self.assertEqual(issue["closedAt"], legacy["closedAt"])
            self.assertEqual(issue["path"], legacy["path"])
            self.assertIn("События с", issue["body"])
            self.assertIsNone(issue["templateVersion"])

    def test_title_search_pages_both_report_kinds_and_intersects_series(self):
        planning = Planning(self.root)
        plan = planning.create_plan("Неделя переезда")
        cycles = Cycles(self.root)
        cycles.close(plan["id"], cycles.preview(plan["id"])["revision"])
        (self.root / "Settings/Formats/Weekly.md").write_text("---\ntitle: Обзор переезда\n---\n")
        weekly = weekly_review(self.root, plan["id"], save=True)
        series = self.series()
        issue = self.period(series, "2026-11-02", "2026-11-08", title="План переезда семьи")
        self.issue(title="Осмотр квартиры")
        args = {"match": " ПЕРЕЕЗД ", "limit": 1}
        first = reports.list_issues(self.root, **args)
        second = reports.list_issues(self.root, offset=first["nextOffset"], **args)
        self.assertEqual((first["total"], second["total"], second["nextOffset"]), (2, 2, None))
        self.assertEqual({first["items"][0]["id"], second["items"][0]["id"]}, {weekly["id"], issue["id"]})
        scoped = reports.list_issues(self.root, match="переезд", series_id=series["id"])
        self.assertEqual([row["id"] for row in scoped["items"]], [issue["id"]])
        self.assertEqual(reports.list_issues(self.root, match="Парфюмерия")["total"], 0)
