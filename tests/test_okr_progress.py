import argparse
from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from second_brain import okr_progress_cli, storage
from second_brain.cycles import Cycles
from second_brain.okr_progress import Progress, enrich_board, enrich_card


class WeightedProgress(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name) / "Synthetic")
        self.service = Progress(self.root)
        self.results = self.service.results
        self.planning = self.service.planning
        self.area = storage.read_record(storage.create(self.root, "direction", "Лаборатория"))["id"]
        self.plan = self.planning.create_plan("Квартал")["id"]
        self.goal = self.results.create_objective("Подготовить серию", plan_id=self.plan, direction=self.area)["id"]
        self.parser = argparse.ArgumentParser()
        okr_progress_cli.add_commands(self.parser.add_subparsers(dest="command"))

    def cli(self, *words):
        return okr_progress_cli.run(self.root, self.parser.parse_args(words))

    def create_result(self, tasks, title="Погрешность", goal=None):
        return self.results.create(title, objective_id=goal or self.goal, unit="%", target="5",
                                   task_ids=[t["id"] for t in tasks])

    def task(self, title):
        return self.planning.create_task(title, direction=self.area)

    def weight(self, kr, value, task=None):
        return self.service.set_weight(kr["id"], weight=value, task_id=task["id"] if task else None,
                                       revision=self.results.show(kr["id"])["revision"])

    def reading(self, task, value, *, kind="fact", date="2026-11-23"):
        return self.service.set_readiness(task["id"], value=value, date=date, kind=kind, actor="manual",
                                          reason="Сверка владельца в лаборатории",
                                          revision=self.planning.task(task["id"])["revision"])

    def test_five_contributions_and_a_shared_task_with_distinct_edge_weights(self):
        tasks = [self.task(f"Серия {i}") for i in range(5)]
        kr = self.create_result(tasks)
        self.weight(kr, 1)
        for task, weight, percent in zip(tasks, (".25", ".15", ".10", ".25", ".25"), (0, 0, 80, 100, 70)):
            self.weight(kr, weight, task)
            self.reading(task, percent, kind="estimate" if percent == 70 else "fact")
        g2 = self.results.create_objective("Согласовать образец", plan_id=self.plan, direction=self.area)
        other = self.create_result(tasks[2:3], "Точность", goal=g2["id"])
        self.weight(other, 1, tasks[2])
        self.weight(other, 1)
        board = self.results.board(self.plan)
        goals = {g["id"]: g for g in board["objectives"]}
        first, = goals[self.goal]["results"]
        self.assertEqual(first["progress"]["value"], "50.5")
        self.assertEqual([first["progress"]["contributions"][t["id"]] for t in tasks], ["0", "0", "8", "25", "17.5"])
        self.assertTrue(first["progress"]["hasEstimates"])
        self.assertEqual(first["progress"]["weightSum"], "1")
        self.assertIsNone(first["measurement"])
        self.assertEqual(first["target"], "5")
        self.assertEqual(goals[self.goal]["progress"]["value"], "50.5")
        self.assertEqual(goals[g2["id"]]["progress"]["value"], "80")
        self.assertEqual(goals[g2["id"]]["progress"]["contributions"], {other["id"]: "80"})
        self.assertEqual(board["progress"]["value"], "65.25")
        self.assertEqual(len(board["items"]), 5)
        self.assertTrue(all(t["completed"] is None for t in board["items"]))
        cycles = Cycles(self.root)
        cycles.close(self.plan, cycles.preview(self.plan)["revision"])
        self.reading(tasks[2], 100, date="2026-11-24")
        closed = self.results.board(self.plan)
        self.assertTrue(closed["historical"])
        self.assertEqual(closed["progress"]["value"], "65.25")
        frozen = next(t for t in closed["items"] if t["id"] == tasks[2]["id"])
        self.assertEqual(frozen["readiness"]["value"], "80")
        self.assertEqual(self.service.readiness(tasks[2]["id"])["readiness"]["value"], "100")

    def test_partial_contributions_keep_precision_and_distinguish_zero_from_unknown(self):
        a, b, c, d = [self.task(title) for title in ("Вода", "Спирт", "Смесь", "Эталон")]
        kr = self.create_result([a, b, c, d])
        for task, weight in ((a, ".1"), (b, 0), (c, ".9")):
            self.weight(kr, weight, task)
        self.reading(a, "37.1234567890123456789012")
        self.reading(d, "10")
        expected = {a["id"]: "3.71234567890123456789012", b["id"]: "0", c["id"]: None, d["id"]: None}
        row = self.results.board(self.plan)["objectives"][0]["results"][0]
        self.assertIsNone(row["progress"]["value"])
        self.assertEqual(row["progress"]["contributions"], expected)
        page = self.cli("okr-progress", self.plan, "--result", kr["id"], "--limit", "2")
        self.assertNotIn("contributions", page["progress"])
        self.assertEqual({t["id"]: t["contribution"] for t in page["items"]}, {k: expected[k] for k in (a["id"], b["id"])})
        standalone = self.results.create("Вне квартала", direction=self.area, objective="Тест серии", unit="г",
                                         task_ids=[a["id"], b["id"]])
        self.weight(standalone, ".1", a)
        self.weight(standalone, 0, b)
        card = self.results.show(standalone["id"])
        enrich_card(self.root, card)
        self.assertEqual(card["progress"]["contributions"], {t["id"]: expected[t["id"]] for t in (a, b)})

    def test_old_snapshot_derives_contributions_only_from_its_frozen_readings(self):
        task = self.task("Контроль")
        kr = self.create_result([task])
        self.weight(kr, 1, task)
        self.weight(kr, 1)
        self.reading(task, 40)

        def previous_format(root, view, structure):
            enrich_board(root, view, structure)
            for goal in structure["objectives"]:
                goal["progress"].pop("contributions")
                for result in goal["results"]:
                    result["progress"].pop("contributions")

        with patch("second_brain.okr_progress.enrich_board", previous_format):
            cycles = Cycles(self.root)
            closed = cycles.close(self.plan, cycles.preview(self.plan)["revision"])
        path = self.root / closed["path"]
        original = path.read_bytes()
        self.reading(task, 90, date="2026-11-24")
        goal, = self.results.board(self.plan)["objectives"]
        self.assertEqual(goal["progress"]["contributions"], {kr["id"]: "40"})
        self.assertEqual(goal["results"][0]["progress"]["contributions"], {task["id"]: "40"})
        self.assertEqual(path.read_bytes(), original)

    def test_incomplete_inputs_do_not_fabricate_zero_and_invalid_weights_do_not_write(self):
        first, second = self.task("Первый"), self.task("Второй")
        kr = self.create_result([first, second])
        row = self.results.board(self.plan)["objectives"][0]["results"][0]
        self.assertEqual(row["progress"]["missingWeights"], [first["id"], second["id"]])
        self.assertEqual(row["progress"]["missingValues"], [first["id"], second["id"]])
        self.assertIsNone(row["progress"]["value"])
        self.weight(kr, ".4", first)
        self.weight(kr, ".3", second)
        self.reading(first, 0)
        self.reading(second, 0)
        incomplete = self.cli("okr-progress", self.plan, "--result", kr["id"])["progress"]
        self.assertEqual((incomplete["value"], incomplete["weightSum"]), (None, "0.7"))
        path = self.root / kr["path"]
        before = path.read_bytes()
        for invalid in ("0.61", -1, 2, "nan"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.weight(kr, invalid, second)
        self.assertEqual(before, path.read_bytes())
        self.weight(kr, ".6", second)
        self.weight(kr, 1)
        self.assertEqual(self.results.board(self.plan)["progress"]["value"], "0")
        self.weight(kr, None, second)
        self.assertIsNone(self.results.board(self.plan)["progress"]["value"])
        sibling = self.create_result([], "Другая серия")
        with self.assertRaisesRegex(ValueError, "Сумма весов"):
            self.weight(sibling, ".01")
        self.assertIsNone(self.results.board(self.plan)["progress"]["value"])
        self.weight(sibling, 0)
        self.weight(kr, ".6", second)
        self.assertEqual(self.results.board(self.plan)["progress"]["value"], "0")
        with self.assertRaisesRegex(ValueError, "свяжите"):
            self.weight(sibling, 1, second)

    def test_explicit_owner_basis_protected_history_quote_and_stale_revision(self):
        task = self.task("Приёмка")
        quote = "2026-11-23. Подтверждена готовность 30%."
        path = storage.create(self.root, "note", "Осмотр", quote, direction=self.area)
        ref = {"path": path.relative_to(self.root).as_posix(), "line": storage.read_record(path)["body_start"]}
        args = {"value": 30, "date": "2026-11-23", "kind": "fact", "actor": "ai",
                "revision": task["revision"], "source": ref}
        with self.assertRaisesRegex(ValueError, "источник"):
            self.service.set_readiness(task["id"], **{**args, "source": None})
        with self.assertRaisesRegex(ValueError, "цитате"):
            self.service.set_readiness(task["id"], **{**args, "value": 50})
        first = self.service.set_readiness(task["id"], **args)
        self.assertEqual(self.service.set_readiness(task["id"], **args)["revision"], first["revision"])
        self.assertIn("href", first["readiness"]["source"])
        with self.assertRaises(storage.ConflictError):
            self.service.set_readiness(task["id"], **{**args, "kind": "estimate", "revision": first["revision"]})
        manual = self.reading(task, 50, kind="estimate")
        with self.assertRaises(storage.ConflictError):
            self.service.set_readiness(task["id"], **{**args, "revision": manual["revision"]})
        with self.assertRaises(storage.ConflictError):
            self.service.set_readiness(task["id"], value=60, date="2026-11-24", kind="fact", actor="manual",
                                       reason="Новое подтверждение", revision=first["revision"])
        self.reading(task, 20, date="2026-11-21")
        check = self.service.readiness(task["id"], limit=2)
        self.assertEqual(check["readiness"]["value"], "50")
        self.assertEqual([r["value"] for r in check["items"]], ["50", "30"])
        self.assertEqual(check["total"], 3)
        self.assertTrue(check["truncated"])
        path.rename(path.with_name("Переименованный осмотр.md"))
        history = self.service.readiness(task["id"])
        self.assertIn("Переименованный", history["items"][1]["source"]["path"])
        path.with_name("Переименованный осмотр.md").write_text(
            path.with_name("Переименованный осмотр.md").read_text().replace("готовность 30%", "другая величина"))
        self.assertTrue(self.service.readiness(task["id"])["items"][1]["source"]["unavailable"])
        self.assertEqual(self.cli("task-readiness", task["id"], "--limit", "1", "--offset", "2")["items"][0]["value"],
                         "20")

    def test_parallel_writers_respect_sibling_weights_and_owner_task_revision(self):
        a, b = self.create_result([], "Одна серия"), self.create_result([], "Другая серия")
        task = self.task("Испытание")
        def race(entries, operation):
            barrier = threading.Barrier(len(entries))
            def work(entry):
                barrier.wait(timeout=3)
                try:
                    operation(entry)
                    return "saved"
                except ValueError as error:
                    return str(error)
            with ThreadPoolExecutor(max_workers=len(entries)) as pool:
                return list(pool.map(work, entries))
        answers = race([a, b], lambda kr: self.service.set_weight(kr["id"], weight=".7", revision=kr["revision"]))
        self.assertEqual(answers.count("saved"), 1)
        self.assertTrue(any("Сумма весов" in s for s in answers))
        answers = race([30, 50], lambda v: self.service.set_readiness(
            task["id"], value=v, date="2026-11-23", kind="estimate", revision=task["revision"],
            actor="manual", reason="Проверка владельца"))
        self.assertEqual(answers.count("saved"), 1)
        self.assertTrue(any("изменилась" in s for s in answers))
        self.assertEqual(self.service.readiness(task["id"])["total"], 1)

    def test_cli_weight_revision_manual_updates_and_compact_drilldown(self):
        a, b = self.task("Вода"), self.task("Глина")
        kr = self.create_result([a, b])
        cmd = ("okr-weight", kr["id"], "--task", a["id"])
        first = self.cli(*cmd, "--value", ".4", "--revision", kr["revision"])
        with self.assertRaises(storage.ConflictError):
            self.cli(*cmd, "--value", ".25", "--revision", kr["revision"])
        self.cli(*cmd, "--clear", "--revision", first["revision"])
        self.cli("task-readiness", b["id"], "--date", "2026-11-24", "--kind", "estimate", "--value", "12,5",
                 "--actor", "manual", "--reason", "Проверка опытного образца", "--revision", b["revision"])
        page = self.cli("okr-progress", self.plan, "--result", kr["id"], "--limit", "1", "--offset", "1")
        self.assertEqual((page["total"], len(page["items"])), (2, 1))
        self.assertEqual(page["items"][0]["readiness"]["value"], "12.5")
        self.assertEqual(page["progress"]["missingWeightCount"], 2)
        self.assertNotIn("missingWeights", page["progress"])
        with self.assertRaises(ValueError):
            self.cli("task-readiness", a["id"], "--reason", "Нет величины")


if __name__ == "__main__":
    unittest.main()
