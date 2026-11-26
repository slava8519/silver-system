"""A read-only, paged view of the current records and confirmed events of each direction."""

from collections import Counter
from pathlib import Path
import re
from urllib.parse import quote

from . import gantt, project_updates, storage
from .planning import Planning, validate_due
from .query import _line_ref
from .schedule import schedule_history


_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})(?:\s*[·:—–]\s*|\.\s+|\s+)(.+)$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((?:<[^>]*>|[^)]+)\)")
_WIKI = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def _short(value, limit=220):
    text = " ".join(value.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _plain(value):
    text = re.sub(r"<!--.*?(?:-->|$)", "", value, flags=re.S)
    text = _MD_LINK.sub(lambda m: m[1], text)
    text = _WIKI.sub(lambda m: m[2] or Path(m[1]).name, text)
    return _short(_BULLET.sub("", text))


def _dated(text, date=None):
    match = _DATE_PREFIX.match(text)
    if match:
        recorded = match[1]
        if "." in recorded:
            recorded = "-".join(reversed(recorded.split(".")))
        try:
            recorded = validate_due(recorded)
        except ValueError:
            return date, text
        if not date or date == recorded:
            return recorded, match[2]
    return date, text


def _card(record, path, root, kind=None):
    return {"id": record["id"], "type": kind or record["type"], "title": _short(record["title"], 100),
            "path": path.relative_to(root).as_posix()}


def _location(card, record, number):
    identifier = quote(card["id"], safe="")
    if card["type"] in ("task", "milestone"):
        digest = _line_ref(record["source_lines"][number - 1])
        href = f"/source/{identifier}?kind=task&line={number}&ref={digest}#cited-line"
    else:
        href = f"#record/{identifier}?line={number}&revision={record['revision']}"
    return {"path": card["path"], "line": number, "href": href}


def _item(text, card, record, line, kind):
    return {"kind": kind, "text": _plain(text), "record": card, "source": _location(card, record, line)}


def _manual(record):
    """An explicit event section gives ordinary Markdown its meaning without guessing from titles or dates."""
    lines = record["body"].splitlines()
    _, code = project_updates._sections(lines)
    section, comment = "intro", False
    for index, raw in enumerate(lines):
        if index in code:
            continue
        if comment:
            if "-->" not in raw:
                continue
            raw = raw.partition("-->")[2]
            comment = False
        if "<!--" in raw:
            comment = "-->" not in raw.partition("<!--")[2]
            raw = raw.partition("<!--")[0]
        heading = _HEADING.match(raw)
        if heading:
            if len(heading[1]) == 2:
                section = heading[2].casefold()
            elif len(heading[1]) != 1 or section != "intro":
                section = ""
            continue
        text = _plain(raw)
        if text and not raw.startswith(("    ", "\t", ">")):
            kind = {"коротко": "brief", "последнее": "event", "события": "event",
                    "intro": "description", "описание": "description"}.get(section)
            if kind:
                yield kind, text, record["body_start"] + index


def _resolved(root, refs, fallback, cache, scope):
    """Only show available supporting passages. Archive originals may contain adjacent personal details."""
    if not refs:
        return fallback
    for ref in refs:
        key = tuple(ref.get(k) for k in ("id", "path", "line", "ref", "quote"))
        if key not in cache:
            try:
                cache[key] = project_updates._source(root, ref, resolve=True)
            except (ValueError, KeyError, OSError):
                cache[key] = None
        found = cache[key]
        if found:
            shared_card = found["id"] in scope.source_areas and scope.source_areas[found["id"]] in (None, scope.card["id"])
            if found["path"].startswith("Sources/Originals/") or not shared_card:
                return fallback
            return {"path": found["path"], "line": found["line"], "href": found["href"]}
    return None


class _Direction:
    def __init__(self, root, record, path, source_areas):
        self.source_areas = source_areas
        self.card = _card(record, path, root)
        self.own = None
        self.description = None
        self.choices = {}
        self.events = {}
        self.projects = self.tasks = 0
        for kind, text, line in _manual(record):
            entry = _item(text, self.card, record, line, kind)
            if kind == "brief" and self.own is None:
                self.own = entry
            elif kind == "description" and self.description is None:
                self.description = entry
            elif kind == "event":
                self.event(entry, dated_prose=True)
        if self.own is None:
            self.own = _state(record, self.card)

    def offer(self, group, priority, entry):
        if not entry or not entry["text"]:
            return
        rank = (priority, entry["record"]["title"].casefold(), entry["record"]["id"])
        if group not in self.choices or rank < self.choices[group][0]:
            self.choices[group] = rank, entry

    def event(self, entry, date=None, *, dated_prose=False):
        if not entry["source"] or not entry["text"]:
            return
        text = entry["text"]
        if date or dated_prose:
            date, text = _dated(text, date)
        event = {"text": text, "date": date, "record": entry["record"], "source": entry["source"]}
        key = (date, text, event["source"]["path"], event["source"]["line"])
        self.events.setdefault(key, event)

    def output(self):
        if self.own:
            briefs, brief_total = [self.own], 1
        else:
            briefs = [entry for _, entry in sorted(self.choices.values(), key=lambda value: value[0])[:2]]
            brief_total = len(self.choices)
            if not briefs and self.description:
                briefs, brief_total = [self.description], 1
        events = sorted(self.events.values(), key=lambda e: (e["date"] or "", e["record"]["id"], e["source"]["line"]),
                        reverse=True)
        latest = events[0] if events and (events[0]["date"] or len(events) == 1) else None
        return {**{k: self.card[k] for k in ("id", "title", "path")}, "projectCount": self.projects,
                "taskCount": self.tasks, "briefs": briefs, "briefTotal": brief_total, "latest": latest,
                "hasUndated": any(e["date"] is None for e in events)}


def _state(record, card):
    for field, label in (("status", "Статус"), ("completed", "Завершено"), ("next_step", "Следующий шаг"),
                         ("forecast", "Прогноз"), ("due", "Согласованный срок")):
        if record.get(field):
            return _item(f"{label} — {record[field]}", card, record, record["field_lines"][field], field)
    return None


def _project(root, scope, path, record, cache):
    card = _card(record, path, root)
    scope.projects += 1
    _, entries, _ = project_updates._layout(record)
    current = project_updates._current(entries)
    if current and current["text"]:
        entry = _item(current["text"], card, record, current["line"], "brief")
        entry["source"] = _resolved(root, current["sources"], entry["source"], cache, scope)
        if entry["source"]:
            scope.offer(card["id"], 0, entry)
    scope.offer(card["id"], 2, _state(record, card))
    for event, _, _ in entries:
        if event["field"] == "event":
            entry = _item(event["text"], card, record, event["line"], "event")
            entry["source"] = _resolved(root, event["sources"], entry["source"], cache, scope)
            scope.event(entry, event["date"], dated_prose=event["updated"] is None and not event["sources"])


def _work(root, scope, path, record, group, cache):
    card = _card(record, path, root, "milestone" if record.get("task_kind") == "milestone" else "task")
    scope.tasks += 1
    lines = record["body"].splitlines(keepends=True)
    for field in ("brief", "latest"):
        try:
            rows = gantt._entries(lines, field, gantt._window(lines, field))
        except (ValueError, KeyError):
            continue
        for saved, index, _ in rows:
            line = record["body_start"] + index + int(lines[index].startswith(gantt._OPEN))
            entry = _item(saved["text"], card, record, line, field)
            entry["source"] = _resolved(root, saved["sources"], entry["source"], cache, scope)
            if field == "latest":
                scope.event(entry, saved.get("date"), dated_prose=saved["id"].startswith("markdown-"))
            elif entry["source"]:
                scope.offer(group, 1, entry)
    scope.offer(group, 3, _state(record, card))
    if record.get("completed"):
        try:
            day = validate_due(record["completed"])
            changes = [c for c in schedule_history(record, card["path"]) if c["field"] == "completed"]
        except (KeyError, ValueError):
            return
        entry = _item("Веха достигнута" if card["type"] == "milestone" else "Работа завершена",
                      card, record, record["field_lines"]["completed"], "completed")
        if changes and changes[-1]["after"] == day and not changes[-1].get("ownerConfirmed"):
            entry["source"] = _resolved(root, changes[-1]["sources"], entry["source"], cache, scope)
        scope.event(entry, day)


def direction_overview(root, *, limit=5, offset=0, direction=None):
    if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
        raise ValueError("Укажите размер страницы от 1 до 20 и неотрицательное смещение.")
    root = Path(root).expanduser().resolve()
    with storage._vault_lock(root, write=False):
        records = list(storage.iter_records(root))
        ids = Counter(r["id"] for _, r in records)
        areas = sorted(((path, r) for path, r in records if r["type"] == "direction" and ids[r["id"]] == 1
                        and (direction is None or r["id"] == direction)),
                       key=lambda pair: (pair[1]["title"].casefold(), pair[1]["id"]))
        if direction is not None and not areas:
            raise ValueError("Направление не найдено или его ID неоднозначен.")
        source_areas = {r["id"]: r.get("direction") for _, r in records if ids[r["id"]] == 1}
        scopes = {r["id"]: _Direction(root, r, path, source_areas) for path, r in areas[offset:offset + limit]}
        if scopes:
            cache, project_areas = {}, {}
            for path, record in records:
                if ids[record["id"]] != 1:
                    continue
                area = record.get("direction")
                if record["type"] == "project":
                    project_areas[record["id"]] = area
                    if area in scopes:
                        _project(root, scopes[area], path, record, cache)
                elif record["type"] == "note" and area in scopes:
                    card = _card(record, path, root)
                    for kind, text, line in _manual(record):
                        if kind == "brief":
                            scopes[area].offer(card["id"], 4, _item(text, card, record, line, kind))
                        elif kind == "event":
                            scopes[area].event(_item(text, card, record, line, kind), dated_prose=True)
            for path, data, record, _ in Planning(root)._entries("task"):
                project = record.get("project")
                area = record.get("direction") or project_areas.get(project)
                if area in scopes:
                    group = project if project_areas.get(project) == area else record["id"]
                    record["source_lines"] = data.decode("utf-8").splitlines()
                    _work(root, scopes[area], path, record, group, cache)
        return {"items": [s.output() for s in scopes.values()], "total": len(areas), "truncated": offset + limit < len(areas)}
