from pathlib import Path
import tempfile
import unittest

from examples.report_vault import generate
from second_brain import reports, storage


class ReportExampleTest(unittest.TestCase):
    def test_three_directions_evidence_and_historical_format(self):
        with tempfile.TemporaryDirectory() as staging:
            root = generate(Path(staging) / "practice")
            issues = reports.list_issues(root)["items"]
            self.assertEqual(len(issues), 3)
            directions = set()
            for row in issues:
                issue = reports.get_issue(root, row["id"])
                self.assertTrue(issue["sources"])
                for citation in issue["sources"]:
                    self.assertTrue(citation["available"])
                    record = storage.read_record(root / citation["path"])
                    directions.add(record["direction"])
            self.assertEqual(len(directions), 3)
            digest, = [row for row in issues if row["seriesId"]]
            self.assertEqual((digest["end"], digest["asOf"]), ("2026-11-22", "2026-11-23"))
            history = reports.get_template(root, digest["templateId"])
            original = reports.get_template(root, digest["templateId"], digest["templateVersion"])
            self.assertGreater(history["version"], original["version"])
            self.assertNotEqual(history["guidance"], original["guidance"])
            self.assertEqual(reports.get_issue(root, digest["id"])["templateRevision"], original["revision"])
