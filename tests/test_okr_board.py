import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from second_brain import cycles, storage
from second_brain.okr import KeyResults
from second_brain.plan_dates import Deadlines
from second_brain.planning import Planning
from second_brain.__main__ import command_line, run


class OkrBoardTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.plan = Planning(self.root)
        self.kr = KeyResults(self.root)
        self.cycles = cycles.Cycles(self.root)
        self.area = self.card("direction", "ИТ")
        self.personal = self.card("direction", "Личное")
        self.fragrance = self.card("direction", "Парфюмерия")
        self.q4 = self.plan.create_plan("IV квартал")
        self.q1 = self.plan.create_plan("I квартал")

    def card(self, kind, title, **options):
        return storage.read_record(storage.create(self.root, kind, title, **options))["id"]

    def goal(self, name="Подготовить запуск", period=None, area=None, **options):
        return self.kr.create_objective(name, plan_id=(period or self.q4)["id"], direction=area or self.area, **options)

    def result(self, goal, title="Срок восстановления", **options):
        return self.kr.create(title, objective_id=goal["id"], unit="мин", **options)

    def test_explicit_plan_parent_and_union_of_shared_existing_tasks(self):
        first = self.card("project", "Маяк", direction=self.area)
        second = self.card("project", "Кедр", direction=self.fragrance)
        work = self.plan.create_task("Проверить сервер", project=first, due="2026-11-29")
        probe = self.plan.create_task("Сравнить пробы", project=second)
        personal = self.plan.create_task("Забрать билеты", direction=self.personal)
        self.plan.add_task(self.q4["id"], work["id"])
        self.plan.add_task(self.q4["id"], personal["id"])
        self.plan.add_task(self.q1["id"], work["id"])
        before = {self.root / c["path"]: (self.root / c["path"]).read_bytes()
                  for c in (work, probe, personal, self.q4, self.q1)}
        goal = self.goal(request_id="g1")
        other = self.goal(period=self.q1)
        self.assertNotEqual(goal["id"], other["id"])
        empty = self.goal("Отдохнуть", area=self.personal)
        a = self.result(goal, task_ids=[work["id"], probe["id"]], request_id="a")
        b = self.result(goal, "Срок ответа", task_ids=[probe["id"]])
        c = self.result(goal, "Время обратной связи")
        self.result(other, task_ids=[work["id"]])
        legacy = self.kr.create("Отдельный результат", objective=goal["title"], direction=self.area, unit="ч",
                                task_ids=[work["id"]])
        board = self.kr.board(self.q4["id"])
        self.assertTrue(board["hierarchyRecorded"])
        self.assertFalse(board["historical"])
        self.assertEqual({o["id"] for o in board["objectives"]}, {goal["id"], empty["id"]})
        active = next(o for o in board["objectives"] if o["id"] == goal["id"])
        self.assertEqual(active["directionTitle"], "ИТ")
        visible_cards = {path.relative_to(self.root).as_posix() for path in self.kr.card_paths()}
        self.assertTrue({goal["path"], a["path"], b["path"], other["path"], legacy["path"]} <= visible_cards)
        self.assertNotIn(work["path"], visible_cards)
        self.assertEqual({r["id"] for r in active["results"]}, {a["id"], b["id"], c["id"]})
        self.assertNotIn(legacy["id"], [r["id"] for r in active["results"]])
        self.assertEqual([r["id"] for r in board["items"]], [work["id"], personal["id"], probe["id"]])
        self.assertEqual([r["projectTitle"] for r in board["items"]], ["Маяк", None, "Кедр"])
        self.assertEqual(board["gaps"], {"unlinkedTaskIds": [personal["id"]], "missingTaskIds": [],
                                         "emptyObjectiveIds": [empty["id"]], "emptyResultIds": [c["id"]]})
        self.assertTrue(all(path.read_bytes() == initial for path, initial in before.items()))
        self.assertEqual([p["id"] for p in self.plan.plans_for_task(probe["id"])], [self.q4["id"]])
        affected = Deadlines(self.root).preview(probe["id"], "2026-12-05")
        self.assertEqual([p["id"] for p in affected["plans"]], [self.q4["id"]])
        (self.root / probe["path"]).unlink()
        board = self.kr.board(self.q4["id"])
        self.assertEqual(board["gaps"]["missingTaskIds"], [probe["id"]])
        self.assertEqual(sum(t.get("missing", False) for t in board["items"]), 1)
        newpath = (self.root / goal["path"]).rename(self.root / "Plans/OKR/Переименовано.md")
        newpath.write_text(newpath.read_text().replace('title: "Подготовить запуск"', 'title: "Подготовить релиз"'))
        self.assertEqual(self.goal(request_id="g1")["title"], "Подготовить релиз")
        self.assertEqual(self.result(goal, task_ids=[work["id"], probe["id"]], request_id="a")["objective"],
                         "Подготовить релиз")
        with self.assertRaises(storage.ConflictError):
            self.goal("Другая цель", request_id="g1")

    def test_assign_legacy_card_preserves_history_manual_text_and_refs(self):
        goal = self.goal()
        wrong = self.goal("Путешествовать", area=self.personal)
        task = self.plan.create_task("Проверка")
        old = self.kr.create("Замер", objective="Исходная формулировка", direction=self.area, unit="мин",
                             task_ids=[task["id"]], target=18)
        measured = self.measure(old, 11)
        file = self.root / measured["path"]
        raw = file.read_text().replace("\n", "\r\n").encode() + "Ручной раздел\r\n".encode()
        file.write_bytes(raw)
        with self.assertRaises(storage.ConflictError):
            self.kr.assign_objective(old["id"], goal["id"], measured["revision"])
        now = self.kr.show(old["id"])
        with self.assertRaises(ValueError):
            self.kr.assign_objective(old["id"], wrong["id"], now["revision"])
        self.assertEqual(raw, file.read_bytes())
        converted = self.kr.assign_objective(old["id"], goal["id"], now["revision"])
        self.assertEqual(converted["history"], measured["history"])
        self.assertEqual(converted["tasks"], measured["tasks"])
        self.assertEqual(converted["id"], old["id"])
        text = file.read_bytes()
        self.assertIn('objective: "Исходная формулировка"'.encode(), text)
        self.assertIn("Ручной раздел\r\n".encode(), text)
        self.assertEqual(converted["planId"], self.q4["id"])
        self.assertEqual(self.kr.assign_objective(old["id"], goal["id"], now["revision"]), converted)
        self.assertEqual(text, file.read_bytes())
        with self.assertRaises(storage.ConflictError):
            self.kr.assign_objective(old["id"], self.goal("Ещё цель")["id"], converted["revision"])

    def measure(self, kr, value):
        line = f"Получено {value} мин."
        path = storage.create(self.root, "note", "Протокол", line)
        source = {"path": path.relative_to(self.root).as_posix(), "line": storage.read_record(path)["body_start"]}
        return self.kr.measure(kr["id"], value=value, date="2026-11-23", source=source, revision=kr["revision"])

    def test_close_freezes_structure_work_dates_and_source_then_accepts_later_live_measurement(self):
        task = self.plan.create_task("Проверка", due="2026-12-02")
        goal = self.goal()
        kr = self.result(goal, task_ids=[task["id"]], target=5)
        pid = self.q4["id"]
        initial = self.cycles.preview(pid)
        self.assertEqual(initial["okrCounts"], {"objectives": 1, "results": 1})
        measured = self.measure(kr, 11)
        with self.assertRaises(storage.ConflictError):
            self.cycles.close(pid, initial["revision"])
        fresh = self.cycles.preview(pid)
        self.cycles.close(pid, fresh["revision"])
        frozen = self.kr.board(pid)
        self.assertTrue(frozen["historical"] and frozen["hierarchyRecorded"])
        self.assertEqual(frozen["items"][0]["due"], "2026-12-02")
        self.assertEqual(frozen["objectives"][0]["results"][0]["measurement"], measured["measurement"])
        with self.assertRaises(storage.ConflictError):
            self.result(goal, "После закрытия")
        later = self.plan.create_task("Новая работа")
        with self.assertRaises(storage.ConflictError):
            self.kr.link(kr["id"], later["id"], measured["revision"])
        self.measure(measured, 7)
        (self.root / goal["path"]).unlink()
        (self.root / task["path"]).unlink()
        self.assertEqual(self.kr.board(pid), frozen)
        self.assertEqual(self.cycles.close(pid, fresh["revision"])["replayed"], True)
        with self.assertRaises(storage.ConflictError):
            self.goal()
        saved = self.root / self.q4["path"]
        self.assertIn("Получено 11 мин.", saved.read_text())
        saved.write_text(saved.read_text().replace("ориентир 5 мин", "ориентир 9 мин", 1))
        with self.assertRaisesRegex(storage.ConflictError, "снимка"):
            self.kr.board(pid)

    def test_preexisting_snapshot_has_no_inferred_goals_from_live_data(self):
        pid = self.q4["id"]
        self.cycles.close(pid, self.cycles.preview(pid)["revision"])
        path, raw, record, header = self.plan._find("plan", pid)
        old_meta = cycles.closed_metadata(record)
        original = storage.comment_json(old_meta)
        old_meta.pop("okr")
        old_header = storage._patch_header(header, record, {cycles.CYCLE_SEAL: cycles._digest([old_meta, []])})
        path.write_bytes(old_header + record["body"].replace(original, storage.comment_json(old_meta)).encode())
        before = path.read_bytes()
        board = self.kr.board(pid)
        self.assertTrue(board["historical"])
        self.assertFalse(board["hierarchyRecorded"])
        self.assertEqual(board["objectives"], [])
        self.assertEqual(path.read_bytes(), before)

    def test_cli_creation_assignment_and_bounded_board_sections(self):
        parser = command_line()
        def cli(*arguments):
            return run(parser.parse_args(["--vault", str(self.root), *arguments]))
        pid = self.q4["id"]
        objective = cli("okr-objective-create", "Сократить время", "--plan", pid, "--direction", self.area,
                        "--request-id", "goal")
        saved = cli("okr-create", "Время", "--objective-id", objective["id"], "--unit", "мин", "--request-id", "kr")
        old = cli("okr-create", "Отдельное", "--objective", "Текст", "--direction", self.area, "--unit", "мин",
                  "--request-id", "legacy")
        attached = cli("okr-assign", old["id"], objective["id"], "--revision", old["revision"])
        task = self.plan.create_task("Перепроверить", project=self.card("project", "Проект", direction=self.area))
        cli("okr-link", old["id"], task["id"], "--revision", attached["revision"])
        page = cli("okr-plan", pid)
        self.assertEqual(page["counts"], {"objectives": 1, "results": 2, "tasks": 1, "gaps": 1})
        self.assertEqual((page["total"], page["items"][0]["resultsTotal"]), (1, 2))
        self.assertNotIn("results", page["items"][0])
        first = cli("okr-plan", pid, "--view", "results", "--objective", objective["id"], "--limit", "1")
        second = cli("okr-plan", pid, "--view", "results", "--offset", "1", "--limit", "1")
        self.assertEqual({r["id"] for r in [*first["items"], *second["items"]]}, {saved["id"], old["id"]})
        self.assertTrue(first["truncated"])
        rows = cli("okr-plan", pid, "--view", "tasks", "--result", old["id"])
        self.assertEqual(rows["items"][0]["id"], task["id"])
        self.assertEqual(cli("okr-plan", pid, "--view", "gaps")["items"][0]["id"], saved["id"])
        self.assertLess(len(json.dumps(first, ensure_ascii=False).encode()), 1800)
        with self.assertRaises(ValueError):
            cli("okr-plan", self.q1["id"], "--view", "tasks", "--result", old["id"])
