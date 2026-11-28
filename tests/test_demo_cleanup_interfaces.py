import json
from http.client import HTTPConnection
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from second_brain import storage
from second_brain.webserver import server


class CleanupInterfaceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "Vault"

    def start(self, mode):
        httpd = server(self.root, 0, mode=mode)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(httpd.shutdown)
        return httpd.server_port

    def request(self, port, path, data=None, status=200, headers=None):
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            if data is None:
                conn.request("GET", path)
            else:
                conn.request("POST", path, json.dumps(data),
                             {"Content-Type": "application/json", **(headers or {})})
            reply = conn.getresponse()
            body = reply.read()
            self.assertIn(reply.status, status if isinstance(status, tuple) else (status,), body[:300])
            return json.loads(body) if reply.headers.get_content_type() == "application/json" else body.decode()
        finally:
            conn.close()

    def cli(self, *args):
        output = subprocess.check_output([sys.executable, "-m", "second_brain", "--vault", str(self.root),
                                          "demo-cleanup", *args], cwd=Path(__file__).resolve().parents[1], timeout=15)
        return json.loads(output)

    def test_confirmed_guide_becomes_workspace_on_same_port_and_on_restart(self):
        port = self.start("demo")
        self.assertEqual(len(self.request(port, "/api/guide")["scenarios"]), 3)
        own = self.request(port, "/api/directions", {"requestId": "custom", "title": "Сад",
                           "noteTitle": "Рассада", "body": "Базилик растёт на кухне."})
        preview = self.request(port, "/api/demo-cleanup")
        self.assertTrue(preview["available"])
        self.assertGreater(preview["removeCount"], 0)
        self.assertEqual(preview["revision"], self.cli()["revision"])
        self.request(port, "/api/demo-cleanup", {}, status=400)
        self.request(port, "/api/demo-cleanup", {"revision": preview["revision"]}, status=403,
                     headers={"Origin": "https://elsewhere.example"})
        self.assertEqual(len(self.request(port, "/api/guide")["scenarios"]), 3)
        result = self.request(port, "/api/demo-cleanup", {"revision": preview["revision"]})
        self.assertTrue(result["cleaned"])
        self.assertEqual(result["workUrl"], "/")
        archive = self.root / result["archive"]
        with ZipFile(archive) as saved:
            self.assertIn("Home.md", saved.namelist())
        self.assertFalse(list(archive.parent.rglob("*.md")))
        mine = {own["id"], own["notes"][0]["id"]}
        for entry in (port, self.start("demo"), self.start("work")):
            self.assertIn("workspace.js", self.request(entry, "/"))
            self.assertEqual({r["id"] for r in self.request(entry, "/api/workspace")["records"]}, mine)
            self.assertTrue(self.request(entry, "/api/demo-cleanup")["cleaned"])
        self.assertIn("Базилик", self.request(port, "/api/ask", {"query": "Где растёт базилик?"})["items"][0]["answer"])

    def test_cli_cleanup_switches_a_running_guide_and_requires_fresh_preview(self):
        port = self.start("demo")
        preview = self.cli()
        note = next((self.root / "Notes").glob("*.md"))
        with note.open("a", encoding="utf-8") as stream:
            stream.write("\nМоё дополнение.\n")
        self.request(port, "/api/demo-cleanup", {"revision": preview["revision"]}, status=409)
        fresh = self.request(port, "/api/demo-cleanup")
        self.assertGreater(fresh["keepCount"], 0)
        self.assertTrue(self.cli("--apply", fresh["revision"])["cleaned"])
        self.assertIn("workspace.js", self.request(port, "/"))
        self.assertIn("Моё дополнение.", note.read_text(encoding="utf-8"))

    def test_installed_guide_archives_examples_even_after_an_old_empty_transition(self):
        port = self.start("demo")
        source = Path(__file__).resolve().parents[1]
        runtime = self.root / ".runtime"
        runtime.mkdir()
        for name in ("second_brain", "examples", "docs", "plugins"):
            shutil.copytree(source / name, runtime / name, ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("README.md", "ROADMAP.md"):
            shutil.copy2(source / name, runtime / name)
        shutil.copytree(source / ".agents", self.root / ".agents")
        preview = self.request(port, "/api/demo-cleanup")
        self.assertEqual((preview["removeCount"], preview["keepCount"]), (8, 0))

        # A completed transition from the old installer must keep the working
        # interface. Its empty ZIP and personal files survive the new cleanup.
        from second_brain import demo_cleanup
        old_revision = "b" * 64
        old_zip = f"Archive/DemoCleanup/{old_revision}.zip"
        (self.root / old_zip).parent.mkdir()
        with ZipFile(self.root / old_zip, "w"):
            pass
        previous = dict(preview, cleaned=True, removeCount=0, keepCount=8,
                        archive=old_zip, revision=old_revision)
        receipt = self.root / ".second-brain-demo/cleaned.json"
        receipt.write_bytes(demo_cleanup._json(previous))
        self.assertIn("workspace.js", self.request(port, "/"))

        my_note = self.root / "Notes/Личная ссылка.md"
        my_note.write_text("[[Проба № 3]]", encoding="utf-8")
        fresh = self.request(port, "/api/demo-cleanup")
        self.assertEqual((fresh["removeCount"], fresh["keepCount"]), (6, 2))
        self.assertEqual(self.cli(), fresh)
        withdraw = demo_cleanup._withdraw
        withdrawn = []
        def interrupt(root, revision, name, digest):
            withdraw(root, revision, name, digest)
            withdrawn.append(name)
            raise KeyboardInterrupt
        with patch.object(demo_cleanup, "_withdraw", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                demo_cleanup.apply(self.root, fresh["revision"])
        self.assertFalse((self.root / withdrawn[0]).exists())
        self.assertEqual(self.cli("--apply", old_revision), previous)
        self.assertTrue((self.root / withdrawn[0]).is_file())
        self.assertFalse((receipt.parent / "pending.json").exists())
        self.assertEqual(self.cli(), fresh)
        applied = self.request(port, "/api/demo-cleanup", {"revision": fresh["revision"]})
        self.assertEqual(self.cli("--apply", fresh["revision"])["revision"], fresh["revision"])
        self.assertTrue(applied["cleaned"])
        with ZipFile(self.root / applied["archive"]) as archive:
            self.assertEqual(len(archive.namelist()), 6)
        self.assertTrue((self.root / old_zip).is_file())
        self.assertTrue((runtime / "README.md").is_file())
        self.assertEqual(my_note.read_text(encoding="utf-8"), "[[Проба № 3]]")

    def test_ordinary_vault_cannot_be_cleaned_via_http(self):
        storage.initialize(self.root)
        own = storage.create(self.root, "direction", "Личное")
        original = own.read_bytes()
        port = self.start("work")
        self.assertFalse(self.request(port, "/api/demo-cleanup")["available"])
        self.request(port, "/api/demo-cleanup", {"revision": "0" * 64}, status=(400, 409))
        self.assertEqual(own.read_bytes(), original)
        self.assertFalse(any((self.root / "Archive").iterdir()))
