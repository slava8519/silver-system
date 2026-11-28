from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from second_brain import storage
from second_brain.__main__ import command_line, run
from second_brain.batch import Batch
from second_brain.cycles import Cycles
from second_brain.gantt import Gantt
from second_brain.plan_dates import Deadlines
from second_brain.planning import Planning
from second_brain.weekly_report import _finding, published_issues


class WeeklyReports(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Vault"
        storage.initialize(self.root)
        self.planning = Planning(self.root)
        self.cycles = Cycles(self.root)
        self.parser = command_line()
        self.task = self.planning.create_task("Посмотреть [пробы]", due="2026-11-20")
        self.first = self.closed("Дела", [self.task], 16)
        self.format = self.root / "Settings/Formats/Weekly.md"

    def closed(self, title, tasks, day):
        plan = self.planning.create_plan(title)
        for item in tasks:
            self.planning.add_task(plan["id"], item["id"])
        stamp = datetime(2026, 11, day, 10, tzinfo=timezone.utc)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return stamp.astimezone(tz)

        with patch("second_brain.cycles.datetime", Clock):
            self.cycles.close(plan["id"], self.cycles.preview(plan["id"])["revision"])
        return plan["id"]

    def cli(self, plan, *args):
        cfg = self.parser.parse_args(["--vault", str(self.root), "weekly-review", plan, *args])
        return run(cfg)

    def test_preview_read_only_save_full_report_and_follow_links(self):
        self.assertEqual([], list((self.root / "Reports").iterdir()))
        tasks = [self.task, self.planning.create_task("Без срока")]
        source_file = self.root / tasks[0]["path"]
        original = source_file.read_bytes()
        plan = self.closed("Ещё один план", tasks, 23)
        preview = self.cli(plan, "--limit", "1", "--gantt-url", "http://localhost:9876/")
        self.assertEqual((preview["total"], len(preview["items"]), preview["truncated"]), (2, 1, True))
        self.assertFalse((self.root / preview["path"]).exists())
        self.assertEqual(self.cli(plan, "--offset", "1")["items"][0]["taskId"], tasks[1]["id"])
        saved = self.cli(plan, "--limit", "1", "--gantt-url", "http://localhost:9876/", "--save")
        path = self.root / saved["path"]
        body = path.read_text()
        for t in tasks:
            self.assertIn(f"http://localhost:9876/#plan/{plan}?task={t['id']}", body)
        for link in re.findall(r"\]\(([^)]+)\)", body):
            if not link.startswith("http://"):
                local = (path.parent / unquote(link.split("#")[0])).resolve()
                self.assertTrue(local.is_file())
        self.assertEqual(source_file.read_bytes(), original)
        self.assertEqual(published_issues(self.root, plan)[0]["path"], saved["path"])
        for task in tasks:
            (self.root / task["path"]).unlink()
        warnings = self.cli(plan, "--warnings", "--offset", "1", "--limit", "1", "--gantt-url", "http://localhost/")
        self.assertEqual((warnings["total"], len(warnings["items"])), (2, 1))
        self.assertEqual(warnings["items"][0]["sources"][0]["href"], "http://localhost/#plan/" + plan +
                         "?task=" + tasks[1]["id"])
        self.assertEqual(len(self.cli(plan, "--limit", "1")["warnings"]), 1)

    def test_event_from_archived_inbox_original_in_preview_and_saved_report(self):
        quoted = "2026-11-20. Проверка образца завершена."
        incoming = self.root / "Inbox/Протокол.md"
        incoming.write_text("# Протокол\n" + quoted + "\n")
        inbox = Batch(self.root)
        item, = inbox.pending()
        archived = inbox.apply({"path": item["path"], "revision": item["revision"], "actions": [
            {"kind": "triage", "line": 2, "quote": quoted, "reason": "Уточнить получателя."}]})["source"]
        with patch("second_brain.gantt.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
            Gantt(self.root).edit({"id": self.task["id"], "revision": self.task["revision"], "actor": "ai",
                                   "field": "latest", "date": "2026-11-20", "text": "Образец проверен.",
                                   "sources": [{"path": archived["path"], "line": 2, "quote": quoted}]})
        plan = self.closed("Неделя проверки", [self.task], 23)
        preview = self.cli(plan)
        self.assertEqual(preview["warnings"], [])
        event, = [fact for fact in preview["items"] if fact["kind"] == "event"]
        source, = event["sources"]
        self.assertEqual((event["text"], source["path"], source["line"], source["quote"]),
                         ("Образец проверен.", archived["path"], 2, quoted))
        link = f"/original/{archived['id']}?start=2"
        self.assertEqual(source["href"], link)
        saved = self.cli(plan, "--gantt-url", "http://localhost:9876", "--save")
        self.assertIn("http://localhost:9876" + link, (self.root / saved["path"]).read_text())

        (self.root / archived["path"]).write_text("Изменённый оригинал")
        changed = self.cli(plan)
        self.assertEqual([fact["kind"] for fact in changed["items"]], ["snapshot"])
        self.assertEqual(changed["warningTotal"], 1)

    def test_concurrent_publish_replay_owner_edit_rename_and_custom_format(self):
        self.format.write_text("---\ntitle: Коротко за неделю\ngroup_by: kind\n---\n")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.cli(self.first, "--save"), range(2)))
        self.assertEqual(sorted(r["replayed"] for r in results), [False, True])
        path = self.root / results[0]["path"]
        self.assertIn("## На момент закрытия\n", path.read_text())
        self.assertIn("Коротко за неделю", path.name)
        path = path.rename(path.with_name("Моя неделя.md"))
        with path.open("a") as stream:
            stream.write("\nКомментарий автора.\n")
        original = path.read_bytes()
        self.format.write_text("---\ntitle: Другой заголовок\ngroup_by: direction\n---")
        retry = self.cli(self.first, "--save")
        self.assertTrue(retry["replayed"])
        self.assertEqual(retry["path"], "Reports/Моя неделя.md")
        self.assertEqual(original, path.read_bytes())
        self.assertEqual(len(published_issues(self.root)), 1)

    def test_previous_issue_by_id_and_two_exact_sources_for_change(self):
        first = self.cli(self.first, "--save")
        deadlines = Deadlines(self.root)
        current = deadlines.preview(self.task["id"], "2026-11-22")
        deadlines.apply(self.task["id"], "2026-11-22", current["revision"])
        next_plan = self.closed("Следующий", [self.task], 23)
        result = self.cli(next_plan, "--gantt-url", "http://localhost/")
        self.assertEqual(result["previous"]["id"], first["id"])
        change, = [f for f in result["items"] if f["kind"] == "change"]
        self.assertEqual(len(change["sources"]), 2)
        for src, pid in zip(change["sources"], [self.first, next_plan]):
            self.assertEqual(src["href"], f"http://localhost/#plan/{pid}?task={self.task['id']}")
            self.assertIn("Посмотреть", (self.root / src["path"]).read_text().splitlines()[src["line"] - 1])
        self.assertIn("2026-11-20 → 2026-11-22", change["text"])
        self.assertIn("20 ноября → 22 ноября", _finding(change, 2026))
        parallel = self.closed("Ещё раньше", [], 16)
        self.cli(parallel, "--save")
        self.assertEqual(self.cli(next_plan)["previous"]["id"], first["id"])
        related = self.closed("Смежный план", [self.task], 16)
        self.cli(related, "--save")
        with self.assertRaisesRegex(ValueError, "previous"):
            self.cli(next_plan)
        selected = self.cli(next_plan, "--previous", first["id"])
        self.assertEqual(selected["previous"]["id"], first["id"])

    def test_invalid_format_and_origin_leave_source_and_reports_untouched(self):
        with self.assertRaisesRegex(ValueError, "gantt-url"):
            self.cli(self.first, "--gantt-url", "file:///etc/passwd", "--save")
        self.format.write_text("---\ngroup_by: other\n---\n")
        with self.assertRaisesRegex(ValueError, "group_by"):
            self.cli(self.first, "--save")
        self.assertEqual(published_issues(self.root), [])

    def test_freestanding_markdown_and_retry_when_originals_are_unavailable(self):
        folder = self.root / "Reports"
        (folder / "Дневник.md").write_text('---\ntype: "незакрытая кавычка\n---\n')
        (folder / "Черновик.md").write_text("---\ntype: weekly_review\n---\n")
        saved = self.cli(self.first, "--save")
        (self.root / self.task["path"]).unlink()
        path = self.root / self.planning.view(self.first)["path"]
        path.unlink()
        self.assertEqual(saved["path"], self.cli(self.first, "--save")["path"])
        self.assertEqual(len(published_issues(self.root, self.first)), 1)

    def test_owner_text_remains_one_markdown_finding(self):
        fact = {"kind": "event", "date": "2026-11-20", "title": "Проверить [макет]",
                "text": "Сверили отчёт.\n# Второй заголовок\n- [ссылка](bad)\n<img src=x>"}
        line = _finding(fact, 2026)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn("20 ноября", line)
        self.assertIn(r"\[макет\]", line)
        self.assertNotRegex(line, r"(?<!\\)\[ссылка\]")
        self.assertNotIn("<img", line)

    def test_damaged_report_metadata_still_reserves_its_id_after_rename(self):
        saved = self.cli(self.first, "--save")
        path = (self.root / saved["path"]).rename(self.root / "Reports" / "Мои итоги.md")
        original = path.read_text()
        for field in ("period_start", "closed_at", "type", "plan_id"):
            with self.subTest(field=field):
                changed = re.sub(f"^{field}:.*$", f"{field}: ошибочное значение", original, flags=re.M)
                path.write_text(changed)
                with self.assertRaisesRegex(storage.ConflictError, "Мои итоги"):
                    self.cli(self.first, "--save")
                self.assertEqual(list(path.parent.glob("*.md")), [path])
                self.assertEqual(path.read_text(), changed)
        path.write_text(re.sub(r"^closed_at:.*\n", "", original, flags=re.M))
        self.assertTrue(self.cli(self.first, "--save")["replayed"])
