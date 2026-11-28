import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import threading
import unittest

from second_brain import reports, storage
from second_brain.gantt import Gantt
from second_brain.planning import Planning
from second_brain.webserver import server


class MarkdownEditorBrowserTests(unittest.TestCase):
    def test_card_issue_and_selected_gantt_row_in_chromium(self):
        chrome = (shutil.which("chromium") or shutil.which("google-chrome")
                  or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        node = shutil.which("node")
        if not node or not Path(chrome).is_file():
            self.skipTest("Для проверки редактора нужны Chromium и Node.js.")

        with TemporaryDirectory() as directory:
            root = storage.initialize(Path(directory) / "Учебный редактор")
            areas = [storage.read_record(storage.create(root, "direction", name))["id"]
                     for name in ("ИТ-проекты", "Личное", "Парфюмерия")]
            note = storage.create(root, "note", "Замена диска", "Стенд доступен.", direction=areas[0])
            record = storage.read_record(note)
            quote = "Встреча у вокзала согласована."
            source = storage.create(root, "note", "Маршрут", quote, direction=areas[1])
            source_line = source.read_text(encoding="utf-8").splitlines().index(quote) + 1
            issue = reports.save_issue(root, title="Подготовка к поездке", template_id="free-report",
                                       template_version=1, draft=True, body="Согласовали встречу.",
                                       sources=[{"path": source.relative_to(root).as_posix(), "line": source_line,
                                                 "quote": quote, "claim": "Место встречи"}])
            template = reports.save_template(root, title="Проверка маршрута", audience="Организатор",
                                             guidance="Укажите дату.", body="## Встреча")
            template_path = root / template["path"]
            original_template = template_path.read_bytes()
            planning = Planning(root)
            plan = planning.create_plan("Зимние пробы")
            task = planning.create_task("Сравнить пробы", direction=areas[2], due="2026-11-30")
            planning.add_task(plan["id"], task["id"])
            data = {"card": record["id"], "issue": issue["id"], "template": template["id"],
                    "plan": plan["id"], "task": task["id"],
                    "issuePath": str(root / issue["path"])}
            profile = Path(directory) / "chrome"
            httpd = server(root, 0, mode="work")
            thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            try:
                chrome_args = [chrome, "--headless", "--disable-gpu", "--no-first-run", "--no-sandbox",
                               "--user-data-dir=" + str(profile), "--remote-debugging-port=0", "about:blank"]
                with subprocess.Popen(chrome_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as browser:
                    try:
                        run = subprocess.run([node, str(Path(__file__).parent / "browser" / "markdown-editor.mjs"),
                                              str(profile / "DevToolsActivePort"),
                                              f"http://127.0.0.1:{httpd.server_port}/", json.dumps(data)],
                                             capture_output=True, text=True, timeout=65)
                        if run.returncode == 77:
                            self.skipTest("Нужен Node.js со встроенным WebSocket.")
                        self.assertEqual(run.returncode, 0, run.stderr)
                    finally:
                        if browser.poll() is None:
                            browser.terminate()
                        try:
                            browser.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            browser.kill()
            finally:
                httpd.shutdown()
                thread.join()
                httpd.server_close()

            self.assertIn("**Диски заменены.**", storage.read_record(note)["body"])
            saved = reports.get_issue(root, issue["id"])
            self.assertIn("Внешняя правка.", saved["body"])
            self.assertNotIn("Устаревший черновик.", saved["body"])
            self.assertEqual(saved["sources"][0]["quote"], quote)
            self.assertEqual(saved["editorialStatus"], "draft")
            self.assertEqual(template_path.read_bytes(), original_template)
            current = reports.get_template(root, template["id"])
            self.assertEqual((current["version"], current["audience"], current["body"]),
                             (3, "Участники поездки", "## Проверка маршрута\n\n**Место уточнено.**"))
            detail = Gantt(root).detail(task["id"])
            self.assertEqual(detail["brief"]["text"], "Кедр выбран для второй пробы.")
            self.assertTrue(detail["brief"]["manual"])
            self.assertEqual((detail["forecast"], detail["due"]), ("2026-11-29", "2026-11-30"))
