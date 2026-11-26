"""Refresh a task's Gantt text from evidence already assigned to that task."""

from datetime import datetime

from . import evidence, storage
from .gantt import Gantt, _changes, _row_fields, _source_keys
from .okr import _check_date, _check_quote
from .okr_progress import _readings
from .schedule import schedule_history


def _dated_quote(sources, day):
    """Use a date stated in a citation, independently of the time the card was edited."""
    for source in sources:
        try:
            if _check_date(day, source["quote"]):
                return source["quote"]
        except ValueError:
            continue
    return None


def _dated_facts(record, relative):
    # Multiple decisions about one day are revisions. Use its last recorded
    # reading; an estimate does not establish measured progress.
    readings = _readings(record)
    effective = {reading["date"]: reading for reading in readings}
    facts = []
    for day, reading in effective.items():
        source = reading["source"]
        if reading["kind"] == "fact" and source and _dated_quote([source], day):
            _check_quote(reading["value"], "%", day, source["quote"])
            facts.append((day, source["quote"], [source]))

    dates = schedule_history(record, relative)
    last = next((row for row in reversed(dates) if row["field"] == "completed"), None)
    if last and last["after"] and record.get("completed") == last["after"]:
        quote = _dated_quote(last["sources"], last["after"])
        if quote:
            facts.append((last["after"], quote, last["sources"]))
    # A corrected value or withdrawn completion is still historical evidence.
    # It cannot supply today's brief solely because its event has a later date.
    historical = {(r["date"], key) for r in readings if r["kind"] == "fact" and r["source"]
                  for key in _source_keys([r["source"]])}
    historical.update((row["after"], key) for row in dates if row["field"] == "completed"
                      for key in _source_keys(row["sources"]))
    current = {(r["date"], key) for r in effective.values() if r["kind"] == "fact" and r["source"]
               for key in _source_keys([r["source"]])}
    if last and last["after"] == record.get("completed"):
        current.update((last["after"], key) for key in _source_keys(last["sources"]))
    return facts, historical - current


def _known_events(record, live):
    """An edited or deleted event is still evidence of the owner's decision."""
    known = set()
    for row in live:
        known.update((row.get("date"), key) for key in _source_keys(row["sources"]))
    for row in _changes(record["body"].splitlines()):
        if row.get("field") == "latest":
            for day, refs in ((row.get("date"), row.get("sources", [])),
                              (row.get("beforeDate"), row.get("beforeSources", []))):
                known.update((day, key) for key in _source_keys(refs))
    return known


def _summary_candidate(brief, events):
    """A later edit timestamp alone proves neither a newer fact nor a stale summary."""
    if brief["manual"] or not events:
        return None
    dated = [e for e in events if e.get("date")]
    if len(events) > 1 and len(dated) != len(events):
        return None
    latest = max(events, key=lambda e: e.get("date") or "")
    if (not latest["sources"] or sum(e.get("date") == latest.get("date") for e in events) > 1
            or latest["text"] == brief["text"]):
        return None
    if not brief["text"]:
        return latest
    previous_keys = _source_keys(brief["sources"])
    if not previous_keys or previous_keys.intersection(_source_keys(latest["sources"])):
        return None
    previous = [e for e in dated if previous_keys.intersection(_source_keys(e["sources"]))]
    if (not previous or not latest.get("date") or max(e["date"] for e in previous) >= latest["date"]
            or not latest["updated"] or not brief["updated"]):
        return None
    event_update = datetime.fromisoformat(latest["updated"])
    brief_update = datetime.fromisoformat(brief["updated"])
    if event_update.tzinfo and brief_update.tzinfo and event_update > brief_update:
        return latest
    return None


