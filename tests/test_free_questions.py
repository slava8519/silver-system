import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from second_brain import demo, storage


class FreeQuestionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.vault = demo.DemoVault(Path(temporary.name) / "Three examples")
        self.root = self.vault.root

    def supported(self, question):
        reply = self.vault.ask({"query": question})
        return {item["title"]: item for item in reply["items"] if item["supported"]}

    def test_compound_question_returns_two_distinct_decisions(self):
        expected = {
            "Переезд базы заказов": "Перед переключением команда проверяет восстановление базы из резервной копии.",
            "Запуск кабинета поддержки": "Для первого запуска согласован вход по одноразовой ссылке.",
        }
        for query in ["Что мы решили по Маяку и Орбите?", "Какие договорённости по Орбите и Маяку?",
                      "О чём мы договорились по проектам «Маяк» и «Орбита»?"]:
            with self.subTest(query=query):
                items = self.supported(query)
                self.assertEqual(items.keys(), expected.keys())
                for title, item in items.items():
                    self.assertEqual(item["answer"], expected[title])
                    source = item["source"]
                    self.assertEqual((self.root / source["path"]).read_text().splitlines()[source["start"] - 1],
                                     expected[title])
                    self.assertTrue(source["href"].startswith("/source/" + item["id"] + "?"))

    def test_scopes_facts_to_the_three_demo_directions(self):
        area = storage.read_record(storage.create(self.root, "direction", "Мои дела"))["id"]
        storage.create(self.root, "note", "Мой ключ", "Запасной ключ лежит на балконе.", direction=area)
        storage.create(self.root, "note", "Новый металл", "Метеорит находился в сейфе.", direction=area)
        self.assertEqual(set(self.supported("Где запасной ключ?")), {"Памятка для поездок"})
        self.assertEqual(set(self.supported("Какая нота осталась у Кедра через два часа?")), {"Проба № 3"})
        self.assertEqual(set(self.supported("Где запасной ключ и какая нота осталась у Кедра через два часа?")),
                         {"Памятка для поездок", "Проба № 3"})
        self.assertFalse(self.supported("Что осталось у Кедра через сутки?"))
        self.assertFalse(self.supported("Сколько стоит флакон Кедра через два часа?"))
        self.assertFalse(self.supported("Где метеорит?"))
        self.assertFalse(self.supported("Какой бюджет у Маяка и Орбиты?"))

    def test_answers_and_links_follow_live_evidence(self):
        query = "Что мы решили по Маяку и Орбите?"
        title = "Переезд базы заказов"
        initial = self.supported(query)[title]
        source = initial["source"]
        citation = {"id": initial["id"], "line": source["start"], "ref": source["ref"]}
        path = storage.find_record(self.root, initial["id"])
        path = path.rename(path.with_name("Перенос заказов.md"))
        record = storage.read_record(path)
        storage.update(self.root, record["id"], "\n\n" + record["body"], record["revision"])
        _, shifted = self.vault.open_citation(citation)
        self.assertEqual(shifted["path"], "Projects/Перенос заказов.md")
        self.assertEqual(shifted["start"], source["start"] + 2)

        record = storage.read_record(path)
        new_fact = "Перед переключением команда проверяет реплику в новом кластере."
        storage.update(self.root, record["id"], record["body"].replace(initial["answer"], new_fact),
                       record["revision"])
        with self.assertRaises(storage.ConflictError):
            self.vault.open_citation(citation)
        self.assertEqual(self.supported(query)[title]["answer"], new_fact)
        path.unlink()
        self.assertEqual(set(self.supported(query)), {"Запуск кабинета поддержки"})

    def test_native_source_links_are_independent_of_the_search_page(self):
        with demo.server(self.root, 0) as httpd:
            worker = threading.Thread(target=httpd.serve_forever, daemon=True)
            worker.start()
            connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
            try:
                connection.request("POST", "/api/ask", json.dumps({"query": "Что мы решили по Маяку и Орбите?"}),
                                   {"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                items = json.load(response)["items"]
                self.assertEqual(len(items), 2)
                for item in items:
                    url = urlsplit(item["source"]["href"])
                    connection.request("GET", url.path + "?" + url.query)
                    response = connection.getresponse()
                    document = response.read().decode()
                    self.assertEqual(response.status, 200)
                    self.assertIn('id="cited-line"', document)
                    self.assertIn(item["answer"], document)
                    self.assertIn(item["source"]["path"], document)
            finally:
                connection.close()
                httpd.shutdown()
                worker.join()


if __name__ == "__main__":
    unittest.main()
