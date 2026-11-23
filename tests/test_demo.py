import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from second_brain import demo, storage


class DemoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Demo Vault"
        self.demo = demo.DemoVault(self.root)
        self.request = {"title": "Сад", "noteTitle": "Первый посев",
                        "body": "Базилик растёт на восточном окне.", "requestId": "retry-1"}

    def test_every_question_reads_its_source(self):
        guide = self.demo.guide()
        self.assertEqual([len(s["questions"]) for s in guide["scenarios"]], [2, 1, 2])
        for scenario in guide["scenarios"]:
            for question in scenario["questions"]:
                reply = self.demo.ask({"questionId": question["id"]})
                source = reply["source"]
                self.assertIsNotNone(source)
                actual = (self.root / source["path"]).read_text().splitlines()
                for line in source["lines"]:
                    self.assertEqual(line["text"], actual[line["n"] - 1])
                if question["id"] == "3-2":
                    self.assertFalse(reply["supported"])
                    self.assertIn("два часа", actual[source["start"] - 1])
                else:
                    self.assertTrue(reply["supported"], question)
                    self.assertEqual(reply["answer"], actual[source["start"] - 1])
        with self.assertRaises(ValueError):
            self.demo.ask({"noteId": "not-owned-by-demo", "query": "ключ"})

    def test_updates_moves_and_missing_data(self):
        record_id = self.demo.catalog["records"]["Памятка для поездок"]
        note = storage.find_record(self.root, record_id)
        note = note.rename(note.with_name("Дорожная заметка.md"))
        record = storage.read_record(note)
        storage.update(self.root, record_id, "После поездки.\n\nЗапасной ключ хранится в зелёном чехле.",
                       record["revision"])
        result = demo.DemoVault(self.root).ask({"questionId": "2-1"})
        self.assertTrue(result["supported"])
        self.assertIn("зелёном", result["answer"])
        self.assertEqual(result["source"]["path"], "Notes/Дорожная заметка.md")
        self.assertEqual(result["source"]["start"], 11)
        note.unlink()
        self.assertIsNone(self.demo.ask({"questionId": "2-1"})["source"])
        perfume_id = self.demo.catalog["records"]["Проба № 3"]
        perfume = storage.read_record(storage.find_record(self.root, perfume_id))
        storage.update(self.root, perfume_id, perfume["body"] + "\nЧерез сутки осталась нота ванили.",
                       perfume["revision"])
        result = self.demo.ask({"questionId": "3-2"})
        self.assertTrue(result["supported"])
        self.assertIn("ванили", result["answer"])

    def test_custom_direction_survives_retries_and_keeps_files(self):
        original = {p: p.read_bytes() for p in self.root.rglob("*.md")}
        initial = self.demo.add_direction(self.request)
        reopened = demo.DemoVault(self.root)
        self.assertEqual(reopened.guide()["custom"], [initial])
        self.assertEqual(reopened.add_direction(self.request), initial)
        note_id = initial["notes"][0]["id"]
        answer = reopened.ask({"noteId": note_id, "query": "Где растёт базилик?"})
        self.assertTrue(answer["supported"])
        self.assertEqual(answer["answer"], self.request["body"])
        self.assertFalse(reopened.ask({"noteId": note_id, "query": "камни"})["supported"])
        for duplicate in ({"requestId": "different"}, {"body": "другой текст"},
                          {"requestId": "other", "title": "Личное"}):
            with self.assertRaises(ValueError):
                reopened.add_direction({**self.request, **duplicate})
        self.assertEqual(original, {p: p.read_bytes() for p in original})
        self.assertEqual(len(list(storage.iter_records(self.root))), 9)

    def test_interrupted_write_can_finish_after_restart(self):
        real_link = demo.os.link

        def interrupt(src, dst):
            if "staged" in Path(src).parts and Path(dst).parent == self.demo.root / "Notes":
                raise OSError("test interruption")
            return real_link(src, dst)

        with patch.object(demo.os, "link", side_effect=interrupt), self.assertRaises(OSError):
            self.demo.add_direction(self.request)
        restarted = demo.DemoVault(self.root)
        direction, = restarted.guide()["custom"]
        self.assertEqual(restarted.add_direction(self.request), direction)
        self.assertEqual(len(list(storage.iter_records(self.root))), 9)

    def test_legacy_catalog_and_live_directions_from_files(self):
        created = self.demo.add_direction(self.request)
        new_note = storage.create(self.root, "note", "Урожай", "Морковь уже взошла.", direction=created["id"])
        new_id = storage.read_record(new_note)["id"]
        other = storage.create(self.root, "direction", "Чтение")
        other_id = storage.read_record(other)["id"]

        old = dict(self.demo.catalog)
        starting = old.pop("directions")
        (self.demo.directory / "catalog.json").write_text(json.dumps(old))
        original = storage.find_record(self.root, starting["Личное"])
        original.write_text(original.read_text().replace('title: "Личное"', 'title: "Дом и поездки"'))
        resumed = demo.DemoVault(self.root)
        self.assertEqual(resumed.catalog["vaultId"], old["vaultId"])
        self.assertEqual(resumed.catalog["directions"], starting)
        custom = {d["id"]: d for d in resumed.guide()["custom"]}
        self.assertEqual(set(custom), {created["id"], other_id})
        self.assertEqual(len(custom[created["id"]]["notes"]), 2)
        self.assertEqual(custom[other_id]["notes"], [])
        self.assertTrue(resumed.ask({"noteId": new_id, "query": "Уже взошла морковь?"})["supported"])
        new_note.unlink()
        self.assertEqual(len(resumed.guide()["custom"][0]["notes"]), 1)

    def test_search_by_note_id_is_independent_of_global_rank(self):
        area = storage.read_record(storage.create(self.root, "direction", "Испытания"))["id"]
        for n in range(22):
            storage.create(self.root, "note", f"Метка {n}", "Обычная метка.", direction=area)
        chosen = storage.create(self.root, "note", "Запасная карточка", "Метка лежит в коробке.", direction=area)
        chosen_id = storage.read_record(chosen)["id"]
        self.assertNotIn(chosen_id, [r["id"] for r in demo.search.search(self.root, "метка", limit=20)])
        result = self.demo.ask({"noteId": chosen_id, "query": "метка"})
        self.assertTrue(result["supported"])
        self.assertEqual(result["answer"], "Метка лежит в коробке.")

    def test_http_local_host_origin_json_and_static_boundaries(self):
        with demo.server(self.root, 0) as httpd:
            worker = threading.Thread(target=httpd.serve_forever, daemon=True)
            worker.start()
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
            try:
                conn.request("GET", "/api/guide")
                res = conn.getresponse()
                self.assertEqual(res.status, 200)
                self.assertEqual(len(json.load(res)["scenarios"]), 3)
                for path, headers, code in [
                    ("/api/directions", {"Content-Type": "text/plain"}, 415),
                    ("/api/directions", {"Origin": "https://another.example"}, 403),
                    ("/api/directions", {"Host": "another.example"}, 403),
                    ("/private", {}, 404),
                ]:
                    conn.request("POST", path, json.dumps(self.request),
                                 {"Content-Type": "application/json", **headers})
                    res = conn.getresponse()
                    self.assertEqual(res.status, code, path)
                    self.assertIn("error", json.load(res))
                conn.request("GET", "/%2e%2e/private/perfqa-mapping.md")
                res = conn.getresponse()
                self.assertEqual(res.status, 404)
                res.read()
                self.assertEqual(self.demo.guide()["custom"], [])
            finally:
                conn.close()
                httpd.shutdown()
                worker.join()


if __name__ == "__main__":
    unittest.main()
