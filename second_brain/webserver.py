"""Local HTTP transport shared by the guide and the working vault."""

from html import escape
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import brief, storage
from .batch import Batch
from .capture import Capture, CaptureTooLarge, MAX_FILE_BYTES, MAX_TEXT_BYTES
from .inbox import Inbox
from .routing import Routing

WEB = Path(__file__).resolve().parent / "web"


def _source_document(title, content, back="/#ask", label="Вопросы по направлениям"):
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · Второй мозг</title><link rel="icon" href="data:,"><link rel="stylesheet" href="/app.css"></head>
<body><main><p><a class="back-link" href="{escape(back, quote=True)}">← {escape(label)}</a></p>
<article class="panel"><h1 class="panel-heading">{escape(title)}</h1>{content}</article></main></body></html>""".encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, status, data, content_type="application/json; charset=utf-8", *, download=None, embed=False):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if download is not None:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(download, safe=""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if embed:
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
        embed = self.server.mode == "work" and path == "/" and params.get("embed") == "1"
        binding = params.get("binding", "") if embed else self.headers.get("X-Brain-Vault")
        if binding is not None and binding != getattr(self.server, "vault_binding", None):
            raise storage.ConflictError("На этом порту открыто другое хранилище. Проверьте подключение в Obsidian.")
        if post:
            capture_text = self.server.mode == "work" and path == "/api/inbox/text"
            if self.server.mode == "work" and path == "/api/inbox/file":
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
            content = self._body(6 * MAX_TEXT_BYTES + 4096 if capture_text else 64000)
            if content is None:
                return
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("Ожидался объект JSON.")
            if self.server.mode == "work" and self._team_request(path, data, post=True):
                return
            if self.server.mode == "work" and self._planning_request(path, data, post=True):
                return
            if capture_text:
                if data.keys() - {"text", "title"}:
                    raise ValueError("Для заметки передайте текст и при желании название.")
                self._send(201, Capture(self.server.vault.root).save_text(data.get("text"), data.get("title")))
                return
            if path == "/api/ask":
                self._send(200, self.server.reader.ask(data))
                return
            if path == "/api/citation":
                _, source = self.server.reader.open_citation(data)
                self._send(200, source)
                return
            if self.server.mode == "work" and path == "/api/records/create":
                self._send(201, self.server.vault.create_record(data))
                return
            if self.server.mode == "work" and path == "/api/records/edit":
                self._send(200, self.server.vault.edit_record(data))
                return
            if self.server.mode == "work" and path == "/api/inbox/process":
                self._send(200, Inbox(self.server.vault.root).process(data))
                return
            if self.server.mode == "work" and path == "/api/inbox/resolve":
                self._send(200, Batch(self.server.vault.root).resolve(data.get("id"), data.get("clarification")))
                return
            if self.server.mode == "work" and path == "/api/inbox/rule":
                self._send(200, Routing(self.server.vault.root).save_rule(data))
                return
            if self.server.mode == "work" and path == "/api/person/correct":
                self._send(200, self.server.people.correct(data))
                return
            if self.server.mode == "demo" and path == "/api/directions":
                self._send(200, self.server.vault.add_direction(data))
                return
        elif self.server.mode == "work" and path == "/api/workspace":
            self._send(200, self.server.vault.overview())
            return
        elif self.server.mode == "work" and self._team_request(path, params, post=False):
            return
        elif self.server.mode == "work" and self._intake_page(path, params):
            return
        elif self.server.mode == "work" and self._planning_request(path, params, post=False):
            return
        elif self.server.mode == "work" and path == "/api/record":
            record_id = parse_qs(url.query).get("id", [""])[0]
            if not record_id:
                raise ValueError("Укажите ID карточки.")
            self._send(200, self.server.vault.get_record(record_id))
            return
        elif self.server.mode == "demo" and path == "/api/guide":
            self._send(200, self.server.vault.guide())
            return
        elif path.startswith("/source/"):
            self._show_document(unquote(path[len("/source/"):]), parse_qs(url.query))
            return
        elif not path.startswith("/api/"):
            entry = "workspace.html" if self.server.mode == "work" else "index.html"
            relative = Path(unquote(path.lstrip("/")) or entry)
            if relative.parts and all(not p.startswith(".") for p in relative.parts):
                file = (WEB / relative).resolve()
                if (WEB in file.parents and file.is_file() and file.suffix.lower() in
                        {".html", ".css", ".js", ".jpg", ".jpeg", ".webp", ".png", ".svg", ".ico", ".woff2"}):
                    self._send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                               embed=embed)
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
        if post and "planId" in data and path in row_actions:
            from .planning import Planning
            root = self.server.vault.root
            with storage._vault_lock(root, write=not path.endswith("/preview")):
                plan = Planning(root).view(data["planId"])
                if plan["status"] == "closed":
                    raise storage.ConflictError("План уже закрыт. Для изменения работы откройте текущую карточку.")
                if not any(row["id"] == data.get("id") for row in plan["items"]):
                    raise storage.ConflictError("Состав плана изменился. Откройте план заново.")
                result = self._plan_data(path, {k: v for k, v in data.items() if k != "planId"}, post=True)
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
            result = {"root": str(root), "name": root.name, "binding": self.server.vault_binding}
        elif not post and path == "/api/plans":
            result = {"items": Planning(root).plans(), "name": root.name, "root": str(root)}
        elif not post and path in ("/api/plan/okrs", "/api/plan/okr"):
            return self._okr_page(root, identifier, catalog=path.endswith("/okrs"))
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

    def _okr_page(self, root, identifier, *, catalog):
        from .okr import KeyResults
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
            self._send(200, _source_document("Источник факта", content, back, "Карточка человека"),
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
            self._send(200, _source_document("Сохранённый оригинал", content, "/#inbox", "Входящие"),
                       "text/html; charset=utf-8")
        else:
            return False
        return True

    def _show_document(self, record_id, params):
        back, label = (("/#overview", "Обзор хранилища") if self.server.mode == "work"
                       else ("/#ask", "Вопросы по направлениям"))
        try:
            number = params.get("line", [""])[0]
            record, source = self.server.reader.open_citation({
                "id": record_id, "line": int(number) if number.isdecimal() else None,
                "ref": params.get("ref", [None])[0],
            })
        except (ValueError, OSError) as error:
            message = str(error) if isinstance(error, ValueError) else "Исходный файл больше не доступен."
            document = _source_document("Не удалось открыть источник", f"<p>{escape(message)}</p>", back, label)
            self._send(409 if isinstance(error, storage.ConflictError) else 404, document, "text/html; charset=utf-8")
            return
        rows = []
        lines = enumerate(record["body"].splitlines(), record["body_start"])
        if source["start"] < record["body_start"]:
            lines = ((item["n"], item["text"]) for item in source["lines"])
        for n, line in lines:
            attrs = 'class="source-line highlight" id="cited-line"' if n == source["start"] else 'class="source-line"'
            rows.append(f'<div {attrs}><span class="line-number">{n}</span><code class="line-text">{escape(line)}</code></div>')
        context = (f'<p class="source-path">{escape(source["path"])}</p>'
                   f'<p class="source-range">Строка {source["start"]}</p><div class="source-lines">')
        content = context + "".join(rows) + "</div>"
        if "evidenceHref" in source:
            content += f'<p><a href="{escape(source["evidenceHref"], quote=True)}">Сверить с оригиналом</a></p>'
        document = _source_document(record["title"], content, back, label)
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


def server(root, port=8765, *, mode="demo"):
    if not 0 <= port <= 65535:
        raise ValueError("Порт должен быть от 0 до 65535.")
    if mode == "work":
        from .people import People
        from .query import WorkspaceQuery
        from .workspace import Workspace
        vault = Workspace(root)
        reader = WorkspaceQuery(root)
    elif mode == "demo":
        from .demo import DemoVault
        vault = reader = DemoVault(root)
    else:
        raise ValueError("Неизвестный режим оболочки.")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.vault, httpd.reader, httpd.mode = vault, reader, mode
    if mode == "work":
        httpd.people = People(root)
        httpd.vault_binding = hashlib.sha256(str(vault.root).encode()).hexdigest()
    return httpd


def serve(root, port, *, mode="demo"):
    with server(root, port, mode=mode) as httpd:
        print(f"http://127.0.0.1:{httpd.server_port}/", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
