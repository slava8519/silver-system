"""Editable contextual project and task relationships on the key result itself."""

from datetime import datetime, timezone
import re
from urllib.parse import unquote

from . import planning, storage
from .okr import _check_revision, _parse_key_result, _text, _valid_revision
from .okr_progress import _WEIGHT, _weights
from .teams import _bounds, _ensure_section


_PROJECTS = ("<!-- okr-projects -->", "<!-- /okr-projects -->")
_HISTORY = ("<!-- okr-links-history -->", "<!-- /okr-links-history -->")
_GENERATED_LINK = re.compile(r"^\s*[-*+]\s+\[(?:\\.|[^\]\\])*\]\([^)]+\)\s*$")


def _connections(lines, markers, kind, *, location="карточке КР"):
    bounds = _bounds(lines, markers)
    rows = {}
    if bounds:
        prefix = f"<!-- {kind}-id:"
        pattern = re.compile(r"(?<=\s)" + re.escape(prefix) + r" ([A-Za-z0-9._~%\-]+) -->")
        for index in range(bounds[0] + 1, bounds[1]):
            line = lines[index]
            marker = pattern.search(line)
            if prefix in line and (not marker or line.count(prefix) != 1):
                raise storage.ConflictError(f"Проверьте ID связи в {location}.")
            if marker:
                identifier = _text(unquote(marker[1], errors="strict"), "ID связи")
                if identifier in rows:
                    raise storage.ConflictError(f"Одна связь дважды указана в {location}.")
                rows[identifier] = (index, marker)
    return rows, bounds


def _project(root, identifier, *, required):
    try:
        path = storage.find_record(root, identifier)
    except FileNotFoundError:
        if required:
            raise
    else:
        record = storage.read_record(path)
        if record["type"] == "project":
            return {"id": identifier, "title": record["title"], "path": path.relative_to(root).as_posix()}
        if required:
            raise ValueError("Выберите существующий проект для контекста КР.")
    return {"id": identifier, "title": None, "path": None, "missing": True}


def context_projects(root, record, cache=None):
    if _PROJECTS[0] not in record["body"] and _PROJECTS[1] not in record["body"]:
        return []
    rows, _ = _connections(record["body"].splitlines(), _PROJECTS, "project")
    if cache is None:
        cache = {}
    for key in rows:
        if key not in cache:
            cache[key] = _project(root, key, required=False)
    return [cache[key] for key in rows]


def _remove(lines, row):
    index, marker = row
    line = lines[index]
    before, after = line[:marker.start()], line[marker.end():]
    # Preserve a manually annotated bullet as ordinary prose.
    if _GENERATED_LINK.fullmatch(before) and not after.strip():
        lines[index] = ""
    else:
        lines[index] = before.rstrip(" \t") + after


def set_connection(service, identifier, linked_id, *, kind, present, revision):
    _valid_revision(revision)
    linked_id = _text(linked_id, "ID связи")
    if type(present) is not bool:
        raise ValueError("Укажите добавление или снятие связи.")
    task = kind == "task"
    markers = (planning._BEGIN, planning._END) if task else _PROJECTS
    with storage._write_lock(service.root):
        path, original, record, header = service._find(identifier)
        lines = record["body"].splitlines(keepends=True)
        if task:
            # Keep the same validation rules as every reader of a plan/KR membership.
            _, end = planning._membership(record["body"])
            if end is None:
                raise ValueError("Проверьте границы списка связанных работ в карточке.")
        rows, bounds = _connections(lines, markers, kind)
        weight = _weights(record).get(("task", linked_id)) if task else None
        if present == (linked_id in rows) and (present or weight is None):
            return service._summarize(path, record, detailed=True)
        _check_revision(record, revision)
        if record.get("objective_id"):
            service._active_plan(service._objective(record["objective_id"])["planId"])
        newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
        note = ("Добавлена" if present else "Снята") + (" связь с работой" if task else " связь с проектом")
        if weight is not None:
            # Also clear an orphaned weight if the owner removed the membership
            # in Obsidian and later links that task again through the app.
            previous = lines[weight[0]].rsplit(_WEIGHT, 1)[0].removeprefix("- ")
            note += " · прежняя запись веса — " + previous
            lines[weight[0]] = ""
        if present:
            linked = service.planning.task(linked_id) if task else _project(service.root, linked_id, required=True)
            link = storage.markdown_link(linked["title"], service.root / linked["path"], path.parent)
            position = _ensure_section(lines, bounds, "## Контекстные проекты", markers, newline)
            lines.insert(position, f"- {link} {storage.id_marker(kind, linked_id)}{newline}")
            note += " · " + link
        elif linked_id in rows:
            index, marker = rows[linked_id]
            note += " · " + lines[index][:marker.start()].strip().removeprefix("- ")
            _remove(lines, rows[linked_id])
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        position = _ensure_section(lines, _bounds(lines, _HISTORY), "## История связей", _HISTORY, newline)
        lines.insert(position, f"- {stamp} · {note} · {storage.markdown_label(linked_id)}{newline}")
        updated = header + "".join(lines).encode()
        saved, _ = _parse_key_result(path, updated)
        result = service._summarize(path, saved, detailed=True)
        storage.replace_bytes(path, original, updated)
        return result
