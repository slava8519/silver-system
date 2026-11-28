"""Page through saved readiness and evidence signals for the tasks of one plan."""

from . import brief, storage
from .cycles import closed_metadata, frozen_rows
from .gantt import _row_fields
from .gantt_feedback import pending_task
from .okr_progress import current_readiness
from .planning import Planning


def _summary(task, reading):
    row = {"id": task["id"], "title": task.get("title"), "value": reading["value"] if reading else None}
    brief.clip_fields(row, ("title",))
    if task.get("missing"):
        row["missing"] = True
    if reading:
        row.update(date=reading["date"], kind=reading["kind"], actor=reading["actor"])
        if reading.get("ownerConfirmed"):
            row["ownerConfirmed"] = True
        if reading.get("line"):
            row["cardLine"] = reading["line"]
        if reading.get("source"):
            ref = reading["source"]
            row["source"] = {"path": ref["path"], "line": ref["line"]}
            if ref.get("unavailable"):
                row["source"]["unavailable"] = True
    return row


def _active_row(root, path, record, sources, day):
    reading = current_readiness(root, record, sources=sources)
    row = _summary(record, reading)
    fields = _row_fields(record, all_entries=True, with_lines=True)
    pending = pending_task(record, path.relative_to(root).as_posix(), fields=fields)
    flags = []
    if pending:
        flags.append("firstGanttReview" if pending["firstReview"] else "unreviewedGantt")
    if not reading:
        flags.append("unknownValue")
    else:
        if (reading.get("source") or {}).get("unavailable"):
            flags.append("unavailableSource")
        # An event date establishes the order of recorded events, not when an editor changed a file.
        for event in fields["latest"]:
            if event.get("sources") and event.get("date") and event["date"] > reading["date"]:
                flags.append("laterCitedEvent")
                row["eventDate"] = event["date"]
                row["eventLine"] = event["line"]
                break
    if day and not record.get("completed"):
        forecast = storage._iso_date(record.get("forecast"))
        start = storage._iso_date(record.get("start"))
        if forecast and forecast < day:
            flags.append("pastForecast")
            row["forecast"] = forecast.isoformat()
        if reading and reading["value"] == "0" and start and (day - start).days >= 7:
            flags.append("zeroAfterWeek")
            row["start"] = start.isoformat()
    if flags:
        row["reviewFlags"] = flags
    return row


def _active_rows(planning, identifiers, day):
    wanted = set(identifiers)
    if not wanted:
        return
    selected = {record["id"]: (path, record) for path, _, record, _ in planning._read_entries("task", wanted)}
    sources = {}
    for identifier in identifiers:
        entry = selected.get(identifier)
        if entry:
            yield _active_row(planning.root, *entry, sources, day)
        else:
            yield {"id": identifier, "title": None, "value": None, "missing": True, "reviewFlags": ["missingTask"]}


def plan_readiness(root, plan_id, *, review_on=None, candidates=False, limit=20, offset=0):
    if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
        raise ValueError("Укажите размер страницы 1–20 и неотрицательное смещение.")
    day = storage._iso_date(review_on)
    if (review_on is not None or candidates) and not day:
        raise ValueError("Для даты проверки укажите --review-on ГГГГ-ММ-ДД.")
    planning = Planning(root)
    with storage._vault_lock(planning.root, write=False):
        path, _, plan, _ = planning._find("plan", plan_id)
        closed = closed_metadata(plan)
        if closed is not None:
            if review_on is not None:
                raise ValueError("Отбор на дату доступен для активного плана. Для снимка опустите --review-on.")
            frozen = frozen_rows(plan, closed)
            plan_total = total = len(frozen)
            items = []
            for task in frozen[offset:offset + limit]:
                row = _summary(task, task.get("readiness"))
                row["snapshotLine"] = task["snapshotLine"]
                items.append(row)
        else:
            members = planning.member_ids(plan)
            plan_total = total = len(members)
            if candidates:
                matches = (row for row in _active_rows(planning, members, day) if row.get("reviewFlags"))
                page = brief.page(matches, limit, offset)
                items, total = page["items"], page["total"]
            else:
                items = list(_active_rows(planning, members[offset:offset + limit], day))
    paths = {}
    for row in items:
        source = row.get("source")
        if source:
            name = source.pop("path")
            source["pathIndex"] = paths.setdefault(name, len(paths))
    result = {"planId": plan_id, "historical": closed is not None, "planTotal": plan_total, "total": total,
              "items": items, "sourcePaths": list(paths), "nextOffset": offset + limit if offset + limit < total else None}
    if closed is not None:
        result.update(snapshotPath=path.relative_to(planning.root).as_posix(), closedAt=closed["closedAt"])
    if review_on is not None:
        result["reviewOn"] = review_on
    return result
