"""Build a closed cycle's date snapshot and its independently sourced weekly events."""

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import PurePosixPath
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import storage
from .evidence import resolve_source
from .gantt import _changes, _entries, _source_keys, _window
from .plan_transaction import previous_versions
from .planning import Planning, TASK_SCALARS, validate_due
from .schedule import schedule_history


_DATE_LABELS = (("start", "плановое начало"), ("due", "согласованный срок"),
                ("forecast", "прогноз"), ("completed", "факт завершения"))


def _period(closed, start, end, timezone_name):
    try:
        zone = timezone.utc if timezone_name == "UTC" else ZoneInfo(timezone_name)
    except (ValueError, TypeError, ZoneInfoNotFoundError) as error:
        raise ValueError("Укажите часовой пояс, например Europe/Minsk или UTC.") from error
    close_day = closed.astimezone(zone).date()
    last = date.fromisoformat(validate_due(end)) if end is not None else close_day
    first = date.fromisoformat(validate_due(start)) if start is not None else last - timedelta(days=6)
    if not 0 <= (last - first).days <= 6 or last > close_day:
        raise ValueError("Выберите от одного до семи дней до даты закрытия включительно.")
    return first.isoformat(), last.isoformat()


def _task_records(planning, rows):
    """Usually only the snapshot's named task files are needed. Follow renames by ID."""
    pending = previous_versions(planning.root)
    records, moved = {}, set()
    for row in rows:
        if not row.get("path"):
            continue
        relative = PurePosixPath(row["path"])
        if relative.parts[:1] != ("Tasks",) or ".." in relative.parts or relative.suffix.lower() != ".md":
            raise ValueError("Проверьте путь к работе в снимке.")
        path = storage._inside(planning.root, relative)
        if path.is_file():
            raw = path.read_bytes()
            if path in pending:
                before, after_hash = pending[path]
                if sha256(raw).hexdigest() == after_hash:
                    raw = before
            try:
                record, _ = storage._parse_record(path, raw, kinds=("task",), optional_scalars=TASK_SCALARS)
            except storage.UnmanagedMarkdown:
                record = None
            if record and record["id"] == row["id"]:
                records[row["id"]] = record, relative.as_posix()
                continue
        moved.add(row["id"])
    if moved:
        for path, _, record, _ in planning._entries("task"):
            if record["id"] in moved:
                records[record["id"]] = record, path.relative_to(planning.root).as_posix()
    return records


def _recorded(updated, cutoff, unchanged=False):
    """True before closure, False afterwards, None for a legacy timestamp with an unresolved tie."""
    if not updated:
        return None
    stamp = datetime.fromisoformat(updated)
    closed, precise = cutoff
    if stamp.tzinfo is None:
        raise ValueError("В истории события не указан часовой пояс.")
    if stamp.replace(microsecond=0) == closed.replace(microsecond=0) and (not precise or "." not in updated):
        return True if unchanged else None
    return stamp <= closed


def _as_of_events(record, cutoff, unchanged, start, end, title, warnings):
    lines = record["body"].splitlines(keepends=True)
    live = {e["id"]: e for e, _, _ in _entries(lines, "latest", _window(lines, "latest"))}
    histories = {}
    for change in _changes(lines):
        if change.get("field") == "latest" and change.get("entryId"):
            histories.setdefault(change["entryId"], []).append(change)
    for identifier in dict.fromkeys([*live, *histories]):
        event = live.get(identifier)
        position = _recorded(event.get("updated"), cutoff, unchanged) if event else None
        if position is True:
            yield event
            continue
        history = histories.get(identifier, [])
        prior = following = None
        for change in history:
            known = _recorded(change.get("updated"), cutoff, unchanged)
            if known is False:
                following = change
                break
            prior = change if known is True else None
        # At a managed edit the history contains both texts, dates and sources. Match both
        # sides of the boundary so a hand-edited or removed version isn't silently restored.
        if (event and position is False and prior and following and prior["after"] == following["before"]
                and prior["date"] == following["beforeDate"]
                and _source_keys(prior["sources"]) == _source_keys(following["beforeSources"])):
            yield {"date": prior["date"], "text": prior["after"], "sources": prior["sources"]}
        elif (not event or position is None or prior or (following and following.get("before"))) and any(
                start <= (change.get("date") or "") <= end for change in history):
            warnings.append(f'«{title}» — версию события при закрытии нужно сверить по истории карточки.')


