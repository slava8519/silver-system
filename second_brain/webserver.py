"""Local HTTP transport shared by the guide and the working vault."""

from contextlib import nullcontext
import hashlib
import hmac
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import brief, demo_cleanup, report_web, storage, work_control
from .batch import Batch
from .capture import Capture, CaptureTooLarge, MAX_FILE_BYTES, MAX_TEXT_BYTES
from .inbox import Inbox
from .routing import Routing
from .search import visible_body_lines

WEB = Path(__file__).resolve().parent / "web"


class Handler(BaseHTTPRequestHandler):
    def _source_document(self, title, content, back, label):
        bridge = '<script type="module" src="/obsidian-workspace.js"></script>' if self.workspace_embed else ""
        return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · Второй мозг</title><link rel="icon" href="data:,"><link rel="stylesheet" href="/app.css">{bridge}</head>
<body><main><p><a class="back-link" href="{escape(back, quote=True)}">← {escape(label)}</a></p>
<article class="panel"><h1 class="panel-heading">{escape(title)}</h1>{content}</article></main></body></html>""".encode()

    def log_message(self, format, *args):
        pass

    def _send(self, status, data, content_type="application/json; charset=utf-8", *, download=None):
        if getattr(self, "embedded_page", False) and isinstance(data, dict) and "error" in data:
            # Stale document links still offer navigation inside the Obsidian tab.
            data = self._source_document("Не удалось открыть страницу", f"<p>{escape(data['error'])}</p>",
                                         "/#overview", "Обзор хранилища")
            content_type = "text/html; charset=utf-8"
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if download is not None:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(download, safe=""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if getattr(self, "embedded_page", False) and content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", "frame-ancestors app://obsidian.md")
        else:
            self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _body(self, limit, *, allow_empty=False):
        length = int(self.headers.get("Content-Length", "-1"))
        if length > limit:
            self._send(413, {"error": "Материал превышает допустимый размер."})
            return None
        if length < 0 or (length == 0 and not allow_empty):
            raise ValueError("Запрос не содержит данных для сохранения.")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Передача прервалась до получения всех данных.")
        return body

    def _dispatch(self, post):
        host = self.headers.get("Host", "")
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed.update(("localhost", "127.0.0.1"))
        if host not in allowed or self.headers.get("Origin", f"http://{host}") != f"http://{host}":
            self._send(403, {"error": "Запрос разрешён только из локальной оболочки."})
            return
        url = urlsplit(self.path)
        path = url.path
        params = {key: values[0] for key, values in parse_qs(url.query).items()}
        # Each request uses one interface. A separate CLI process can also finish the guide.
        guide = self.server.guide
        if guide is not None and demo_cleanup.is_cleaned(self.server.vault.root):
            guide = None
        self.mode = "demo" if guide is not None else "work"
        self.reader = guide if guide is not None else self.server.reader
        frame_mode = params.get("embed")
        document = path in ("/", "/about.html") or path.startswith(("/source/", "/evidence/", "/original/", "/report-source/"))
        self.workspace_embed = not post and frame_mode == "workspace" and (document or path.startswith("/attachment/"))
        compact = not post and self.mode == "work" and path == "/" and frame_mode == "1"
        self.embedded_page = False
        binding = self.headers.get("X-Brain-Vault")
        if ((self.workspace_embed and not self.server.work_mode)
                or ((self.workspace_embed or compact) and params.get("binding") != self.server.vault_binding)
                or (binding is not None and (self.mode != "work" or binding != self.server.vault_binding))):
            raise storage.ConflictError("На этом порту открыто другое хранилище. Проверьте подключение в Obsidian.")
        self.embedded_page = compact or (self.workspace_embed and document)
        if post:
            capture_text = self.mode == "work" and path == "/api/inbox/text"
            if self.mode == "work" and path == "/api/inbox/file":
                if self.headers.get_content_type() != "application/octet-stream":
                    self._send(415, {"error": "Передайте содержимое файла."})
                    return
                content = self._body(MAX_FILE_BYTES, allow_empty=True)
                if content is not None:
                    self._send(201, Capture(self.server.vault.root).save_file(params.get("name"), content))
                return
            if self.headers.get_content_type() != "application/json":
                self._send(415, {"error": "Ожидался запрос JSON."})
                return
            # JSON escapes can use six bytes for one character of pasted text.
            json_limit = 6 * MAX_TEXT_BYTES + 4096 if capture_text else 64000
            if self.mode == "work" and path in ("/api/reports/issue", "/api/reports/template"):
                json_limit = 3 * 1024 * 1024  # Includes the report, citations and JSON Unicode escapes.
            content = self._body(json_limit)
            if content is None:
                return
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("Ожидался объект JSON.")
            if path == "/api/work/stop" and self.server.work_control:
                proof = data.get("proof")
                if (set(data) != {"instance", "proof"} or data["instance"] != self.server.work_control
                        or binding != self.server.vault_binding or not isinstance(proof, str)
                        or not proof.isascii() or not hmac.compare_digest(proof, self.server.stop_proof)):
                    self._send(403, {"error": "Не удалось подтвердить остановку этой рабочей оболочки."})
                    return
                self._send(200, {"stopping": True})
                self.server.shutdown()
                return
            if path == "/api/demo-cleanup":
                if set(data) != {"revision"} or not isinstance(data["revision"], str) or not data["revision"]:
                    raise ValueError("Для очистки подтвердите актуальный предпросмотр.")
                result = demo_cleanup.apply(self.server.vault.root, data["revision"])
                self._send(200, {**result, "workUrl": "/"})
                return
            if self.mode == "work" and self._team_request(path, data, post=True):
                return
            if self.mode == "work" and self._planning_request(path, data, post=True):
                return
            if self.mode == "work" and path.startswith("/api/reports/"):
                self._send(200, report_web.write(self.server.vault.root, path.removeprefix("/api/reports/"), data))
                return
            if capture_text:
                if data.keys() - {"text", "title"}:
                    raise ValueError("Для заметки передайте текст и при желании название.")
                self._send(201, Capture(self.server.vault.root).save_text(data.get("text"), data.get("title")))
                return
            if path == "/api/ask":
                self._send(200, self.reader.ask(data))
                return
            if path == "/api/citation":
                _, source = self.reader.open_citation(data)
                self._send(200, source)
                return
            if self.mode == "work" and path == "/api/records/create":
                self._send(201, self.server.vault.create_record(data))
                return
            if self.mode == "work" and path == "/api/records/edit":
                self._send(200, self.server.vault.edit_record(data))
                return
            if self.mode == "work" and path == "/api/project-brief":
                from .project_updates import write_update
                if data.keys() != {"id", "text", "revision"}:
                    raise ValueError("Укажите проект, текст сводки и прочитанную версию.")
                result = write_update(self.server.vault.root, data["id"], field="brief", text=data["text"],
                                      revision=data["revision"], actor="manual")
                self._send(200, result)
                return
            if self.mode == "work" and path == "/api/inbox/process":
                self._send(200, Inbox(self.server.vault.root).process(data))
                return
            if self.mode == "work" and path == "/api/inbox/resolve":
                self._send(200, Batch(self.server.vault.root).resolve(data.get("id"), data.get("clarification")))
                return
            if self.mode == "work" and path == "/api/inbox/rule":
                self._send(200, Routing(self.server.vault.root).save_rule(data))
                return
            if self.mode == "work" and path == "/api/person/correct":
                self._send(200, self.server.people.correct(data))
                return
            if self.mode == "demo" and path == "/api/directions":
                self._send(200, guide.add_direction(data))
                return
        elif path == "/api/demo-cleanup":
            self._send(200, demo_cleanup.preview(self.server.vault.root))
            return
        elif self.mode == "work" and path == "/api/workspace":
            self._send(200, {**self.server.vault.overview(), "root": str(self.server.vault.root)})
            return
        elif self.mode == "work" and path == "/api/direction-states":
            from .direction_overview import direction_overview
            result = direction_overview(self.server.vault.root, limit=int(params.get("limit", 5)),
                                        offset=int(params.get("offset", 0)), direction=params.get("direction"))
            self._send(200, result)
            return
        elif self.mode == "work" and path in ("/api/map", "/api/map/source"):
            from .relation_map import RelationMap
            service = RelationMap(self.server.vault.root)
            if path == "/api/map/source":
                result = service.read_source(params.get("id"), int(params.get("line", 0)), params.get("revision"))
            else:
                action = params.get("action", "home")
                result = service.query(action, id=params.get("id"), group=params.get("group"),
                                       query=params.get("query", ""), offset=int(params.get("offset", 0)),
                                       limit=int(params.get("limit", 5)))
                if action == "focus":
                    result = {**result, "root": str(self.server.vault.root)}
            self._send(200, result)
            return
        elif self.mode == "work" and self._team_request(path, params, post=False):
            return
        elif self.mode == "work" and self._intake_page(path, params):
            return
        elif self.mode == "work" and self._planning_request(path, params, post=False):
            return
        elif self.mode == "work" and path.startswith("/api/reports/"):
            self._send(200, report_web.read(self.server.vault.root, path.removeprefix("/api/reports/"), params))
            return
        elif self.mode == "work" and path.startswith("/report-source/"):
            from .reports import read_issue_source
            identifier = unquote(path.removeprefix("/report-source/"))
            source = read_issue_source(self.server.vault.root, identifier, int(params.get("index", -1)))
            back = "/#reports/issue?id=" + quote(identifier, safe="")
            document = self._source_document("Источник отчёта", report_web.source_html(source), back, "Выпуск отчёта")
            self._send(200, document, "text/html; charset=utf-8")
            return
        elif self.mode == "work" and path == "/api/project-updates":
            from .project_updates import read_updates
            result = read_updates(self.server.vault.root, params.get("id"),
                                  limit=int(params.get("limit", 5)), offset=int(params.get("offset", 0)))
            self._send(200, result)
            return
        elif self.mode == "work" and path == "/api/record":
            record_id = parse_qs(url.query).get("id", [""])[0]
            if not record_id:
                raise ValueError("Укажите ID карточки.")
            self._send(200, {**self.server.vault.get_record(record_id), "vaultName": self.server.vault.root.name})
            return
        elif self.mode == "demo" and path == "/api/guide":
            self._send(200, guide.guide())
            return
        elif path.startswith("/source/"):
            self._show_document(unquote(path[len("/source/"):]), parse_qs(url.query))
            return
        elif not path.startswith("/api/"):
            entry = "workspace.html" if self.mode == "work" else "index.html"
            relative = Path(unquote(path.lstrip("/")) or entry)
            if relative.parts and all(not p.startswith(".") for p in relative.parts):
                file = (WEB / relative).resolve()
                if (WEB in file.parents and file.is_file() and file.suffix.lower() in
                        {".html", ".css", ".js", ".jpg", ".jpeg", ".webp", ".png", ".svg", ".ico", ".woff2"}):
                    self._send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                    return
        self._send(404, {"error": "Страница не найдена."})

    def _team_request(self, path, data, *, post):
        reads = {"/api/teams", "/api/team", "/api/person/teams", "/api/people/context"}
        writes = {"/api/people/create", "/api/teams/create", "/api/teams/rename",
                  "/api/teams/member", "/api/people/context"}
        if path not in (writes if post else reads):
            return False
        from .teams import Teams
        service = Teams(self.server.vault.root)
        status = 200
        if not post:
            page = {"limit": int(data.get("limit", 20)), "offset": int(data.get("offset", 0))}
            if path == "/api/teams":
                result = service.list(match=data.get("match", ""), **page)
            elif path == "/api/team":
                result = service.show(data.get("id"), **page)
            elif path == "/api/person/teams":
                result = service.for_person(data.get("id"), match=data.get("match", ""), **page)
            else:
                result = service.context(**page)
        elif path in ("/api/people/create", "/api/teams/create"):
            if data.keys() != {"name", "requestId"}:
                raise ValueError("Для создания укажите имя и ключ запроса.")
            create = service.create_person if path == "/api/people/create" else service.create
            result = create(data["name"], request_id=data["requestId"])
            status = 201
        elif path == "/api/teams/rename":
            if data.keys() != {"id", "name", "revision"}:
                raise ValueError("Для переименования укажите команду, название и прочитанную версию.")
            result = service.rename(data["id"], data["name"], data["revision"])
        elif path == "/api/teams/member":
            if data.keys() != {"id", "person", "present", "revision"} or type(data["present"]) is not bool:
                raise ValueError("Укажите команду, участника, действие и прочитанную версию.")
            result = service.member(data["id"], data["person"], present=data["present"], revision=data["revision"])
        else:
            if "revision" not in data or not ({"owner", "team"} & data.keys()) or data.keys() - {"revision", "owner", "team"}:
                raise ValueError("Укажите прочитанную версию профиля и выбор человека или команды.")
            result = service.set_context(**data)
        self._send(status, result)
        return True

    def _planning_request(self, path, data, *, post):
        if not path.startswith("/api/plan"):
            return False
        row_actions = {"/api/plan/task/edit", "/api/plan/dependency",
                       "/api/plan/due/preview", "/api/plan/due/apply",
                       "/api/plan/schedule/preview", "/api/plan/schedule/apply",
                       "/api/plan/scenario/preview", "/api/plan/scenario/apply"}
        context_keys = data.keys() & {"planId", "projectId"}
        if post and context_keys and path in row_actions:
            from .planning import Planning
            root = self.server.vault.root
            if len(context_keys) != 1:
                raise ValueError("Укажите одно представление, из которого открыта работа.")
            with storage._vault_lock(root, write=not path.endswith("/preview")):
                planning = Planning(root)
                if "projectId" in context_keys:
                    project_id = data["projectId"]
                    if not isinstance(project_id, str) or not project_id:
                        raise ValueError("Укажите проект.")
                    try:
                        project = storage.read_record(storage.find_record(root, project_id))
                    except FileNotFoundError as error:
                        raise storage.ConflictError("Карточка проекта больше недоступна. Обновите Гант.") from error
                    if project["type"] != "project":
                        raise storage.ConflictError("Карточка проекта изменилась. Обновите Гант.")
                    if planning.task(data.get("id"))["project"] != project_id:
                        raise storage.ConflictError("Работа больше не входит в этот проект. Обновите Гант.")
                else:
                    plan = planning.view(data["planId"])
                    if plan["status"] == "closed":
                        raise storage.ConflictError("План уже закрыт. Для изменения работы откройте текущую карточку.")
                    if not any(row["id"] == data.get("id") for row in plan["items"]):
                        raise storage.ConflictError("Состав плана изменился. Откройте план заново.")
                result = self._plan_data(path, {k: v for k, v in data.items() if k not in context_keys}, post=True)
        else:
            result = self._plan_data(path, data, post=post)
        if result is None:
            return False
        self._send(200, result)
        return True

    def _plan_data(self, path, data, *, post):
        from .gantt import Gantt
        from .plan_dates import Deadlines
        from .planning import Planning

        root = self.server.vault.root
        identifier = data.get("id")
        if not post and path == "/api/planning-vault":
            result = {"root": str(root), "name": root.name, "binding": self.server.vault_binding,
                      "workspaceEmbed": self.server.work_mode}
            if self.server.work_control:
                result["workControl"] = self.server.work_control
        elif not post and path == "/api/plans":
            result = {"items": Planning(root).plans(), "name": root.name, "root": str(root)}
        elif not post and path == "/api/plan/activity":
            from .project_activity import ProjectActivity
            result = ProjectActivity(root).catalog()
        elif post and path == "/api/plan/project-membership":
            from .project_membership import ProjectMembership
            if data.keys() != {"id", "projectId", "present", "revision"} or type(data["present"]) is not bool:
                raise ValueError("Укажите план, проект, действие и прочитанную версию плана.")
            result = ProjectMembership(root).set(identifier, data["projectId"], present=data["present"],
                                                 revision=data["revision"], actor="manual")
        elif post and path == "/api/plan/project-task":
            from .project_membership import ProjectMembership
            if data.keys() != {"id", "projectId", "taskId", "revision"}:
                raise ValueError("Укажите план, проект, выбранную работу и прочитанную версию плана.")
            result = ProjectMembership(root).add_task(identifier, data["projectId"], data["taskId"],
                                                       revision=data["revision"], actor="manual")
        elif not post and path in ("/api/plan/feedback", "/api/plan/project-history"):
            limit, offset = int(data.get("limit", brief.DEFAULT_LIMIT)), int(data.get("offset", 0))
            if not 1 <= limit <= 20 or offset < 0:
                raise ValueError("Укажите от 1 до 20 записей и неотрицательное смещение.")
            if path.endswith("/feedback"):
                from .gantt_feedback import GanttFeedback
                result = brief.page(GanttFeedback(root).pending(data.get("planId")), limit, offset)
            else:
                from .project_feedback import ProjectFeedback
                if data.get("pending", "0") not in ("0", "1"):
                    raise ValueError("Для несверенной истории укажите pending=1.")
                result = ProjectFeedback(root).history(identifier, pending=data.get("pending") == "1",
                                                       limit=limit, offset=offset)
        elif post and path == "/api/plan/reviewed":
            from .gantt_feedback import GanttFeedback
            if data.keys() != {"id", "token"}:
                raise ValueError("Укажите ID и отпечаток прочитанных ручных правок.")
            result = GanttFeedback(root).acknowledge(identifier, data["token"])
        elif not post and path == "/api/plan/project-gantt":
            from .project_gantt import ProjectGantt
            result = {"name": root.name, "root": str(root), **ProjectGantt(root).view(identifier)}
        elif post and path == "/api/plan/project-work":
            if (not {"title", "kind", "projectId", "requestId"} <= data.keys()
                    or data.keys() - {"title", "kind", "projectId", "requestId", "due"}
                    or not isinstance(data["projectId"], str) or not data["projectId"] or not data["requestId"]):
                raise ValueError("Укажите проект, тип работы, название и ключ запроса.")
            result = Planning(root).create_task(data["title"], kind=data["kind"], project=data["projectId"],
                                                due=data.get("due"), request_id=data["requestId"])
        elif not post and path in ("/api/plan/okrs", "/api/plan/okr"):
            return self._okr_page(root, identifier, catalog=path.endswith("/okrs"))
        elif not post and path == "/api/plan/okr-board":
            from .okr import KeyResults
            result = {"name": root.name, "root": str(root), **KeyResults(root).board(identifier)}
        elif not post and path == "/api/plan/okr-periods":
            from .okr_web import periods
            return periods(root)
        elif not post and path in ("/api/plan/okr-sources", "/api/plan/okr-source-lines"):
            from .okr_web import source_page
            result = source_page(root, path.removeprefix("/api/plan/okr-"), data)
        elif not post and path == "/api/plan/okr/readiness":
            from .okr_progress import Progress
            limit, offset = int(data.get("limit", 10)), int(data.get("offset", 0))
            if not 1 <= limit <= 20 or offset < 0:
                raise ValueError("Проверьте размер страницы и смещение.")
            result = Progress(root).readiness(identifier, limit=limit, offset=offset)
        elif post and path in {"/api/plan/okr/" + action for action in
                              ("criterion", "criterion-target", "measure", "observe", "weight", "readiness")}:
            from .okr_web import write
            return write(root, path.rsplit("/", 1)[1], data)
        elif post and path == "/api/plan/parent":
            from .plan_tree import set_parent
            if data.keys() != {"id", "taskId", "parentId", "revision"}:
                raise ValueError("Укажите план, работу, родителя и версию плана.")
            result = set_parent(Planning(root), identifier, data["taskId"], data["parentId"], data["revision"])
        elif not post and path == "/api/plan/okr-options":
            from .okr import KeyResults
            planning = Planning(root)
            with storage._vault_lock(root, write=False):
                records = {r["id"]: r for _, r in storage.iter_records(root) if r["type"] in ("direction", "project")}
                tasks = [{key: task[key] for key in ("id", "title", "direction", "project")} for task in planning.tasks()]
                for task in tasks:
                    for parent in ("direction", "project"):
                        task[parent + "Title"] = records.get(task[parent], {}).get("title")
                plans = [{k: p[k] for k in ("id", "title", "status")} for p in planning.plans()]
                active = {p["id"] for p in plans if p["status"] == "active"}
                result = {"directions": [{"id": r["id"], "title": r["title"]} for r in records.values()
                                         if r["type"] == "direction"], "tasks": tasks,
                          "projects": [{k: r.get(k) for k in ("id", "title", "direction")} for r in records.values()
                                       if r["type"] == "project"],
                          "plans": plans, "objectives": [{k: o[k] for k in ("id", "title", "direction", "planId")}
                                                         for o in KeyResults(root).objectives() if o["planId"] in active]}
        elif post and path in ("/api/plan/create", "/api/plan/objective/create",
                               "/api/plan/okr/create", "/api/plan/okr/link", "/api/plan/okr/unlink",
                               "/api/plan/okr/context", "/api/plan/okr/assign"):
            return self._write_plan_okr(root, path, data)
        elif not post and path == "/api/plan":
            result = Gantt(root).view(identifier)
            if result["status"] == "closed":
                from .weekly_report import published_issues
                try:
                    result["weeklyReviews"] = published_issues(root, identifier)
                except (OSError, ValueError):
                    result["weeklyReviewError"] = "Не удалось прочитать выпуски из Reports."
        elif not post and path == "/api/plan/task":
            result = Gantt(root).detail(identifier)
        elif not post and path == "/api/plan/task-location":
            planning = Planning(root)
            with storage._vault_lock(root, write=False):
                row = planning.task(identifier)
                result = {"id": row["id"], "path": row["path"], "plans": planning.plans_for_task(identifier)}
        elif post and path in {"/api/plan/close/preview", "/api/plan/close/apply"}:
            from .cycles import Cycles
            preview = path.endswith("/preview")
            if set(data) != ({"id"} if preview else {"id", "revision"}):
                raise ValueError("Для предпросмотра передайте ID плана. Для закрытия добавьте ревизию предпросмотра.")
            cycles = Cycles(root)
            result = cycles.preview(identifier) if preview else cycles.close(identifier, data["revision"])
        elif not post and path == "/api/plan/tasks":
            from .search import _normalize
            limit, offset = int(data.get("limit", 10)), int(data.get("offset", 0))
            if not 1 <= limit <= 20 or offset < 0:
                raise ValueError("Укажите от 1 до 20 работ и неотрицательное смещение.")
            query = _normalize(data.get("match", "").strip())
            rows = ({key: row[key] for key in ("id", "title", "path", "kind", "project", "direction")}
                    for row in Planning(root).tasks() if query in _normalize(row["title"]))
            result = brief.page(rows, limit, offset)
        elif not post and path == "/api/plan/dependencies":
            from .dependencies import Dependencies
            result = Dependencies(root).links(identifier)
        elif post and path == "/api/plan/dependency":
            from .dependencies import Dependencies
            if data.keys() - {"id", "predecessor", "action"} or data.get("action") not in ("add", "remove"):
                raise ValueError("Для зависимости укажите работу, предшественника и действие add или remove.")
            result = Dependencies(root).link(identifier, data.get("predecessor"), remove=data["action"] == "remove",
                                             actor="manual")
        elif post and path == "/api/plan/milestone":
            if data.keys() - {"title", "planId", "project", "direction", "due", "requestId"} or not data.get("requestId"):
                raise ValueError("Передайте план, название вехи и ключ запроса.")
            planning = Planning(root)
            with storage._write_lock(root):
                plan = planning.view(data.get("planId"))
                if plan["status"] == "closed":
                    raise storage.ConflictError("План уже закрыт. Добавьте веху в активный план.")
                result = planning.create_task(data.get("title"), kind="milestone", project=data.get("project"),
                                              direction=data.get("direction"), due=data.get("due"), request_id=data["requestId"])
                planning.add_task(plan["id"], result["id"])
        elif post and path in {"/api/plan/scenario/preview", "/api/plan/scenario/apply"}:
            from .dependencies import Dependencies
            if data.keys() - {"id", "forecast", "revision"} or not isinstance(data.get("forecast"), str):
                raise ValueError("Для сценария передайте работу и предполагаемую дату завершения.")
            service = Dependencies(root)
            if path.endswith("/preview"):
                result = service.preview(identifier, data["forecast"], actor="manual")
            else:
                result = service.apply(identifier, data["forecast"], data.get("revision"), actor="manual")
        elif not post and path == "/api/plan/events":
            offset = int(data.get("offset", 0))
            if offset < 0:
                raise ValueError("Укажите неотрицательное смещение.")
            card = Gantt(root).detail(identifier, all_entries=True)
            rows = (item for item in card["latest"] if not item.get("deleted"))
            result = {"revision": card["revision"], **brief.page(rows, 12, offset)}
        elif post and path == "/api/plan/task/edit":
            if "actor" in data:
                raise ValueError("Правку в интерфейсе сохраняет владелец.")
            result = Gantt(root).edit({**data, "actor": "manual"})
        elif post and path in {"/api/plan/schedule/preview", "/api/plan/schedule/apply"}:
            from .schedule import Schedule
            if "value" not in data:
                raise ValueError("Передайте дату или null для снятия даты.")
            if data.keys() - {"id", "field", "value", "reason", "sources", "revision"}:
                raise ValueError("Недопустимое поле при изменении даты задачи.")
            schedule = Schedule(root)
            field, value = data.get("field"), data["value"]
            evidence = {"actor": "manual", "reason": data.get("reason"), "sources": data.get("sources")}
            if path.endswith("/preview"):
                result = schedule.preview(identifier, field, value, **evidence)
            else:
                result = schedule.apply(identifier, field, value, data.get("revision"), **evidence)
        elif post and path in {"/api/plan/due/preview", "/api/plan/due/apply"}:
            if "due" not in data:
                raise ValueError("Укажите новый срок или передайте null, чтобы снять его.")
            deadlines = Deadlines(root)
            if path.endswith("/preview"):
                result = deadlines.preview(identifier, data["due"])
            else:
                result = deadlines.apply(identifier, data["due"], data.get("revision"))
        else:
            return None
        return result

    def _write_plan_okr(self, root, path, data):
        from .okr import KeyResults
        from .planning import Planning

        if path in ("/api/plan/okr/link", "/api/plan/okr/unlink"):
            if data.keys() != {"id", "taskId", "revision"}:
                raise ValueError("Для связи укажите результат, работу и версию карточки результата.")
            service = KeyResults(root)
            change = service.unlink if path.endswith("/unlink") else service.link
            return change(data["id"], data["taskId"], data["revision"])
        if path == "/api/plan/okr/context":
            if data.keys() != {"id", "projectId", "present", "revision"} or type(data["present"]) is not bool:
                raise ValueError("Выберите проект, действие и версию ключевого результата.")
            return KeyResults(root).context_project(data["id"], data["projectId"], present=data["present"],
                                                    revision=data["revision"])
        if path == "/api/plan/okr/assign":
            if data.keys() != {"id", "objectiveId", "revision"}:
                raise ValueError("Выберите цель и укажите версию карточки результата.")
            return KeyResults(root).assign_objective(data["id"], data["objectiveId"], data["revision"])
        required = {"title", "requestId"}
        optional = set()
        if path == "/api/plan/objective/create":
            required.update(("planId", "direction"))
        elif path == "/api/plan/okr/create":
            required.add("objectiveId")
            optional.update(("unit", "target", "taskIds"))
        if not required <= data.keys() or data.keys() - required - optional or not data["requestId"]:
            raise ValueError("Проверьте поля формы и ключ запроса на создание.")
        if path == "/api/plan/create":
            return Planning(root).create_plan(data["title"], request_id=data["requestId"])
        if path == "/api/plan/objective/create":
            return KeyResults(root).create_objective(data["title"], plan_id=data["planId"],
                                                     direction=data["direction"], request_id=data["requestId"])
        return KeyResults(root).create(data["title"], objective_id=data["objectiveId"], unit=data.get("unit"),
                                       target=data.get("target"), task_ids=data.get("taskIds", []),
                                       request_id=data["requestId"])

    def _okr_page(self, root, identifier, *, catalog):
        from .okr import KeyResults
        from .okr_progress import enrich_card
        from .planning import Planning

        service = KeyResults(root)
        with storage._vault_lock(root, write=False):
            rows = service.list() if catalog else [service.show(identifier)]
            if rows:
                directions = {record["id"]: record["title"] for _, record in storage.iter_records(root)
                              if record["type"] == "direction"}
                for row in rows:
                    row["directionTitle"] = directions.get(row["direction"])
            if catalog:
                for row in rows:
                    if row["measurement"]:
                        reading = row["measurement"]
                        row["measurement"] = {"value": reading["value"], "date": reading["date"],
                                              "sourceUnavailable": bool(reading["source"].get("unavailable"))}
                return {"name": root.name, "items": rows}

            card = rows[0]
            enrich_card(root, card)
            linked = {task["id"]: task for task in card["tasks"]}
            for task in linked.values():
                task["plans"] = []
            if linked:
                for plan, members in Planning(root).memberships(include_closed=True):
                    for task_id in linked.keys() & members:
                        linked[task_id]["plans"].append({"id": plan["id"], "title": plan["title"],
                                                        "status": plan.get("status", "active")})
            return {**card, "name": root.name, "root": str(root)}

    def _intake_page(self, path, params):
        people = self.server.people
        if path == "/api/inbox":
            root = self.server.vault.root
            self._send(200, {"items": Inbox(root).list_items(),
                             "triageOpen": sum(row["status"] in {"open", "damaged"} for row in Batch(root).triage())})
        elif path in ("/api/inbox/triage", "/api/inbox/rules"):
            limit, offset = int(params.get("limit", 10)), int(params.get("offset", 0))
            if not 1 <= limit <= 20 or offset < 0:
                raise ValueError("Укажите от 1 до 20 записей и неотрицательное смещение.")
            root = self.server.vault.root
            rows = Batch(root).triage() if path.endswith("/triage") else Routing(root).rules()
            self._send(200, brief.page(rows, limit, offset))
        elif path == "/api/inbox/preview":
            self._send(200, Inbox(self.server.vault.root).preview(params.get("path", ""),
                       int(params.get("start", 1)), int(params.get("limit", 30))))
        elif path == "/api/people":
            self._send(200, people.candidate_page(params.get("match", ""), limit=int(params.get("limit", 20)),
                                                  direction_id=params.get("direction"),
                                                  offset=int(params.get("offset", 0))))
        elif path == "/api/person":
            card = people.detail(params.get("id", ""), personal=params.get("personal") == "1")
            if "direction" in params:
                card["facts"] = [f for f in card["facts"] if f["direction"] == params["direction"]]
            self._send(200, card)
        elif path.startswith("/evidence/"):
            parts = path.strip("/").split("/")
            if len(parts) != 3:
                raise FileNotFoundError()
            _, person_id, fact_id = parts
            personal = params.get("personal") == "1"
            source = Inbox(self.server.vault.root).fact_evidence(person_id, fact_id, personal)
            back = "/#person/" + quote(person_id, safe="") + ("?personal=1" if personal else "")
            content = (f'<p class="source-path">{escape(source["path"])}</p>'
                       f'<p>Строка {source["start"]} · подтверждённая цитата</p>'
                       f'<div class="source-line highlight"><code>{escape(source["text"])}</code></div>')
            self._send(200, self._source_document("Источник факта", content, back, "Карточка человека"),
                       "text/html; charset=utf-8")
        elif path.startswith("/attachment/"):
            archived, raw = Inbox(self.server.vault.root).attachment(path[len("/attachment/"):])
            self._send(200, raw, "application/octet-stream", download=archived.name)
        elif path.startswith("/original/"):
            source_id = path[len("/original/"):]
            start = int(params.get("start", 1))
            original = Inbox(self.server.vault.root).original(source_id, start=start, limit=30)
            rows = "".join(f'<div class="source-line"><span class="line-number">{line["n"]}</span>'
                           f'<code class="line-text">{escape(line["text"])}</code></div>' for line in original["lines"])
            pages = []
            for number, label in ((max(1, start - 30), "Предыдущие строки"), (start + 30, "Следующие строки")):
                if number != start and number <= original["total"]:
                    pages.append(f'<a href="?start={number}">{label}</a>')
            origin = ""
            if "attachmentId" in original:
                origin = f'<p><a href="/attachment/{original["attachmentId"]}" download>Скачать исходное вложение</a></p>'
            content = f'<p class="source-path">{escape(original["path"])}</p>{origin}{rows}<p>{" · ".join(pages)}</p>'
            self._send(200, self._source_document("Сохранённый оригинал", content, "/#inbox", "Входящие"),
                       "text/html; charset=utf-8")
        else:
            return False
        return True

    def _show_document(self, record_id, params):
        back, label = (("/#overview", "Обзор хранилища") if self.mode == "work"
                       else ("/#ask", "Вопросы по направлениям"))
        try:
            number = params.get("line", [""])[0]
            number = int(number) if number.isdecimal() else None
            ref = params.get("ref", [None])[0]
            if self.mode == "work" and params.get("kind") == ["task"]:
                from .planning import Planning
                from .query import _line_ref
                root = self.server.vault.root
                path, raw, record, _ = Planning(root)._find("task", record_id)
                lines = raw.decode("utf-8").splitlines()
                if not number or not ref:
                    raise ValueError("Для источника работы нужны строка и её отпечаток.")
                if number > len(lines) or _line_ref(lines[number-1]) != ref:
                    matches = [i for i, text in enumerate(lines, 1) if _line_ref(text) == ref]
                    if len(matches) != 1:
                        raise storage.ConflictError("Строка работы изменилась. Обновите обзор.")
                    number = matches[0]
                source = {"start": number, "path": path.relative_to(root).as_posix()}
                visible = [(number, lines[number-1])]
            else:
                record, source = self.reader.open_citation({"id": record_id, "line": number, "ref": ref})
                if source["start"] < record["body_start"]:
                    visible = ((item["n"], item["text"]) for item in source["lines"])
                else:
                    visible = visible_body_lines(record)
        except (ValueError, OSError) as error:
            message = str(error) if isinstance(error, ValueError) else "Исходный файл больше не доступен."
            document = self._source_document("Не удалось открыть источник", f"<p>{escape(message)}</p>", back, label)
            self._send(409 if isinstance(error, storage.ConflictError) else 404, document, "text/html; charset=utf-8")
            return
        rows = []
        for n, line in visible:
            attrs = 'class="source-line highlight" id="cited-line"' if n == source["start"] else 'class="source-line"'
            rows.append(f'<div {attrs}><span class="line-number">{n}</span><code class="line-text">{escape(line)}</code></div>')
        context = (f'<p class="source-path">{escape(source["path"])}</p>'
                   f'<p class="source-range">Строка {source["start"]}</p><div class="source-lines">')
        content = context + "".join(rows) + "</div>"
        if "evidenceHref" in source:
            content += f'<p><a href="{escape(source["evidenceHref"], quote=True)}">Сверить с оригиналом</a></p>'
        document = self._source_document(record["title"], content, back, label)
        self._send(200, document, "text/html; charset=utf-8")

    def _handle(self, post):
        try:
            self._dispatch(post)
        except CaptureTooLarge as error:
            self._send(413, {"error": str(error)})
        except storage.ConflictError as error:
            self._send(409, {"error": str(error)})
        except FileNotFoundError:
            self._send(404, {"error": "Карточка или хранилище больше не доступны."})
        except (ValueError, UnicodeError) as error:
            self._send(400, {"error": str(error)})
        except (OSError, sqlite3.Error):
            self._send(409, {"error": "Файл изменился или недоступен. Обновите страницу и повторите."})

    def do_GET(self):
        self._handle(False)

    def do_POST(self):
        self._handle(True)


class WorkHandler(Handler):
    # Idle browser connections must release their workers during graceful shutdown.
    timeout = 10


def server(root, port=8765, *, mode="demo"):
    if not 0 <= port <= 65535:
        raise ValueError("Порт должен быть от 0 до 65535.")
    from .people import People
    from .query import WorkspaceQuery
    from .workspace import Workspace
    guide = None
    if mode == "demo":
        from .demo import DemoVault
        root = Path(root).expanduser()
        if root.is_symlink():
            raise ValueError("Для деморежима выберите обычную папку.")
        if not demo_cleanup.is_cleaned(root):
            guide = DemoVault(root)
    elif mode != "work":
        raise ValueError("Неизвестный режим оболочки.")
    vault = Workspace(root)
    reader = WorkspaceQuery(root)
    people = People(root)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), WorkHandler if mode == "work" else Handler)
    if mode == "work":
        httpd.daemon_threads = False  # server_close waits for accepted writes to finish.
    httpd.vault, httpd.reader, httpd.guide = vault, reader, guide
    httpd.people = people
    httpd.vault_binding = hashlib.sha256(str(vault.root).encode()).hexdigest()
    httpd.work_mode = mode == "work"
    httpd.work_control = None
    return httpd


def serve(root, port, *, mode="demo"):
    with server(root, port, mode=mode) as httpd, (work_control.enable(httpd) if mode == "work" else nullcontext()):
        if mode == "work":
            work_control.watch_parent(httpd)
        print(f"http://127.0.0.1:{httpd.server_port}/", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
