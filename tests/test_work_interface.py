import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from examples.working_vault import generate
from second_brain.webserver import server


class WorkingInterfaceTests(unittest.TestCase):
    def test_offline_and_browser_read_the_same_edited_sources(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = generate(Path(temporary.name) / "Workshop")
        question = "Что решили по Меридиану и Палитре?"

        def ask_offline():
            output = subprocess.check_output(
                [sys.executable, "-m", "second_brain", "--vault", str(root), "ask", question],
                cwd=Path(__file__).resolve().parents[1], text=True, timeout=10)
            return json.loads(output)

        before = ask_offline()
        self.assertEqual(len(before["items"]), 2)
        original = before["items"][0]
        file = root / original["source"]["path"]
        revised = original["answer"].replace("матовой", "хлопковой")
        self.assertNotEqual(original["answer"], revised)
        file.write_text(file.read_text(encoding="utf-8").replace(original["answer"], "\n" + revised),
                        encoding="utf-8")
        after = ask_offline()
        self.assertEqual(after["items"][0]["answer"], revised)
        self.assertEqual(after["items"][0]["source"]["start"], original["source"]["start"] + 1)

        with server(root, 0, mode="work") as httpd:
            worker = threading.Thread(target=httpd.serve_forever)
            worker.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
                self.addCleanup(conn.close)
                conn.request("POST", "/api/ask", json.dumps({"query": question}),
                             {"Content-Type": "application/json"})
                reply = conn.getresponse()
                self.assertEqual(reply.status, 200)
                self.assertEqual(json.load(reply), after)
                for item in after["items"]:
                    conn.request("GET", item["source"]["href"].split("#")[0])
                    source = conn.getresponse()
                    self.assertEqual(source.status, 200)
                    self.assertIn(item["answer"], source.read().decode())
                conn.request("GET", original["source"]["href"].split("#")[0])
                stale = conn.getresponse()
                self.assertEqual(stale.status, 409)
                stale.read()
                conn.request("GET", "/api/guide")
                guide = conn.getresponse()
                self.assertEqual(guide.status, 404)
                guide.read()
            finally:
                httpd.shutdown()
                worker.join()
        self.assertFalse((root / "Settings" / "Demo").exists())


if __name__ == "__main__":
    unittest.main()
