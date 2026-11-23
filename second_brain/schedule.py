"""Forecasts and confirmed completion dates on shared Markdown task cards."""

from datetime import date, datetime, timezone
import hashlib
import json
import re

from . import storage
from .evidence import linked_source, verify_sources
from .planning import Planning, TASK_SCALARS, validate_due


_LABELS = {"forecast": "Прогноз", "completed": "Факт завершения"}
_MARK = " <!-- brain-schedule: "


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _entries(record, path):
    entries = []
    seen = set()
    for number, line in enumerate(record["body"].splitlines(), record["body_start"]):
        if _MARK not in line:
            continue
        try:
            if not line.startswith("- ") or not line.endswith(" -->"):
                raise ValueError
            change = json.loads(line.rsplit(_MARK, 1)[1][:-4])
            token = change["revision"]
            if (not re.fullmatch("[0-9a-f]{64}", token) or token in seen or change["field"] not in _LABELS
                    or not re.fullmatch("[0-9a-f]{64}", change["requestHash"])
                    or change["actor"] not in ("ai", "manual") or type(change["ownerConfirmed"]) is not bool):
                raise ValueError
            validate_due(change["before"])
            validate_due(change["after"])
            change["sources"] = [linked_source(s) for s in change["sources"]]
            seen.add(token)
        except (TypeError, KeyError, ValueError) as error:
            raise storage.ConflictError(f"Проверьте запись истории дат в {path}, строка {number}.") from error
        entries.append({**change, "path": path, "line": number})
    return entries


def schedule_history(record, relative_path):
    """Return authored changes, oldest first, with clickable original evidence."""
    return [{k: v for k, v in entry.items() if k != "requestHash"} for entry in _entries(record, relative_path)]


def _current_field(record, header, field):
    current = record.get(field)
    if current is not None:
        return validate_due(current)
    number = record["field_lines"].get(field)
    if number is not None:
        lines = header.decode().splitlines()
        raw = lines[number - 1].partition(":")[2].strip()
        # A legacy vault can already use the same YAML key for a list or mapping.
        if raw and not raw.startswith("#") and not re.fullmatch(r"(?:null|~)(?:\s+#.*)?", raw, re.I):
            raise ValueError(f"Для изменения сложного поля {field} откройте Markdown.")
        following = next((line for line in lines[number:] if line.strip() and not line.lstrip().startswith("#")), "")
        if following.startswith((" ", "\t", "- ")):
            raise ValueError(f"Для изменения многострочного поля {field} откройте Markdown.")
    return None


def _request(field, value, actor, reason, sources, owner_confirmed):
    if not isinstance(field, str) or field not in _LABELS:
        raise ValueError("Выберите прогноз или факт завершения.")
    validate_due(value)
    if actor not in ("manual", "ai") or type(owner_confirmed) is not bool:
        raise ValueError("Укажите автора и явное подтверждение владельца.")
    if reason is not None:
        if not isinstance(reason, str) or len(reason) > 600 or any(ord(c) < 32 for c in reason):
            raise ValueError("Опишите основание одной строкой до 600 символов.")
        reason = reason.strip() or None
    if sources is None:
        sources = []
    if not isinstance(sources, list) or len(sources) > 8 or any(not isinstance(s, dict) for s in sources):
        raise ValueError("Основания указываются списком ссылок на строки.")
    if field == "completed":
        if value and value > date.today().isoformat():
            raise ValueError("Факт завершения нельзя датировать будущим днём.")
        if not sources and not (reason and (actor == "manual" or owner_confirmed)):
            raise ValueError("Для факта завершения нужен источник или пояснение с подтверждением владельца.")
    return {"field": field, "after": value, "actor": actor, "reason": reason, "sources": sources,
            "ownerConfirmed": owner_confirmed}


