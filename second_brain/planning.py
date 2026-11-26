"""Tasks with one current due date, and Markdown plans that reference them by ID."""

from datetime import date
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote
import uuid

from . import storage
from .cycles import CYCLE_SEAL, closed_metadata, frozen_rows


FOLDERS = {"task": "Tasks", "plan": "Plans"}
TASK_SCALARS = ("project", "task_kind", "start", "forecast", "completed", "gantt_reviewed")
_MEMBER = re.compile(r"^\s*[-*+]\s+.+\s+<!-- task-id: ([A-Za-z0-9._~%\-]+) -->\s*$")
_BEGIN, _END = "<!-- plan-members -->", "<!-- /plan-members -->"


def validate_due(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Срок задачи задаётся датой ГГГГ-ММ-ДД.")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("Укажите существующую календарную дату.") from error
    return value


def _membership(body):
    """Read only the plan's editable membership section; preserve all other prose."""
    lines = body.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == _BEGIN]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == _END]
    if not starts and not ends:
        return [], None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError("Проверьте границы списка задач в файле плана.")
    ids = []
    seen = set()
    for line in lines[starts[0] + 1:ends[0]]:
        if "<!-- task-id:" not in line:
            continue
        match = _MEMBER.fullmatch(line)
        if not match:
            raise ValueError("Проверьте ссылку и ID задачи в списке плана.")
        identifier = unquote(match[1], errors="strict")
        if identifier in seen:
            raise ValueError(f"Задача дважды указана в плане: {identifier}")
        ids.append(identifier)
        seen.add(identifier)
    return ids, ends[0]


def _insert_task(lines, end, task, target, plan_path, newline):
    link = storage.markdown_link(task["title"], target, plan_path.parent)
    member = f"- {link} {storage.id_marker('task', task['id'])}{newline}"
    if end is None:
        lines.extend([newline, _BEGIN + newline, member, _END + newline])
    else:
        lines.insert(end, member)


