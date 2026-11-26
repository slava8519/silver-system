from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from examples.gantt_vault import generate
from second_brain import demo_cleanup, storage
from second_brain.batch import Batch
from second_brain.project_gantt import ProjectGantt
from second_brain.project_updates import read_updates


class ProjectUpdatesExampleTests(unittest.TestCase):
    def test_optional_project_events_have_work_and_three_archived_sources(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "ProjectUpdates"
            root = generate(path, with_project_updates=True)
            project = next(record for _, record in storage.iter_records(root) if "Маяк" in record["aliases"])
            updates = read_updates(root, project["id"])
            chart = ProjectGantt(root).view(project["id"])
            self.assertEqual(chart["project"]["projectUpdates"]["brief"], updates["brief"])
            self.assertIn("Проверить восстановление базы", [row["title"] for row in chart["items"]])
            self.assertEqual([event["date"] for event in updates["events"]],
                             ["2026-11-26", "2026-11-24", "2026-11-20"])
            self.assertEqual(updates["brief"]["sources"], updates["events"][0]["sources"])
            manifest, _ = demo_cleanup._seed(root)
            batch = Batch(root)
            rule = next(row for row in batch.routing.rules() if row["direction"] == project["direction"])
            self.assertIsNone(rule["revision"])
            self.assertIsNone(rule["path"])
            self.assertEqual(batch.pending(), [])
            for event in updates["events"]:
                source, = event["sources"]
                original = root / source["path"]
                self.assertTrue(source["available"])
                self.assertEqual(original.read_text().splitlines()[source["line"] - 1], source["quote"])
                self.assertIn(event["date"], source["quote"])
                receipt = "Sources/Processed/" + source["id"] + ".md"
                self.assertTrue((root / receipt).is_file())
                self.assertEqual(manifest[receipt], sha256((root / receipt).read_bytes()).hexdigest())
            with self.assertRaises(FileExistsError):
                generate(path, with_project_updates=True)

    def test_default_gantt_fixture_has_no_added_protocols(self):
        with TemporaryDirectory() as temporary:
            root = generate(Path(temporary) / "Gantt")
            project = next(record for _, record in storage.iter_records(root) if "Маяк" in record["aliases"])
            self.assertIsNone(read_updates(root, project["id"])["brief"])
            self.assertEqual(list((root / "Sources/Processed").glob("*.md")), [])