def _refresh_task(gantt, record, relative, report, source_cache):
    def resolved(refs):
        sources = []
        for ref in refs:
            key = (ref["id"], ref["path"], ref["line"], ref["ref"], ref["quote"])
            if key not in source_cache:
                try:
                    source_cache[key] = evidence.resolve_source(gantt.root, ref)
                except (ValueError, OSError):
                    source_cache[key] = None
            if source_cache[key] is None:
                issue = {"path": relative, "reason": f'Перепроверьте цитату в {ref["path"]}, строка {ref["line"]}.'}
                if issue not in report["issues"]:
                    report["issues"].append(issue)
                return []
            sources.append(source_cache[key])
        return sources

    fields = _row_fields(record, all_entries=True)
    brief = fields["brief"]
    events = [row for row in fields["latest"] if not row.get("deleted")]
    for entry in [brief, *events]:
        if entry["sources"]:
            resolved(entry["sources"])
    known = _known_events(record, events)
    revision = record["revision"]
    event_added = False
    facts, superseded = _dated_facts(record, relative)
    for day, text, refs in facts:
        keys = {(day, key) for key in _source_keys(refs)}
        if known.intersection(keys) or any(e.get("date") == day and e["text"] == text for e in events):
            continue
        sources = resolved(refs)
        if not sources:
            continue
        saved = gantt.edit({"id": record["id"], "revision": revision, "actor": "ai", "field": "latest",
                            "text": text, "date": day, "sources": sources})
        if saved["revision"] != revision:
            report["eventsAdded"] += 1
            if not event_added:
                report["changed"] += 1
                report.setdefault("example", {"id": record["id"], "path": relative})
            event_added = True
            revision = saved["revision"]
        known.update(keys)
    if event_added:
        # The ordinary editor returns a bounded page. Older events can still
        # establish which evidence supported the previous brief.
        fields = gantt.detail(record["id"], all_entries=True)
        events = [row for row in fields["latest"] if not row.get("deleted")]
        brief = fields["brief"]
    if brief["manual"]:
        report["manualSummaries"] += 1
    if superseded:
        events = [e for e in events if not any((e.get("date"), k) in superseded for k in _source_keys(e["sources"]))]
        if _source_keys(brief["sources"]).intersection(key for _, key in superseded):
            report["issues"].append({"path": relative,
                                     "reason": "Основание сводки относится к уточнённому замеру или завершению. Сверьте её текст."})
    candidate = _summary_candidate(brief, events)
    if candidate:
        if len(candidate["text"]) > 600 or "\n" in candidate["text"]:
            report["issues"].append({"path": relative,
                                     "reason": "Для подробного события нужна краткая формулировка по источнику."})
            return
        refs = resolved(candidate["sources"])
        if not refs:
            return
        saved = gantt.edit({"id": record["id"], "revision": revision, "actor": "ai", "field": "brief",
                            "text": candidate["text"], "sources": refs})
        if saved["revision"] != revision:
            report["summariesUpdated"] += 1
            if not event_added:
                report["changed"] += 1
                report.setdefault("example", {"id": record["id"], "path": relative})


def refresh_planning(root):
    """Read live plans and frozen cycles, updating only the underlying work cards."""
    gantt = Gantt(root)
    report = {"examined": 0, "changed": 0, "eventsAdded": 0, "summariesUpdated": 0,
              "manualSummaries": 0, "plans": 0, "closedPlans": 0, "issues": []}
    source_cache = {}
    with storage._write_lock(gantt.root):
        for kind, folder in (("task", "Tasks"), ("plan", "Plans")):
            try:
                for path, _, record, _ in gantt.planning._entries(kind):
                    relative = path.relative_to(gantt.root).as_posix()
                    try:
                        if kind == "plan":
                            report["plans"] += 1
                            plan = gantt.planning._view(path, record, validate_dates=True)
                            if plan["status"] == "closed":
                                report["closedPlans"] += 1
                            else:
                                for item in plan["items"]:
                                    if item.get("missing"):
                                        report["issues"].append({"path": relative,
                                                                 "reason": f'В плане не найдена задача {item["id"]}.'})
                        else:
                            report["examined"] += 1
                            _refresh_task(gantt, record, relative, report, source_cache)
                    except (ValueError, OSError) as error:
                        report["issues"].append({"path": relative, "reason": str(error)})
                    except (KeyError, TypeError):
                        report["issues"].append({"path": relative,
                                                 "reason": "Проверьте сохранённые ссылки и служебные поля истории в Markdown."})
            except (ValueError, OSError) as error:
                report["issues"].append({"path": folder, "reason": str(error)})
    return report