def _sources(root, refs):
    return [{**resolve_source(root, ref), "kind": "source"} for ref in refs]


def _snapshot_fact(plan, row):
    title = row.get("title") or row["id"]
    ident = quote(row["id"], safe="")
    target = f"/#plan/{quote(plan['id'], safe='')}?task={ident}"
    citation = {"kind": "snapshot", "path": plan["path"], "line": row["snapshotLine"], "href": target}
    dates = {key: row.get(key) for key, _ in _DATE_LABELS}
    details = [f"{label} {dates[key]}" for key, label in _DATE_LABELS if dates[key]]
    if row.get("missing"):
        description = "при закрытии карточка отсутствовала"
    elif not details:
        description = "в снимке даты не указаны"
    else:
        if dates["forecast"] and dates["due"] and dates["forecast"] > dates["due"]:
            days = (date.fromisoformat(dates["forecast"]) - date.fromisoformat(dates["due"])).days
            details.append(f"прогноз позже срока на {days} дн.")
        if not dates["completed"]:
            details.append("факт завершения не записан")
        description = ", ".join(details)
    subject = ("Веха " if row.get("kind") == "milestone" else "") + f"«{title}»"
    return {"kind": "snapshot", "taskId": row["id"], "title": title, "directionId": row.get("direction"),
            "directionTitle": row.get("directionTitle"), "missing": bool(row.get("missing")),
            "dates": dates, "text": f"{subject} — {description}.", "sources": [citation]}


def _events(row, current, cutoff, start, end, snapshot, root, warnings):
    completion = row.get("completed")
    in_period = completion and start <= completion <= end
    common = {key: snapshot[key] for key in ("taskId", "title", "directionId", "directionTitle")}
    completion_sources = list(snapshot["sources"])
    if current:
        record, path = current
        unchanged = record["revision"] == row["revision"]
        for event in _as_of_events(record, cutoff, unchanged, start, end, common["title"], warnings):
            day = event.get("date")
            if not day or not start <= day <= end:
                continue
            if not event["sources"]:
                warnings.append(f'«{common["title"]}», {day} — у события нет проверяемой ссылки на запись.')
                continue
            try:
                sources = _sources(root, event["sources"])
            except (ValueError, OSError):
                warnings.append(f'«{common["title"]}», {day} — исходная строка события требует сверки.')
                continue
            yield {**common, "kind": "event", "date": day, "text": event["text"], "sources": sources}
        if in_period:
            history = [change for change in schedule_history(record, path) if change["field"] == "completed"
                       and _recorded(change["updated"], cutoff, unchanged)]
            if history and history[-1]["after"] == completion:
                try:
                    completion_sources += _sources(root, history[-1]["sources"])
                except (ValueError, OSError):
                    warnings.append(f'«{common["title"]}» — исходная запись о завершении требует сверки.')
    elif not row.get("missing"):
        warnings.append(f'«{common["title"]}» — карточка событий недоступна.')
    if in_period:
        yield {**common, "kind": "event", "date": completion,
               "text": f'Подтверждено завершение работы «{common["title"]}».', "sources": completion_sources}


def build_weekly_evidence(root, plan_id, *, start=None, end=None, timezone_name="UTC"):
    """Use the frozen dates for state, event dates for inclusion, and close time as the knowledge cutoff."""
    planning = Planning(root)
    with storage._vault_lock(planning.root, write=False):
        plan = planning.view(plan_id)
        if plan["status"] != "closed":
            raise ValueError("Для недельного обзора выберите закрытый план.")
        closed = datetime.fromisoformat(plan["closedAt"])
        start, end = _period(closed, start, end, timezone_name)
        cutoff = closed, "." in plan["closedAt"]
        facts, warnings = [], []
        records = _task_records(planning, plan["items"])
        for row in plan["items"]:
            snapshot = _snapshot_fact(plan, row)
            facts.append(snapshot)
            notes = []
            facts.extend(_events(row, records.get(row["id"]), cutoff, start, end, snapshot, planning.root, notes))
            for note in dict.fromkeys(notes):
                warnings.append({"text": note, "sources": [dict(snapshot["sources"][0])]})
        return {"planId": plan_id, "planTitle": plan["title"], "planPath": plan["path"], "closedAt": plan["closedAt"],
                "start": start, "end": end, "timezone": timezone_name, "facts": facts, "warnings": warnings}
