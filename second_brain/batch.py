"""Apply a Codex inbox review and keep owner questions in the source receipt."""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

from . import inbox, people, storage
from .routing import Routing


_MARKER = "<!-- inbox-batch: "
_FIRST = "initial"
_HEADING = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
_DECISION_FIELDS = {"person": ("direction", "fact_kind", "visibility"),
                    "project": ("direction", "project", "field", "expected_revision", "value"),
                    "project_event": ("direction", "project", "expected_revision")}
_PROJECT_ACTIONS = ("project", "project_event")


def _stored_path(value, folder):
    if not isinstance(value, str) or "\0" in value:
        return False
    path = Path(value)
    return (not path.is_absolute() and len(path.parts) >= 2 and path.parts[0] == folder
            and all(not part.startswith(".") for part in path.parts))


def _journal(previous, digest):
    """Rebuild jobs and questions without trusting edited receipt metadata or paths."""
    jobs, questions = {}, {}
    if not previous or _MARKER.encode() not in previous:
        return jobs, questions
    try:
        for row in previous.splitlines():
            if not row.startswith(_MARKER.encode()):
                continue
            event = people._markup(row.decode("utf-8"), _MARKER)
            op = event["op"]
            if op == "begin":
                name, path, source = event["job"], event["path"], event["source_path"]
                if (name in jobs or (name != _FIRST and name not in questions)
                        or not _stored_path(path, "Inbox") or not isinstance(source, str)
                        or not re.fullmatch(rf"Sources/Originals/{digest}\.[a-z0-9]{{1,16}}", source)
                        or (jobs and (path != jobs[_FIRST]["path"] or source != jobs[_FIRST]["source_path"]))
                        or not isinstance(event["actions"], list)
                        or not all(isinstance(a, dict) and a.get("kind") in (*_DECISION_FIELDS, "triage", "context", "transcript")
                                   and type(a.get("line")) is int and isinstance(a.get("quote"), str)
                                   for a in event["actions"])):
                    raise ValueError()
                if Path(path).suffix.lower() not in inbox.EXTENSIONS:
                    if not event["actions"] or Batch._attachment_actions(event["actions"], name != _FIRST) != event["actions"]:
                        raise ValueError()
                elif any(action["kind"] == "transcript" for action in event["actions"]):
                    raise ValueError()
                jobs[name] = {**event, "steps": {}, "done": False}
            elif op == "step":
                name, index, result = event["job"], event["index"], event["result"]
                job = jobs[name]
                if (job["done"] or type(index) is not int or index != len(job["steps"])
                        or index >= len(job["actions"]) or result["line"] != job["actions"][index]["line"]
                        or result["quote"] != job["actions"][index]["quote"]
                        or result["kind"] not in (job["actions"][index]["kind"], "triage")):
                    raise ValueError()
                job["steps"][index] = result
                if result["kind"] == "triage":
                    if (result["id"] != _question_id(digest, name, index) or result["path"] != job["path"]
                            or result["revision"] != digest or result["source_path"] != job["source_path"]
                            or not people._single_line(result["reason"])):
                        raise ValueError()
                    questions[result["id"]] = {**result, "status": "open"}
                if result["kind"] in _PROJECT_ACTIONS and not all(isinstance(result[k], str) for k in
                        ("project", "initial_revision", "chain_revision", "path", "revision")):
                    raise ValueError()
                if result["kind"] == "project" and not all(isinstance(result[k], str) for k in ("field", "value")):
                    raise ValueError()
                if result["kind"] == "project_event" and not isinstance(result["eventId"], str):
                    raise ValueError()
                if result["kind"] == "transcript" and result["path"] != f"Inbox/Transcripts/{digest}.md":
                    raise ValueError()
            elif op == "end":
                job = jobs[event["job"]]
                if job["done"] or len(job["steps"]) != len(job["actions"]):
                    raise ValueError()
                job["done"] = True
                questions.pop(event["job"], None)
            elif op == "clarify":
                item = questions[event["id"]]
                if item["status"] != "open" or not people._single_line(event["clarification"]):
                    raise ValueError()
                item.update(status="ready", clarification=event["clarification"])
            else:
                raise ValueError()
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise storage.ConflictError("Повреждены служебные строки разбора. Сверьте файл разбора в Obsidian.") from error
    return jobs, questions


def _question_id(digest, job, index):
    suffix = sha256(f"{job}/{index}".encode()).hexdigest()[:20]
    return f"{digest}:{suffix}"


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}:[0-9a-f]{20}", value):
        raise ValueError("Укажите ID вопроса из очереди владельца.")
    return value.split(":")[0]


