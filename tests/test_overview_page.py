import json
import shutil
import subprocess
import sys
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from second_brain import history_archive, project_updates, storage
from second_brain.gantt import Gantt
from second_brain.planning import Planning
from second_brain.webserver import server


class OverviewPageTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name) / "Учебный обзор")
        self.http = server(self.root, 0, mode="work")
        worker = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(self.http.shutdown)

    def get(self, endpoint, *, expected=200, **params):
        connection = HTTPConnection("127.0.0.1", self.http.server_port, timeout=5)
        try:
            connection.request("GET", endpoint + ("?" + urlencode(params) if params else ""))
            response = connection.getresponse()
            self.assertEqual(response.status, expected, endpoint)
            return json.load(response) if response.headers.get_content_type() == "application/json" else response.read().decode()
        finally:
            connection.close()

    def cli(self, *args):
        result = subprocess.check_output(
            [sys.executable, "-m", "second_brain", "--vault", str(self.root), "overview", *args],
            cwd=Path(__file__).resolve().parents[1], text=True, timeout=10)
        return json.loads(result)

    def test_empty_states_leave_existing_overview_and_navigation_available(self):
        states = self.get("/api/direction-states")
        self.assertEqual((states["items"], states["total"], states["truncated"]), ([], 0, False))
        self.assertEqual(self.cli("--states"), states)
        self.assertEqual(self.get("/api/workspace")["records"], [])
        self.assertEqual(self.cli("--brief")["counts"]["direction"], 0)
        self.assertTrue(self.cli("--folders")["items"])
        shell = self.get("/")
        self.assertNotIn('data-page="plans"', shell)
        for route in ("overview", "inbox", "activity", "records", "people", "map"):
            with self.subTest(route=route):
                self.assertIn(f'href="#{route}"', shell)
        self.assertIn('href="/about.html"', shell)

    def browser_html(self, route, profile):
        chrome = (shutil.which("chromium") or shutil.which("google-chrome")
                  or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if not Path(chrome).is_file():
            self.skipTest("Для проверки навигации нужен Chromium.")
        chrome_args = [chrome, "--headless", "--disable-gpu", "--no-first-run", "--no-sandbox",
                       "--user-data-dir=" + profile]
        url = f"http://127.0.0.1:{self.http.server_port}/#{route}"
        node = shutil.which("node")
        if node:
            port_file = Path(profile) / "DevToolsActivePort"
            port_file.unlink(missing_ok=True)
            with subprocess.Popen([*chrome_args, "--remote-debugging-port=0", "about:blank"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as browser:
                try:
                    helper = Path(__file__).parent / "browser/document.mjs"
                    result = subprocess.run([node, str(helper), str(port_file), url],
                                            capture_output=True, text=True, timeout=30)
                    if result.returncode != 77:  # Older Node versions lack a built-in WebSocket.
                        self.assertEqual(result.returncode, 0, result.stderr)
                        return result.stdout
                finally:
                    try:
                        browser.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        browser.terminate()
                        try:
                            browser.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            browser.kill()
        # Keep the Chromium-only path for installations without a supported Node.
        for _ in range(3):
            html = subprocess.check_output([
                *chrome_args, "--virtual-time-budget=5000", "--dump-dom", url],
                text=True, timeout=20, stderr=subprocess.DEVNULL)
            if '<main id="content" tabindex="-1" aria-busy="true"' not in html:
                return html
        self.fail(f"Страница #{route} не дождалась ответа API.")

    def test_one_planning_tab_for_bookmarks_filtered_catalog_and_chart(self):
        plan = Planning(self.root).create_plan("Зимние пробы")
        with TemporaryDirectory() as profile:
            for route in ("plans?q=пробы", "activity?view=plans", "plan/" + plan["id"]):
                with self.subTest(route=route):
                    html = self.browser_html(route, profile)
                    self.assertIn('data-page="activity" aria-current="page">Проекты и Гант', html)
                    self.assertNotIn('data-page="plans"', html)
                    self.assertIn("Зимние пробы", html)
                    if not route.startswith("plan/"):
                        self.assertIn('aria-pressed="true">Планы</button>', html)

    def test_browser_opens_new_and_edit_forms_with_an_unrelated_broken_note(self):
        healthy = storage.read_record(storage.create(self.root, "project", "Здоровая карточка"))
        (self.root / "Notes/Правка.md").write_text("---\nid: unfinished\ntype: note\ntitle: В работе\n", encoding="utf-8")
        self.get("/api/workspace", expected=400)
        with TemporaryDirectory() as profile:
            for route, title in (("new/note", "Новая карточка"), ("edit/" + healthy["id"], "Редактировать карточку")):
                with self.subTest(route=route):
                    html = self.browser_html(route, profile)
                    if route.startswith("edit/"):
                        self.assertIn(f'id="editor-dialog-title" tabindex="-1">{title}</h2>', html)
                        self.assertIn('class="editor-dialog" aria-labelledby="editor-dialog-title" open', html)
                    else:
                        self.assertIn(f'class="work-title">{title}</h1>', html)
                    self.assertIn('class="work-panel work-editor"', html)

    def test_record_links_open_managed_and_plain_markdown_inside_the_card_folders(self):
        card = storage.create(self.root, "project", "Зелёный чай", "## Образцы\n\nТри пробы.")
        path = card.relative_to(self.root).as_posix()
        by_path = self.get("/api/record", path=path)
        self.assertEqual(by_path, self.get("/api/record", id=by_path["id"]))
        folder = self.root / "Notes" / "Sampling"
        folder.mkdir()
        note = folder / "Дегустация.md"
        raw = "---\norigin: demo\n---\n# Дегустация\n\n## Вывод\n\nОбразец принят."
        note.write_text(raw, encoding="utf-8")
        result = self.get("/api/record", path="Directions/Дегустация.md", short="1")
        self.assertEqual((result["type"], result["path"], result["raw"], result["body"]),
                         ("markdown", "Notes/Sampling/Дегустация.md", raw, raw[raw.index("#"):]))
        self.assertEqual(self.get("/api/record", path="Дегустация.md", short="1"), result)
        # Two short names must never silently take the reader to the wrong source.
        (self.root / "Projects" / note.name).write_text("Другие пробы.", encoding="utf-8")
        self.get("/api/record", path="Directions/Дегустация.md", short="1", expected=400)
        self.assertEqual(self.get("/api/record", path=result["path"], short="1")["raw"], raw)
        (self.root / "Notes" / "Outside.md").symlink_to(self.root / "Home.md")
        for invalid in ("Settings/Test.md", "Notes/../../Home.md", "/Notes/Test.md", "Notes/.cache/File.md",
                        "Notes/Outside.md", "Notes/Test.exe", "Notes/..\\Home.md", "People/Test.md"):
            with self.subTest(invalid=invalid):
                self.get("/api/record", path=invalid, expected=400)
        self.get("/api/record", path="Notes/Missing.md", expected=404)

    def test_current_project_by_id_path_and_short_name_omits_the_full_markdown(self):
        old = "Снимок до переключения."
        body = "## История статусов\n\n" + (old + "\n") * 400
        path = storage.create(self.root, "project", "Окно поставки", body, status="Согласовано")
        record = storage.read_record(path)
        revision = record["revision"]
        for field, text in [("brief", "Поставка согласована.")] + [("event", f"Решение {n}.") for n in range(4)]:
            saved = project_updates.write_update(self.root, record["id"], field=field, text=text,
                                                 actor="manual", revision=revision)
            revision = saved["revision"]
        relative = path.relative_to(self.root).as_posix()
        for selector in ({"id": record["id"]}, {"path": relative}, {"path": path.name, "short": 1}):
            with self.subTest(selector=selector):
                current = self.get("/api/record", **selector, view="current")
                self.assertTrue({"raw", "body", "displayBody", "body_start"}.isdisjoint(current))
                self.assertEqual((current["status"], current["revision"]), ("Согласовано", revision))
                self.assertEqual(current["fieldSources"]["status"]["path"], relative)
                updates = current["projectUpdates"]
                self.assertEqual((updates["brief"]["text"], updates["total"]), ("Поставка согласована.", 4))
                self.assertEqual([e["text"] for e in updates["events"]], ["Решение 3.", "Решение 2.", "Решение 1."])
                self.assertNotIn(old, json.dumps(current, ensure_ascii=False))
                full = self.get("/api/record", **selector)
                self.assertEqual(full["raw"], path.read_text())
                self.assertIn(old, full["body"])
                self.assertIn("Решение 0.", full["body"])

        with TemporaryDirectory() as profile:
            route = "record/" + record["id"]
            compact = self.browser_html(route, profile)
            self.assertIn("Полный текст и история", compact)
            self.assertNotIn(old, compact)
            self.assertNotIn("Решение 0.", compact)
            full = self.browser_html(route + "?view=full", profile)
            self.assertIn("К сводке проекта", full)
            self.assertIn(old, full)
            self.assertIn("Решение 0.", full)
            line = next(n for n, text in enumerate(path.read_text().splitlines(), 1) if text == old)
            self.assertIn('class="source-line highlight"', self.browser_html(route + "?line=" + str(line), profile))

    def test_verified_quarter_opens_as_read_only_markdown_in_the_workspace(self):
        body = "## Журнал\n" + "".join(f"- 2020-01-{n:02d} — Зимняя проба {n}.\n" for n in range(1, 7))
        project = storage.create(self.root, "project", "Парфюмерная коллекция", body)
        original = storage.read_record(project)
        key = original["id"]
        sentence = "- 2020-01-01 — Зимняя проба 1."
        initial = project.read_text().splitlines()
        old = {"line": initial.index(sentence) + 1, "revision": original["revision"]}
        plan = history_archive.rotate(self.root, key, as_of="2025-01-01")
        self.assertEqual(plan["moved"], 1)
        history_archive.rotate(self.root, key, as_of="2025-01-01", apply=plan["revision"])
        archive, = plan["archives"]
        current = self.get("/api/record", id=key, view="current")
        self.assertEqual(current["projectUpdates"]["archives"], [{"path": archive["path"], "quarter": "2020-Q1"}])
        self.assertIsNone(current["projectUpdates"]["brief"])
        read = self.get("/api/record", path=archive["path"])
        self.assertEqual((read["project_id"], read["type"], read["raw"]),
                         (key, "markdown", (self.root / archive["path"]).read_text()))
        self.assertIn("Зимняя проба 1.", read["body"])
        bookmark = self.get("/api/record", id=key, **old)
        self.assertEqual(bookmark["path"], read["path"])
        self.assertEqual(bookmark["raw"].splitlines()[bookmark["resolvedLine"] - 1], sentence)
        with TemporaryDirectory() as profile:
            html = self.browser_html("record?" + urlencode({"path": archive["path"]}), profile)
            self.assertIn("Зимняя проба 1.", html)
            self.assertIn('href="#record/' + key + '"', html)
            self.assertNotIn("Редактировать</a>", html)
            archived = self.browser_html("record/" + key + "?" + urlencode(old), profile)
            self.assertIn('class="source-line highlight"><span class="line-number">' + str(bookmark["resolvedLine"]) +
                          '</span><code class="line-text">' + sentence, archived)
            self.assertNotIn("Карточка изменилась после открытия обзора", archived)
            kept = "- 2020-01-06 — Зимняя проба 6."
            project.write_text(project.read_text().replace(kept, "- 2020-01-06 — Исправление."))
            stale = dict(old, line=initial.index(kept) + 1)
            html = self.browser_html("record/" + key + "?" + urlencode(stale), profile)
            self.assertIn("Карточка изменилась после открытия обзора", html)
            self.assertNotIn('class="source-line highlight"', html)
        (self.root / archive["path"]).write_text(read["raw"] + "Внешняя правка.\n")
        self.get("/api/record", path=archive["path"], expected=409)

    def test_folder_counts_cover_reports_templates_series_and_plan_groups(self):
        cards = [("Reports/2026/Выпуск.md", "report_issue", "issue"), ("Reports/Неделя.md", "weekly_review", "week"),
                 ("Settings/Templates/Reports/example/v1.md", "report_template", "example"),
                 ("Settings/Templates/Reports/example/v2.md", "report_template", "example"),
                 ("Settings/Formats/ReportSeries/Серия.md", "report_series", "series"),
                 ("Plans/Groups/calendar/Направление.md", "gantt_group", "group")]
        for name, kind, identifier in cards:
            path = self.root / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_text(f'---\ntype: {kind}\nid: {identifier}\nschema_version: 1\ntitle: Карточка\n---\nМатериал.\n')
        (self.root / "Reports/Черновик.md").write_text("# Обычный Markdown")
        (self.root / "Plans/Копия.md").write_bytes((self.root / cards[0][0]).read_bytes())
        rows = self.get("/api/workspace")["folders"]
        counts = {r["path"]: r["count"] for r in rows}
        for folder, expected in (("Reports", 2), ("Reports/2026", 1), ("Settings", 3),
                                 ("Settings/Templates/Reports", 2), ("Settings/Formats/ReportSeries", 1),
                                 ("Plans", 1), ("Plans/Groups", 1)):
            self.assertEqual(counts[folder], expected, folder)
        offset = next(n for n, row in enumerate(rows) if row["path"] == "Reports")
        self.assertEqual(self.cli("--folders", "--offset", str(offset), "--limit", "1")["items"], [rows[offset]])

    def test_catalog_pages_and_scoped_states_refresh_from_live_markdown(self):
        areas = [storage.read_record(storage.create(self.root, "direction", name))["id"]
                 for name in ("ИТ-проекты", "Личное", "Парфюмерия")]
        project = storage.create(self.root, "project", "Разбор заявок", direction=areas[0])
        card = storage.read_record(project)
        summary = "Правила разбора согласованы."
        event = "Владелец утвердил правила разбора."
        updated = project_updates.write_update(self.root, card["id"], actor="manual", field="brief",
                                                text=summary, revision=card["revision"])
        project_updates.write_update(self.root, card["id"], actor="manual", field="event",
                                     text=event, date="2026-11-19", revision=updated["revision"])

        seen = []
        for offset in range(3):
            page = self.get("/api/direction-states", limit=1, offset=offset)
            self.assertEqual(self.cli("--states", "--limit", "1", "--offset", str(offset)), page)
            self.assertEqual((len(page["items"]), page["total"], page["truncated"]), (1, 3, offset < 2))
            seen.append(page["items"][0]["id"])
        self.assertCountEqual(seen, areas)

        def scoped():
            response = self.get("/api/direction-states", direction=areas[0])
            self.assertEqual(self.cli("--states", "--direction", areas[0]), response)
            item, = response["items"]
            return item

        area = scoped()
        self.assertEqual((area["id"], area["projectCount"], area["taskCount"]), (areas[0], 1, 0))
        self.assertIn(summary, [brief["text"] for brief in area["briefs"]])
        self.assertEqual((area["latest"]["text"], area["latest"]["date"]), (event, "2026-11-19"))
        for entry in (*area["briefs"], area["latest"]):
            source = entry["source"]
            self.assertEqual(entry["record"]["id"], card["id"])
            self.assertTrue(source["href"])
            detail = self.get("/api/record", id=entry["record"]["id"])
            self.assertEqual(detail["path"], source["path"])
            self.assertIn(entry["text"], detail["raw"].splitlines()[source["line"] - 1])
        revised = "Владелец опубликовал правила разбора."
        project.write_text(project.read_text(encoding="utf-8").replace(summary, revised), encoding="utf-8")
        area = scoped()
        self.assertIn(revised, [brief["text"] for brief in area["briefs"]])
        self.assertEqual(area["latest"]["text"], event)
        empty, = self.get("/api/direction-states", direction=areas[2])["items"]
        self.assertEqual((empty["briefs"], empty["latest"]), ([], None))
        self.assertEqual(self.get("/api/record", id=card["id"])["projectUpdates"]["brief"]["text"], revised)

    def test_task_event_opens_its_cited_line_and_detects_an_external_correction(self):
        area = storage.read_record(storage.create(self.root, "direction", "Личное"))["id"]
        task = Planning(self.root).create_task("Подготовить документы", direction=area)
        text = "Документы переданы."
        Gantt(self.root).edit({"id": task["id"], "revision": task["revision"], "actor": "manual",
                              "field": "latest", "date": "2026-11-20", "text": text})
        state, = self.get("/api/direction-states", direction=area)["items"]
        self.assertEqual((state["taskCount"], state["latest"]["text"]), (1, text))
        url = state["latest"]["source"]["href"].split("#", 1)[0]
        document = self.get(url)
        self.assertIn('id="cited-line"', document)
        self.assertIn(text, document)
        path = self.root / task["path"]
        revised = "Документы проверены."
        path.write_text(path.read_text(encoding="utf-8").replace(text, revised), encoding="utf-8")
        self.get(url, expected=409)
        item, = self.cli("--states", "--direction", area)["items"]
        self.assertEqual(item["latest"]["text"], revised)
