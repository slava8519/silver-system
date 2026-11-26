from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from second_brain import reports, storage
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
