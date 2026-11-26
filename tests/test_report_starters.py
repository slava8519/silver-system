"""Exercise starter formats on synthetic records without any preinstalled templates."""

import json
from pathlib import Path
import tempfile
import unittest

from second_brain import storage
from second_brain.__main__ import command_line, run


class ReportStarterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.parser = command_line()

    def cli(self, *args):
        return run(self.parser.parse_args(["--vault", str(self.root), *args]))

    def note(self, direction, title, *observations):
        area = self.cli("create", "direction", direction)
        note = self.cli("create", "note", title, "--direction", area["id"], "--body", "\n".join(observations))
        first = storage.read_record(self.root / note["path"])["body_start"]
        return [(note["path"], str(first + i), quote) for i, quote in enumerate(observations)]

    def publish(self, template_id, title, body, citations, claims, *options):
        definition = self.cli("report-template-show", template_id, "--version", "1")
        self.assertIsNone(definition["path"])
        fixed_sections = [line for line in definition["body"].splitlines() if line.startswith("## ") and "[" not in line]
        self.assertEqual([line for line in body.splitlines() if line in fixed_sections], fixed_sections)
        self.assertEqual(len(citations), len(claims))
        evidence = [{"path": path, "line": int(line), "quote": quote, "claim": claim}
                    for (path, line, quote), claim in zip(citations, claims)]
        payload = self.root.parent / "sources.json"
        payload.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
        receipt = self.cli("report-save", title, "--template", template_id, "--template-version", "1",
                           "--template-revision", definition["revision"], "--body", body, "--sources-file",
                           str(payload), *options)
        card = self.cli("report-show", receipt["id"])
        self.assertEqual((card["templateId"], card["templateRevision"], card["body"]),
                         (template_id, definition["revision"], body))
        self.assertEqual(len(card["sources"]), len(citations))
        for index, (path, number, quote) in enumerate(citations):
            checked = self.cli("report-source", card["id"], "--index", str(index))
            self.assertTrue(checked["available"])
            self.assertEqual((checked["path"], checked["line"], checked["quote"]), (path, int(number), quote))
            self.assertEqual(checked["claim"], claims[index])
        return card

    def test_weekly_direction_without_plan_keeps_events_separate_from_later_follow_up(self):
        sources = self.note("Дом", "Учёт воды",
                            "19 ноября 2026 года заменили счётчик холодной воды. Начальное показание — 0,0 м³.",
                            "23 ноября 2026 года управляющая компания запросила фотографию начального показания.")
        card = self.publish(
            "weekly-direction", "Дом · итоги за 16–22 ноября",
            "За неделю с 16 по 22 ноября дома заменили счётчик холодной воды.\n\n"
            "## Что изменилось\n\n- С 19 ноября расход можно отсчитывать от показания 0,0 м³.\n\n"
            "## Что дальше\n\n- 23 ноября управляющая компания запросила фотографию начального показания.",
            sources, ["Дата замены и исходное показание счётчика", "Запрос фотографии после отчётной недели"],
            "--start", "2026-11-16", "--end", "2026-11-22", "--as-of", "2026-11-23")
        self.assertEqual((card["start"], card["end"], card["asOf"]), ("2026-11-16", "2026-11-22", "2026-11-23"))
        self.assertEqual(list((self.root / "Plans").rglob("*.md")), [])

    def test_one_on_one_evidence_only_includes_shared_conversation_material(self):
        sources = self.note("ИТ-проекты", "Общие договорённости Лены и Льва",
                            "18 ноября 2026 года Лена и Лев договорились проверить округление сумм в отчёте.",
                            "23 ноября 2026 года Лев подготовил два примера округления для обсуждения.")
        private = "Личная заметка для себя о публичных выступлениях."
        self.cli("create", "note", "Личный дневник Лены", "--body", private)
        card = self.publish(
            "one-on-one", "Встреча Лены и Льва",
            "Лев, предлагаю вернуться к округлению сумм в отчёте.\n\n"
            "## С чего начнём\n\n- Что сейчас важнее всего обсудить?\n\n"
            "## Темы разговора\n\n- 18 ноября мы договорились проверить округление. "
            "Ты подготовил два примера. Что они помогли заметить?\n\n"
            "## Поддержка и следующие шаги\n\n- Какая помощь тебе пригодилась бы?\n"
            "- О чём стоит договориться до следующей встречи?", sources,
            ["Общая договорённость о проверке округления", "Два примера подготовлены к разговору"])
        emitted = [(self.root / card["path"]).read_text(encoding="utf-8"), json.dumps(card, ensure_ascii=False)]
        for index in range(len(sources)):
            context = self.cli("report-source", card["id"], "--index", str(index))
            emitted.extend(row["text"] for row in context["lines"])
        for visible in emitted:
            self.assertNotIn(private, visible)
            self.assertNotIn("Личный дневник Лены", visible)

    def test_topic_agenda_uses_only_evidence_for_the_open_choice(self):
        source = self.note("Парфюмерия", "Условия ноябрьской пробы",
                           "24 ноября 2026 года первую кедровую пробу нанесли на блоттер, вторую — на лён.")
        self.cli("create", "note", "Заказ флаконов", "--body", "24 ноября согласовали объём флакона.")
        card = self.publish(
            "topic-agenda", "Выбор носителя для кедровых проб",
            "## На чём сравним две кедровые пробы?\n\n"
            "К концу созвона хотим выбрать общий носитель для следующей проверки.\n\n"
            "## Что уже известно\n\n- 24 ноября носители различались. Первую пробу нанесли на блоттер, вторую — на лён.\n\n"
            "## Что обсудить\n\n1. Что хотим сравнить в одинаковых условиях?\n"
            "2. Какой носитель лучше подходит для этого сравнения — блоттер или лён?", source,
            ["Начальные условия сравнения образцов"])
        listed, = self.cli("report-list", "--match", "носителя")["items"]
        self.assertEqual(listed["id"], card["id"])
        self.assertEqual([s["path"] for s in card["sources"]], [source[0][0]])
        self.assertNotIn("флакон", (self.root / card["path"]).read_text(encoding="utf-8"))

    def test_project_status_series_preserves_each_cutoff_when_forecast_and_format_change(self):
        notes = self.note("ИТ-проекты", "Сверка прогноза",
                          "13 ноября 2026 года запуск ожидали 24 ноября, в согласованный срок.",
                          "20 ноября 2026 года прогноз запуска перенесли на 26 ноября. Согласованный срок — 24 ноября.")
        direction = storage.read_record(self.root / notes[0][0])["direction"]
        project = self.cli("create", "project", "Кабинет заказов", "--direction", direction)
        task = self.cli("task-create", "Запустить кабинет", "--project", project["id"], "--due", "2026-11-24")
        series = self.cli("report-series-create", "Кабинет заказов", "--template", "project-status",
                          "--cadence", "weekly", "--start", "2026-11-09")
        original = self.cli("report-template-show", "project-status")

        def publish_forecast(day, forecast, version, source):
            path, line, quote = source
            schedule = ("task-schedule", task["id"], "--forecast", forecast, "--source", path, "--line", line)
            self.cli(*schedule, "--apply", self.cli(*schedule)["revision"])
            state = self.cli("task-schedule", task["id"])
            self.assertEqual((state["due"], state["forecast"], state["completed"]), ("2026-11-24", forecast, None))
            period = self.cli("report-series-show", series["id"])
            return self.cli("report-save", "Кабинет заказов · " + day, "--body", quote, "--template", "project-status",
                            "--template-version", str(version), "--series", series["id"], "--start", period["nextStart"],
                            "--end", period["nextEnd"], "--as-of", day, "--source", path, line, quote,
                            "--source", state["path"], str(state["forecastLine"]), forecast)

        first = publish_forecast("2026-11-13", "2026-11-24", 1, notes[0])
        amended = self.cli("report-template-save", original["title"], "--id", original["id"], "--revision",
                           original["revision"], "--audience", original["audience"], "--body", original["body"],
                           "--guidance", original["guidance"] + "\nУкажите причину изменения прогноза, если она известна.")
        second = publish_forecast("2026-11-20", "2026-11-26", amended["version"], notes[1])
        page = self.cli("report-list", "--series", series["id"])
        self.assertEqual([(r["id"], r["templateVersion"], r["asOf"]) for r in page["items"]],
                         [(second["id"], 2, "2026-11-20"), (first["id"], 1, "2026-11-13")])
        self.assertTrue(self.cli("report-source", first["id"], "--index", "0")["available"])
        previous = self.cli("report-source", first["id"], "--index", "1")
        self.assertEqual((previous["quote"], previous["available"]), ("2026-11-24", False))
        self.assertTrue(self.cli("report-source", second["id"], "--index", "1")["available"])
