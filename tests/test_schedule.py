from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from second_brain import storage
from second_brain.gantt import Gantt
from second_brain.plan_dates import Deadlines
from second_brain.planning import Planning
from second_brain.schedule import Schedule


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.planning = Planning(self.root)
        self.schedule = Schedule(self.root)
        self.task = self.planning.create_task("Выполнить сверку", due="2020-04-24")
        self.identifier = self.task["id"]
        self.path = self.root / self.task["path"]
        self.plans = [self.planning.create_plan(title) for title in ("Проект", "Неделя")]
        for p in self.plans:
            self.planning.add_task(p["id"], self.identifier)

    def put(self, field, value, **options):
        options = {"actor": "manual", **options}
        preview = self.schedule.preview(self.identifier, field, value, **options)
        return self.schedule.apply(self.identifier, field, value, preview["revision"], **options)

    def test_fields_are_independent_and_shared_by_two_unchanged_plans(self):
        files = [(self.root / p["path"]).read_bytes() for p in self.plans]
        initial = self.path.read_bytes()
        preview = self.schedule.preview(self.identifier, "forecast", "2020-04-28", actor="ai")
        self.assertEqual(self.path.read_bytes(), initial)
        self.assertEqual((preview["due"], preview["before"], preview["completed"]), ("2020-04-24", None, None))
        self.assertEqual(set(p["id"] for p in preview["plans"]), set(p["id"] for p in self.plans))
        self.put("forecast", "2020-04-28", actor="ai")
        self.put("forecast", "2020-04-27", reason="Проверку перенесли на понедельник")
        self.put("completed", "2020-04-26", reason="Владелец подтвердил проверку")
        self.assertEqual([c["field"] for c in self.schedule.history(self.identifier)], ["forecast", "forecast", "completed"])
        for p, original in zip(self.plans, files):
            row, = self.planning.view(p["id"])["items"]
            self.assertEqual((row["due"], row["forecast"], row["completed"]),
                             ("2020-04-24", "2020-04-27", "2020-04-26"))
            self.assertEqual((self.root / p["path"]).read_bytes(), original)
            for field in ("due", "forecast", "completed"):
                self.assertTrue(self.path.read_text().splitlines()[row[field + "Line"] - 1].startswith(field + ":"))
        deadline = Deadlines(self.root)
        receipt = deadline.preview(self.identifier, "2020-04-30")
        deadline.apply(self.identifier, "2020-04-30", receipt["revision"])
        self.put("forecast", None)
        self.assertEqual(self.planning.task(self.identifier)["completed"], "2020-04-26")
        self.put("completed", None, reason="Исправлена ошибочная отметка")
        self.assertEqual(self.planning.task(self.identifier)["due"], "2020-04-30")

    def test_completion_requires_a_result_and_can_be_earlier_than_planned(self):
        gantt = Gantt(self.root)
        gantt.edit({"id": self.identifier, "revision": self.planning.task(self.identifier)["revision"],
                    "actor": "manual", "field": "start", "value": "2020-04-23"})
        for options in ({"actor": "manual"}, {"actor": "ai", "reason": "Возможно готово"},
                        {"actor": "ai", "owner_confirmed": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.put("completed", "2020-04-22", **options)
        future = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):
            self.put("completed", future, reason="Планируем закончить завтра")
        verified = self.put("completed", "2020-04-22", actor="ai", reason="Владелец сообщил, что работа завершена",
                            owner_confirmed=True)
        self.assertTrue(verified["change"]["ownerConfirmed"])
        self.assertEqual(verified["change"]["actor"], "ai")
        self.assertEqual(self.put("forecast", future)["completed"], "2020-04-22")
        with self.assertRaises(ValueError):
            self.put("forecast", "2030-02-31")
        for field in ({}, [], None, "due"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.schedule.preview(self.identifier, field, None, actor="manual")

    def test_verified_evidence_is_bound_to_preview_and_survives_file_rename(self):
        quote = "Работа завершена 21 апреля 2020 года."
        note = storage.create(self.root, "note", "Результат сверки", quote)
        line = note.read_text().splitlines().index(quote) + 1
        source = {"path": note.relative_to(self.root).as_posix(), "line": line, "quote": quote}
        args = dict(actor="ai", sources=[source])
        preview = self.schedule.preview(self.identifier, "completed", "2020-04-21", **args)
        original = note.read_bytes()
        note.write_text(note.read_text().replace(quote, "Результат неизвестен."))
        with self.assertRaises(storage.ConflictError):
            self.schedule.apply(self.identifier, "completed", "2020-04-21", preview["revision"], **args)
        note.unlink()
        with self.assertRaises(storage.ConflictError):
            self.schedule.apply(self.identifier, "completed", "2020-04-21", preview["revision"], **args)
        self.assertIsNone(self.planning.task(self.identifier)["completed"])
        note.write_bytes(original)
        saved = self.schedule.apply(self.identifier, "completed", "2020-04-21", preview["revision"], **args)
        ref, = saved["change"]["sources"]
        self.assertIn(ref["id"], ref["href"])
        self.assertIn(ref["ref"], ref["href"])
        note.rename(note.with_name("Другое имя.md"))
        replay = self.schedule.apply(self.identifier, "completed", "2020-04-21", preview["revision"], **args)
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.schedule.history(self.identifier)[0]["sources"], [ref])

    def test_completion_date_must_be_supported_by_a_checked_quote(self):
        line = "Работа завершена 21 апреля 2020 года."
        note = storage.create(self.root, "note", "Приёмка", line)
        source = {"path": note.relative_to(self.root).as_posix(), "line": storage.read_record(note)["body_start"]}
        header = note.read_text().removesuffix(line)
        before = self.path.read_bytes()
        for text, quote, day in ((line, line, "2020-01-01"), (line, "21 апреля", "2019-04-21"),
                                 (line, "Работа завершена", "2020-04-21"),
                                 ("Срок 2020-04-20 или 2020-04-21.", None, "2020-04-20"),
                                 ("Завершено 4 марта.", None, "2020-03-05")):
            note.write_text(header + text)
            with self.subTest(text=text, quote=quote, day=day), self.assertRaisesRegex(ValueError, "датой завершения"):
                self.put("completed", day, actor="ai", sources=[{**source, "quote": quote or text}])
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.put("completed", "2020-03-04", actor="ai", sources=[source])["completed"], "2020-03-04")

    def test_replay_after_later_change_and_conflicts(self):
        preview = self.schedule.preview(self.identifier, "forecast", "2020-04-25", actor="ai")
        for field, value, actor in (("forecast", None, "ai"), ("completed", "2020-04-25", "manual"),
                                    ("forecast", "2020-04-25", "manual")):
            with self.subTest(field=field, value=value, actor=actor), self.assertRaises(storage.ConflictError):
                self.schedule.apply(self.identifier, field, value, preview["revision"], actor=actor, reason="Иная правка")
        self.planning.add_task(self.planning.create_plan("Ещё план")["id"], self.identifier)
        with self.assertRaises(storage.ConflictError):
            self.schedule.apply(self.identifier, "forecast", "2020-04-25", preview["revision"], actor="ai")
        first = self.put("forecast", "2020-04-25", actor="ai")
        second = self.put("forecast", "2020-04-29", actor="ai")
        replay = self.schedule.apply(self.identifier, "forecast", "2020-04-25", first["revision"], actor="ai")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["forecast"], second["forecast"])
        self.assertFalse(self.put("forecast", "2020-04-29")["changed"])
        preview = self.schedule.preview(self.identifier, "forecast", None, actor="ai")
        self.path.write_text(self.path.read_text().replace('forecast: "2020-04-29"', 'forecast: "2020-04-28"'))
        with self.assertRaises(storage.ConflictError):
            self.schedule.apply(self.identifier, "forecast", None, preview["revision"], actor="ai")
        self.assertEqual(self.planning.task(self.identifier)["forecast"], "2020-04-28")

    def test_manual_yaml_text_newlines_and_one_atomic_receipt(self):
        original = self.path.read_bytes()
        for custom in (b"forecast: [recheck]", b"forecast: |\n  on Tuesday", b"forecast:\n- a custom value"):
            bad = original.replace(b"---\n", b"---\n" + custom + b"\n", 1)
            self.path.write_bytes(bad)
            with self.assertRaises(ValueError):
                self.put("forecast", None)
            self.assertEqual(self.path.read_bytes(), bad)
        custom = 'forecast: "2020-04-25" # владельца\nextra: [custom]'.encode()
        raw = original.replace(b"---\n", b"---\n" + custom + b"\n", 1).replace(b"\n", b"\r\n")
        self.path.write_bytes(raw)
        preview = self.schedule.preview(self.identifier, "forecast", None, actor="manual")
        def commit():
            return self.schedule.apply(self.identifier, "forecast", None, preview["revision"], actor="manual")
        with patch("second_brain.storage.os.replace", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                commit()
        self.assertEqual(self.path.read_bytes(), raw)
        with ThreadPoolExecutor(2) as pool:
            results = [f.result() for f in [pool.submit(commit), pool.submit(commit)]]
        self.assertEqual(sorted(r["replayed"] for r in results), [False, True])
        saved = self.path.read_bytes()
        self.assertIn("# владельца\r\nextra: [custom]".encode(), saved)
        self.assertNotIn(b"\n", saved.replace(b"\r\n", b""))
        entry, = self.schedule.history(self.identifier)
        self.assertIn("2020-04-25 → не указано", self.path.read_text().splitlines()[entry["line"] - 1])


if __name__ == "__main__":
    unittest.main()
