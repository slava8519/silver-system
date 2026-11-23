from contextlib import contextmanager
from hashlib import sha256
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from second_brain import storage
from second_brain.webserver import server


class CaptureInterfaces(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = storage.initialize(Path(scratch.name) / "Vault")

    @contextmanager
    def serving(self, mode="work"):
        with server(self.root, 0, mode=mode) as httpd:
            worker = threading.Thread(target=httpd.serve_forever)
            worker.start()
            def request(method, route, body=None, *, raw=False, **headers):
                conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
                try:
                    conn.request(method, route, body, headers)
                    result = conn.getresponse()
                    if raw:
                        return result.status, result.read(), result.headers
                    return result.status, json.load(result)
                finally:
                    conn.close()
            try:
                yield request
            finally:
                httpd.shutdown()
                worker.join()

    def cli(self, *args, content=None):
        return json.loads(subprocess.run([sys.executable, "-m", "second_brain", "--vault", str(self.root), *args],
                                         input=content, capture_output=True, check=True, timeout=10).stdout)

    def test_capture_roundtrip_and_deferred_processing(self):
        text = "Строка из переписки.\r\nВторая строка.\n"
        data = b"\x89PNG\r\n\x1a\n\x00\xff"
        name = "снимок + №2.png"
        with self.serving() as request:
            code, note = request("POST", "/api/inbox/text", json.dumps({"text": text, "title": "Чат"}),
                                 **{"Content-Type": "application/json"})
            self.assertEqual(code, 201)
            route = "/api/inbox/file?" + urlencode({"name": name})
            code, shot = request("POST", route, data, **{"Content-Type": "application/octet-stream"})
            self.assertEqual(code, 201)
            rows = request("GET", "/api/inbox")[1]["items"]
        self.assertEqual({r["path"] for r in rows}, {note["path"], shot["path"]})
        self.assertFalse(any(row["processed"] for row in rows))
        self.assertEqual((self.root / note["path"]).read_bytes(), text.encode())
        self.assertEqual((self.root / shot["path"]).read_bytes(), data)
        self.assertEqual(shot["revision"], sha256(data).hexdigest())
        self.assertEqual(sorted(r["status"] for r in self.cli("inbox-new")["items"]), ["attachment", "new"])
        self.assertEqual(list((self.root / "Sources/Processed").iterdir()), [])
        duplicate = self.cli("inbox-file", str(self.root / shot["path"]))
        self.assertNotEqual(duplicate["path"], shot["path"])
        self.assertEqual(duplicate["revision"], shot["revision"])
        captured = self.cli("inbox-text", "--title", "Из чата", content=text.encode())
        self.assertEqual((self.root / captured["path"]).read_bytes(), text.encode())

    def test_same_origin_mode_and_limits(self):
        route = "/api/inbox/file?name=note.pdf"
        options = {"Content-Type": "application/octet-stream"}
        with self.serving() as request:
            self.assertEqual(request("POST", route, b"file", Origin="https://unrelated.invalid", **options)[0], 403)
            self.assertEqual(request("POST", route, b"file", **{"Content-Type": "text/plain"})[0], 415)
            self.assertEqual(request("POST", route, b"", **options, **{"Content-Length": str(33 * 1024 ** 2)})[0], 413)
            escaped = json.dumps({"text": "я" * (512 * 1024 + 1)})
            self.assertEqual(request("POST", "/api/inbox/text", escaped, **{"Content-Type": "application/json"})[0], 413)
            self.assertEqual(request("POST", "/api/inbox/file?name=..%2FREADME.md", b"x", **options)[0], 400)
        self.assertEqual(list((self.root / "Inbox").iterdir()), [])

    def test_transcript_page_opens_download_at_cited_line(self):
        raw = b"untrusted owner file, kept verbatim"
        file = self.root.parent / "capture.html"
        file.write_bytes(raw)
        attachment = self.cli("inbox-file", str(file))
        revision = attachment["revision"]
        transcript = self.cli("inbox-apply", content=json.dumps({
            "path": attachment["path"], "revision": revision,
            "actions": [{"kind": "transcript", "line": 0, "quote": "", "text": "Первая реплика."}]}).encode())
        path = transcript["transcript"]["path"]
        text_rev = self.cli("inbox-read", path)["revision"]
        self.cli("inbox-apply", content=json.dumps({"path": path, "revision": text_rev, "actions": []}).encode())
        with self.serving() as request:
            target = "/attachment/" + revision
            status, page, _ = request("GET", "/original/" + text_rev + "?start=3", raw=True)
            self.assertEqual(status, 200)
            self.assertIn(target, page.decode())
            status, body, headers = request("GET", target, raw=True)
            self.assertEqual((status, body), (200, raw))
            self.assertEqual(headers.get_content_type(), "application/octet-stream")
            self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))

    def test_demo_has_no_capture_endpoints(self):

        # Demo has its own initialisation and must not accept working-vault uploads.
        self.root = self.root.parent / "Demo"
        with self.serving("demo") as request:
            for endpoint in ("/api/inbox/text", "/api/inbox/file?name=note.pdf"):
                code, _ = request("POST", endpoint, "{}", **{"Content-Type": "application/json"})
                self.assertEqual(code, 404)
        self.assertEqual(list((self.root / "Inbox").iterdir()), [])
