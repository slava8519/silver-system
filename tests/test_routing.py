from concurrent.futures import ThreadPoolExecutor
import hashlib
from html import unescape
import http.client
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from second_brain import storage
from second_brain.query import WorkspaceQuery
from second_brain.routing import Routing
from second_brain.webserver import server


class DirectionRoutingTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name))
        self.routing = Routing(self.root)
        self.direction = self.new_direction("Керамика", aliases=["Мастерская"])
        self.other = self.new_direction("Походы")
        self.project = storage.create(self.root, "project", "Пробный обжиг", "Формы подготовлены.",
                                      direction=self.direction, status="планирование")
        self.rule = self.routing.set_rule(self.direction, "Переносить согласованный статус обжига.",
                                          ["status", "due"], ["shared"])
        self.raw = ("Обжиг. Утверждены «сушка» и срок 2026-12-10. Личное напоминание про шкафчик.\n"
                    "Обжиг. Утверждён следующий шаг «проверить термопару».\n").encode("utf-8")
        self.source = hashlib.sha256(self.raw).hexdigest()
        self.original = self.root / "Sources/Originals" / (self.source + ".md")
        self.original.write_bytes(self.raw)
        self.action = {"kind": "project", "direction": self.direction, "rule_revision": self.rule["revision"],
                       "project": storage.read_record(self.project)["id"], "field": "status", "value": "сушка",
                       "quote": "Обжиг. Утверждены «сушка» и срок 2026-12-10.", "line": 1,
                       "expected_revision": storage.read_record(self.project)["revision"]}

    def new_direction(self, title, aliases=None):
        return storage.read_record(storage.create(self.root, "direction", title, aliases=aliases))["id"]

    def test_rules_follow_direction_id_and_default_to_project_history(self):
        rows = self.routing.rules()
        empty = next(row for row in rows if row["direction"] == self.other)
        self.assertEqual((empty["revision"], empty["project_fields"], empty["person_visibilities"], empty["path"]),
                         (None, ["event", "summary"], [], None))
        path = storage.find_record(self.root, self.direction)
        path.rename(path.with_name("Переименовано.md"))
        rule = next(row for row in self.routing.rules() if row["direction"] == self.direction)
        self.assertEqual(rule, self.rule)
        self.assertEqual(rule["aliases"], ["Мастерская"])
        with self.assertRaises(ValueError):
            self.routing.set_rule(self.action["project"], "Условия", [], [])

    def test_rule_updates_require_observed_version_and_preserve_user_yaml(self):
        path = self.root / self.rule["path"]
        initial = path.read_bytes().replace(b"project_fields:", b"custom_template: x\nproject_fields:")
        path.write_bytes(initial.replace(b"\n", b"\r\n"))
        with self.assertRaises(storage.ConflictError):
            self.routing.set_rule(self.direction, "Новые правила", ["due"], [], self.rule["revision"])
        rule = next(row for row in self.routing.rules() if row["direction"] == self.direction)
        new = self.routing.set_rule(self.direction, "Только сроки", ["due"], [], rule["revision"])
        self.assertNotEqual(new["revision"], rule["revision"])
        self.assertIn(b"custom_template: x\r\n", path.read_bytes())
        self.assertEqual(new["instructions"], "Только сроки")
        with self.assertRaises(ValueError):
            self.routing.set_rule(self.direction, "Ок", ["nonexistent_field"], [], new["revision"])
        path.write_text(path.read_text().replace('["due"]', '["driver"]'))
        bad = next(row for row in self.routing.rules() if row["direction"] == self.direction)
        self.assertTrue(bad["error"])
        self.assertEqual(bad["project_fields"], [])
        with self.assertRaises(ValueError):
            self.routing.apply_project({**self.action, "rule_revision": bad["revision"]}, self.source)

    def test_project_summary_permission_requires_events_in_same_rule(self):
        path = self.root / self.rule["path"]
        before = path.read_bytes()
        data = {"direction": self.direction, "instructions": "Сверяй состояние проектов.",
                "project_fields": ["status", "summary"], "person_visibilities": [],
                "expected_revision": self.rule["revision"]}
        with self.assertRaisesRegex(ValueError, "summary.*event"):
            self.routing.save_rule(data)
        self.assertEqual(path.read_bytes(), before)
        saved = self.routing.save_rule({**data, "project_fields": ["event", "summary"]})
        self.assertEqual(saved["project_fields"], ["event", "summary"])
        events_only = self.routing.save_rule({**data, "project_fields": ["event"], "expected_revision": saved["revision"]})
        self.assertEqual(events_only["project_fields"], ["event"])

    def test_omitted_fields_inherit_while_explicit_permissions_remain_exact(self):
        def save(area, **options):
            return self.routing.save_rule({"direction": area, "instructions": "Новая инструкция.", **options})
        default = save(self.other)
        self.assertEqual((default["project_fields"], default["person_visibilities"]), (["event", "summary"], []))
        legacy = save(self.direction, expected_revision=self.rule["revision"])
        self.assertEqual((legacy["project_fields"], legacy["person_visibilities"]), (["status", "due"], ["shared"]))
        disabled = save(self.other, project_fields=[], expected_revision=default["revision"])
        updated = save(self.other, expected_revision=disabled["revision"])
        self.assertEqual(updated["project_fields"], [])
        path = self.root / updated["path"]
        path.write_bytes(b"".join(row for row in path.read_bytes().splitlines(keepends=True)
                                  if not row.startswith(b"project_fields:")))
        old_bytes = path.read_bytes()
        inherited = next(row for row in self.routing.rules() if row["direction"] == self.other)
        self.assertEqual(inherited["project_fields"], ["event", "summary"])
        self.assertEqual(path.read_bytes(), old_bytes)

    def test_shared_project_records_only_sourced_value_and_manual_history(self):
        old = self.project.read_bytes().replace(b"aliases: []", b"aliases: []\ncustom:\n  marker: keep")
        self.project.write_bytes(old.replace(b"\n", b"\r\n"))
        action = {**self.action, "expected_revision": storage.read_record(self.project)["revision"]}
        first = self.routing.apply_project(action, {"id": self.source, "path": str(self.original)})
        current = storage.read_record(self.project)
        self.assertEqual(current["status"], "сушка")
        self.assertEqual(current["revision"], first["revision"])
        self.assertEqual(first["chain_revision"], current["revision"])
        self.assertIn("прежде — планирование", current["body"])
        self.assertIn("Формы подготовлены.", current["body"])
        self.assertIn("[Источник, строка 1](../Sources/Originals/" + self.source + ".md)", current["body"])
        self.assertNotIn("шкафчик", current["body"])
        self.assertNotIn("2026-12-10", current["body"])
        self.assertIn(b"custom:\r\n  marker: keep", self.project.read_bytes())
        with ThreadPoolExecutor(max_workers=2) as pool:
            repeats = list(pool.map(lambda _: self.routing.apply_project(action, self.source), range(2)))
        self.assertTrue(all(r["replayed"] and r["chain_revision"] == first["revision"] for r in repeats))
        manually = self.project.read_bytes().replace("сушка".encode(), "заметка владельца".encode(), 1)
        self.project.write_bytes(manually)
        replay = self.routing.apply_project(action, self.source)
        self.assertEqual(self.project.read_bytes(), manually)
        self.assertNotEqual(replay["chain_revision"], replay["revision"])
        with self.assertRaises(storage.ConflictError):
            self.routing.apply_project({**action, "field": "due", "value": "2026-12-10",
                                        "expected_revision": replay["chain_revision"]}, self.source)

    def test_source_policy_direction_and_project_revision_are_checked_before_mutation(self):
        old = self.project.read_bytes()
        other_rule = self.routing.set_rule(self.other, "Вести сроки подготовки к поездке.", ["due"], ["personal"])
        bad = [
            {"value": "додумано"}, {"line": 2}, {"line": True}, {"quote": "сушка из другого документа"},
            {"field": "next_step"}, {"rule_revision": None}, {"visibility": "personal"},
            {"direction": self.other, "rule_revision": other_rule["revision"], "field": "due", "value": "2026-12-10"},
            {"expected_revision": "obsolete"},
        ]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.routing.apply_project({**self.action, **change}, self.source)
            self.assertEqual(self.project.read_bytes(), old)
        self.original.write_bytes(self.raw + b"!\n")
        with self.assertRaises(storage.ConflictError):
            self.routing.apply_project(self.action, self.source)
        self.assertEqual(self.project.read_bytes(), old)

    def test_person_visibility_cannot_escape_current_direction_policy(self):
        action = {"kind": "person", "direction": self.direction, "rule_revision": self.rule["revision"],
                  "fact_kind": "observation", "visibility": "shared"}
        self.routing.validate_person(action)
        for change in ({"visibility": "personal"}, {"direction": self.other}, {"fact_kind": "rating"},
                       {"rule_revision": None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.routing.validate_person({**action, **change})
        self.routing.set_rule(self.direction, "Другое правило", [], ["personal"], self.rule["revision"])
        with self.assertRaises(storage.ConflictError):
            self.routing.validate_person(action)

    def test_recover_applied_project_after_rule_change_but_check_every_new_write(self):
        first = self.routing.apply_project(self.action, self.source)
        before = self.project.read_bytes()
        self.routing.set_rule(self.direction, "Состояние разбирает владелец.", [], [], self.rule["revision"])
        self.assertTrue(self.routing.apply_project(self.action, self.source)["replayed"])
        with self.assertRaises(storage.ConflictError):
            self.routing.apply_project({**self.action, "field": "due", "value": "2026-12-10",
                                        "expected_revision": first["revision"]}, self.source)
        self.assertEqual(self.project.read_bytes(), before)

    def test_current_status_uses_yaml_for_each_project_and_ignores_cleared_history(self):
        path = self.project
        storage.edit(self.root, self.action["project"], self.action["expected_revision"], {"aliases": ["Озеро"]})
        action = {**self.action, "expected_revision": storage.read_record(path)["revision"]}
        saved = self.routing.apply_project(action, self.source)
        source = "Статус «готово» утверждён.".encode()
        digest = hashlib.sha256(source).hexdigest()
        (self.original.parent / (digest + ".txt")).write_bytes(source)
        self.routing.apply_project({**action, "expected_revision": saved["revision"],
                                    "quote": source.decode(), "value": "готово"}, digest)
        storage.create(self.root, "project", "Берег", "Старый статус здесь тоже сохранён.", status="приёмка")
        reader = WorkspaceQuery(self.root)
        result = reader.ask({"query": "Какой текущий статус у Озера и Берега?"})["items"]
        self.assertEqual([item["answer"] for item in result], ["готово", "приёмка"])
        current = result[0]["source"]
        self.assertEqual(current["lines"], [{"n": current["start"], "text": 'status: "готово"'}])
        with server(self.root, 0, mode="work") as httpd:
            worker = threading.Thread(target=httpd.serve_forever)
            worker.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
                self.addCleanup(conn.close)
                def open_source():
                    conn.request("GET", current["href"].split("#")[0])
                    response = conn.getresponse()
                    return response.status, unescape(response.read().decode())
                code, html = open_source()
                self.assertEqual(code, 200)
                self.assertIn('id="cited-line"', html)
                self.assertIn('status: "готово"', html)
                self.assertNotIn("сушка", html)
                self.assertNotIn("inbox-change", html)
                path.write_bytes(path.read_bytes().replace(b"aliases:", b"# owner\naliases:"))
                moved = reader.open_citation({"id": action["project"], "ref": current["ref"], "line": current["start"]})[1]
                self.assertEqual(moved["start"], current["start"] + 1)
                storage.edit(self.root, action["project"], storage.read_record(path)["revision"], {"status": None})
                remaining = reader.ask({"query": "Статусы Озера и Берега?"})
                self.assertEqual([r["answer"] for r in remaining["items"]], ["приёмка"])
                self.assertIn("Озеро", remaining["message"])
                self.assertEqual(open_source()[0], 409)
            finally:
                httpd.shutdown()
                worker.join()

    def test_named_current_due_and_next_step_preserve_other_questions(self):
        storage.create(self.root, "project", "Блокнот", "Решили выбрать бархат для обложки.",
                       due="2026-12-10", next_step="Закрепить образец")
        reader = WorkspaceQuery(self.root)
        for question, expected in [("Какой срок у Блокнота?", "2026-12-10"),
                                   ("Следующий шаг по Блокноту?", "Закрепить образец"),
                                   ("Что решили по Блокноту?", "Решили выбрать бархат для обложки.")]:
            with self.subTest(question=question):
                answer, = reader.ask({"query": question})["items"]
                self.assertEqual(answer["answer"], expected)
