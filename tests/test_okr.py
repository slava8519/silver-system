from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote
import unittest

from second_brain import storage
from second_brain.okr import KeyResults
from second_brain.planning import Planning
from second_brain.query import WorkspaceQuery


class KeyResultsTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.plans = Planning(self.root)
        self.results = KeyResults(self.root)
        self.area = storage.read_record(storage.create(self.root, "direction", "Работа"))["id"]

    def create(self, **options):
        return self.results.create("Время восстановления", objective="Подготовить переключение", unit="мин",
                                   direction=self.area, **options)

    def source(self, text, title="Протокол замера"):
        path = storage.create(self.root, "note", title, text, direction=self.area)
        record = storage.read_record(path)
        return {"path": path.relative_to(self.root).as_posix(), "line": record["body_start"], "quote": text}

    def snapshot(self):
        return {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*.md")}

    def test_enabling_adds_one_card_without_touching_tasks_or_plans(self):
        task = self.plans.create_task("Проверить восстановление", direction=self.area)
        plans = [self.plans.create_plan(name) for name in ("Неделя", "Запуск")]
        for plan in plans:
            self.plans.add_task(plan["id"], task["id"])
        before = self.snapshot()
        self.assertEqual(self.results.list(), [])
        self.assertFalse((self.root / "Plans/OKR").exists())
        result = self.create(target="15,00", task_ids=[task["id"]], request_id="first")
        self.assertIsNone(result["measurement"])
        self.assertEqual(result["history"], [])
        self.assertEqual((result["target"], result["unit"]), ("15", "мин"))
        self.assertEqual(result["tasks"], [{k: task[k] for k in ("id", "title", "path")}])
        after = self.snapshot()
        self.assertEqual(set(after) - set(before), {Path(result["path"])})
        self.assertTrue(all(after[p] == content for p, content in before.items()))
        self.assertEqual({p["id"] for p in self.plans.plans()}, {p["id"] for p in plans})
        md = unquote((self.root / result["path"]).read_text())
        self.assertIn("../../" + task["path"], md)
        self.assertIn("<!-- task-id: " + task["id"], md)
        for plan in plans:
            self.assertEqual(self.plans.view(plan["id"])["items"][0]["id"], result["tasks"][0]["id"])
        self.assertEqual(self.results.list(self.area)[0]["id"], result["id"])
        self.assertNotIn("history", self.results.list()[0])
        self.assertNotIn("tasks", self.results.list()[0])
        self.assertEqual(self.results.list("another-direction"), [])

    def test_owner_clears_and_revises_the_optional_target_without_affecting_measurements(self):
        result = self.create(target=15)
        proof = self.source("2026-11-22. Время — 18 мин.")
        measured = self.results.measure(result["id"], value=18, date="2026-11-22",
                                        source=proof, revision=result["revision"])
        path = self.root / result["path"]
        original = path.read_text()
        for scalar in ("", "null", "~  # снято", '""', "''", "# до уточнения"):
            with self.subTest(scalar=scalar):
                path.write_text(original.replace('target: "15"', "target: " + scalar))
                summary, = self.results.list()
                self.assertIsNone(summary["target"])
                self.assertIsNone(summary["targetLine"])
                self.assertEqual(self.results.show(result["id"])["history"], measured["history"])
        for scalar in ("неизвестно", '"null"', "[10, 20]", "\n  - 10", "|\n  15", "10\n  20"):
            with self.subTest(scalar=scalar), self.assertRaises(ValueError):
                path.write_text(original.replace('target: "15"', "target: " + scalar))
                self.results.show(result["id"])
        path.write_text(original.replace('target: "15"', "target: 12.500 # новый ориентир"))
        revised = self.results.show(result["id"])
        self.assertEqual(revised["target"], "12.5")
        self.assertEqual(revised["history"], measured["history"])

    def test_idempotent_creation_after_rename_and_explicit_link(self):
        task = self.plans.create_task("Подготовить копию")
        result = self.create(request_id="first")
        linked = self.results.link(result["id"], task["id"], result["revision"])
        self.assertEqual(self.results.link(result["id"], task["id"], result["revision"]), linked)
        path = self.root / result["path"]
        new_name = path.rename(path.with_name("Критерий готовности.md"))
        self.assertEqual(self.root / self.create(request_id="first")["path"], new_name)
        with self.assertRaises(storage.ConflictError):
            self.create(request_id="first", target=20)
        tp = self.root / task["path"]
        updated = tp.rename(tp.with_name("Подготовка копии.md"))
        self.assertEqual(self.root / self.results.show(result["id"])["tasks"][0]["path"], updated)
        updated.unlink()
        self.assertEqual(self.results.show(result["id"])["tasks"][0],
                         {"id": task["id"], "title": None, "path": None, "missing": True})

    def test_source_citation_replay_backfill_and_same_day_correction(self):
        result = self.create()
        identifier = result["id"]
        line = "2026-11-22. Результат второго замера — 18,50 мин."
        source = self.source(line)
        def write(card, value, date, source):
            return self.results.measure(identifier, value=value, date=date, source=source, revision=card["revision"])
        first = write(result, 18.5, "2026-11-22", source)
        self.assertEqual(first, write(result, "18.5000", "2026-11-22", source))
        early = write(first, 24, "2026-11-20", self.source("2026-11-20. Время — 24 мин."))
        changed = write(early, 17, "2026-11-22", self.source("2026-11-22. Уточнено — 17 мин."))
        self.assertEqual(changed["measurement"]["value"], "17")
        self.assertEqual([(h["date"], h["value"]) for h in changed["history"]],
                         [("2026-11-20", "24"), ("2026-11-22", "18.5"), ("2026-11-22", "17")])
        md = (self.root / changed["path"]).read_text()
        self.assertLess(md.index("· 17 мин ·"), md.index("· 18.5 мин ·"))
        self.assertLess(md.index("· 18.5 мин ·"), md.index("· 24 мин ·"))
        proof = first["measurement"]["source"]
        self.assertEqual((proof["quote"], proof["line"], proof["path"]), (line, source["line"], source["path"]))
        path = self.root / source["path"]
        path.write_text(path.read_text().replace(line, "Дополнение.\n" + line))
        moved = path.rename(path.with_name("Исторический протокол.md"))
        _, resolved = WorkspaceQuery(self.root).open_citation(proof)
        self.assertEqual((self.root / resolved["path"], resolved["start"]), (moved, source["line"] + 1))
        current = self.results.show(identifier)["history"][1]["source"]
        self.assertEqual((self.root / current["path"], current["line"]), (moved, source["line"] + 1))
        moved.write_text(moved.read_text().replace("18,50", "18,75"))
        with self.assertRaises(storage.ConflictError):
            WorkspaceQuery(self.root).open_citation(proof)
        historical = self.results.show(identifier)["history"][1]
        self.assertEqual(historical["value"], "18.5")
        self.assertEqual(historical["source"], {**proof, "unavailable": True})

    def test_unsupported_value_date_and_invalid_source_fail_without_writes(self):
        result = self.create()
        source = self.source("2026-11-22. Было 118 мин. Отдельно зафиксированы 18 с и −8 мин.")
        expected = self.snapshot()
        for value, date, proof in [
            (18, "2026-11-22", source), (8, "2026-11-22", source),
            (-8, "2026-11-20", source), (-8, None, source), (-8, "2026-02-30", source),
            ("NaN", "2026-11-22", source), (True, "2026-11-22", source),
            (-8, "2026-11-22", None), (-8, "2026-11-22", {**source, "quote": "Время — −8 мин."}),
            (-8, "2026-11-22", {**source, "path": "Notes/../Plans/OKR/.."}),
        ]:
            with self.subTest(value=value, date=date, source=proof), self.assertRaises(ValueError):
                self.results.measure(result["id"], value=value, date=date, source=proof, revision=result["revision"])
            self.assertEqual(self.snapshot(), expected)
        zero = self.source("Измерение шума — 0 мин.")
        self.assertEqual(self.results.measure(result["id"], value="0,00", date="2026-11-20",
                                             source=zero, revision=result["revision"])["measurement"]["value"], "0")

    def test_complete_quantity_respects_case_grouping_sign_and_date_even_with_a_short_quote(self):
        result = self.results.create("Мощность", objective="Проверить питание", unit="мВт", direction=self.area)
        def measure(value, text, quote=None):
            source = self.source(text)
            if quote is not None:
                source["quote"] = quote
            return self.results.measure(result["id"], value=value, date="2026-11-22",
                                        source=source, revision=result["revision"])
        for value, text, quote in [
            ("18", "22 ноября 2026. Измерено 18 МВт.", None),
            ("18", "21 ноября 2026. Измерено 18 мВт.", "18 мВт"),
            ("18", "21.11.2026. Измерено 18 мВт.", "18 мВт"),
            ("18", "2026-11-21. Измерено 18 мВт.", "18 мВт"),
            ("18", "Измерено −18 мВт.", "18 мВт"),
            ("18", "Измерено 118 мВт.", "18 мВт"),
            ("200", "Измерено 1 200 мВт.", "200 мВт"),
            ("22", "В таблице 2026-11-22 мВт.", "22 мВт"),
        ]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                measure(value, text, quote)
        self.assertEqual(measure("1200,5", "22 ноября 2026. Измерено 1 200,50 мВт.")["measurement"]["value"], "1200.5")

    def test_concurrent_measurements_and_preserving_owner_text(self):
        result = self.create()
        path = self.root / result["path"]
        manual = (path.read_text().replace("schema_version: 1", "custom: [one, two]\nschema_version: 1")
                  + "\n## Обсуждение\nТекст владельца\n").replace("\n", "\r\n").encode()
        path.write_bytes(manual)
        proof = self.source("2026-11-22. Замеры — 2 мин и 4 мин.")
        def measure(value, revision):
            return self.results.measure(result["id"], value=value, date="2026-11-22", source=proof, revision=revision)
        with self.assertRaises(storage.ConflictError):
            measure(2, result["revision"])
        self.assertEqual(path.read_bytes(), manual)
        revision = self.results.show(result["id"])["revision"]
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(measure, v, revision) for v in (2, 4)]
        self.assertEqual(sum(isinstance(f.exception(), storage.ConflictError) for f in futures), 1)
        saved = path.read_bytes()
        self.assertEqual(b"".join(l for l in saved.splitlines(keepends=True) if b"<!-- okr-source:" not in l), manual)
        # A spontaneous manual rewrite of the visible measurement has to agree with the original quote.
        path.write_bytes(saved.replace(b"2026-11-22 \xc2\xb7 ", b"2026-11-22 \xc2\xb7 123"))
        with self.assertRaises(storage.ConflictError):
            self.results.show(result["id"])

    def test_invalid_links_and_duplicate_ids_fail_before_publishing(self):
        with self.assertRaises(FileNotFoundError):
            self.create(task_ids=["missing"])
        self.assertFalse((self.root / "Plans/OKR").exists())
        with self.assertRaises(ValueError):
            self.create(target=float("inf"))
        result = self.create()
        path = self.root / result["path"]
        path.with_name("Копия.md").write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, "ID"):
            self.results.show(result["id"])
