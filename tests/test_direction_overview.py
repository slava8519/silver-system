from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import parse_qs, urlsplit

from second_brain import project_updates, storage
from second_brain.direction_overview import direction_overview
from second_brain.gantt import Gantt
from second_brain.planning import Planning
from second_brain.query import _line_ref
from second_brain.schedule import Schedule


class DirectionOverviewTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = storage.initialize(Path(tmp.name))
        self.plans = Planning(self.root)
        self.gantt = Gantt(self.root)

    def create(self, kind, title, body="", **kwargs):
        path = storage.create(self.root, kind, title, body, **kwargs)
        return storage.read_record(path)["id"], path

    def row(self, direction):
        return direction_overview(self.root, direction=direction)["items"][0]

    def test_direction_own_brief_manual_dates_and_ordinary_description(self):
        a, path = self.create("direction", "Управление", "# Управление\nОписание.\n\n## Коротко\n"
                               "Все пилоты на проверке.\n\n## Последнее\n- Вернули один сценарий.\n"
                               "- 15.11.2026 — Передали второй пилот.\n\n## Пример\n2030-12-10 Придумано.")
        pid, _ = self.create("project", "Пилот", "## Коротко\nСводка пилота.", direction=a)
        b, _ = self.create("direction", "Личное", "# Личное\nСемейные дела.\n\n## План\nПроверить счета.")
        empty, _ = self.create("direction", "Без записей", "## Предложения\nМожет быть, испытать.")
        work = self.plans.create_task("Испытание", due="2031-11-12", project=pid)
        self.gantt.edit({"id": work["id"], "revision": work["revision"], "actor": "manual",
                         "field": "latest", "text": "Позже внесли более раннее событие.", "date": "2026-11-02"})

        row = self.row(a)
        self.assertEqual((row["projectCount"], row["taskCount"]), (1, 1))
        self.assertEqual([item["text"] for item in row["briefs"]], ["Все пилоты на проверке."])
        self.assertEqual((row["latest"]["date"], row["latest"]["text"], row["hasUndated"]),
                         ("2026-11-15", "Передали второй пилот.", True))
        for item in [*row["briefs"], row["latest"]]:
            src = item["source"]
            self.assertEqual(src["path"], path.relative_to(self.root).as_posix())
            self.assertIn(item["text"], path.read_text().splitlines()[src["line"] - 1])
            self.assertIn("revision=" + storage.read_record(path)["revision"], src["href"])
        self.assertEqual([(r["kind"], r["text"]) for r in self.row(b)["briefs"]], [("description", "Семейные дела.")])
        self.assertEqual((self.row(empty)["briefs"], self.row(empty)["latest"]), ([], None))

    def test_events_without_dates_cannot_be_sorted_by_input_time(self):
        a, _ = self.create("direction", "Парфюмерия")
        note, path = self.create("note", "Дневник",
                                 "~~~markdown\n## События\n2026-12-31 Вымышленное событие.\n~~~\n"
                                 "## События\n- Подписали блоттеры.\n", direction=a)
        row = self.row(a)
        self.assertTrue(row["hasUndated"])
        self.assertEqual((row["latest"]["date"], row["latest"]["record"]["id"]), (None, note))
        path.write_text(path.read_text() + "- Получили эссенцию.\n")
        row = self.row(a)
        self.assertTrue(row["hasUndated"])
        self.assertIsNone(row["latest"])
        path.write_text(path.read_text() + "- 2026-11-03 · Закрыли прошлую серию.\n")
        row = self.row(a)
        self.assertEqual((row["latest"]["date"], row["latest"]["text"], row["hasUndated"]),
                         ("2026-11-03", "Закрыли прошлую серию.", True))

    def test_project_tasks_direct_work_and_confirmed_completion(self):
        a, _ = self.create("direction", "ИТ", "Старое описание")
        first, _ = self.create("project", "Пилот", "## Коротко\nПроверка идёт.", direction=a)
        second, _ = self.create("project", "Выкладка", direction=a)
        t = self.plans.create_task("Проверить макет", project=first, due="2030-12-08")
        parallel = self.plans.create_task("Выкладка", project=second)
        m = self.plans.create_task("Рубеж проекта", direction=a, kind="milestone")
        for parent in ("Неделя", "Месяц"):
            self.plans.add_task(self.plans.create_plan(parent)["id"], t["id"])
        for task in (t, parallel):
            self.gantt.edit({"id": task["id"], "revision": task["revision"], "actor": "manual",
                             "field": "brief", "text": task["title"]})
        self.assertIsNone(self.row(a)["latest"])
        calendar = Schedule(self.root)
        options = {"actor": "manual", "reason": "Проверено."}
        preview = calendar.preview(m["id"], "completed", "2020-11-15", **options)
        calendar.apply(m["id"], "completed", "2020-11-15", preview["revision"], **options)
        row = self.row(a)
        self.assertEqual((row["projectCount"], row["taskCount"]), (2, 3))
        self.assertEqual([b["record"]["id"] for b in row["briefs"]], [first, parallel["id"]])
        self.assertEqual(row["briefTotal"], 3)
        event = row["latest"]
        self.assertEqual((event["record"]["type"], event["date"], event["text"]),
                         ("milestone", "2020-11-15", "Веха достигнута"))
        for item in [event, row["briefs"][1]]:
            src = item["source"]
            line = (self.root / src["path"]).read_text().splitlines()[src["line"] - 1]
            url = urlsplit(src["href"])
            self.assertEqual((url.path, url.fragment), ("/source/" + item["record"]["id"], "cited-line"))
            self.assertEqual(parse_qs(url.query), {"kind": ["task"], "line": [str(src["line"])], "ref": [_line_ref(line)]})

    def test_private_archive_and_foreign_notes_do_not_expose_adjacent_details(self):
        a, _ = self.create("direction", "Работа")
        b, _ = self.create("direction", "Другие дела")
        pid, project = self.create("project", "Пилот", direction=a, status="Подготовка")
        _, foreign = self.create("note", "Личная беседа", "2026-11-09 Пройден отбор. Не для коллег.", direction=b)
        source = {"path": foreign.relative_to(self.root).as_posix(), "line": storage.read_record(foreign)["body_start"],
                  "quote": "Пройден отбор."}
        project_updates.write_update(self.root, pid, field="brief", text="Отбор пройден.", sources=[source],
                                     revision=storage.read_record(project)["revision"])
        original = "Только для себя\n2026-11-10 Акт подписан. Секретный сосед.\n".encode()
        identifier = sha256(original).hexdigest()
        original_path = self.root / "Sources" / "Originals" / (identifier + ".txt")
        original_path.write_bytes(original)
        ref = {"path": original_path.relative_to(self.root).as_posix(), "line": 2, "quote": "2026-11-10 Акт подписан."}
        project_updates.write_update(self.root, pid, field="event", text="Акт подписан.", sources=[ref],
                                     date="2026-11-10", revision=storage.read_record(project)["revision"])
        row = self.row(a)
        raw = json.dumps(row, ensure_ascii=False)
        for token in ("Секретный", "Не для коллег", "Личная беседа", "/original/", "Только для себя"):
            self.assertNotIn(token, raw)
        self.assertTrue(all(item["source"]["path"] == project.relative_to(self.root).as_posix()
                            for item in [*row["briefs"], row["latest"]]))
        original_path.unlink()
        foreign.unlink()
        row = self.row(a)
        self.assertIsNone(row["latest"])
        self.assertEqual([item["kind"] for item in row["briefs"]], ["status"])

    def test_live_renamed_source_bounded_pages_and_no_writes(self):
        self.create("direction", "Я")
        a, _ = self.create("direction", "А")
        self.create("direction", "Б")
        pid, project = self.create("project", "Дело", direction=a)
        _, note = self.create("note", "Решение", "Первый абзац.\nПодтвердили передачу.", direction=a)
        src = {"path": note.relative_to(self.root).as_posix(),
               "line": storage.read_record(note)["body_start"] + 1, "quote": "Подтвердили передачу."}
        project_updates.write_update(self.root, pid, field="brief", text="Подтвердили передачу. " * 30, sources=[src],
                                     revision=storage.read_record(project)["revision"])
        moved = note.rename(note.with_name("Новый протокол.md"))
        moved.write_text(moved.read_text().replace("Первый абзац.", "Описание.\nПервый абзац."))
        def snapshot():
            return {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.root.rglob("*") if p.is_file()}
        before = snapshot()
        pages = [direction_overview(self.root, limit=1, offset=i) for i in range(4)]
        self.assertEqual(snapshot(), before)
        self.assertEqual([p["items"][0]["title"] for p in pages[:3]], ["А", "Б", "Я"])
        self.assertEqual([p["truncated"] for p in pages], [True, True, False, False])
        self.assertEqual([p["total"] for p in pages], [3] * 4)
        brief = pages[0]["items"][0]["briefs"][0]
        self.assertTrue(brief["text"].endswith("…"))
        self.assertLessEqual(len(brief["text"]), 220)
        self.assertEqual((brief["source"]["path"], brief["source"]["line"]),
                         (moved.relative_to(self.root).as_posix(), src["line"] + 1))
        for kwargs in ({"limit": 0}, {"limit": True}, {"limit": 21}, {"offset": -1}, {"direction": "missing"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                direction_overview(self.root, **kwargs)
