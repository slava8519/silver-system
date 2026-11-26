import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from examples.inbox_vault import generate
from second_brain import storage
from second_brain.inbox import Inbox
from second_brain.routing import Routing
from second_brain.vault_check import check_after_intake, check_vault


class VaultCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "vault"

    def test_three_directions_and_renamed_cards_are_clean_with_no_writes(self):
        generate(self.root, with_teams=True)
        original_person = next((self.root / "People").glob("*.md"))
        original_person.rename(original_person.with_name("Другое имя файла.md"))

        def snapshot():
            return {p.relative_to(self.root): (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in self.root.rglob("*") if p.is_file()}

        before = snapshot()
        result = check_vault(self.root)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["checked"]["rules"], 3)
        self.assertGreaterEqual(result["checked"]["receipts"], 3)
        self.assertGreater(result["checked"]["cards"], 10)
        self.assertEqual(result, check_vault(self.root))
        self.assertEqual(snapshot(), before)
        self.assertLess(len(json.dumps(result).encode()), 300)

    def test_partial_vault_and_independent_problems_paginate(self):
        storage.initialize(self.root)
        direction = storage.create(self.root, "direction", "Сад")
        identifier = storage.read_record(direction)["id"]
        rule = Routing(self.root).set_rule(identifier, "Проверять пробы.")
        (self.root / rule["path"]).write_text("сломано", encoding="utf-8")
        (self.root / "Sources/Originals").rmdir()
        result = check_vault(self.root, limit=1)
        second = check_vault(self.root, limit=1, offset=1)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["issues"]["total"], 2)
        self.assertTrue(result["issues"]["truncated"])
        self.assertFalse(second["issues"]["truncated"])
        issues = result["issues"]["items"] + second["issues"]["items"]
        self.assertEqual({issue["code"] for issue in issues}, {"intake_rule_invalid", "vault_folder"})
        self.assertEqual(check_vault(self.root, offset=3)["issues"]["items"], [])

    def test_source_scope_during_write_and_full_cross_check_after_deletion(self):
        storage.initialize(self.root)
        direction = storage.read_record(storage.create(self.root, "direction", "Дом"))["id"]
        path = "Inbox/Договорённость.txt"
        service = Inbox(self.root)
        private_line = "Личная договорённость владельца."
        (self.root / path).write_text(private_line, encoding="utf-8")
        revision = service.preview(path)["revision"]
        result = service.process({"path": path, "revision": revision, "person": {"new": True, "name": "Гость"},
                                  "facts": [{"kind": "fact", "direction": direction, "visibility": "personal",
                                             "text": private_line, "line": 1}]})
        self.assertEqual(result["check"]["status"], "ok")
        source = self.root / result["source"]["path"]
        source.write_text("Другая строка", encoding="utf-8")
        finding = check_vault(self.root)
        self.assertIn("fact_quote_mismatch", {row["code"] for row in finding["issues"]["items"]})
        self.assertNotIn(private_line, json.dumps(finding, ensure_ascii=False))
        source.write_text(private_line, encoding="utf-8")
        (self.root / result["person"]["path"]).unlink()
        self.assertEqual(check_after_intake(self.root, revision)["status"], "ok")
        self.assertIn("receipt_target_missing", {row["code"] for row in check_vault(self.root)["issues"]["items"]})

    def test_empty_initialized_vault_can_be_checked_without_creating_lock_or_index(self):
        storage.initialize(self.root)
        paths = set(self.root.rglob("*"))
        result = check_vault(self.root)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(set(result["checked"].values()), {0})
        self.assertEqual(set(self.root.rglob("*")), paths)
