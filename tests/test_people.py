import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from second_brain import storage
from second_brain.people import People


class PeopleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = storage.initialize(Path(self.temp.name) / "Notebook")
        self.people = People(self.root)
        self.studio = storage.read_record(storage.create(self.root, "direction", "Мастерская"))["id"]
        self.garden = storage.read_record(storage.create(self.root, "direction", "Сад"))["id"]
        self.source = {"id": "a" * 64, "path": "Sources/Originals/" + "a" * 64 + ".md"}
        (self.root / self.source["path"]).write_text("Куратор витрин\nВедёт обмен семенами\nНужна тихая комната\n")
        self.facts = [
            {"kind": "role", "direction": self.studio, "visibility": "shared", "line": 1, "text": "Куратор витрин"},
            {"kind": "role", "direction": self.garden, "visibility": "shared", "line": 2, "text": "Ведёт обмен семенами"},
            {"kind": "observation", "direction": self.garden, "visibility": "personal", "line": 3,
             "text": "Нужна тихая комната"},
        ]
        self.identity = {"name": "Рен Вера", "aliases": ["Рэн"], "new": True}
        with storage._write_lock(self.root):
            self.person = self.people.resolve(self.identity, self.source["id"])

    def apply(self, facts=None):
        with storage._write_lock(self.root):
            return self.people.apply(self.person, self.source, self.facts if facts is None else facts)

    def test_shared_fields_and_candidates_never_project_a_personal_observation(self):
        self.apply()
        public = self.people.detail(self.person["id"])
        private = self.people.detail(self.person["id"], personal=True)
        self.assertEqual([f["kind"] for f in public["facts"]], ["role", "role"])
        self.assertEqual([f["text"] for f in private["facts"]], [x["text"] for x in self.facts])
        self.assertNotIn("тихая комната", json.dumps(public, ensure_ascii=False))
        found = list(self.people.shared_facts([self.garden]))
        self.assertEqual([f["text"] for f in found], ["Ведёт обмен семенами"])
        self.assertEqual(found[0]["personId"], self.person["id"])
        self.assertEqual(found[0]["aliases"], ["Рэн"])
        self.assertEqual(self.people.candidates("рэн", direction_id=self.garden), [self.person])
        for f in found:
            self.assertEqual((self.root / f["path"]).read_text().splitlines()[f["cardLine"] - 1], "> " + f["text"])
        private_fact = private["facts"][-1]
        change = {"id": self.person["id"], "factId": private_fact["id"], "revision": private["revision"],
                  "text": "Предпочитает тишину"}
        for implicit in [False, "true", 1]:
            with self.assertRaises(ValueError):
                self.people.correct({**change, "personal": implicit})
        self.people.correct({**change, "personal": True})
        self.assertNotIn("тишину", json.dumps(list(self.people.shared_facts()), ensure_ascii=False))

    def test_replay_manual_changes_correction_history_and_conflict(self):
        full = self.apply()
        path = self.root / self.person["path"]
        first_bytes = path.read_bytes()
        self.assertEqual(self.apply()["revision"], full["revision"])
        self.assertEqual(path.read_bytes(), first_bytes)
        role = full["facts"][0]
        text = path.read_text().replace("> Куратор витрин", "> Куратор экспозиции")
        path.write_text(text + "\nМоя заметка вне блока.\n")
        direct = self.people.detail(self.person["id"])
        observed = direct["facts"][0]
        self.assertEqual(observed["text"], "Куратор экспозиции")
        self.assertTrue(observed["editedInFile"])
        self.assertEqual(observed["history"], [{"action": "imported", "text": role["quote"]}])
        change = {"id": self.person["id"], "factId": role["id"], "revision": full["revision"], "text": "Куратор зала"}
        with self.assertRaises(storage.ConflictError):
            self.people.correct(change)
        reconciled = self.apply()
        self.assertEqual(reconciled["facts"][0]["text"], observed["text"])
        self.assertFalse(reconciled["facts"][0]["editedInFile"])
        self.assertEqual(self.apply()["revision"], reconciled["revision"])
        fixed = self.people.correct({**change, "revision": reconciled["revision"]})
        self.assertEqual(fixed["facts"][0]["history"], [
            {"action": "imported", "text": role["quote"]}, {"action": "observed", "text": observed["text"]}])
        self.assertEqual(fixed["facts"][0]["sourceId"], self.source["id"])
        self.assertEqual(fixed["facts"][0]["quote"], "Куратор витрин")
        self.assertIn("Моя заметка вне блока.", path.read_text())
        self.assertEqual(self.apply()["revision"], fixed["revision"])
        self.assertEqual(self.people.detail(self.person["id"])["facts"], fixed["facts"])

    def test_identity_is_explicit_reusable_and_survives_renames(self):
        with storage._write_lock(self.root):
            with self.assertRaises(ValueError):
                self.people.resolve({"name": self.identity["name"]}, self.source["id"])
            self.assertEqual(self.people.resolve(self.identity, self.source["id"]), self.person)
            other = self.people.resolve(self.identity, "b" * 64)
            self.assertNotEqual(other["id"], self.person["id"])
            self.assertEqual(self.people.resolve({"id": self.person["id"]}, "b" * 64), self.person)
        self.assertEqual({c["id"] for c in self.people.candidates("рэн")}, {other["id"], self.person["id"]})
        moved = self.root / "People" / "Renamed.md"
        (self.root / self.person["path"]).rename(moved)
        moved.write_text(moved.read_text().replace('title: "Рен Вера"', 'title: "Вера Р."'))
        with storage._write_lock(self.root):
            again = self.people.resolve(self.identity, self.source["id"])
        self.assertEqual((again["id"], again["name"], again["path"]), (self.person["id"], "Вера Р.", "People/Renamed.md"))
        (moved.parent / ".hidden.md").write_text("Private")
        self.assertEqual(set(self.people.paths()), {moved, self.root / other["path"]})
        (moved.parent / "Index.md").write_text("# Мои контакты")
        self.assertEqual(set(self.people.card_paths()), {moved, self.root / other["path"]})
        self.assertEqual(self.people.candidates(limit=1, offset=1), self.people.candidates()[1:2])
        self.assertEqual(self.people.candidates(direction_id=self.studio), [])
        self.apply([self.facts[-1]])
        self.assertEqual(self.people.candidates(direction_id=self.garden), [])

    def test_changed_visibility_and_duplicate_ids_fail_closed(self):
        full = self.apply()
        with self.assertRaises(storage.ConflictError):
            self.apply([{**self.facts[0], "visibility": "personal"}])
        self.assertEqual(self.people.detail(self.person["id"])["revision"], full["revision"])
        path = self.root / self.person["path"]
        data = path.read_text()
        path.write_text(data.replace('"visibility":"personal"', '"visibility":"typo"'))
        with self.assertRaises(ValueError):
            list(self.people.shared_facts())
        path.write_text(data)
        (path.parent / "Copy.md").write_text(data)
        with self.assertRaises(ValueError):
            self.people.detail(self.person["id"])


if __name__ == "__main__":
    unittest.main()
