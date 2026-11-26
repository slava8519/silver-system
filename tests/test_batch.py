from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from second_brain import storage
from second_brain.batch import Batch
from second_brain.inbox import Inbox
from second_brain.people import People
from second_brain.project_updates import read_updates, write_update
from second_brain.routing import Routing


class BatchTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name) / "Mixed")
        self.batch = Batch(self.root)
        self.inbox = Inbox(self.root)
        self.direction = storage.read_record(storage.create(self.root, "direction", "Музыка"))["id"]
        self.rule = Routing(self.root).set_rule(self.direction, "Концерт: статусы и общие роли.",
                                                 ["status", "due", "next_step"], ["shared", "personal"])
        self.project = storage.read_record(storage.create(self.root, "project", "Концерт",
                                                          direction=self.direction))

    def incoming(self, name, content):
        path = self.root / "Inbox" / name
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return next(row for row in self.batch.pending() if row["path"] == "Inbox/" + name)

    def plan(self, item, *actions):
        plan = {"path": item["path"], "revision": item["revision"], "actions": list(actions)}
        if item.get("triage_id"):
            plan["triage_id"] = item["triage_id"]
        return plan

    def project_action(self, field, value, line, quote):
        return {"kind": "project", "project": self.project["id"], "direction": self.direction,
                "field": field, "value": value, "line": line, "quote": quote,
                "expected_revision": self.project["revision"], "rule_revision": self.rule["revision"]}

    def person_action(self, name, line, quote, scope="shared"):
        return {"kind": "person", "person": {"name": name, "new": True}, "direction": self.direction,
                "fact_kind": "role", "visibility": scope, "line": line, "quote": quote,
                "rule_revision": self.rule["revision"]}

    def allow_project_events(self, *permissions):
        self.rule = self.batch.routing.set_rule(self.direction, self.rule["instructions"],
                                                ["status", *permissions], [], self.rule["revision"])

    def event_action(self, line, quote, **changes):
        return {"kind": "project_event", "project": self.project["id"], "direction": self.direction,
                "expected_revision": self.project["revision"], "rule_revision": self.rule["revision"],
                "line": line, "quote": quote, **changes}

    def test_project_event_from_inbox_and_status_share_a_revision(self):
        self.allow_project_events("event", "summary")
        first = "2026-05-18 репетиция завершена."
        second = "2026-05-20 концерт готов."
        item = self.incoming("Репетиция.txt", first + "\n" + second)
        request = self.plan(item, self.event_action(1, first, date="2026-05-18"),
                            self.project_action("status", "готов", 2, second),
                            self.event_action(2, second, date="2026-05-20", summary="Подготовка завершена."))
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.map(self.batch.apply, (request, request))
        self.assertEqual(a, b)
        self.assertEqual(a["triage"], [])
        self.assertEqual([r["kind"] for r in a["applied"]], ["project_event", "project", "project_event"])
        self.assertNotEqual(a["applied"][0]["eventId"], a["applied"][2]["eventId"])
        project = storage.read_record(storage.find_record(self.root, self.project["id"]))
        self.assertEqual(project["status"], "готов")
        for value in (first, second, "Подготовка завершена.", a["source"]["id"]):
            self.assertIn(value, project["body"])
        self.assertEqual(self.batch.pending(), [])

    def test_old_rule_and_unconfirmed_event_date_leave_questions(self):
        item = self.incoming("Музыка.md", "Репетиция завершена.")
        result = self.batch.apply(self.plan(item, self.event_action(1, "Репетиция завершена.")))
        self.assertFalse(result["applied"])
        self.assertEqual(len(result["triage"]), 1)
        self.allow_project_events("event")
        quote = "Зрители заполнили зал."
        other = self.incoming("Зал.md", quote)
        result = self.batch.apply(self.plan(other, self.event_action(1, quote, date="2026-05-20")))
        self.assertFalse(result["applied"])
        self.assertEqual(len(result["triage"]), 1)
        path = storage.find_record(self.root, self.project["id"])
        self.assertEqual(storage.read_record(path)["revision"], self.project["revision"])

    def test_event_permission_preserves_fact_without_granting_summary_access(self):
        self.allow_project_events("event")
        quote = "Репетиция состоялась."
        item = self.incoming("Афиша.txt", quote)
        result = self.batch.apply(self.plan(item, self.event_action(1, quote, summary="Перейти к съёмке.")))
        self.assertEqual(result["applied"][0]["summaryStatus"], "not_permitted")
        text = storage.find_record(self.root, self.project["id"]).read_text(encoding="utf-8")
        self.assertIn(quote, text)
        self.assertNotIn("Перейти к съёмке.", text)

    def test_late_and_manually_reviewed_events_keep_the_current_summary(self):
        self.allow_project_events("event", "summary")
        project_id = self.project["id"]
        for date, fact, summary, expected_state in (
                ("2026-05-20", "Концерт состоялся.", "Готовим запись.", "updated"),
                ("2026-05-15", "Утверждена программа.", "Готовим концерт.", "older_event"),
                ("2026-05-22", "Запись передана.", "Запись передана.", "manual_preserved")):
            quote = f"{date}. {fact}"
            item = self.incoming(date + ".md", quote)
            self.project = storage.read_record(storage.find_record(self.root, project_id))
            result = self.batch.apply(self.plan(item, self.event_action(1, quote, date=date, summary=summary)))
            self.assertEqual(result["applied"][0]["summaryStatus"], expected_state)
            updates = read_updates(self.root, project_id)
            if expected_state == "older_event":
                self.assertEqual(updates["brief"]["text"], "Готовим запись.")
                write_update(self.root, project_id, revision=updates["revision"], field="brief",
                             actor="manual", text="Ждём согласования записи.")
        self.assertEqual(updates["brief"]["text"], "Ждём согласования записи.")
        self.assertEqual(updates["total"], 3)
        self.assertTrue(all(e["sources"][0]["available"] for e in updates["events"]))

    def test_project_event_resume_preserves_intervening_manual_edit(self):
        self.allow_project_events("event")
        quote = "Концерт готов."
        item = self.incoming("Проверка.txt", quote)
        request = self.plan(item, self.event_action(1, quote), self.project_action("status", "готов", 1, quote))
        append = self.batch._append
        def interrupt(path, digest, event, label=""):
            if event["op"] == "step":
                raise InterruptedError
            return append(path, digest, event, label)
        with patch.object(self.batch, "_append", side_effect=interrupt), self.assertRaises(InterruptedError):
            self.batch.apply(request)
        path = storage.find_record(self.root, self.project["id"])
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\nРучное уточнение между разборами.\n")
        current = path.read_bytes()
        result = self.batch.apply(request)
        self.assertEqual([r["kind"] for r in result["applied"]], ["project_event"])
        self.assertEqual(len(result["triage"]), 1)
        self.assertEqual(path.read_bytes(), current)
        self.assertEqual(self.batch.apply(request), result)

    def test_mixed_source_projects_people_and_owner_question(self):
        content = "# Протокол\r\nКонцерт готов.\r\nСрок 20 мая.\r\nАда дирижирует.\r\nМайя отвечает за свет.\r\nКто заберёт пюпитр?\r\n"
        item = self.incoming("Встреча.txt", content)
        (self.root / "Inbox/Ещё одна копия.md").write_bytes(content.encode())
        self.assertEqual(len(self.batch.pending()), 1)
        request = self.plan(item, self.project_action("status", "готов", 2, "Концерт готов"),
                            self.project_action("due", "20 мая", 3, "Срок 20 мая"),
                            self.person_action("Ада", 4, "Ада дирижирует"),
                            self.person_action("Майя", 5, "Майя отвечает за свет", "personal"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(self.batch.apply, [request, request])
        self.assertEqual(first, second)
        self.assertEqual(len(first["applied"]), 4)
        self.assertEqual(len(first["triage"]), 1)
        self.assertEqual(self.batch.pending(), [])
        owner_item, = self.batch.triage()
        self.assertEqual(owner_item["line"], 6)
        self.assertEqual(owner_item["quote"], "Кто заберёт пюпитр?")
        self.assertEqual(owner_item["status"], "open")
        self.assertEqual((self.root / first["source"]["path"]).read_bytes(), content.encode())
        project = storage.read_record(storage.find_record(self.root, self.project["id"]))
        self.assertEqual((project["status"], project["due"]), ("готов", "20 мая"))
        self.assertEqual([f["personName"] for f in People(self.root).shared_facts()], ["Ада"])
        self.assertEqual(len(People(self.root).candidates()), 2)
        self.assertTrue(all(i["processed"] for i in self.inbox.list_items()))
        self.assertEqual(len(list((self.root / "Sources/Originals").iterdir())), 1)

    def test_owner_clarification_after_file_replaced_uses_only_old_fragment(self):
        item = self.incoming("Задача.md", "Заказ готовить. В других делах порядок.\nЧерновик.")
        result = self.batch.apply(self.plan(item, {"kind": "triage", "line": 1, "quote": "Заказ готовить",
                                                   "reason": "Укажите проект."},
                                            {"kind": "context", "line": 2, "quote": "Черновик."}))
        question, = result["triage"]
        self.assertEqual(self.batch.pending(), [])
        self.batch.resolve(question["id"], "Относится к концерту, следующий шаг.")
        self.assertEqual(self.batch.triage()[0]["status"], "ready")
        with self.assertRaises(storage.ConflictError):
            self.batch.resolve(question["id"], "Другое решение.")
        (self.root / item["path"]).write_text("Новая самостоятельная запись.")
        ready, new = self.batch.pending()
        self.assertNotEqual(ready["revision"], new["revision"])
        self.assertEqual(ready["clarification"], "Относится к концерту, следующий шаг.")
        for line, quote in [(1, "В других делах порядок"), (2, "Черновик.")]:
            with self.assertRaises(ValueError):
                self.batch.apply(self.plan(ready, self.project_action("next_step", quote, line, quote)))
        applied = self.batch.apply(self.plan(ready, self.project_action("next_step", "готовить", 1, "Заказ готовить")))
        self.assertEqual(applied["applied"][0]["value"], "готовить")
        self.assertEqual(self.batch.triage(), [])
        self.assertEqual(self.batch.pending(), [new])
        self.assertEqual(applied, self.batch.apply(self.plan(ready)))  # retries after a lost response

    def test_manual_result_is_final_and_changed_source_needs_new_read(self):
        first = self.incoming("Факт.txt", "Рита проводит сверку.")
        self.inbox.process({"path": first["path"], "revision": first["revision"],
                            "person": {"new": True, "name": "Рита"},
                            "facts": [{"kind": "role", "direction": self.direction, "visibility": "shared",
                                       "line": 1, "text": "Рита проводит сверку"}]})
        self.assertEqual(self.batch.pending(), [])
        with self.assertRaises(storage.ConflictError):
            self.batch.apply(self.plan(first))
        second = self.incoming("Изменится.md", "Первая версия.")
        (self.root / second["path"]).write_text("Другая версия.")
        with self.assertRaises(storage.ConflictError):
            self.batch.apply(self.plan(second))
        self.assertNotEqual(self.batch.pending()[0]["revision"], second["revision"])

    def test_stale_policy_or_manual_project_status_sends_only_conflict_to_owner(self):
        item = self.incoming("Протокол.md", "Проект готов.\nЯна раздаёт билеты.\nМила отвечает за зал.")
        stale = self.person_action("Мила", 3, "Мила отвечает за зал")
        stale["rule_revision"] = "old"
        storage.edit(self.root, self.project["id"], self.project["revision"], {"status": "на сверке"})
        out = self.batch.apply(self.plan(item, self.project_action("status", "готов", 1, "Проект готов"),
                                         self.person_action("Яна", 2, "Яна раздаёт билеты"), stale))
        self.assertEqual([r["kind"] for r in out["applied"]], ["person"])
        self.assertEqual([r["line"] for r in out["triage"]], [1, 3])
        self.assertEqual(storage.read_record(storage.find_record(self.root, self.project["id"]))["status"], "на сверке")
        self.assertEqual([p["name"] for p in People(self.root).candidates()], ["Яна"])

    def test_interruption_after_project_write_before_receipt_step(self):
        item = self.incoming("Выпуск.txt", "Состояние готов.\nСрок — завтра.")
        plan = self.plan(item, self.project_action("status", "готов", 1, "Состояние готов"),
                         self.project_action("due", "завтра", 2, "Срок — завтра"))
        append = self.batch._append

        def stop_after_first_write(path, digest, event, label=""):
            if event["op"] == "step":
                raise InterruptedError
            return append(path, digest, event, label)

        with patch.object(self.batch, "_append", side_effect=stop_after_first_write):
            with self.assertRaises(InterruptedError):
                self.batch.apply(plan)
        path = storage.find_record(self.root, self.project["id"])
        self.assertEqual(storage.read_record(path)["status"], "готов")
        (self.root / item["path"]).unlink()
        resume, = self.batch.pending()
        self.assertEqual(resume["status"], "resume")
        with path.open("a") as stream:
            stream.write("\nКомментарий владельца между запусками.\n")
        out = self.batch.apply(self.plan(resume))
        self.assertEqual([s["field"] for s in out["applied"]], ["status"])
        self.assertEqual([s["line"] for s in out["triage"]], [2])
        self.assertNotIn("due", storage.read_record(path))
        self.assertEqual(path.read_text().count("[Источник, строка 1]"), 1)
        self.assertTrue(path.read_text().endswith("между запусками.\n"))

    def test_conflicting_visibility_and_project_values_wait_for_owner_before_writes(self):
        item = self.incoming("Разногласия.txt", "Майя ведёт переговоры.\nСтатус готов.\nСтатус отложен.")
        person = self.person_action("Майя", 1, "Майя ведёт переговоры.")
        bad = self.plan(item, {**person, "fact_kind": ["role"]})
        with self.assertRaises(ValueError):
            self.batch.apply(bad)
        self.assertEqual(self.batch.pending(), [item])
        result = self.batch.apply(self.plan(item, person, {**person, "visibility": "personal"},
                                            self.project_action("status", "готов", 2, "Статус готов"),
                                            self.project_action("status", "отложен", 3, "Статус отложен")))
        self.assertEqual(result["applied"], [])
        self.assertEqual([q["line"] for q in result["triage"]], [1, 2, 3])
        self.assertEqual(People(self.root).candidates(), [])
        self.assertEqual(storage.read_record(storage.find_record(self.root, self.project["id"])), self.project)
        archived = self.root / result["source"]["path"]
        archived.write_text("Изменён оригинал.")
        damaged, = self.batch.triage()
        self.assertEqual(damaged["status"], "damaged")
        self.assertEqual(self.batch.pending(), [])

    def test_invalid_or_duplicate_people_leave_questions(self):
        old = self.incoming("Доклад.txt", "Ада выбирает песни.")
        self.batch.apply(self.plan(old, self.person_action("Ада", 1, "Ада выбирает песни")))
        second = self.incoming("Черновик.txt", "Ада пишет ноты.")
        out = self.batch.apply(self.plan(second, self.person_action("Ада", 1, "Ада пишет ноты")))
        self.assertEqual(len(out["triage"]), 1)
        self.assertEqual(len(People(self.root).candidates()), 1)
        candidate, = People(self.root).candidates()
        self.batch.resolve(out["triage"][0]["id"], "Та же Ада.")
        ready, = self.batch.pending()
        action = self.person_action("Ада", 1, "Ада пишет ноты")
        action["person"] = {"id": candidate["id"]}
        self.batch.apply(self.plan(ready, action))
        self.assertEqual(len(People(self.root).detail(candidate["id"])["facts"]), 2)

    def test_corrupt_receipt_isolated_and_no_forged_paths_are_used(self):
        first = self.incoming("Архив.md", "Как распределить?")
        done = self.batch.apply(self.plan(first))
        path = self.root / done["processedPath"]
        valid = path.read_bytes()
        for contents in (
                valid.replace('Inbox/Архив.md'.encode(), b"Inbox/../../Elsewhere.md"),
                valid + b'\n<!-- inbox-batch: {"op":"clarify"} -->\n',
                valid.replace(b"source_id:", b"wrong_id:")):
            with self.subTest(contents=contents[-100:]):
                path.write_bytes(contents)
                other = self.incoming("Второй.md", "# Другое дело")
                self.assertEqual(self.batch.pending(), [other])
                damaged, = self.batch.triage()
                self.assertEqual((damaged["status"], damaged["receipt_path"]), ("damaged", done["processedPath"]))
                with self.assertRaises(ValueError):
                    self.batch.apply(self.plan(first))
                self.assertEqual(path.read_bytes(), contents)
                (self.root / other["path"]).unlink()
        path.write_bytes(valid)
        self.assertEqual(self.batch.triage()[0]["status"], "open")

    def test_non_utf8_file_does_not_block_another_source(self):
        broken = self.incoming("Нечитаемый.txt", b"\xff\xfe\x00")
        valid = self.incoming("Читаемый.md", "# Встреча")
        self.assertEqual(broken["status"], "unreadable")
        self.assertEqual(len(self.batch.pending()), 2)
        q, = self.batch.apply(self.plan(broken))["triage"]
        self.assertEqual((q["line"], q["quote"]), (0, ""))
        self.assertEqual((self.root / q["source_path"]).read_bytes(), b"\xff\xfe\x00")
        self.batch.apply(self.plan(valid, {"kind": "context", "line": 1, "quote": "# Встреча"}))
        self.assertEqual(self.batch.pending(), [])
        self.batch.resolve(q["id"], "Будет прислан исправленный файл, эту версию пропустить.")
        ready, = self.batch.pending()
        self.batch.apply(self.plan(ready, {"kind": "context", "line": 0, "quote": ""}))
        self.assertEqual(self.batch.triage(), [])
