import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from second_brain import storage


class BriefCommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.vault = storage.initialize(Path(temp.name) / "Полевые заметки")
        self.area = self.create("direction", "Наблюдения")
        self.other = self.create("direction", "Дневник")

    def create(self, kind, title, body="", **options):
        return storage.read_record(storage.create(self.vault, kind, title, body, **options))["id"]

    def call(self, *args, ok=True):
        result = subprocess.run([sys.executable, "-m", "second_brain", "--vault", str(self.vault), *args],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout
        self.assertNotEqual(result.returncode, 0)
        return result.stderr

    def values(self, *args):
        return json.loads(self.call(*args))

    def test_ask_keeps_source_refs_and_reports_partial_results(self):
        decision = "Решили собрать образцы. " + "Обоснование выбора. " * 120 + "Конец решения."
        bird = self.create("project", "Экспедиция", decision, aliases=["Сокол"], direction=self.area)
        frog = self.create("project", "Водоём", "Решили отметить приток.", aliases=["Тритон"], direction=self.other)
        question = "Что решили по Соколу и Тритону?"
        full = self.values("ask", question)
        brief = self.values("ask", question, "--brief", "--limit", "1")
        self.assertEqual(brief["total"], 2)
        self.assertTrue(brief["truncated"])
        item, = brief["items"]
        self.assertEqual((item["id"], item["label"]), (bird, "Сокол"))
        self.assertTrue(item["answerTruncated"])
        self.assertTrue(decision.startswith(item["answer"]))
        self.assertLessEqual(len(item["answer"]), 600)
        self.assertNotIn("Конец решения.", json.dumps(brief, ensure_ascii=False))
        source = item["source"]
        self.assertEqual(set(source), {"path", "start", "end"})
        self.assertEqual(source["start"], full["items"][0]["source"]["start"])
        self.assertIn(decision, self.call("read", source["path"], str(source["start"]), str(source["end"])))
        scoped = self.values("ask", question, "--direction", self.other, "--brief")
        self.assertEqual([i["id"] for i in scoped["items"]], [frog])
        self.assertTrue(scoped["message"])
        absent = self.values("ask", "Где ананас?", "--brief")
        self.assertEqual(absent["items"], [])
        self.assertTrue(absent["message"])
        for value in ("0", "21"):
            self.call("ask", question, "--brief", "--limit", value, ok=False)

    def test_bounded_discovery_and_scoped_counts(self):
        for i in range(8):
            self.create("project", "Прогулка " + str(i), "Текст вне списка.", aliases=["Полёт " + str(i)],
                        direction=self.area)
        self.create("note", "Полёт в дневнике", direction=self.other)
        scoped = self.values("list", "--type", "project", "--direction", self.area, "--match", "ПОЛЕТ")
        self.assertEqual((len(scoped["items"]), scoped["total"], scoped["truncated"]), (5, 8, True))
        tail = self.values("list", "--type", "project", "--direction", self.area, "--match", "ПОЛЕТ", "--offset", "5")
        self.assertEqual((len(tail["items"]), tail["truncated"]), (3, False))
        self.assertTrue(all(r["type"] == "project" and r["direction"] == self.area for r in scoped["items"]))
        complete = self.values("list", "--type", "project", "--match", "ПРОГУЛКА", "--limit", "20")
        self.assertEqual(len(complete["items"]), 8)
        self.assertFalse(complete["truncated"])
        self.assertIsInstance(self.values("list", "--direction", self.area), list)
        global_counts = self.values("overview", "--brief")
        self.assertEqual(global_counts, {"name": self.vault.name, "counts": {"project": 8, "direction": 2, "note": 1}})
        area = self.values("overview", "--brief", "--direction", self.area)
        self.assertEqual(area["counts"], {"project": 8, "direction": 1, "note": 0})
        self.assertEqual(area["direction"]["id"], self.area)
        self.call("overview", "--brief", "--direction", complete["items"][0]["id"], ok=False)

    def test_folders_are_paginated_without_disclosing_card_names(self):
        self.create("project", "Авторский проект", direction=self.area)
        (self.vault / "Specimens").mkdir()
        (self.vault / ".settings").mkdir()
        full = self.values("overview")
        first = self.values("overview", "--folders", "--limit", "2")
        self.assertEqual(first, {"items": full["folders"][:2], "total": len(full["folders"]), "truncated": True})
        rest = self.values("overview", "--folders", "--limit", "20", "--offset", "2")
        self.assertFalse(rest["truncated"])
        folders = first["items"] + rest["items"]
        self.assertEqual(folders, full["folders"])
        self.assertIn({"path": "Projects", "count": 1}, folders)
        self.assertIn({"path": "Specimens", "count": 0}, folders)
        self.assertNotIn("Авторский проект", json.dumps(folders, ensure_ascii=False))
        self.call("overview", "--folders", "--offset", "-1", ok=False)

    def test_brief_revision_can_update_and_clear_fields_after_idempotent_create(self):
        command = ("create", "project", "Пересчитать птиц", "--status", "Наблюдаем", "--next-step", "Взять бинокль.",
                   "--due", "2026-12-01", "--body", "Только в теле. " * 300, "--request-id", "bird-counting")
        first = self.values(*command)
        before = self.values("show", first["id"], "--brief")
        self.assertNotIn("Только в теле", json.dumps(before, ensure_ascii=False))
        source = before["fieldSources"]["status"]
        self.assertIn("Наблюдаем", self.call("read", source["path"], str(source["line"]), str(source["line"])))
        self.values("update", first["id"], "--revision", before["revision"], "--clear-status", "--clear-due",
                    "--clear-next-step")
        self.assertEqual(first, self.values(*command))
        updated = self.values("show", first["id"], "--brief")
        self.assertNotEqual(updated["revision"], before["revision"])
        for key in ("status", "due", "nextStep"):
            self.assertIsNone(updated[key])
            self.assertNotIn(key, updated["fieldSources"])
        self.call("update", first["id"], "--revision", before["revision"], "--status", "Устарело", ok=False)
        self.assertEqual(len(self.values("list", "--type", "project")["items"]), 1)
