from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from second_brain import cycles, storage
from second_brain.cycles import Cycles
from second_brain.dependencies import Dependencies
from second_brain.gantt import Gantt
from second_brain.gantt_feedback import GanttFeedback
from second_brain.plan_dates import Deadlines
from second_brain.planning import Planning
from second_brain.schedule import Schedule


class CycleTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.plans = Planning(self.root)
        self.cycles = Cycles(self.root)
        self.week = self.plans.create_plan("Неделя")
        self.plan_file = self.root / self.week["path"]
        self.area = storage.read_record(storage.create(self.root, "direction", "Мастерская"))
        self.project = storage.read_record(storage.create(self.root, "project", "Запуск", direction=self.area["id"]))
        self.work = self.plans.create_task("Проверить | образцы", project=self.project["id"], due="2026-11-25")
        self.mark = self.plans.create_task("Готовность", kind="milestone")
        self.plans.add_task(self.week["id"], self.work["id"])
        self.plans.add_task(self.week["id"], self.mark["id"])
        self.set_date("forecast", "2026-11-30")

    def set_date(self, field, value):
        schedule = Schedule(self.root)
        params = {"actor": "manual", "reason": "По учебной записи"}
        preview = schedule.preview(self.work["id"], field, value, **params)
        schedule.apply(self.work["id"], field, value, preview["revision"], **params)

    def files(self):
        return {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def close(self):
        preview = self.cycles.preview(self.week["id"])
        return self.cycles.close(self.week["id"], preview["revision"])

    def test_whole_snapshot_is_one_file_and_preserves_original_rows_after_changes(self):
        raw = self.plan_file.read_text().replace("schema_version:", "owner_field: [7]\nschema_version:")
        original = (raw + "\nЗапись владельца").replace("\n", "\r\n").encode()
        self.plan_file.write_bytes(original)
        before = self.files()
        preview = self.cycles.preview(self.week["id"])
        self.assertEqual(self.files(), before)
        self.assertEqual(preview["revision"], self.cycles.preview(self.week["id"])["revision"])
        self.assertIsNone(preview["closedAt"])
        closed = self.cycles.close(self.week["id"], preview["revision"])
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(datetime.fromisoformat(closed["closedAt"]).utcoffset(), timedelta(0))
        after = self.files()
        self.assertEqual([p for p in before if before[p] != after[p]], [self.plan_file])
        self.assertEqual(set(before), set(after))
        self.assertIn("Запись владельца".encode(), after[self.plan_file])
        self.assertNotIn(b"\n", after[self.plan_file].replace(b"\r\n", b""))
        first, second = closed["items"]
        self.assertEqual((first["title"], first["due"], first["forecast"], first["projectTitle"], first["directionTitle"]),
                         ("Проверить | образцы", "2026-11-25", "2026-11-30", "Запуск", "Мастерская"))
        self.assertEqual((second["kind"], second["completed"], second["forecast"]), ("milestone", None, None))
        self.assertIn("2026-11-30", self.plan_file.read_text().splitlines()[first["snapshotLine"] - 1])

        next_week = self.plans.create_plan("Следующая неделя")
        self.plans.add_task(next_week["id"], first["id"])
        self.set_date("forecast", "2026-12-01")
        self.set_date("completed", "2020-02-29")
        storage.edit(self.root, self.project["id"], self.project["revision"], {"title": "Запуск 2"})
        work_path = self.root / self.work["path"]
        work_path.write_text(work_path.read_text().replace('title: "Проверить | образцы"', 'title: "Сверить"'))
        renamed = work_path.rename(work_path.with_name("Сверка.md"))
        (self.root / second["path"]).unlink()
        self.assertEqual(self.files()[self.plan_file], after[self.plan_file])
        self.assertEqual(self.plans.task(first["id"])["path"], renamed.relative_to(self.root).as_posix())
        self.assertEqual(Gantt(self.root).view(self.week["id"])["items"], closed["items"])
        active, = Gantt(self.root).view(next_week["id"])["items"]
        self.assertEqual((active["title"], active["forecast"], active["projectTitle"], active["completed"]),
                         ("Сверить", "2026-12-01", "Запуск 2", "2020-02-29"))
        self.assertEqual(self.plans.add_task(self.week["id"], second["id"])["items"], closed["items"])
        with self.assertRaisesRegex(storage.ConflictError, "закрыт"):
            self.plans.add_task(self.week["id"], self.plans.create_task("Новая")["id"])
        replay = self.cycles.close(self.week["id"], preview["revision"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["closedAt"], closed["closedAt"])

    def test_stale_receipt_rejects_membership_date_scope_and_plan_changes(self):
        preview = self.cycles.preview(self.week["id"])
        for edit in [
            lambda: self.set_date("forecast", "2026-12-03"),
            lambda: storage.edit(self.root, self.area["id"], self.area["revision"], {"title": "Студия"}),
            lambda: self.plans.add_task(self.week["id"], self.plans.create_task("Вне проекта")["id"]),
            lambda: self.plan_file.write_text(self.plan_file.read_text() + "\nЗаметка\n"),
        ]:
            edit()
            before = self.files()
            with self.assertRaises(storage.ConflictError):
                self.cycles.close(self.week["id"], preview["revision"])
            self.assertEqual(before, self.files())
            preview = self.cycles.preview(self.week["id"])
        self.assertEqual(self.cycles.close(self.week["id"], preview["revision"])["status"], "closed")

    def test_concurrent_confirmations_publish_once_and_preserve_failed_write(self):
        proposal = self.cycles.preview(self.week["id"])
        args = self.week["id"], proposal["revision"]
        before = self.files()
        with patch.object(storage, "replace_bytes", side_effect=OSError("storage unavailable")):
            with self.assertRaises(OSError):
                self.cycles.close(*args)
        self.assertEqual(before, self.files())
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.cycles.close(*args), range(2)))
        self.assertEqual(sorted(r["replayed"] for r in results), [False, True])
        self.assertEqual(results[0]["closedAt"], results[1]["closedAt"])
        with self.assertRaises(storage.ConflictError):
            self.cycles.close(args[0], "f" * 64)
        self.assertEqual(self.plan_file.read_text().count("## Итог цикла"), 1)

    def test_native_edit_while_rendering_snapshot_keeps_plan_open(self):
        original = cycles._cells
        path = self.root / self.work["path"]
        def render(row):
            path.write_text(path.read_text().replace('forecast: "2026-11-30"', 'forecast: "2026-12-01"'))
            return original(row)
        before = self.plan_file.read_bytes()
        with patch.object(cycles, "_cells", side_effect=render):
            with self.assertRaises(storage.ConflictError):
                self.close()
        self.assertEqual(self.plan_file.read_bytes(), before)
        self.assertEqual(self.plans.task(self.work["id"])["forecast"], "2026-12-01")

    def test_closed_receipt_independent_of_live_membership_title_and_reference_line(self):
        old = self.close()
        data = self.plan_file.read_text()
        self.plan_file.write_text(data.replace('title: "Неделя"', 'title: "Исправлено позже"')
                                  .replace("<!-- plan-members -->", "\n\nЗапись владельца\n<!-- plan-members -->")
                                  .replace("<!-- /plan-members -->", "<!-- граница списка потеряна -->"))
        path = self.plan_file.rename(self.plan_file.with_name("Прошлый цикл.md"))
        result = self.plans.view(self.week["id"])
        self.assertEqual((result["path"], result["title"]), ("Plans/Прошлый цикл.md", "Неделя"))
        self.assertEqual(self.plans.plans()[0]["title"], result["title"])
        self.assertGreater(result["items"][0]["snapshotLine"], old["items"][0]["snapshotLine"])
        line = result["items"][0]["snapshotLine"] - 1
        self.assertIn("2026-11-25", path.read_text().splitlines()[line])
        self.assertEqual(list(GanttFeedback(self.root).pending(self.week["id"])), [])
        self.assertEqual(self.plans.plans_for_task(self.work["id"]), [])
        self.assertEqual(Deadlines(self.root).preview(self.work["id"], "2026-12-10")["plans"], [])
        scenario = Dependencies(self.root).preview(self.work["id"], "2026-12-03")
        self.assertEqual(scenario["rows"][0]["plans"], [])
        # Accidental edits to a historical value must not silently display a live or altered date.
        lines = path.read_text().splitlines(keepends=True)
        lines[line] = lines[line].replace("2026-11-25", "2026-11-26", 1)
        path.write_text("".join(lines))
        with self.assertRaisesRegex(storage.ConflictError, "снимка"):
            self.plans.view(self.week["id"])

    def test_missing_cards_unknown_dates_and_empty_plan(self):
        path = self.root / self.work["path"]
        original = path.read_text()
        path.write_text(original.replace('forecast: "2026-11-30"', 'forecast: [2026-11-30]'))
        with self.assertRaisesRegex(ValueError, "forecast.*Tasks"):
            self.cycles.preview(self.week["id"])
        path.write_text(original.replace('forecast: "2026-11-30"', 'forecast: null # ещё неизвестно'))
        (self.root / self.mark["path"]).unlink()
        present, missing = self.close()["items"]
        self.assertIsNone(present["forecast"])
        self.assertTrue(missing["missing"])
        self.assertIsNone(missing["path"])
        self.assertIn(self.mark["id"], self.plan_file.read_text().splitlines()[missing["snapshotLine"] - 1])
        empty = self.plans.create_plan("Свободный период")
        preview = self.cycles.preview(empty["id"])
        self.assertEqual(self.cycles.close(empty["id"], preview["revision"])["items"], [])
