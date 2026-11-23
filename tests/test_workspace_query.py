from pathlib import Path
import tempfile
import unittest

from second_brain import query, storage


class WorkspaceQueryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = (Path(temp.name) / "Field notes").resolve()
        storage.initialize(self.root)
        self.reader = query.WorkspaceQuery(self.root)
        self.garden = self.create("direction", "Семенной фонд")
        self.studio = self.create("direction", "Звукозапись")
        self.herbs = self.create("project", "Разметка грядок", "Цель — посеять травы.\n\n"
                                 "## Решения\nПряные травы выращиваем в отдельных ящиках.",
                                 aliases=["Ирис"], direction=self.garden["id"])
        self.album = self.create("project", "Запись квартета", "Согласован состав из четырёх музыкантов.",
                                 aliases=["Эхо"], direction=self.studio["id"])

    def create(self, kind, title, body="", **options):
        return storage.read_record(storage.create(self.root, kind, title, body, **options))

    def test_ordinary_vault_returns_two_decisions_and_independent_citations(self):
        result = self.reader.ask({"query": "Что решили по Ирису и Эхо?"})
        self.assertEqual([item["id"] for item in result["items"]], [self.herbs["id"], self.album["id"]])
        self.assertFalse((self.root / "Settings/Demo").exists())
        for item in result["items"]:
            link = item["source"]
            record, opened = self.reader.open_citation({"id": item["id"], "line": link["start"], "ref": link["ref"]})
            self.assertEqual(record["id"], item["id"])
            self.assertEqual(opened, link)
            line = (self.root / opened["path"]).read_text().splitlines()[opened["start"] - 1]
            self.assertEqual(line, item["answer"])

    def test_direction_filter_cannot_broaden_a_limited_reader(self):
        limited = query.WorkspaceQuery(self.root, [self.garden["id"]])
        prompt = {"query": "Что решили по Ирису и Эхо?"}
        result = self.reader.ask({**prompt, "directionIds": [self.garden["id"]]})
        self.assertEqual([item["id"] for item in result["items"]], [self.herbs["id"]])
        self.assertTrue(result["message"])
        self.assertEqual(result, limited.ask(prompt))
        for invalid in [[self.studio["id"]], [self.album["id"]], "entire vault"]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                limited.ask({**prompt, "directionIds": invalid})
        other = self.reader.ask({"query": "Что решили по Эхо?"})["items"][0]
        with self.assertRaises(ValueError):
            limited.open_citation({"id": other["id"], "line": other["source"]["start"], "ref": other["source"]["ref"]})
        self.assertEqual(self.reader.ask({**prompt, "directionIds": []})["items"], [])

    def test_request_for_citations_after_question_keeps_all_results(self):
        question = "Что решили по Ирису и Эхо?"
        baseline = self.reader.ask({"query": question})
        for instruction in ["Дай ссылку на запись для каждого решения.", "Приведи источник для каждого факта."]:
            with self.subTest(instruction=instruction):
                self.assertEqual(self.reader.ask({"query": question + " " + instruction}), baseline)
        other = self.reader.ask({"query": question + " Дай ссылку на сертификат."})
        self.assertIn("сертификат", other["message"])

    def test_unassigned_note_only_appears_in_unrestricted_queries(self):
        loose = self.create("note", "Наблюдение", "Термометр находится в малиновом футляре.")
        prompt = {"query": "Где термометр?"}
        self.assertEqual(self.reader.ask(prompt)["items"][0]["id"], loose["id"])
        self.assertEqual(self.reader.ask({**prompt, "directionIds": [self.garden["id"]]})["items"], [])

    def test_tentative_text_is_not_substituted_for_a_decision(self):
        draft = self.create("project", "Водосбор", "Цель — собрать воду.\nВариант с двумя бочками.\n"
                            "Решение пока не принято.\nКакое решение нужно?", aliases=["Каскад"])
        result = self.reader.ask({"query": "Что решили по Ирису и Каскаду?"})
        self.assertEqual([item["id"] for item in result["items"]], [self.herbs["id"]])
        self.assertIn("Каскад", result["message"])
        self.assertFalse(self.reader.ask({"query": "Что решили по Каскаду?"})["items"])
        storage.update(self.root, draft["id"], "## Решения\nКрыша отводит воду в цистерну.", draft["revision"])
        updated, = self.reader.ask({"query": "Что решили по Каскаду?"})["items"]
        self.assertEqual(updated["answer"], "Крыша отводит воду в цистерну.")

    def test_english_record_names_and_decision_sections(self):
        a = self.create("project", "Signal flags", "## Decisions\nThe buoy carries a white flag.")
        b = self.create("project", "Winter trail", "We agreed to mark the trail with yellow posts.")
        items = self.reader.ask({"query": "What did we decide for Signal flags and Winter trail?"})["items"]
        self.assertEqual([item["id"] for item in items], [a["id"], b["id"]])


if __name__ == "__main__":
    unittest.main()
