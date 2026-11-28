import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from examples.working_vault import generate
from second_brain.people import People
from second_brain.query import WorkspaceQuery
from second_brain.webserver import server


class IntakeInterfaces(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = generate(Path(directory.name) / "Fieldwork")
        self.people = People(self.root)
        self.person, = self.people.candidates()

    def cli(self, *args):
        result = subprocess.check_output([sys.executable, "-m", "second_brain", "--vault", str(self.root), *args],
                                         cwd=Path(__file__).resolve().parents[1], text=True, timeout=10)
        return json.loads(result)

    def test_cli_intake_scope_and_original_read(self):
        inbox = self.cli("inbox")
        followup, = [item for item in inbox["items"] if not item["processed"]]
        preview = self.cli("inbox-read", followup["path"])
        direction, = self.cli("list", "--match", "Полевые записи", "--type", "direction")["items"]
        last = preview["lines"][-1]
        command = ("inbox-process", followup["path"], "--revision", preview["revision"],
                   "--person", self.person["id"], "--fact", "fact", direction["id"], "shared", str(last["n"]), last["text"])
        receipt = self.cli(*command)
        self.assertEqual(self.cli(*command), receipt)
        self.assertEqual(self.cli("original", receipt["source"]["id"], "--start", str(last["n"]))["lines"], [last])
        card = self.cli("person", self.person["id"], "--direction", direction["id"])
        self.assertEqual({f["kind"] for f in card["facts"]}, {"role", "fact"})
        self.assertEqual(self.cli("people", "--direction", direction["id"])["items"], [self.person])
        self.assertNotIn("тихолист", json.dumps(card, ensure_ascii=False))
        self.assertEqual(self.cli("ask", "тихолист", "--brief")["items"], [])
        self.cli("rebuild")
        self.assertEqual(self.cli("search", "тихолист"), [])
        self.assertNotIn("тихолист".encode(), (self.root / ".second-brain/search.sqlite3").read_bytes())
        roles = self.cli("ask", "Какие роли у Дарьи Мироновой?", "--direction", direction["id"], "--brief")
        self.assertEqual(len(roles["items"]), 1)
        self.assertIn("маршрута", roles["items"][0]["answer"])
        evidence = roles["items"][0]["source"]["evidence"]
        original = self.cli("evidence", evidence["personId"], evidence["factId"])
        self.assertEqual((original["text"], original["start"]), (roles["items"][0]["answer"], 6))
        self.assertEqual(self.cli("overview", "--brief")["peopleCount"], 1)
        folders = self.cli("overview", "--folders", "--limit", "20")
        self.assertIn({"path": "People", "count": 1, "files": 1}, folders["items"])

    def test_browser_citation_after_manual_edit_and_private_view(self):
        reader = WorkspaceQuery(self.root)
        links = reader.ask({"query": "Какие роли у Дарьи Мироновой?"})["items"]
        self.assertEqual(len(links), 2)
        card = self.people.detail(self.person["id"], personal=True)
        private, = [f for f in card["facts"] if f["visibility"] == "personal"]
        original_href = links[0]["source"]["evidenceHref"]
        with server(self.root, 0, mode="work") as httpd:
            worker = threading.Thread(target=httpd.serve_forever)
            worker.start()
            try:
                def request(path):
                    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
                    try:
                        conn.request("GET", path.split("#")[0])
                        response = conn.getresponse()
                        return response.status, response.read().decode()
                    finally:
                        conn.close()

                status, body = request(links[0]["source"]["href"])
                self.assertEqual(status, 200)
                self.assertIn(original_href, body)
                self.assertNotIn("тихолист", body)
                self.assertIn(links[0]["answer"], request(original_href)[1])
                href = f"/evidence/{card['id']}/{private['id']}"
                self.assertEqual(request(href)[0], 404)
                self.assertIn("тихолист", request(href + "?personal=1")[1])
                self.assertNotIn("тихолист", request("/api/person?id=" + card["id"])[1])
                original = (self.root / private["sourcePath"]).read_bytes()
                self.assertIn("тихолист", request("/original/" + private["sourceId"])[1])
                fact = next(f for f in card["facts"] if f["text"] == links[0]["answer"])
                self.cli("person-correct", card["id"], fact["id"], "--revision", card["revision"],
                         "--text", "Дарья принимает цветопробы после проверки.")
                self.assertEqual(request(links[0]["source"]["href"])[0], 409)
                self.assertEqual((self.root / private["sourcePath"]).read_bytes(), original)
                self.assertIn(fact["quote"], request(original_href)[1])
                self.assertIn("принимает цветопробы", request("/api/person?id=" + card["id"])[1])
            finally:
                httpd.shutdown()
                worker.join()

    def test_cli_pages_a_one_line_transcript_without_losing_coordinates(self):
        relative = "Inbox/Длинная строка.txt"
        text = "д" * 900
        (self.root / relative).write_text(text, encoding="utf-8")
        first = self.cli("inbox-read", relative, "--limit", "1")["lines"][0]
        second = self.cli("inbox-read", relative, "--limit", "1", "--column", str(first["nextColumn"]))["lines"][0]
        self.assertLess(len(first["text"]), len(text))
        self.assertEqual((first["n"], second["n"]), (1, 1))
        self.assertEqual(first["text"] + second["text"], text)

    def test_previous_work_vault_without_intake_directories_still_opens(self):
        root = self.root.parent / "Minimal"
        (root / "Notes").mkdir(parents=True)
        with server(root, 0, mode="work") as httpd:
            self.assertEqual(httpd.vault.overview()["records"], [])
            self.assertFalse((root / "Inbox").exists())
