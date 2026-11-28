import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from second_brain import storage
from second_brain.__main__ import command_line, run
from second_brain.webserver import server


class ReportInterfaceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = storage.initialize(Path(tmp.name) / "Vault")
        self.args = command_line()
        self.httpd = server(self.root, port=0, mode="work")
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.httpd.shutdown)

    def cli(self, *args):
        return run(self.args.parse_args(["--vault", str(self.root), *args]))

    def request(self, path, payload=None, *, status=200, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            conn.request("GET" if payload is None else "POST", path,
                         body=None if payload is None else json.dumps(payload),
                         headers={"Content-Type": "application/json", **(headers or {})})
            response = conn.getresponse()
            body = response.read().decode()
            self.assertEqual(response.status, status, body[:2000])
            return json.loads(body) if "json" in response.getheader("Content-Type") else body
        finally:
            conn.close()

    def test_issue_from_stdin_keeps_template_version_and_opens_verified_lines(self):
        definition = {"title": "Протокол наблюдений", "body": "## Результат", "audience": "Участники пробы",
                      "guidance": "Опишите результат наблюдения.", "requestId": "scent-format"}
        first = self.request("/api/reports/template", definition)
        selected = self.cli("report-template-show", first["id"])
        self.assertEqual(selected["revision"], first["revision"])
        quote = "Кедровая нота сохранилась после стирки."
        filename = "Notes/Результат пробы.md"
        path = self.root / filename
        path.write_text("# Результат\n\n" + quote + "\n", encoding="utf-8")
        evidence = self.root.parent / "sources.json"
        evidence.write_text(json.dumps([{"path": filename, "line": 3, "claim": "Наблюдение после стирки",
                                         "quote": quote}]), encoding="utf-8")
        body = "## Результат\n\n" + quote
        argv = [sys.executable, "-m", "second_brain", "--vault", str(self.root), "report-save", "Проба на льне",
                "--body-file", "-", "--template", first["id"], "--template-version", str(first["version"]),
                "--template-revision", selected["revision"], "--sources-file", str(evidence), "--request-id", "linen"]
        saved = subprocess.run(argv, input=body, encoding="utf-8", check=True, capture_output=True, timeout=15)
        self.assertEqual(len(saved.stdout.splitlines()), 1)
        receipt = json.loads(saved.stdout)
        self.assertNotIn("body", receipt)
        self.assertEqual(receipt["sourceCount"], 1)
        reader = self.request("/api/reports/issue?id=" + receipt["id"])
        self.assertEqual(reader["body"].strip(), body)
        self.assertEqual(self.cli("report-show", receipt["id"], "--brief")["sourceCount"], 1)
        source, = reader["sources"]
        self.assertEqual(self.cli("report-source", receipt["id"], "--index", "0")["quote"], source["quote"])
        cited = self.request(source["href"].split("#", 1)[0])
        self.assertIn(quote, cited)
        self.assertIn('id="cited-line"', cited)
        next_version = self.cli("report-template-save", definition["title"], "--id", first["id"],
                                "--revision", first["revision"], "--body", "## Выводы", "--audience", definition["audience"],
                                "--guidance", "Запишите воспроизводимые наблюдения.")
        self.assertGreater(next_version["version"], first["version"])
        self.assertEqual(self.request(f'/api/reports/template?id={first["id"]}&version={first["version"]}')["body"].strip(),
                         definition["body"])
        self.assertEqual(self.cli("report-show", receipt["id"])["templateVersion"], first["version"])
        self.request("/api/reports/template", {**definition, "body": "## Чужая правка", "id": first["id"],
                                             "revision": first["revision"], "requestId": "stale-edit"}, status=409)
        path.unlink()
        unavailable = self.request(source["href"].split("#", 1)[0])
        self.assertIn("сохранена цитата", unavailable)
        self.assertIn(quote, unavailable)

    def test_series_same_period_retried_between_web_and_cli(self):
        card = self.cli("report-series-create", "Совет дома", "--template", "meeting-agenda",
                        "--cadence", "weekly", "--start", "2026-11-23", "--request-id", "home-council")
        series_id = card["id"]
        self.assertEqual(self.request("/api/reports/series?id=" + series_id)["nextEnd"], "2026-11-29")
        self.assertEqual(self.cli("report-series-list", "--due-on", "2026-11-28")["total"], 0)
        self.assertEqual(self.request("/api/reports/series?dueOn=2026-11-29")["items"][0]["id"], series_id)
        body = "Выберем время осмотра крыши."
        payload = {"title": "Осмотр крыши", "body": body, "templateId": "meeting-agenda", "templateVersion": 1,
                   "seriesId": series_id, "start": "2026-11-23", "end": "2026-11-29", "requestId": "roof"}
        published = self.request("/api/reports/issue", payload)
        retried = self.cli("report-save", payload["title"], "--template", "meeting-agenda", "--template-version", "1",
                           "--body", body, "--series", series_id, "--start", "2026-11-23", "--end", "2026-11-29",
                           "--request-id", payload["requestId"])
        self.assertEqual(published["id"], retried["id"])
        issue, = self.request("/api/reports/issues?seriesId=" + series_id)["items"]
        self.assertNotIn("body", issue)
        self.assertEqual((issue["id"], self.cli("report-series-show", series_id)["nextStart"]),
                         (published["id"], "2026-11-30"))
        page = self.cli("report-template-list", "--limit", "1")
        self.assertTrue(page["truncated"])
        self.assertNotIn("body", page["items"][0])

    def test_long_russian_report_round_trips_without_enlarging_the_catalog(self):
        body = "За встречу согласовали условия следующей проверки.\n" * 750
        saved = self.request("/api/reports/issue", {"title": "Протокол", "templateId": "free-report",
                                                   "templateVersion": 1, "body": body})
        self.assertEqual(self.cli("report-show", saved["id"])["body"], body.rstrip())
        listing = self.cli("report-list", "--limit", "1")
        self.assertLess(len(json.dumps(listing, ensure_ascii=False).encode()), 1500)
        self.assertEqual(listing["items"][0]["id"], saved["id"])

    def test_finalize_the_draft_reviewed_in_the_ui_after_a_concurrent_edit(self):
        series = self.cli("report-series-create", "Наблюдения", "--template", "free-report",
                          "--cadence", "weekly", "--start", "2026-11-23")
        args = ("report-save", "Контрольный образец", "--template", "free-report", "--template-version", "1",
                "--body", "Кедр заметен через час.", "--series", series["id"], "--start", "2026-11-23",
                "--end", "2026-11-29", "--request-id", "sample")
        draft = self.cli(*args, "--draft")
        self.assertEqual(draft["editorialStatus"], "draft")
        self.assertTrue(self.cli(*args, "--draft")["replayed"])
        issue, = self.request("/api/reports/issues?seriesId=" + series["id"])["items"]
        self.assertEqual((issue["id"], issue["editorialStatus"]), (draft["id"], "draft"))
        path = self.root / issue["path"]
        path.write_text(path.read_text().replace("через час.", "через два часа."))
        changed = path.read_bytes()
        request = {"id": draft["id"], "revision": draft["revision"]}
        self.request("/api/reports/finalize", request, status=409)
        self.assertEqual(path.read_bytes(), changed)
        reader = self.request("/api/reports/issue?id=" + draft["id"])
        request["revision"] = reader["revision"]
        finished = self.request("/api/reports/finalize", request)
        self.assertEqual(finished["editorialStatus"], "final")
        self.assertEqual((finished["id"], finished["path"]), (draft["id"], draft["path"]))
        self.assertNotEqual(reader["revision"], finished["revision"])
        self.assertNotIn("body", finished)
        after = path.read_bytes()
        self.request("/api/reports/finalize", request, status=409)
        self.assertEqual(self.cli("report-finalize", draft["id"], "--revision", finished["revision"]), finished)
        self.assertEqual(self.cli(*args, "--draft")["editorialStatus"], "final")
        self.assertEqual(path.read_bytes(), after)
        final = self.cli("report-show", draft["id"])
        self.assertEqual((final["body"], final["finalizedAt"]), (reader["body"], finished["finalizedAt"]))
        self.assertEqual(self.request("/api/reports/issues?seriesId=" + series["id"])["total"], 1)
        self.assertEqual(self.cli("report-series-show", series["id"])["nextStart"], "2026-11-30")

    def test_finalize_cli_accepts_a_web_draft_and_preserves_historical_statuses(self):
        note = self.root / "Notes/Наблюдение.md"
        note.write_text("Проба на льне\n")
        card = {"title": "Наблюдение", "templateId": "free-report", "templateVersion": 1,
                "body": "Проба сохранилась на льне.", "sources": [{"path": "Notes/Наблюдение.md", "line": 1}]}
        self.assertEqual(self.request("/api/reports/issue", card)["editorialStatus"], "final")
        self.request("/api/reports/issue", {**card, "draft": "true"}, status=400)
        draft = self.request("/api/reports/issue", {**card, "draft": True})
        path = self.root / draft["path"]
        base = path.read_text().replace('editorial_status: "draft"\n', '')
        def load_historical(props):
            historical = base.replace("creation_hash:", props + "creation_hash:", 1)
            path.write_bytes(historical.replace("\n", "\r\n").encode())
            return self.cli("report-show", draft["id"])

        for props, status in (("", None), ("draft: false\n", None), ("publication_status: posted\n", "posted"),
                              ("draft: true\n", "draft")):
            historical = load_historical(props)
            self.assertEqual(historical.get("editorialStatus"), status)
            if status != "draft":
                before = path.read_bytes()
                self.request("/api/reports/finalize", {"id": draft["id"], "revision": historical["revision"]}, status=400)
                self.assertEqual(path.read_bytes(), before)
        historical = load_historical("publication_status: draft # вручную\ncustom:\n  - сохранено\n")
        before = path.read_bytes()
        saved = self.cli("report-finalize", draft["id"], "--revision", historical["revision"])
        after = path.read_bytes()
        added = ('editorial_status: "final"\r\nfinalized_at: "' + saved["finalizedAt"] + '"\r\n').encode()
        self.assertEqual(after.replace(added, b"", 1), before)
        result = self.cli("report-show", draft["id"])
        self.assertEqual(result["editorialStatus"], "final")
        for field in ("path", "body", "templateRevision", "title", "sources"):
            self.assertEqual(result[field], historical[field])

    def test_template_origin_and_search_use_current_version_before_paging(self):
        original = self.cli("report-template-show", "meeting-agenda")
        self.assertEqual((original["origin"], original["path"]), ("builtin", None))
        self.cli("report-series-create", "Домовой совет", "--template", original["id"],
                 "--cadence", "monthly", "--start", "2026-11-01")
        frozen = self.cli("report-template-show", original["id"])
        self.assertTrue(frozen["path"])
        self.assertEqual(frozen["origin"], "builtin")
        definition = {"title": original["title"], "guidance": "", "body": "## Вопросы",
                      "audience": "Участники совета"}
        own = self.request("/api/reports/template", definition)
        second = self.request("/api/reports/template", {**definition, "title": "Повестка совета",
                                                       "audience": "Жильцы"})
        revised = self.request("/api/reports/template", {**definition, "title": "Сбор совета",
                                                        "id": original["id"], "revision": frozen["revision"]})
        self.assertEqual((revised["origin"], revised["version"]), ("builtin", 2))
        current = self.cli("report-template-list", "--origin", "builtin", "--match", "СОВЕТА")
        self.assertEqual([row["id"] for row in current["items"]], [original["id"]])
        old = self.cli("report-template-list", "--origin", "builtin", "--match", original["title"])
        self.assertEqual(old["total"], 0)
        self.assertEqual(self.cli("report-template-list", "--match", original["title"])["items"][0]["id"], own["id"])
        first = self.cli("report-template-list", "--origin", "custom", "--match", " СОВЕТА ", "--limit", "1")
        query = urlencode({"origin": "custom", "match": "Совета", "limit": 1, "offset": first["nextOffset"]})
        last = self.request("/api/reports/templates?" + query)
        self.assertEqual((first["total"], last["total"], last["nextOffset"]), (2, 2, None))
        self.assertEqual({(r["id"], r["origin"]) for r in first["items"] + last["items"]},
                         {(own["id"], "custom"), (second["id"], "custom")})

    def test_as_of_date_survives_cli_and_web_separately_from_period(self):
        dates = {"start": "2026-11-16", "end": "2026-11-22", "asOf": "2026-11-23"}
        payload = {"title": "Итоги испытаний", "templateId": "free-report", "templateVersion": 1,
                   "body": "Контрольную проверку завершили.", "requestId": "week-results", **dates}
        card = self.request("/api/reports/issue", payload)
        receipt = self.cli("report-save", card["title"], "--body", payload["body"], "--template", "free-report",
                           "--template-version", "1", "--start", dates["start"], "--end", dates["end"],
                           "--as-of", dates["asOf"], "--request-id", payload["requestId"])
        self.assertEqual(receipt["id"], card["id"])
        self.assertTrue(receipt["replayed"])
        self.assertEqual({key: receipt[key] for key in dates}, dates)
        self.assertEqual(self.request("/api/reports/issues")["items"][0]["asOf"], dates["asOf"])

    def test_title_search_pages_across_authored_and_weekly_issues(self):
        plan = self.cli("plan-create", "Испытания")["id"]
        self.cli("plan-close", plan, "--apply", self.cli("plan-close", plan)["revision"])
        weekly = self.cli("weekly-review", plan, "--save")
        first = self.cli("report-save", "Обзор работ", "--template", "free-report", "--template-version", "1",
                         "--body", "Проверку согласовали.")
        self.cli("report-save", "Протокол", "--template", "free-report", "--template-version", "1",
                 "--body", "Следующий обзор проведём после проверки.")
        page_one = self.cli("report-list", "--match", "ОБЗОР", "--limit", "1")
        params = urlencode({"match": "ОБЗОР", "limit": 1, "offset": 1})
        page_two = self.request("/api/reports/issues?" + params)
        self.assertEqual((page_one["total"], page_two["total"]), (2, 2))
        self.assertEqual({page_one["items"][0]["id"], page_two["items"][0]["id"]}, {weekly["id"], first["id"]})

    def test_status_filter_tracks_the_same_issue_before_and_after_approval(self):
        params = ["--template", "free-report", "--template-version", "1", "--body", "Сверили условия пробы."]
        draft = self.cli("report-save", "Кедровая проба", *params, "--draft")
        self.cli("report-save", "Условия пробы", *params, "--draft")
        self.cli("report-save", "Кедровая проба за октябрь", *params)
        query = urlencode({"match": "КЕДРОВАЯ", "status": "draft", "limit": 1})
        before = self.request("/api/reports/issues?" + query)
        self.assertEqual((before["total"], before["items"][0]["id"]), (1, draft["id"]))
        self.cli("report-finalize", draft["id"], "--revision", draft["revision"])
        self.assertEqual(self.request("/api/reports/issues?" + query)["total"], 0)
        self.assertEqual(self.cli("report-list", "--match", "кедровая", "--status", "final")["total"], 2)
        self.request("/api/reports/issues?status=anything", status=400)

    def test_invalid_input_and_wrong_binding_do_not_write(self):
        before = sorted(self.root.rglob("*.md"))
        self.request("/api/reports/templates?limit=0", status=400)
        self.request("/api/reports/templates?origin=unknown", status=400)
        self.request("/api/reports/issue", {"title": "Пустой выпуск"}, status=400)
        self.request("/api/reports/issue", {"title": "Результат", "templateId": "free-report", "templateVersion": 1,
                                           "templateRevision": "0" * 64, "body": "Работа завершена."}, status=409)
        self.request("/api/reports/series", {"title": "Серия", "id": "x", "cadence": "daily",
                                            "start": "2026-08-01", "templateId": "free-report"}, status=400)
        self.request("/api/reports/templates", headers={"X-Brain-Vault": "other-vault"}, status=409)
        self.assertEqual(sorted(self.root.rglob("*.md")), before)
