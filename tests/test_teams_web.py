import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from second_brain import storage
from second_brain.webserver import server


class TeamsWebTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = storage.initialize(Path(tmp.name) / "Vault")
        self.httpd = server(self.root, 0, mode="work")
        worker = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(self.httpd.shutdown)

    def request(self, path, data=None, status=200):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            conn.request("GET" if data is None else "POST", "/api/" + path,
                         body=None if data is None else json.dumps(data), headers={"Content-Type":"application/json"})
            response = conn.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.status, status, result)
            return result
        finally:
            conn.close()

    def test_inbox_and_directory_remain_available_with_unrelated_broken_notes(self):
        direction = storage.create(self.root, "direction", "Дом")
        identifier = storage.read_record(direction)["id"]
        # A direction can move between any of the three managed record folders.
        moved = self.root / "Projects" / "area.md"
        moved.write_bytes(direction.read_bytes().replace(b"---\n", b"---\n" + b"# metadata\n" * 300, 1))
        direction.unlink()
        person = self.request("people/create", {"name": "Ира", "requestId": "person"}, 201)
        team = self.request("teams/create", {"name": "Семья", "requestId": "team"}, 201)
        before = self.request("workspace/context?directions=1")
        self.assertEqual(before, {"name": "Vault", "directions": [{"id": identifier, "title": "Дом"}]})

        notes = self.root / "Notes"
        header = '---\ntype: note\nschema_version: 1\nid: note-{}\ntitle: Note\n---\n'
        for n in range(100):
            (notes / f"{n}.md").write_text(header.format(n) + "x" * 2048)
        (notes / "duplicate.md").write_text(header.format(5))
        (notes / "broken.md").write_bytes(b'---\nid: bad\ntype: note\ntitle: "broken\n---\n')
        self.request("workspace", status=400)
        self.assertEqual(self.request("workspace/context"), {"name": "Vault"})
        self.assertEqual(self.request("workspace/context?directions=1"), before)
        self.assertEqual(self.request("inbox/rules")["items"][0]["direction"], identifier)
        self.assertEqual(self.request("people")["items"][0]["id"], person["id"])
        self.assertEqual(self.request("people?direction=" + identifier)["total"], 0)
        self.assertEqual(self.request("person?id=" + person["id"])["name"], "Ира")
        self.assertEqual(self.request("team?id=" + team["id"])["name"], "Семья")
        self.assertIsNone(self.request("people/context")["owner"])
        self.request("inbox/text", {"text": "Новый материал"}, 201)
        self.assertEqual(len(self.request("inbox")["items"]), 1)

    def test_empty_directory_namesakes_two_teams_and_independent_context(self):
        ctx = self.request("people/context")
        self.assertEqual((ctx["owner"], ctx["team"], self.request("teams")["items"]), (None, None, []))
        people = [self.request("people/create", {"name": "Ира", "requestId": key}, 201) for key in ("a", "b")]
        teams = [self.request("teams/create", {"name": name, "requestId": name}, 201) for name in ("A", "B")]
        for url in ("people", "teams"):
            first = self.request(url + "?limit=1")
            second = self.request(url + "?limit=1&offset=1")
            self.assertEqual((first["total"], second["total"]), (2, 2))
            self.assertNotEqual(first["items"][0]["path"], second["items"][0]["path"])
            if url == "people":
                self.assertEqual([p["name"] for p in first["items"] + second["items"]], ["Ира", "Ира"])

        person, other = people
        self.assertEqual(self.request("person?id=" + person["id"])["facts"], [])
        for team in teams:
            self.request("teams/member", {"id": team["id"], "person": person["id"], "present": True,
                                         "revision": team["revision"]})
        self.assertEqual(self.request("person/teams?id=" + person["id"])["total"], 2)
        state = self.request("people/context", {"revision": ctx["revision"], "owner": other["id"]})
        self.assertIsNone(state["team"])
        state = self.request("people/context", {"revision": state["revision"], "team": teams[0]["id"]})
        self.assertEqual([p["id"] for p in state["members"]], [person["id"]])
        self.assertEqual(state["owner"]["id"], other["id"])
        state = self.request("people/context", {"revision": state["revision"], "owner": None})
        self.assertEqual((state["owner"], state["team"]["id"]), (None, teams[0]["id"]))
        state = self.request("people/context", {"revision": state["revision"], "team": None})
        self.assertEqual(state["members"], [])
        first = self.request("team?id=" + teams[0]["id"])
        after = self.request("teams/member", {"id": first["id"], "person": person["id"], "present": False,
                                              "revision": first["revision"]})
        self.assertEqual(after["members"], [])
        remaining, = self.request("person/teams?id=" + person["id"])["items"]
        self.assertEqual(remaining["id"], teams[1]["id"])

    def test_stale_team_revision_and_malformed_context_do_not_overwrite_manual_work(self):
        team = self.request("teams/create", {"name": "Начальное имя", "requestId": "key"}, 201)
        path = self.root / team["path"]
        with path.open("a", encoding="utf-8") as f:
            f.write("\nРучное примечание владельца.\n")
        before = path.read_bytes()
        edit = {"id": team["id"], "name": "Новое имя", "revision": team["revision"]}
        self.request("teams/rename", edit, 409)
        self.assertEqual(path.read_bytes(), before)
        latest = self.request("team?id=" + team["id"])
        self.assertEqual(self.request("teams/rename", {**edit, "revision": latest["revision"]})["name"], "Новое имя")
        self.assertIn("Ручное примечание", path.read_text())
        self.request("people/context", {"team": team["id"]}, 400)
        current = self.request("people/context")
        self.assertIsNone(current["team"])
        self.request("people/context", {"owner": None, "unexpected": True, "revision": current["revision"]}, 400)
        self.request("teams/member", {"id": team["id"], "person": "a", "present": "false",
                                      "revision": latest["revision"]}, 400)
