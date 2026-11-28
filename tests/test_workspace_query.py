from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from second_brain import brief, query, storage


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

    def test_exact_project_name_wins_over_partial_name_and_short_alias(self):
        self.create("project", "Запуск кабинета партнёров", due="2027-02-01", aliases=["Запуск кабинета"])
        self.create("project", "Запуск кабинета склада", due="2027-04-01")
        target = self.create("project", "Запуск кабинета поддержки", due="2026-12-01")
        answer = brief.answer(self.reader.ask({"query": "Какой срок у проекта «Запуск кабинета поддержки»?"}), 1)
        self.assertEqual([(item["id"], item["answer"]) for item in answer["items"]], [(target["id"], "2026-12-01")])
        self.assertFalse(answer["truncated"])

    def test_shared_adjective_cannot_substitute_for_requested_object(self):
        self.create("note", "Опись квартиры", "Запасной ключ лежит в синем чехле.")
        self.assertEqual(self.reader.ask({"query": "Где запасной термометр?"})["items"], [])
        self.assertEqual(self.reader.ask({"query": "Где запасной ключ от гаража?"})["items"], [])
        item, = self.reader.ask({"query": "Где хранится запасной ключ?"})["items"]
        self.assertEqual(item["answer"], "Запасной ключ лежит в синем чехле.")

    def test_conversational_requests_still_require_the_actual_subject(self):
        key = self.create("note", "Опись квартиры", "Запасной ключ хранится в синем чехле.")
        self.create("note", "Другой предмет", "Запасной термометр лежит в зелёном ящике.")
        for question in ("Ответь, пожалуйста, где у меня хранится запасной ключ?",
                         "Подскажи, где я могу найти свой запасной ключ?",
                         "Можешь подсказать, где именно лежит мой запасной ключ?"):
            with self.subTest(question=question):
                self.assertEqual([i["id"] for i in self.reader.ask({"query": question})["items"]], [key["id"]])
                self.assertEqual(self.reader.ask({"query": question.replace("ключ", "фонарь")})["items"], [])

    def test_compound_question_preserves_distinct_citations_in_one_card(self):
        note = self.create("note", "Опись квартиры", "Запасной ключ лежит в синем чехле.\nПаспорт хранится в сейфе.")
        result = self.reader.ask({"query": "Где запасной ключ и где паспорт?"})
        self.assertEqual([item["answer"] for item in result["items"]], note["body"].splitlines())
        for i, item in enumerate(result["items"]):
            self.assertEqual(item["source"]["start"], note["body_start"] + i)
            _, source = self.reader.open_citation({"id": item["id"], "line": item["source"]["start"],
                                                   "ref": item["source"]["ref"]})
            self.assertEqual(source, item["source"])
        self.assertTrue(brief.answer(result, 1)["truncated"])
        repeat = self.reader.ask({"query": "Где паспорт и где паспорт?"})
        self.assertEqual(len(repeat["items"]), 1)

    def test_state_fields_share_a_named_project_without_searching_unrelated_prose(self):
        project = self.create("project", "Проверка Северного маяка", status="done", next_step="Работы завершены")
        self.create("note", "Глоссарий", "Статус может быть любым.\nСледующий шаг — открыть карточку.")
        other = self.create("project", "Сбор и проверка", status="active", next_step="Сверить записи")
        for question in ("Какой статус и следующий шаг у проекта «Проверка Северного маяка»?",
                         "Какой статус и какой следующий шаг у проекта «Проверка Северного маяка»?",
                         "Какой статус у проекта «Проверка Северного маяка» и следующий шаг?"):
            with self.subTest(question=question):
                items = self.reader.ask({"query": question})["items"]
                self.assertEqual([(i["id"], i["answer"], i["source"]["start"]) for i in items],
                                 [(project["id"], project[field], project["field_lines"][field])
                                  for field in ("status", "next_step")])
        items = self.reader.ask({"query": "Какой статус у проекта «Проверка Северного маяка» "
                                          "и следующий шаг у проекта «Сбор и проверка»?"})["items"]
        self.assertEqual([(i["id"], i["answer"]) for i in items],
                         [(project["id"], "done"), (other["id"], "Сверить записи")])

        for title in ("Калибровка компаса", "Проверка Северного маяка и телескопа"):
            with self.subTest(unknown=title):
                question = f"Какой статус и следующий шаг у проекта «{title}»?"
                self.assertEqual(self.reader.ask({"query": question})["items"], [])

    def test_quoted_project_names_keep_their_individual_boundaries(self):
        first = self.create("project", "Сбор", due="2026-12-01")
        second = self.create("project", "Проверка", due="2026-12-02")
        combined = self.create("project", "Сбор и проверка", due="2026-12-03")
        for question, projects in (
            ("Какие сроки у проектов «Сбор» и «Проверка»?", [first, second]),
            ("Какие сроки у двух проектов «Сбор» и «Проверка»?", [first, second]),
            ("Какой срок у проекта «Сбор и проверка»?", [combined]),
        ):
            with self.subTest(question=question):
                result = self.reader.ask({"query": question})
                self.assertEqual([(i["id"], i["answer"], i["source"]["start"]) for i in result["items"]],
                                 [(p["id"], p["due"], p["field_lines"]["due"]) for p in projects])
                self.assertEqual(result["message"], "")

    def test_project_fields_survive_formatting_overlapping_titles_and_paraphrases(self):
        target = self.create("project", "Проверка — статус", "Участвовали Мария и владелец образца.",
                             status="active", next_step="Отправить образец")
        path = storage.find_record(self.root, target["id"])
        path.write_text(path.read_text().replace("status:", 'owner: "Ирина"\nstatus:'))
        self.create("note", "Проверка — статус", "Статус — завершено. Следующий шаг — запросить деньги.")
        for prompt, expected in (("Какой статус", "active"), ("Что делать дальше", "Отправить образец"),
                                 ("Кто владелец", "Ирина")):
            with self.subTest(prompt=prompt):
                item, = self.reader.ask({"query": f"{prompt} у проекта «Проверка — статус»?"})["items"]
                self.assertEqual((item["id"], item["answer"]), (target["id"], expected))
                source = item["source"]
                _, opened = self.reader.open_citation({"id": item["id"], "line": source["start"], "ref": source["ref"]})
                self.assertEqual(opened, source)
        self.assertFalse(self.reader.ask({"query": "Кто владелец проекта «Разметка грядок»?"})["items"])

    def test_field_and_event_words_in_project_names_keep_their_literal_meaning(self):
        self.create("project", "Статус", status="В работе")
        self.create("project", "Новости и события", "Бюджет — 700 рублей.\n## Последнее\nОткрыли выставку.")
        status, = self.reader.ask({"query": "Какой статус у проекта «Статус»?"})["items"]
        self.assertEqual(status["answer"], "В работе")
        budget, = self.reader.ask({"query": "Какой бюджет у проекта «Новости и события»?"})["items"]
        self.assertEqual(budget["answer"], "Бюджет — 700 рублей.")

    def test_brief_style_and_journal_clause_preserve_requested_fact(self):
        self.create("project", "Ремонт", "Бюджет составил 500 рублей.\n## Коротко\nПрототип готов."
                    "\n## Последнее\nИспытание началось.")
        for q in ("Кратко, какой бюджет у проекта «Ремонт»?", "Кратко расскажи, какой бюджет у проекта «Ремонт»?",
                  "Дай кратко бюджет проекта «Ремонт»?", "Какой бюджет у проекта «Ремонт» и что изменилось?"):
            with self.subTest(q=q):
                items = self.reader.ask({"query": q})["items"]
                self.assertEqual(items[0]["answer"], "Бюджет составил 500 рублей.")
                if "изменилось" in q:
                    self.assertEqual(items[1]["answer"], "Испытание началось.")

    def test_mixed_questions_preserve_independent_projects_and_unscoped_notes(self):
        a = self.create("project", "Витрина", "Бюджет — 500 рублей.\n## Последнее\nПодписали договор.")
        b = self.create("project", "Мастерская", "Бюджет — 100 рублей.\n## Последнее\nПровели испытания.")
        note = self.create("note", "Инвентарь", "Запасной ключ лежит в чехле.")
        queries = (("Какой бюджет у проекта «Витрина», а что изменилось по проекту «Мастерская»?", a),
                   ("Где запасной ключ и что изменилось по проекту «Мастерская»?", note))
        for question, first in queries:
            with self.subTest(q=question):
                items = self.reader.ask({"query": question})["items"]
                self.assertEqual([(i["id"], i["answer"]) for i in items],
                                 [(first["id"], first["body"].splitlines()[0]), (b["id"], "Провели испытания.")])

    def test_unknown_project_is_reported_alongside_available_event(self):
        project = self.create("project", "Закат", "## Последнее\nПоявился макет.")
        result = self.reader.ask({"query": "Что изменилось в проектах «Закат» и «Светофор»?"})
        item, = result["items"]
        self.assertEqual(item["id"], project["id"])
        self.assertIn("Светофор", result["message"])

    def test_direction_query_does_not_load_unrelated_malformed_card(self):
        path = storage.find_record(self.root, self.album["id"])
        path.write_bytes(path.read_bytes().replace(b"schema_version: 1", b"schema_version: 2"))
        question = {"query": "Что решили по Ирису?", "directionIds": [self.garden["id"]]}
        with patch.object(storage, "read_record", wraps=storage.read_record) as reads:
            result = self.reader.ask(question)
        self.assertEqual([i["id"] for i in result["items"]], [self.herbs["id"]])
        self.assertNotIn(path, [call.args[0] for call in reads.call_args_list])
        with self.assertRaisesRegex(ValueError, "Неподдерживаемая версия"):
            self.reader.ask({**question, "directionIds": [self.studio["id"]]})

    def test_scent_question_requires_evidence_from_the_named_note(self):
        self.create("note", "Клавир", "В партитуре четыре ноты.", direction=self.studio["id"])
        self.create("note", "Договор", "Нотариус подтвердил подпись.")
        self.assertEqual(self.reader.ask({"query": "Какой аромат?"})["items"], [])
        self.assertEqual(self.reader.ask({"query": "Какой аромат у клавира?"})["items"], [])
        sample = self.create("note", "Проба", "После высыхания остаётся аромат кедра.")
        item, = self.reader.ask({"query": "Какой аромат?"})["items"]
        self.assertEqual((item["id"], item["answer"]), (sample["id"], sample["body"]))

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
