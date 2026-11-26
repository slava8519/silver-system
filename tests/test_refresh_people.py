import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from second_brain import storage
from second_brain.inbox import Inbox
from second_brain.people import People
from second_brain.refresh_people import refresh_people
from second_brain.teams import Teams


class RefreshPeopleTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name) / "Vault")
        self.people = People(self.root)
        self.inbox = Inbox(self.root)

    def add(self, area, index):
        direction = storage.read_record(storage.create(self.root, "direction", area))["id"]
        public = f"Ведёт проверку {index}."
        personal = "Личное наблюдение."
        data = f"{public}\n{personal}\n".encode()
        name = f"Встреча {index}.txt"
        (self.root / "Inbox" / name).write_bytes(data)
        receipt = self.inbox.process({
            "path": "Inbox/" + name, "revision": hashlib.sha256(data).hexdigest(),
            "person": {"new": True, "name": "Участник " + str(index)},
            "facts": [{"line": 1, "text": public, "visibility": "shared", "kind": "role", "direction": direction},
                      {"line": 2, "text": personal, "visibility": "personal", "kind": "observation",
                       "direction": direction}],
        })
        path = self.root / receipt["person"]["path"]
        path.write_text(path.read_text().replace("> " + public, "> Сверяет результат.")
                        .replace("> " + personal, "> Уточнено лично."))
        return path, receipt

    def refresh(self):
        with storage._write_lock(self.root):
            return refresh_people(self.root)

    def test_shared_edits_are_observed_once_and_originals_and_private_notes_are_preserved(self):
        entries = [self.add(area, index) for index, area in enumerate(("ИТ", "Личное", "Парфюмерия"), 1)]
        group = Teams(self.root).create("Группа", request_id="group")
        first = self.refresh()
        self.assertIn(first.pop("example")["id"], [receipt["personId"] for _, receipt in entries])
        self.assertEqual(first, {"examined": 3, "changed": 3, "historyAdded": 3, "issues": []})
        after = [path.read_bytes() for path, _ in entries]
        self.assertEqual(self.refresh()["historyAdded"], 0)
        self.assertEqual(after, [path.read_bytes() for path, _ in entries])
        for index, (_, receipt) in enumerate(entries, 1):
            person = receipt["personId"]
            public, private = self.people.detail(person, personal=True)["facts"]
            self.assertEqual(public["text"], "Сверяет результат.")
            self.assertEqual(public["history"], [{"action": "imported", "text": f"Ведёт проверку {index}."}])
            self.assertFalse(public["editedInFile"])
            self.assertEqual(self.inbox.fact_evidence(person, public["id"])["text"], public["quote"])
            self.assertEqual(private["text"], "Уточнено лично.")
            self.assertTrue(private["editedInFile"])
        self.assertEqual(Teams(self.root).show(group["id"])["members"], [])

    def test_invalid_origin_and_duplicate_identity_do_not_create_history(self):
        lost_path, lost = self.add("Дом", 1)
        dup_path, _ = self.add("Клуб", 2)
        twin = dup_path.with_name("Вторая карточка.md")
        twin.write_bytes(dup_path.read_bytes())
        (self.root / lost["source"]["path"]).write_text("Текст оригинала изменён.")
        files = [lost_path, dup_path, twin]
        before = [path.read_bytes() for path in files]
        result = self.refresh()
        self.assertEqual((result["examined"], result["changed"], result["historyAdded"]), (3, 0, 0))
        self.assertEqual(len(result["issues"]), 2)
        self.assertNotIn("Уточнено лично", str(result))
        self.assertEqual(before, [path.read_bytes() for path in files])

    def test_people_without_inbox_in_an_older_vault_can_still_be_examined(self):
        path, _ = self.add("Дом", 1)
        (self.root / "Inbox").rename(self.root / "OldInbox")
        Teams(self.root).create_person("Вторая участница", request_id="extra")
        before = path.read_bytes()
        result = self.refresh()
        self.assertEqual((result["examined"], result["changed"], result["historyAdded"]), (2, 0, 0))
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["issues"][0]["path"], path.relative_to(self.root).as_posix())
        self.assertEqual(before, path.read_bytes())
