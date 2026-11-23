from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from second_brain import storage
from second_brain.plan_dates import Deadlines
from second_brain.planning import Planning


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.planning = Planning(self.root)
        self.dates = Deadlines(self.root)
        self.task = self.planning.create_task("Проверить образцы")
        self.task_id = self.task["id"]
        self.file = self.root / self.task["path"]

    def snapshot(self):
        return {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_preview_and_atomic_commit_keep_both_plans_as_references(self):
        ids = []
        for title in ("Запуск", "Неделя"):
            plan = self.planning.create_plan(title)
            ids.append(plan["id"])
            self.planning.add_task(plan["id"], self.task_id)
        before = self.snapshot()
        proposal = self.dates.preview(self.task_id, "2028-02-29")
        self.assertEqual(self.snapshot(), before)
        self.assertIsNone(proposal["before"])
        self.assertEqual({p["id"] for p in proposal["plans"]}, set(ids))
        self.assertTrue(all(self.planning.view(i)["items"][0]["due"] is None for i in ids))
        receipt = self.dates.apply(self.task_id, "2028-02-29", proposal["revision"])
        self.assertTrue(receipt["changed"])
        self.assertFalse(receipt["replayed"])
        now = self.snapshot()
        self.assertEqual([name for name in now if now[name] != before[name]], [Path(self.task["path"])])
        for i in ids:
            item, = self.planning.view(i)["items"]
            self.assertEqual((item["id"], item["due"]), (self.task_id, "2028-02-29"))
        change, = self.dates.history(self.task_id)
        self.assertEqual(change, receipt["change"])
        self.assertIn("без срока → 2028-02-29", self.file.read_text().splitlines()[change["line"] - 1])

    def test_conflicts_bind_entire_task_membership_and_requested_date(self):
        proposal = self.dates.preview(self.task_id, "2027-01-06")
        with self.assertRaises(storage.ConflictError):
            self.dates.apply(self.task_id, None, proposal["revision"])
        plan = self.planning.create_plan("Календарь")
        self.planning.add_task(plan["id"], self.task_id)
        with self.assertRaises(storage.ConflictError):
            self.dates.apply(self.task_id, "2027-01-06", proposal["revision"])
        current = self.dates.preview(self.task_id, "2027-01-06")
        manual = self.file.read_bytes() + "Приоритет согласован вручную.\n".encode()
        self.file.write_bytes(manual)
        with self.assertRaises(storage.ConflictError):
            self.dates.apply(self.task_id, "2027-01-06", current["revision"])
        self.assertEqual(self.file.read_bytes(), manual)
        self.assertEqual(self.dates.history(self.task_id), [])

    def test_manual_fields_and_original_newlines_survive_and_log_stays_together(self):
        raw = self.file.read_bytes()
        custom = 'custom_fields:\n  owner: "Ольга"  # ручное поле\ndue: "2027-01-05"  # вручную\n'
        raw = raw.replace(b"---\n", b"---\n" + custom.encode(), 1).replace(b"\n", b"\r\n")
        self.file.write_bytes(raw)
        proposal = self.dates.preview(self.task_id, "2027-01-07")
        self.dates.apply(self.task_id, "2027-01-07", proposal["revision"])
        tail = "\r\n## Комментарий владельца\r\nОтдельный абзац.\r\n".encode()
        self.file.write_bytes(self.file.read_bytes() + tail)
        clear = self.dates.preview(self.task_id, None)
        self.dates.apply(self.task_id, None, clear["revision"])
        saved = self.file.read_bytes()
        self.assertTrue(saved.endswith(tail))
        self.assertNotIn(b"\n", saved.replace(b"\r\n", b""))
        self.assertIn('custom_fields:\r\n  owner: "Ольга"  # ручное поле'.encode(), saved)
        self.assertIn("# вручную".encode(), saved)
        self.assertIsNone(self.planning.task(self.task_id)["due"])
        self.assertEqual([(r["before"], r["after"]) for r in self.dates.history(self.task_id)],
                         [("2027-01-05", "2027-01-07"), ("2027-01-07", None)])
        self.assertEqual(saved.count("## История сроков".encode()), 1)

    def test_replay_after_a_later_change_never_restores_an_old_due(self):
        first = self.dates.preview(self.task_id, "2027-01-10")
        self.dates.apply(self.task_id, first["after"], first["revision"])
        later = self.dates.preview(self.task_id, "2027-02-02")
        self.dates.apply(self.task_id, later["after"], later["revision"])
        current = self.file.read_bytes()
        replay = self.dates.apply(self.task_id, first["after"], first["revision"])
        self.assertFalse(replay["changed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["due"], "2027-02-02")
        self.assertEqual(replay["change"]["after"], "2027-01-10")
        self.assertEqual(self.file.read_bytes(), current)
        self.assertEqual(len(self.dates.history(self.task_id)), 2)

    def test_retry_concurrency_failed_publication_and_noop(self):
        same = self.dates.preview(self.task_id, None)
        previous = self.file.read_bytes()
        self.assertFalse(self.dates.apply(self.task_id, None, same["revision"])["changed"])
        self.assertEqual(previous, self.file.read_bytes())
        proposal = self.dates.preview(self.task_id, "2027-04-06")
        def commit():
            return self.dates.apply(self.task_id, proposal["after"], proposal["revision"])
        with patch("second_brain.storage.os.replace", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                commit()
        self.assertEqual(previous, self.file.read_bytes())
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: commit(), range(2)))
        self.assertEqual(sorted(r["changed"] for r in results), [False, True])
        self.assertEqual(len(self.dates.history(self.task_id)), 1)

    def test_invalid_dates_and_custom_nonscalar_due_are_preserved(self):
        for value in ("2027-02-29", "2027-2-03", "", "к пятнице"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.dates.preview(self.task_id, value)
        manual = self.file.read_bytes().replace(b"---\n", b"---\ndue: [later]\n", 1)
        self.file.write_bytes(manual)
        with self.assertRaises(ValueError):
            self.dates.preview(self.task_id, None)
        self.assertEqual(self.file.read_bytes(), manual)
