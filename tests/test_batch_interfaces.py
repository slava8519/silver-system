import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from examples.inbox_vault import generate
from second_brain import storage
from second_brain.inbox import Inbox
from second_brain.webserver import server


class BatchInterfaces(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = generate(Path(temporary.name) / "Vault")

    def cli(self, *args, data=None):
        run = subprocess.run([sys.executable, "-m", "second_brain", "--vault", str(self.root), *args],
                             input=json.dumps(data, ensure_ascii=False) if data is not None else None,
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_three_directions_and_owner_followup(self):
        rows = self.cli("inbox-new")["items"]
        self.assertEqual(len(rows), 4)
        rules = {r["title"]: r for r in self.cli("inbox-rules")["items"]}
        it, personal, perfume = (rules[key] for key in ("Управление ИТ-проектами", "Личное", "Парфюмерия"))
        person, = self.cli("people", "--match", "Лидия Белова")["items"]
        projects = {alias: rec for _, rec in storage.iter_records(self.root)
                    if rec["type"] == "project" for alias in rec["aliases"]}
        rows.sort(key=lambda r: r["path"])

        def project(rule, name, field, value):
            card = projects[name]
            return dict(kind="project", direction=rule["direction"], rule_revision=rule["revision"],
                        project=card["id"], field=field, value=value,
                        expected_revision=self.cli("show", card["id"], "--brief")["revision"])

        def participant(rule, candidate, kind, visibility):
            return dict(kind="person", direction=rule["direction"], rule_revision=rule["revision"],
                        person=candidate, fact_kind=kind, visibility=visibility)

        decisions = [
            [project(it, "Маяк", "status", "готов к переключению"),
             project(it, "Орбита", "next_step", "проверить вход по одноразовой ссылке"),
             participant(it, {"id": person["id"]}, "role", "shared")],
            [participant(personal, {"id": person["id"]}, "fact", "personal")],
            [project(perfume, "Кедровая серия", "next_step", "повторить пробу на блоттере"),
             participant(perfume, {"new": True, "name": "Ева Ланская"}, "role", "shared")],
            [dict(kind="triage", reason="Какой проект получил новый срок?")],
        ]
        for row, actions in zip(rows, decisions):
            path, digest = row["path"], row["revision"]
            lines = self.cli("inbox-read", path)["lines"]
            plan = dict(path=path, revision=digest, actions=[
                dict(action, line=line["n"], quote=line["text"]) for action, line in zip(actions, lines)])
            receipt = self.cli("inbox-apply", data=plan)
            self.assertEqual((self.root / receipt["source"]["path"]).read_bytes(), (self.root / path).read_bytes())
        self.assertEqual(self.cli("inbox-new")["total"], 0)
        self.assertEqual(self.cli("show", projects["Маяк"]["id"], "--brief")["status"], "готов к переключению")
        for name, expected in (("Орбита", "проверить вход"), ("Кедровая серия", "повторить пробу")):
            self.assertTrue(self.cli("show", projects[name]["id"], "--brief")["nextStep"].startswith(expected))
        self.assertEqual(len(self.cli("people")["items"]), 2)
        self.assertNotIn("лиловом", str(self.cli("person", person["id"])))
        self.assertIn("лиловом", str(self.cli("person", person["id"], "--personal")))
        self.assertEqual(self.cli("search", "лиловом"), [])
        question, = self.cli("inbox-triage")["items"]
        self.assertEqual(question["line"], 1)

        with server(self.root, 0, mode="work") as httpd:
            worker = threading.Thread(target=httpd.serve_forever)
            worker.start()
            def request(path, body=None):
                conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
                try:
                    conn.request("POST" if body is not None else "GET", path,
                                 body=json.dumps(body) if body is not None else None,
                                 headers={"Content-Type": "application/json"})
                    result = conn.getresponse()
                    return result.status, json.loads(result.read())
                finally:
                    conn.close()
            try:
                self.assertEqual(request("/api/inbox")[1]["triageOpen"], 1)
                self.assertTrue(request("/api/inbox/rules?limit=2")[1]["truncated"])
                self.assertEqual(request("/api/inbox/triage")[1]["items"], [question])
                self.assertEqual(request("/api/inbox/resolve", {"id": question["id"], "clarification": "Это Маяк."})[0], 200)
            finally:
                httpd.shutdown()
                worker.join()
        ready, = self.cli("inbox-new")["items"]
        self.assertEqual(ready["clarification"], "Это Маяк.")
        (self.root / ready["path"]).unlink()
        change = project(it, "Маяк", "due", "2026-12-18")
        self.cli("inbox-apply", data=dict(path=ready["path"], revision=ready["revision"],
                 triage_id=ready["triage_id"], actions=[dict(change, line=question["line"], quote=question["quote"])]))
        self.assertEqual(self.cli("inbox-new")["total"], 0)
        self.assertEqual(self.cli("inbox-triage")["total"], 0)
        self.assertEqual(self.cli("show", projects["Маяк"]["id"], "--brief")["due"], "2026-12-18")
        self.assertEqual(Inbox(self.root).original(ready["revision"])["lines"][0]["text"], question["quote"])
