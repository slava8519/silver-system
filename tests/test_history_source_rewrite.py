import http.client
from pathlib import Path
import tempfile
import threading
import unittest

from second_brain import archive_reader, evidence, history_archive, query, report_sources, storage
from second_brain.webserver import server


class HistorySourceRewriteTests(unittest.TestCase):
    def test_original_citation_and_rewritten_link_resolve_to_the_same_archived_passage(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = storage.initialize(Path(tmp.name))
        old_link = "[[Notes/Условия]]"
        new_link = "[[Notes/Условия#Выбор|Условия]]"
        original = f"Подробности в {old_link}."
        corrected = f"Подробности в {new_link}."
        storage.create(root, "note", "Условия", "## Выбор\nРешение согласовано.")
        path = storage.create(root, "project", "Снимки", "## История статусов\n"
                              f"### 2024-01-03 — первый\n{original}\n"
                              f"### 2025-02-02 — второй\n{original}\n")
        project = storage.read_record(path, include_raw=True)
        reader = query.WorkspaceQuery(root)
        coordinates = [n for n, line in enumerate(project["raw"].splitlines(), 1) if line == original]
        saved = [{"id": project["id"], "path": path.relative_to(root).as_posix(), "line": number,
                  "quote": original, "ref": query._line_ref(original)} for number in coordinates]
        review = {"revision": project["revision"],
                  "wikiLink": {"line": coordinates[1], "old": old_link, "new": new_link}}
        options = {"as_of": "2026-11-28", "min_kept": 0, "reviewed_links": review}
        preview = history_archive.rotate(root, project["id"], **options)
        history_archive.rotate(root, project["id"], **options, apply=preview["revision"])
        self.assertEqual(preview["moved"], 2)
        for year, old in zip(("2024", "2025"), saved):
            record, fragment = reader.open_citation(old)
            self.assertTrue(Path(fragment["path"]).name.startswith(year))
            self.assertEqual(fragment["ref"], old["ref"])
            self.assertIn({"n": fragment["start"], "text": original}, fragment["lines"])
        archive_ref = {**saved[1], "id": record["id"], "path": fragment["path"], "line": fragment["start"]}
        new_ref = {**archive_ref, "ref": query._line_ref(corrected), "quote": corrected}
        self.assertEqual(archive_reader.visible_lines(record)[fragment["start"]], corrected)
        for link in (saved[1], archive_ref, new_ref):
            with self.subTest(link=link):
                _, answer = reader.open_citation(link)
                self.assertIn({"n": answer["start"], "text": link["quote"]}, answer["lines"])
                self.assertEqual(evidence.resolve_source(root, link)["ref"], link["ref"])
                report, = report_sources.verify(root, [link])
                self.assertEqual(report["ref"], link["ref"])
                for source in (link, report):
                    result = report_sources.resolve(root, source, "", context=True)
                    self.assertTrue(result["available"])
                    self.assertIn({"n": archive_ref["line"], "text": link["quote"]}, result["lines"])
        # A fingerprint created only during publication cannot be a pre-rotation citation.
        with self.assertRaises(storage.ConflictError):
            reader.open_citation({**saved[1], "ref": new_ref["ref"]})

        httpd = server(root, port=0, mode="work")
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(httpd.shutdown)
        for reference in (saved[1], archive_ref, new_ref):
            href = query._source_link({"id": reference["id"]}, reference["line"], reference["ref"])
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
            try:
                conn.request("GET", href.split("#")[0])
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(f'<code class="line-text">{reference["quote"]}</code>', response.read().decode())
            finally:
                conn.close()