class Batch:
    def __init__(self, root):
        self.inbox = inbox.Inbox(root)
        self.root = self.inbox.root
        self.routing = Routing(self.root)

    def _receipts(self):
        for path in sorted(storage._inside(self.root, "Sources/Processed").glob("*.md")):
            if re.fullmatch("[0-9a-f]{64}", path.stem):
                try:
                    raw = self.inbox._receipt_bytes(self.inbox._receipt(path.stem), path.stem)
                    jobs, questions = _journal(raw, path.stem)
                    if questions or any(not job["done"] for job in jobs.values()):
                        archived = self.inbox._original(path.stem)
                        if inbox._file_digest(archived)[0] != path.stem:
                            raise storage.ConflictError("Архивный оригинал изменился. Проверьте сохранённую версию.")
                except (OSError, ValueError) as error:
                    receipt_path = path.relative_to(self.root).as_posix()
                    source = ""
                    try:
                        source = self.inbox._original(path.stem).relative_to(self.root).as_posix()
                    except (OSError, ValueError):
                        pass
                    identifier = _question_id(path.stem, "damaged", 0)
                    damaged = {"id": identifier, "kind": "triage", "status": "damaged",
                               "reason": str(error), "revision": path.stem, "path": receipt_path,
                               "receipt_path": receipt_path, "source_path": source, "line": 0, "quote": ""}
                    jobs, questions = {}, {identifier: damaged}
                yield path.stem, jobs, questions

    @staticmethod
    def _pending_from_journal(digest, jobs, questions):
        if not jobs:
            return []
        first = jobs[_FIRST]
        base = {"path": first["path"], "revision": digest, "source_path": first["source_path"]}
        if Path(first["path"]).suffix.lower() not in inbox.EXTENSIONS:
            base["attachment"] = True
        result = []
        for name, job in jobs.items():
            if not job["done"]:
                item = {**base, "status": "resume"}
                if name != _FIRST:
                    item["triage_id"] = name
                result.append(item)
        for item in questions.values():
            if item["status"] == "ready" and item["id"] not in jobs:
                result.append({**base, "status": "ready", "triage_id": item["id"], "line": item["line"],
                               "quote": item["quote"], "reason": item["reason"], "clarification": item["clarification"]})
        return result

    def pending(self):
        result, seen = [], set()
        for digest, jobs, questions in self._receipts():
            seen.add(digest)
            result.extend(self._pending_from_journal(digest, jobs, questions))
        for path in self.inbox._files():
            attachment = path.suffix.lower() not in inbox.EXTENSIONS
            try:
                if attachment:
                    digest, size = inbox._file_digest(path)
                else:
                    raw = path.read_bytes()
                    digest, size = inbox._digest(raw), len(raw)
            except FileNotFoundError:
                continue  # Obsidian moved this entry during the scan.
            if digest in seen:
                continue
            seen.add(digest)
            item = {"path": path.relative_to(self.root).as_posix(), "revision": digest,
                    "size": size, "status": "new"}
            if attachment:
                item.update(attachment=True, status="attachment")
            else:
                try:
                    inbox._lines(raw)
                except ValueError as error:
                    item.update(status="unreadable", reason=str(error))
            result.append(item)
        return result

    def triage(self):
        return [item for _, _, questions in self._receipts() for item in questions.values()]

    def _append(self, path, digest, event, label=""):
        previous = self.inbox._receipt_bytes(path, digest)
        if previous is None:
            if event["op"] != "begin":
                raise storage.ConflictError("Разбор удалён во время записи.")
            relative = json.dumps(event["path"], ensure_ascii=False)
            original = inbox._link("Оригинал", self.root / event["source_path"], path.parent)
            text = ('---\ntype: processed_source\nschema_version: 1\n'
                    f'source_id: "{digest}"\ninbox_path: {relative}\n---\n\n# Разбор входящего\n\n{original}\n')
        else:
            text = previous.decode("utf-8")
        newline = "\r\n" if text.startswith("---\r\n") else "\n"
        rows = [people._marker(_MARKER, event)]
        if label:
            rows.append(label)
        if not text.endswith(("\n", "\r")):
            text += newline
        content = (text + newline + newline.join(rows) + newline).encode("utf-8")
        with storage._staged(path, content) as staged:
            if previous is None:
                os.link(staged, path)
            else:
                if path.is_symlink() or path.read_bytes() != previous:
                    raise storage.ConflictError("Разбор изменился во время записи.")
                staged.chmod(stat.S_IMODE(path.stat().st_mode))
                os.replace(staged, path)

    def resolve(self, identifier, clarification):
        digest = _identity(identifier)
        if not people._single_line(clarification):
            raise ValueError("Введите уточнение для этого фрагмента в одной строке.")
        path = self.inbox._receipt(digest)
        with storage._write_lock(self.root):
            _, questions = _journal(self.inbox._receipt_bytes(path, digest), digest)
            item = questions.get(identifier)
            if not item:
                raise FileNotFoundError("Открытый вопрос не найден.")
            if item["status"] == "ready":
                if item["clarification"] != clarification:
                    raise storage.ConflictError("Для этого вопроса уже сохранено другое уточнение.")
            else:
                self._append(path, digest, {"op": "clarify", "id": identifier, "clarification": clarification},
                             f'- Уточнение владельца к строке {item["line"]}: {inbox._label(clarification)}')
            return {**item, "status": "ready", "clarification": clarification}

    @staticmethod
    def _review_conflicts(actions, digest):
        """Catch incompatible choices before either value is published to a card."""
        destinations, choices = [], {}
        for action in actions:
            destination = None
            kind = action["kind"]
            if kind in _DECISION_FIELDS:
                if ("rule_revision" not in action
                        or (action["rule_revision"] is not None and not people._single_line(action["rule_revision"]))
                        or not all(people._single_line(action.get(key)) for key in _DECISION_FIELDS[kind])):
                    raise ValueError("У действия отсутствует обязательное текстовое поле или ревизия.")
                if kind == "person":
                    identity = people.selected_id(action.get("person"), digest)
                    destination = (kind, identity, action["direction"], action["fact_kind"], action["line"], action["quote"])
                    value = action["visibility"]
                elif kind == "project_event":
                    for field in ("text", "date", "summary"):
                        if action.get(field) is not None and not people._single_line(action[field]):
                            raise ValueError("Текст события, его дата и сводка должны занимать по одной строке.")
                    destination = (kind, action["project"], action["line"], action["quote"])
                    value = (action.get("text") or action["quote"], action.get("date"), action.get("summary"))
                else:
                    destination = (kind, action["project"], action["field"])
                    value = action["value"]
                choices.setdefault(destination, set()).add(value)
            destinations.append(destination)
        reviewed, disputed = [], set()
        for action, destination in zip(actions, destinations):
            if not destination or len(choices[destination]) == 1:
                reviewed.append(action)
                continue
            conflict = (destination, action["line"], action["quote"])
            if conflict not in disputed:
                reasons = {"person": "Какую видимость назначить этому факту?",
                           "project": "Для одного поля предложены разные значения. Какое решение действует?",
                           "project_event": "Для одного фрагмента предложены разные записи события. Какую сохранить?"}
                reason = reasons[action["kind"]]
                reviewed.append({"kind": "triage", "line": action["line"], "quote": action["quote"], "reason": reason})
                disputed.add(conflict)
        return reviewed

    @staticmethod
    def _attachment_actions(actions, question):
        empty = {"line": 0, "quote": ""}
        if not actions:
            return [{**empty, "kind": "triage", "reason": "Как разобрать это вложение? Нужны текст или уточнение."}]
        if len(actions) == 1 and isinstance(actions[0], dict):
            action = actions[0]
            if action == {**empty, "kind": "context"} and question:
                return actions
            if action.get("quote") == "" and type(action.get("line")) is int and action["line"] == 0:
                if action.get("kind") == "triage" and people._single_line(action.get("reason")):
                    return actions
                if action.get("kind") == "transcript":
                    text = action.get("text")
                    if isinstance(text, str) and text.strip() and "\0" not in text:
                        return actions
        raise ValueError("Для вложения нужна одна расшифровка или вопрос со строкой 0 и пустой цитатой.")

    def _transcribe(self, source, text):
        folder = storage._inside(self.root, "Inbox/Transcripts")
        if folder.exists() and not folder.is_dir():
            raise storage.ConflictError("Путь Inbox/Transcripts занят файлом. Уточните расположение расшифровки.")
        folder.mkdir(exist_ok=True)
        path = storage._inside(folder, source["id"] + ".md")
        link = inbox._link("исходного вложения", self.root / source["path"], folder)
        body = (f"# Расшифровка {link}\n\n" + text).encode("utf-8")
        with storage._staged(path, body) as staged:
            try:
                os.link(staged, path)
            except FileExistsError:
                if not path.is_file() or path.is_symlink() or path.read_bytes() != body:
                    raise storage.ConflictError("По этому пути уже лежит другая расшифровка. Сверьте её в Inbox.")
        return {"path": path.relative_to(self.root).as_posix()}

    @staticmethod
    def _actions(actions, raw, question, digest):
        try:
            lines = inbox._lines(raw)
        except ValueError as error:
            if question and actions:
                if actions == [{"kind": "context", "line": 0, "quote": ""}]:
                    return actions
                raise ValueError("Содержимое файла недоступно для извлечения фактов.")
            if actions:
                raise ValueError("Для нечитаемого файла передайте пустой список действий.")
            return [{"kind": "triage", "line": 0, "quote": "", "reason": str(error)}]
        covered = set()
        for action in actions:
            if not isinstance(action, dict) or action.get("kind") not in (*_DECISION_FIELDS, "triage", "context"):
                raise ValueError("Укажите вид действия: person, project, project_event, triage или context.")
            number, quote = action.get("line"), action.get("quote")
            if (type(number) is not int or not 1 <= number <= len(lines) or not people._single_line(quote)
                    or quote not in lines[number - 1]):
                raise ValueError("Проверьте номер строки и дословную цитату в каждом действии.")
            if question and (number != question["line"] or quote not in question["quote"]):
                raise ValueError("Уточнение относится только к исходному отложенному фрагменту.")
            if action["kind"] == "triage" and not people._single_line(action.get("reason")):
                raise ValueError("Для вопроса владельцу нужна причина.")
            covered.add(number)
        reviewed = Batch._review_conflicts(actions, digest)
        if question:
            if not actions:
                raise ValueError("Укажите действие для уточнённого фрагмента.")
            return reviewed
        has_body = any(line.strip() and not _HEADING.match(line) for line in lines)
        skipped = [{"kind": "triage", "line": num, "quote": line,
                    "reason": "Для этой строки не указано подтверждённое действие."}
                   for num, line in enumerate(lines, 1) if num not in covered and line.strip()
                   and not (has_body and _HEADING.match(line))]
        return [*reviewed, *skipped]

    def _person(self, action, source, incoming, raw, owner_reviewed):
        self.routing.validate_person(action)
        fact = {"kind": action.get("fact_kind"), "visibility": action.get("visibility"),
                "direction": action.get("direction"), "line": action["line"], "text": action["quote"]}
        choice = action.get("person")
        if isinstance(choice, dict) and choice.get("new") is True:
            candidates = people.People(self.root).candidates(choice.get("name"), limit=10)
            key = people.selected_id(choice, source["id"])
            if any(candidate["id"] != key for candidate in candidates) and not (owner_reviewed and choice.get("key")):
                raise storage.ConflictError("Найдена карточка с похожим именем. Уточните личность.")
        result = self.inbox._process_person(incoming, raw, source["id"], choice, [fact])
        return {"person": result["personId"], "path": result["person"]["path"]}

    def _step(self, action, source, incoming, raw, chained, owner_reviewed):
        kind = action["kind"]
        if kind == "transcript":
            return self._transcribe(source, action["text"])
        if kind in _PROJECT_ACTIONS:
            initial = action.get("expected_revision")
            project = action.get("project")
            if not isinstance(initial, str) or not isinstance(project, str):
                raise ValueError("Нужны ID проекта и прочитанная версия карточки.")
            key = (project, initial)
            effective = {**action, "expected_revision": chained.get(key, initial)}
            if kind == "project":
                saved = self.routing.apply_project_locked(effective, source)
                detail = {"field": action["field"], "value": action["value"]}
            else:
                saved = self.routing.apply_project_event_locked(effective, source)
                detail = {"eventId": saved["eventId"], "summaryStatus": saved["summaryStatus"]}
            return {"project": project, "path": saved["path"], **detail, "revision": saved["revision"],
                    "chain_revision": saved["chain_revision"], "initial_revision": initial}
        if kind == "person":
            return self._person(action, source, incoming, raw, owner_reviewed)
        return {"reason": action["reason"]} if kind == "triage" else {}

    @staticmethod
    def _result(job, source, receipt):
        steps = list(job["steps"].values())
        result = {"path": job["path"], "revision": source["id"], "source": source, "processedPath": receipt,
                "applied": [s for s in steps if s["kind"] in _DECISION_FIELDS],
                "triage": [s for s in steps if s["kind"] == "triage"],
                "context": sum(s["kind"] == "context" for s in steps)}
        for step in steps:
            if step["kind"] == "transcript":
                result["transcript"] = {"path": step["path"]}
        return result

    def apply(self, plan):
        if (not isinstance(plan, dict) or plan.keys() - {"path", "revision", "actions", "triage_id"}
                or not isinstance(plan.get("actions"), list) or not isinstance(plan.get("path"), str)):
            raise ValueError("Передайте path, revision и список actions для одного документа.")
        digest = plan.get("revision")
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("Нужна прочитанная ревизия входящего.")
        name = plan.get("triage_id", _FIRST)
        if name != _FIRST and _identity(name) != digest:
            raise ValueError("Вопрос относится к другому источнику.")
        receipt = self.inbox._receipt(digest)
        with storage._write_lock(self.root):
            previous = self.inbox._receipt_bytes(receipt, digest)
            jobs, questions = _journal(previous, digest)
            if previous and not jobs:
                raise storage.ConflictError("Этот источник уже разобран вручную.")
            raw = None
            if jobs:
                first = jobs[_FIRST]
                incoming = self.root / first["path"]  # An archived review uses this only as its source label.
                original = self.inbox._original(digest)
            else:
                if name != _FIRST:
                    raise FileNotFoundError("Разбор для этого вопроса не найден.")
                incoming = self.inbox._incoming(plan["path"], attachments=True)
                original = self.inbox._original(digest, incoming.suffix.lower())
            attachment = incoming.suffix.lower() not in inbox.EXTENSIONS
            if attachment:
                if jobs:
                    if inbox._file_digest(original)[0] != digest:
                        raise storage.ConflictError("Архивное вложение изменилось.")
                else:
                    self._attachment_actions(plan["actions"], None)
                    original = self.inbox._archive_attachment(incoming, digest)
            elif not jobs:
                raw = incoming.read_bytes()
                if inbox._digest(raw) != digest:
                    raise storage.ConflictError("Входящий изменился. Перечитайте его и проверьте строки.")
            if not attachment and original.exists():
                raw = original.read_bytes()
                if inbox._digest(raw) != digest:
                    raise storage.ConflictError("Архивный оригинал изменился.")
            source = {"id": digest, "path": original.relative_to(self.root).as_posix()}
            result_path = receipt.relative_to(self.root).as_posix()
            job = jobs.get(name)
            if job and job["done"]:
                return self._result(job, source, result_path)
            if job is None:
                question = questions.get(name)
                if name != _FIRST and (not question or question["status"] != "ready"):
                    raise storage.ConflictError("Для повторного разбора дождитесь уточнения владельца.")
                actions = (self._attachment_actions(plan["actions"], question) if attachment
                           else self._actions(plan["actions"], raw, question, digest))
                if not original.exists():
                    with storage._staged(original, raw) as staged:
                        os.link(staged, original)
                event = {"op": "begin", "job": name, "path": incoming.relative_to(self.root).as_posix(),
                         "source_path": source["path"], "actions": actions}
                self._append(receipt, digest, event, "## Пакетный разбор")
                job = {**event, "steps": {}}
            chained = {}
            for index, action in enumerate(job["actions"]):
                result = job["steps"].get(index)
                if result is None:
                    base = {"kind": action["kind"], "line": action["line"], "quote": action["quote"]}
                    try:
                        result = {**base, **self._step(action, source, incoming, raw, chained, name != _FIRST)}
                    except (ValueError, FileNotFoundError) as error:
                        result = {**base, "kind": "triage", "reason": str(error)}
                    label = ""
                    if result["kind"] == "triage":
                        result.update(id=_question_id(digest, name, index), path=job["path"],
                                      source_path=source["path"], revision=digest, status="open")
                        label = (f'- Вопрос владельцу · строка {result["line"]} — {inbox._label(result["quote"])}. '
                                 f'{inbox._label(result["reason"])}')
                    elif result["kind"] in _PROJECT_ACTIONS:
                        link = inbox._link("Проект", self.root / result["path"], receipt.parent)
                        field = result.get("field", "событие")
                        label = f'- {link} · {field} · строка {result["line"]} — {inbox._label(result["quote"])}'
                    elif result["kind"] == "transcript":
                        label = inbox._link("Расшифровка для разбора", self.root / result["path"], receipt.parent)
                    self._append(receipt, digest, {"op": "step", "job": name, "index": index,
                                                  "result": result}, label)
                    job["steps"][index] = result
                if result["kind"] in _PROJECT_ACTIONS:
                    chained[(result["project"], result["initial_revision"])] = result["chain_revision"]
            self._append(receipt, digest, {"op": "end", "job": name}, "Разбор завершён.")
            return self._result(job, source, result_path)