def _with_change(root, path, record, header, change, entries):
    newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
    header = storage._patch_header(header, record, {change["field"]: change["after"]})
    body = record["body"].splitlines(keepends=True)
    insert_at = entries[-1]["line"] - record["body_start"] + 1 if entries else len(body)
    line = f'- {_LABELS[change["field"]]}: {change["before"] or "не указано"} → {change["after"] or "не указано"}'
    if change["reason"]:
        line += " · " + storage.markdown_label(change["reason"])
    for ref in change["sources"]:
        line += " · " + storage.markdown_link(f'{ref["path"]} · {ref["line"]}', root / ref["path"], path.parent)
    line += _MARK + storage.comment_json(change) + " -->" + newline
    if not entries:
        line = newline + "## История дат" + newline + newline + line
    if insert_at and not body[insert_at - 1].endswith(("\n", "\r")):
        line = newline + line
    body.insert(insert_at, line)
    return header + "".join(body).encode()


class Schedule:
    def __init__(self, root):
        self.planning = Planning(root)
        self.root = self.planning.root

    def history(self, task_id):
        path, _, record, _ = self.planning._find("task", task_id)
        return schedule_history(record, path.relative_to(self.root).as_posix())

    def _context(self, record, path):
        plans = [{k: p[k] for k in ("id", "title", "path")} for p in self.planning.plans_for_task(record["id"])]
        return {"id": record["id"], "title": record["title"], "path": path.relative_to(self.root).as_posix(),
                "due": record.get("due"), "forecast": record.get("forecast"), "completed": record.get("completed"),
                "taskRevision": record["revision"], "plans": plans}

    def _preview(self, context, record, header, request):
        before = _current_field(record, header, request["field"])
        sources = verify_sources(self.root, request["sources"])
        verified = {**request, "sources": sources}
        revision = _hash(["schedule", record["id"], record["revision"], verified,
                          sorted(p["id"] for p in context["plans"])])
        return {**context, **verified, "before": before, "revision": revision, "changed": before != request["after"]}

    def preview(self, task_id, field, value, *, actor, reason=None, sources=None, owner_confirmed=False):
        request = _request(field, value, actor, reason, sources, owner_confirmed)
        path, _, record, header = self.planning._find("task", task_id)
        return self._preview(self._context(record, path), record, header, request)

    def apply(self, task_id, field, value, revision, *, actor, reason=None, sources=None, owner_confirmed=False):
        if not isinstance(revision, str) or not re.fullmatch("[0-9a-f]{64}", revision):
            raise ValueError("Укажите версию из предварительного просмотра.")
        request = _request(field, value, actor, reason, sources, owner_confirmed)
        with storage._write_lock(self.root):
            path, data, record, header = self.planning._find("task", task_id)
            context = self._context(record, path)
            entries = _entries(record, context["path"])
            existing = next((e for e in entries if e["revision"] == revision), None)
            if existing:
                if existing["requestHash"] != _hash(request):
                    raise storage.ConflictError("Для этого предпросмотра уже сохранено другое изменение.")
                change = {k: v for k, v in existing.items() if k != "requestHash"}
                return {**context, "field": field, "revision": revision, "changed": False, "replayed": True,
                        "before": change["before"], "after": value, "change": change}
            try:
                proposal = self._preview(context, record, header, request)
            except (ValueError, FileNotFoundError) as error:
                raise storage.ConflictError("Карточка или основание изменились. Повторите предпросмотр.") from error
            if revision != proposal["revision"]:
                raise storage.ConflictError("Задача, состав планов или основание изменились. Повторите предпросмотр.")
            if not proposal["changed"]:
                return {**proposal, "replayed": False, "change": None}
            change = {**request, "sources": proposal["sources"], "updated": datetime.now(timezone.utc).isoformat(),
                      "revision": revision, "requestHash": _hash(request), "before": proposal["before"]}
            updated = _with_change(self.root, path, record, header, change, entries)
            saved, _ = storage._parse_record(path, updated, kinds=("task",), optional_scalars=TASK_SCALARS)
            entry = schedule_history(saved, context["path"])[-1]
            storage.replace_bytes(path, data, updated)
            return {**proposal, field: value, "taskRevision": saved["revision"], "replayed": False, "change": entry}
