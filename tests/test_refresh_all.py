import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from examples.gantt_vault import generate
from second_brain import project_updates, search, storage
from second_brain.gantt import Gantt
from second_brain.inbox import Inbox
from second_brain.planning import Planning
from second_brain.refresh import refresh_all


class RefreshAllTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve() / "Practice"

    def cli(self, *arguments):
        output = subprocess.check_output(
            [sys.executable, "-m", "second_brain", "--vault", str(self.root), "refresh-all", *arguments],
            cwd=Path(__file__).resolve().parents[1], text=True)
        return json.loads(output)

    def markdown(self):
        return {p.relative_to(self.root): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*.md")}

    def test_one_command_updates_three_directions_and_retries_without_writes(self):
        generate(self.root, with_project_updates=True)
        directions = [record for _, record in storage.iter_records(self.root) if record["type"] == "direction"]
        projects = []
        for area in directions:
            path = storage.create(self.root, "project", "Проба " + area["title"],
                                  body="Решили провести сверку.\n", direction=area["id"])
            projects.append(storage.read_record(path)["id"])
        task = Planning(self.root).create_task("Сверить один экземпляр")
        statement = "2026-11-26. Экземпляр сверили с оригиналом."
        note = storage.create(self.root, "note", "Результат сверки", body=statement)
        source = {"path": note.relative_to(self.root).as_posix(), "line": storage.read_record(note)["body_start"],
                  "quote": statement}
        Gantt(self.root).edit({"id": task["id"], "revision": task["revision"], "actor": "ai", "field": "latest",
                               "text": "Экземпляр проверен.", "date": "2026-11-26", "sources": [source]})
        inbox = Inbox(self.root)
        original = "Ведёт проверку."
        (self.root / "Inbox" / "Роли.md").write_text(original)
        receipt = inbox.process({"path": "Inbox/Роли.md", "revision": inbox.preview("Inbox/Роли.md")["revision"],
                                 "person": {"new": True, "name": "Рита"},
                                 "facts": [{"line": 1, "text": original, "visibility": "shared", "kind": "role",
                                            "direction": directions[0]["id"]}]})
        person = self.root / receipt["person"]["path"]
        person.write_text(person.read_text().replace("> " + original, "> Ведёт финальную проверку."))
        pending = self.root / "Inbox" / "Ещё не разобрано.md"
        pending.write_text("Утвердили другой вариант.")
        before = self.markdown()
        first = self.cli("--limit", "1")
        after = self.markdown()
        changed = [path for path in before if before[path] != after[path]]
        self.assertEqual(first["issues"]["items"], [])
        self.assertEqual(first["changedCards"], len(changed))
        self.assertGreaterEqual(first["eventsAdded"], 3)
        self.assertGreaterEqual(first["summariesUpdated"], 3)
        self.assertEqual(first["cards"]["direction"], 3)
        self.assertEqual(first["cards"]["task"], 12)
        self.assertEqual(first["cards"]["plan"], 4)
        self.assertEqual(first["personHistoryAdded"], 1)
        self.assertEqual(Gantt(self.root).detail(task["id"])["brief"]["text"], "Экземпляр проверен.")
        self.assertEqual(set(before), set(after))
        self.assertEqual({path.parts[0] for path in changed}, {"Projects", "Tasks", "People"})
        for project in projects:
            state = project_updates.read_updates(self.root, project)
            self.assertEqual(state["total"], 1)
            self.assertTrue(state["events"][0]["sources"][0]["available"])
            self.assertEqual(state["brief"]["text"], state["events"][0]["text"])
            self.assertTrue(search.search(self.root, "сверку", record_id=project))
        again = self.cli()
        self.assertEqual([again[name] for name in ("changedCards", "eventsAdded", "summariesUpdated", "personHistoryAdded")],
                         [0, 0, 0, 0])
        self.assertFalse(again["indexRebuilt"])
        self.assertEqual(self.markdown(), after)

    def test_empty_vault_and_index_failure_return_a_bounded_report(self):
        storage.initialize(self.root)
        empty = refresh_all(self.root)
        self.assertEqual((empty["totalCards"], empty["changedCards"], empty["issues"]["total"]), (0, 0, 0))
        path = storage.create(self.root, "note", "Повреждённая запись")
        path.write_text(path.read_text().replace("schema_version: 1", "schema_version: 7"))
        damaged = self.cli("--limit", "1")
        self.assertFalse(damaged["indexRebuilt"])
        self.assertEqual(damaged["issues"]["total"], 2)
        self.assertTrue(damaged["issues"]["truncated"])
        self.assertEqual(damaged["issues"]["items"][0]["path"], path.relative_to(self.root).as_posix())
        next_page = self.cli("--limit", "1", "--offset", "1")
        self.assertEqual(next_page["issues"]["items"][0]["path"], ".second-brain/search.sqlite3")