class Planning:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir() or not any((self.root / folder).is_dir() for folder in storage.KINDS.values()):
            raise ValueError("Укажите существующее хранилище. Для новой папки сначала выполните init.")

    def _entries(self, kind):
        with storage._vault_lock(self.root, write=False):
            rows = list(self._read_entries(kind))
        yield from rows

    def _read_entries(self, kind):
        from .plan_transaction import previous_versions
        pending = previous_versions(self.root) if kind == "task" else {}
        seen = set()
        for path in storage.iter_markdown_paths(self.root, folders=(FOLDERS[kind],)):
            data = path.read_bytes()
            if path in pending:
                before, after_hash = pending[path]
                if hashlib.sha256(data).hexdigest() == after_hash:
                    data = before
            try:
                record, header = storage._parse_record(path, data, kinds=(kind,),
                                                       optional_scalars=TASK_SCALARS if kind == "task" else (CYCLE_SEAL,))
            except storage.UnmanagedMarkdown:
                continue
            identifier = record["id"]
            if identifier in seen:
                raise ValueError(f"Один ID найден в нескольких файлах: {identifier}")
            seen.add(identifier)
            yield path, data, record, header

    def _find(self, kind, identifier):
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("Укажите ID карточки.")
        matches = [row for row in self._entries(kind) if row[2]["id"] == identifier]
        if not matches:
            raise FileNotFoundError(f"Карточка не найдена: {identifier}")
        return matches[0]

    def _summary(self, path, record):
        return {"id": record["id"], "type": record["type"], "title": record["title"],
                "path": path.relative_to(self.root).as_posix(), "revision": record["revision"]}

    def _project_directions(self, records):
        projects = {r["project"] for r in records if r.get("project") and not r.get("direction")}
        directions = {}
        if projects:
            for _, parent in storage.iter_records(self.root):
                if parent["id"] in projects:
                    if parent["type"] != "project" or parent["id"] in directions:
                        raise ValueError(f"Неоднозначная связь задачи с проектом: {parent['id']}")
                    directions[parent["id"]] = parent.get("direction")
        return directions

    def _task_summary(self, path, record, directions):
        project = record.get("project")
        return {**self._summary(path, record), "project": project,
                "kind": "milestone" if record.get("task_kind") == "milestone" else "task",
                "direction": record.get("direction") or directions.get(project),
                "start": record.get("start"), "startLine": record["field_lines"].get("start"),
                "due": record.get("due"), "dueLine": record["field_lines"].get("due"),
                "forecast": record.get("forecast"), "forecastLine": record["field_lines"].get("forecast"),
                "completed": record.get("completed"), "completedLine": record["field_lines"].get("completed")}

    def task(self, task_id):
        path, _, record, _ = self._find("task", task_id)
        return self._task_summary(path, record, self._project_directions([record]))

    def tasks(self):
        rows = [(path, record) for path, _, record, _ in self._entries("task")]
        directions = self._project_directions(record for _, record in rows)
        return [self._task_summary(path, record, directions) for path, record in rows]

    def plans(self):
        return [self._plan_summary(path, record, closed_metadata(record)) for path, _, record, _ in self._entries("plan")]

    def _plan_summary(self, path, record, closed):
        result = self._summary(path, record)
        if closed is None:
            return {**result, "status": "active", "closedAt": None}
        return {**result, "title": closed["title"], "status": "closed", "closedAt": closed["closedAt"]}

    def card_paths(self):
        for kind in FOLDERS:
            for path, _, _, _ in self._entries(kind):
                yield path

    def plans_for_task(self, task_id):
        return [summary for summary, members in self.active_memberships() if task_id in members]

    def active_memberships(self):
        return self.memberships(include_closed=False)

    def memberships(self, *, include_closed):
        from .okr import KeyResults
        with storage._vault_lock(self.root, write=False):
            plans = [(path, record, closed_metadata(record)) for path, _, record, _ in self._entries("plan")]
            linked = KeyResults(self.root).plan_task_ids({r["id"] for _, r, closed in plans if closed is None})
            memberships = []
            for path, record, closed in plans:
                if closed is None:
                    direct, _ = _membership(record["body"])
                    members = list(dict.fromkeys([*direct, *linked.get(record["id"], ())]))
                elif include_closed:
                    members = [row["id"] for row in frozen_rows(record, closed)]
                else:
                    continue
                summary = self._summary(path, record) if closed is None else self._plan_summary(path, record, closed)
                memberships.append((summary, members))
        yield from memberships

    def view(self, plan_id):
        with storage._vault_lock(self.root, write=False):
            path, _, plan, _ = self._find("plan", plan_id)
            return self._view(path, plan)

    def _view(self, path, plan, *, validate_dates=False, linked_task_ids=None):
        from .plan_tree import check_parents, parent_links
        closed = closed_metadata(plan)
        result = self._plan_summary(path, plan, closed)
        if closed is not None:
            result["items"] = frozen_rows(plan, closed)
            if closed.get("includedProjects"):
                result["includedProjectCount"] = len(closed["includedProjects"])
            return result
        ids, _ = _membership(plan["body"])
        if linked_task_ids is None:
            from .okr import KeyResults
            linked_task_ids = KeyResults(self.root).plan_task_ids({plan["id"]}).get(plan["id"], ())
        ids = list(dict.fromkeys([*ids, *linked_task_ids]))
        if not ids:
            return {**result, "items": []}
        needed = set(ids)
        selected = {record["id"]: (p, record, header) for p, _, record, header in self._entries("task")
                    if record["id"] in needed}
        directions = self._project_directions(record for _, record, _ in selected.values())
        items = []
        for identifier in ids:
            if identifier in selected:
                member_path, record, header = selected[identifier]
                if validate_dates:
                    from .schedule import _current_field
                    for field in ("start", "due", "forecast", "completed"):
                        try:
                            _current_field(record, header, field)
                        except ValueError as error:
                            raise ValueError(f"Проверьте {field} в {member_path.relative_to(self.root)}. {error}") from error
                items.append(self._task_summary(member_path, record, directions))
            else:
                items.append({"id": identifier, "type": "task", "kind": None, "missing": True, "title": None, "path": None,
                              "revision": None, "project": None, "direction": None, "start": None, "startLine": None,
                              "due": None, "dueLine": None, "forecast": None, "forecastLine": None,
                              "completed": None, "completedLine": None})
        parents = parent_links(plan["body"])
        check_parents(parents)
        for row in items:
            if row["id"] in parents:
                row["parentId"] = parents[row["id"]]
        return {**result, "items": items}

    def _group_titles(self, plan):
        parents = {row[field] for row in plan["items"] for field in ("project", "direction") if row.get(field)}
        titles = {r["id"]: r["title"] for _, r in storage.iter_records(self.root) if r["id"] in parents} if parents else {}
        for row in plan["items"]:
            row.update(projectTitle=titles.get(row.get("project")), directionTitle=titles.get(row.get("direction")))

    def create_task(self, title, *, direction=None, project=None, due=None, request_id=None, kind="task"):
        if kind not in ("task", "milestone"):
            raise ValueError("Выберите задачу или веху.")
        values = {"direction": direction, "project": project, "due": validate_due(due)}
        if kind == "milestone":
            values["task_kind"] = kind
        return self._create("task", title, values, request_id)

    def create_plan(self, title, *, request_id=None):
        return self._create("plan", title, {}, request_id)

    def _create(self, kind, title, options, request_id):
        if not isinstance(title, str) or not title.strip() or title.splitlines() != [title] or "\0" in title:
            raise ValueError("Укажите название карточки в одну строку.")
        fields = {"type": kind, "schema_version": 1, "title": title}
        fields.update({k: v for k, v in options.items() if v is not None})
        if request_id is None:
            identifier = str(uuid.uuid4())
        else:
            if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
                raise ValueError("Некорректный идентификатор запроса.")
            identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"second-brain/{kind}/{request_id}"))
            fields["creation_hash"] = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
        fields["id"] = identifier
        read_back = self.task if kind == "task" else self.view
        with storage._write_lock(self.root):
            if request_id is not None:
                try:
                    _, _, previous, _ = self._find(kind, identifier)
                except FileNotFoundError:
                    pass
                else:
                    if previous.get("creation_hash") != fields["creation_hash"]:
                        raise storage.ConflictError("Идентификатор запроса уже использован с другими данными.")
                    return read_back(identifier)
            folder = storage._inside(self.root, FOLDERS[kind])
            body = f"# {title}\n"
            if kind == "plan":
                body += f"\n## Задачи\n\n{_BEGIN}\n{_END}\n"
            else:
                for key in ("project", "direction"):
                    if key not in fields:
                        continue
                    storage._validate_fields({"direction": fields[key]})
                    target = storage.find_record(self.root, fields[key])
                    parent = storage.read_record(target)
                    if parent["type"] != key:
                        raise ValueError("Проверьте тип указанного проекта или направления.")
                    if key == "project" and parent["direction"]:
                        explicit = fields.pop("direction", None)
                        if explicit is not None and explicit != parent["direction"]:
                            raise ValueError("Указанное направление отличается от направления проекта.")
                    body += "\n" + storage.markdown_link(parent["title"], target, folder) + "\n"
            folder.mkdir(exist_ok=True)
            storage._publish_record(self.root, fields, body, kinds=FOLDERS)
            return read_back(identifier)

    def add_task(self, plan_id, task_id, *, expected_revision=None):
        with storage._write_lock(self.root):
            path, data, plan, header = self._find("plan", plan_id)
            if closed_metadata(plan) is not None:
                frozen = self._view(path, plan)
                if any(row["id"] == task_id for row in frozen["items"]):
                    return frozen
                raise storage.ConflictError("План закрыт. Выберите активный план для добавления работы.")
            target, _, task, _ = self._find("task", task_id)
            members, end = _membership(plan["body"])
            if task_id in members:
                return self.view(plan_id)
            if expected_revision is not None and expected_revision != plan["revision"]:
                raise storage.ConflictError("План изменился. Перечитайте его перед добавлением задачи.")
            newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
            lines = plan["body"].splitlines(keepends=True)
            _insert_task(lines, end, task, target, path, newline)
            storage.replace_bytes(path, data, header + "".join(lines).encode())
            return self.view(plan_id)
