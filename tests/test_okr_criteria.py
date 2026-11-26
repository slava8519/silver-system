from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from second_brain import storage
from second_brain.__main__ import command_line, run
from second_brain.cycles import Cycles
from second_brain.okr import KeyResults
from second_brain.planning import Planning


class CriteriaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name) / "Vault")
        self.area = storage.read_record(storage.create(self.root, "direction", "Мастерская"))["id"]
        self.plan = Planning(self.root).create_plan("Учебный квартал")
        self.service = KeyResults(self.root)
        self.goal = self.service.create_objective("Проверить выпуск", plan_id=self.plan["id"], direction=self.area)
        self.count = 0

    def proof(self, sentence):
        self.count += 1
        path = storage.create(self.root, "note", f"Проверка {self.count}", sentence)
        return {"path": path.relative_to(self.root).as_posix(), "line": storage.read_record(path)["body_start"],
                "quote": sentence}

    def create(self, **kwargs):
        return self.service.create("Готовность", objective_id=self.goal["id"], **kwargs)

    def add(self, kr, name, kind, **kwargs):
        return self.service.add_criterion(kr["id"], title=name, kind=kind, revision=kr["revision"],
                                          request_id=name, **kwargs)

    def observe(self, kr, index, value, date, proof, *, baseline=False):
        return self.service.observe_criterion(kr["id"], kr["criteria"][index]["id"], value=value, date=date,
                                              source=proof, revision=kr["revision"], baseline=baseline)

    def test_primary_baseline_new_measurements_and_boolean_must_all_be_proven(self):
        kr = self.create(unit="%", target=5)
        baseline = {"value": 11, "date": "2026-10-01", "source": self.proof("Доля пропусков 11%.")}
        first = self.add(kr, "Порог ошибок", "numeric", operator="<=", primary=True, baseline=baseline)
        self.assertIsNone(first["criteria"][0]["met"])
        self.assertIsNone(first["measurement"])
        self.assertEqual(first["criteria"][0]["baseline"]["value"], "11")
        self.assertEqual(self.add(kr, "Порог ошибок", "numeric", operator="<=", primary=True, baseline=baseline), first)
        with self.assertRaises(storage.ConflictError):
            self.add(first, "Порог ошибок", "numeric", operator=">=", primary=True)
        with self.assertRaises(ValueError):
            self.add(first, "Другая метрика", "numeric", operator=">=", primary=True)
        mixed = self.add(first, "Проверка на втором стенде", "boolean")
        yes = self.proof("2026-11-22. Второй стенд проверен — да.")
        current = self.observe(mixed, 1, True, "2026-11-22", yes)
        self.assertTrue(current["criteria"][1]["met"])
        self.assertIsNone(current["criteriaMet"])
        proof = self.proof("2026-11-22. Получено 4,2%.")
        current = self.observe(current, 0, "4,2", "2026-11-22", proof)
        self.assertTrue(current["criteriaMet"])
        self.assertEqual(current["measurement"], current["criteria"][0]["measurement"])
        self.assertEqual(current["history"], current["criteria"][0]["history"])
        raw = (self.root / current["path"]).read_bytes()
        self.assertEqual(self.service.measure(current["id"], value=4.2, date="2026-11-22", source=proof,
                                              revision=kr["revision"]), current)
        self.assertEqual((self.root / current["path"]).read_bytes(), raw)
        failed = self.observe(current, 1, False, "2026-11-23", self.proof("Второй стенд проверен — нет."))
        self.assertFalse(failed["criteriaMet"])
        backfilled = self.observe(failed, 1, True, "2026-11-20", self.proof("2026-11-20. Стенд проверен — да."))
        self.assertEqual([o["value"] for o in backfilled["criteria"][1]["history"]], [True, True, False])
        board = self.service.board(self.plan["id"])["objectives"][0]["results"][0]
        self.assertNotIn("history", board["criteria"][0])
        self.assertEqual(board["criteria"][1]["measurement"], failed["criteria"][1]["measurement"])
        self.assertFalse(board["criteriaMet"])

    def test_explicit_comparison_target_revisions_and_previous_thresholds(self):
        kr = self.create(unit="мин", target=20)
        bool_added = self.add(kr, "Протокол согласован", "boolean")
        proof = self.proof("Владелец согласовал выпуск.")
        signed = self.observe(bool_added, 0, True, "2026-11-21", proof)
        self.assertIsNone(signed["criteriaMet"])
        n = self.add(signed, "Порог", "numeric", primary=True, target=15, operator="<=")
        c = n["criteria"][1]
        self.assertEqual((c["targetHistory"][0]["fromTarget"], c["targetHistory"][0]["fromOperator"]), ("20", None))
        seen = self.observe(n, 1, 16, "2026-11-22", self.proof("Проверка заняла 16 мин."))
        self.assertFalse(seen["criteriaMet"])
        change = dict(operator=">=", target=16, revision=seen["revision"])
        updated = self.service.set_criterion_target(n["id"], c["id"], **change)
        self.assertTrue(updated["criteriaMet"])
        self.assertEqual(updated["target"], "16")
        self.assertEqual(updated["criteria"][1]["target"], "16")
        self.assertEqual(updated["criteria"][1]["targetHistory"][-1]["fromOperator"], "<=")
        self.assertEqual(self.service.set_criterion_target(n["id"], c["id"], **change), updated)
        with self.assertRaises(storage.ConflictError):
            self.service.set_criterion_target(n["id"], c["id"], operator="=", target=12, revision=seen["revision"])
        newer = self.service.set_criterion_target(n["id"], c["id"], operator="=", target="16.1", revision=updated["revision"])
        self.assertFalse(newer["criteriaMet"])
        self.assertEqual(len(newer["history"]), 1)

    def test_numeric_source_case_date_baseline_chronology_and_evidence_loss(self):
        kr = self.create()
        cond = self.add(kr, "Мощность", "numeric", unit="мВт", operator=">=", target=8)
        orig = (self.root / cond["path"]).read_bytes()
        for value, date, source in [
            (8, "2026-11-22", self.proof("Было 18 мВт.")),
            (8, "2026-11-22", self.proof("Было 8 МВт.")),
            (8, "2026-11-22", self.proof("2026-11-21. Было 8 мВт.")),
        ]:
            with self.assertRaises(ValueError):
                self.observe(cond, 0, value, date, source)
        self.assertEqual((self.root / cond["path"]).read_bytes(), orig)
        base = self.observe(cond, 0, 6, "2026-11-22", self.proof("Базовая мощность 6 мВт."), baseline=True)
        old = self.observe(base, 0, 9, "2026-11-20", self.proof("До смены стенда было 9 мВт."))
        self.assertIsNone(old["criteriaMet"])
        proof = self.proof("Проверка: 8 мВт.")
        met = self.observe(old, 0, 8, "2026-11-23", proof)
        self.assertTrue(met["criteriaMet"])
        f = self.root / proof["path"]
        moved = f.rename(f.with_name("Новый протокол.md"))
        moved.write_text(moved.read_text().replace(proof["quote"], "Вводная.\n" + proof["quote"]))
        new = self.service.show(kr["id"])["criteria"][0]
        self.assertEqual(new["measurement"]["source"]["line"], proof["line"] + 1)
        self.assertEqual(self.root / new["measurement"]["source"]["path"], moved)
        moved.write_text(moved.read_text().replace("8 мВт", "2 мВт"))
        missing = self.service.show(kr["id"])
        self.assertIsNone(missing["criteriaMet"])
        self.assertTrue(missing["criteria"][0]["measurement"]["source"]["unavailable"])

    def test_qualitative_only_refusals_and_closed_snapshots(self):
        kr = self.create()
        self.assertIsNone(kr["unit"])
        with self.assertRaises(ValueError):
            self.create(target=5)
        with self.assertRaises(ValueError):
            self.service.measure(kr["id"], value=2, date="2026-11-22", source=self.proof("Было 2."), revision=kr["revision"])
        cond = self.add(kr, "Приёмка", "boolean")
        proof = self.proof("2026-11-22. Приёмка — нет.")
        for value, date in [(True, "2026-11-22"), (False, "2026-11-23"), ("false", "2026-11-22")]:
            with self.assertRaises(ValueError):
                self.observe(cond, 0, value, date, proof)
        false = self.observe(cond, 0, False, "2026-11-22", proof)
        cycles = Cycles(self.root)
        pid = self.plan["id"]
        cycles.close(pid, cycles.preview(pid)["revision"])
        frozen = self.service.board(pid)
        self.observe(false, 0, True, "2026-11-24", self.proof("Приёмка — да."))
        self.assertTrue(self.service.show(kr["id"])["criteriaMet"])
        self.assertEqual(self.service.board(pid), frozen)
        with self.assertRaises(storage.ConflictError):
            self.add(self.service.show(kr["id"]), "Позднее соглашение", "boolean")

    def test_boolean_source_accepts_observations_and_disambiguates_explicit_answers(self):
        kr = self.add(self.create(), "В протоколе не осталось ошибок", "boolean")
        confirmed = self.observe(kr, 0, True, "2026-11-22", self.proof("В протоколе ошибок нет."))
        self.assertTrue(confirmed["criteriaMet"])
        two = self.proof("Приёмка — да. Повторная поставка — нет.")
        with self.assertRaises(ValueError):
            self.observe(confirmed, 0, True, "2026-11-23", two)
        with self.assertRaises(ValueError):
            self.observe(confirmed, 0, True, "2026-11-23", {**two, "quote": "Повторная поставка — нет."})
        result = self.observe(confirmed, 0, True, "2026-11-23", {**two, "quote": "Приёмка — да."})
        self.assertTrue(result["criteriaMet"])

    def test_cli_preservation_history_and_conflicting_writes(self):
        kr = self.create()
        path = self.root / kr["path"]
        contents = path.read_text() + "\nРучные уточнения владельца.\n"
        path.write_bytes(contents.replace("\n", "\r\n").encode())
        kr = self.service.show(kr["id"])
        parser = command_line()
        def cli(*args):
            return run(parser.parse_args(["--vault", str(self.root), *args]))
        created = cli("okr-criterion-add", kr["id"], "Оценка", "--kind", "numeric", "--unit", "ч",
                      "--operator", "=", "--target", "2", "--request-id", "k1", "--revision", kr["revision"])
        cid = created["criterion"]["id"]
        proof = self.proof("Оценка 2 ч.")
        args = ("okr-criterion-measure", kr["id"], cid, "--date", "2026-11-22", "--value", "2", "--source",
                proof["path"], "--line", str(proof["line"]), "--quote", proof["quote"], "--revision", created["revision"])
        observed = cli(*args)
        self.assertEqual(cli(*args), observed)
        goals = cli("okr-show", kr["id"], "--criteria", "--limit", "1")
        self.assertTrue(goals["items"][0]["met"])
        self.assertEqual(goals["total"], 1)
        self.assertNotIn("history", goals["items"][0])
        hist = cli("okr-criterion-show", kr["id"], cid, "--history", "measurements", "--limit", "1")
        self.assertEqual(hist["items"][0]["source"]["quote"], proof["quote"])
        def write(value):
            return self.service.set_criterion_target(kr["id"], cid, operator="<=", target=value, revision=observed["revision"])
        with ThreadPoolExecutor(2) as pool:
            writes = [pool.submit(write, value) for value in (3, 4)]
        self.assertEqual(sum(isinstance(w.exception(), storage.ConflictError) for w in writes), 1)
        raw = path.read_bytes()
        self.assertIn("Ручные уточнения владельца.\r\n".encode(), raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        path.write_bytes(raw.replace("· = 2 ч".encode(), "· = 20 ч".encode(), 1))
        with self.assertRaises(storage.ConflictError):
            self.service.show(kr["id"])
