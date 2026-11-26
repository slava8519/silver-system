import hmac
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit

from second_brain import storage


class WorkControlTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.home = Path(scratch.name)
        self.root = storage.initialize(self.home / "Vault")

    def launch(self, port=0, *, owned=False):
        # Isolate the local user's control files while exercising the actual server entry point.
        script = """
from pathlib import Path
from unittest.mock import patch
import sys
from second_brain.webserver import serve
with patch.object(Path, 'home', return_value=Path(sys.argv[1])):
    serve(Path(sys.argv[2]), int(sys.argv[3]), mode='work')
"""
        process = subprocess.Popen([sys.executable, "-c", script, str(self.home), str(self.root), str(port)],
                                   env={**os.environ, "SECOND_BRAIN_PARENT_STDIN": "1" if owned else "0"},
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        def close():
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
        self.addCleanup(close)
        url = process.stdout.readline().strip()
        self.assertIsNotNone(urlsplit(url).port, url)
        return process, urlsplit(url).port

    def request(self, port, path, payload=None, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("POST" if payload is not None else "GET", path, json.dumps(payload) if payload else None,
                         {"Content-Type": "application/json", **headers})
            response = conn.getresponse()
            return response.status, json.load(response)
        finally:
            conn.close()

    def test_only_current_user_and_instance_can_stop_and_pending_upload_finishes(self):
        process, port = self.launch()
        process.stdin.close()  # A foreground server is independent of Obsidian.
        identity = self.request(port, "/api/planning-vault")[1]
        binding, instance = identity["binding"], identity["workControl"]
        path = self.home / ".second-brain" / "run" / (instance + ".key")
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        message = f"stop:{instance}:{port}:{binding}".encode("ascii")
        proof = hmac.digest(bytes.fromhex(path.read_text()), message, "sha256").hex()
        stop = "/api/work/stop"
        payload = {"instance": instance, "proof": proof}
        headers = {"X-Brain-Vault": binding}
        self.assertEqual(self.request(port, stop, payload)[0], 403)
        self.assertEqual(self.request(port, stop, payload, **{"X-Brain-Vault": "wrong"})[0], 409)
        self.assertEqual(self.request(port, stop, payload, Origin="http://example.invalid", **headers)[0], 403)
        self.assertEqual(self.request(port, stop, {**payload, "proof": "0" * 64}, **headers)[0], 403)

        upload = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.addCleanup(upload.close)
        upload.putrequest("POST", "/api/inbox/file?name=manual.txt")
        upload.putheader("Content-Type", "application/octet-stream")
        upload.putheader("Content-Length", "6")
        upload.endheaders(b"abc")
        self.assertEqual(self.request(port, stop, payload, **headers), (200, {"stopping": True}))
        upload.send(b"def")
        saved = upload.getresponse()
        self.assertEqual(saved.status, 201)
        self.assertEqual((self.root / json.load(saved)["path"]).read_bytes(), b"abcdef")
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertFalse(path.exists())

        next_process, _ = self.launch(port, owned=True)
        next_identity = self.request(port, "/api/planning-vault")[1]
        self.assertNotEqual(next_identity["workControl"], instance)
        self.assertEqual(self.request(port, stop, payload, **headers)[0], 403)
        next_process.stdin.close()
        self.assertEqual(next_process.wait(timeout=5), 0)
        self.assertEqual(list(path.parent.glob("*.key")), [])
