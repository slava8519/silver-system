"""Track which version of an owner's Gantt decisions has been read by their agent."""

import hashlib
import json

from . import storage
from .dependencies import dependency_history, predecessor_ids
from .gantt import _row_fields
from .planning import Planning
from .schedule import schedule_history


def _snapshot(record, path):
    fields = _row_fields(record, all_entries=True)
    manual = [("brief", fields["brief"])] if fields["brief"]["manual"] else []
    manual.extend(("latest", entry) for entry in fields["latest"] if entry["manual"])
    decisions = []
    for field, entry in manual:
        refs = [(s["id"], s["ref"], s["quote"]) for s in entry["sources"]]
        decisions.append([field, entry.get("id"), entry.get("date"), entry["text"], refs])
    dates = [record.get("start"), record.get("due")]
    history = schedule_history(record, path)
    if history or record.get("forecast") or record.get("completed"):
        # Retain review tokens from vaults that have only the original two dates.
        dates.extend([record.get("forecast"), record.get("completed"), history[-1]["revision"] if history else None])
    snapshot = [record["id"], dates, decisions]
    predecessors = sorted(predecessor_ids(record))
    kind = record.get("task_kind", "task")
    if predecessors or kind != "task":
        snapshot.append([kind, predecessors])
    # An add followed by a removal can restore the same set of predecessors.
    last_edit = next((e for e in reversed(dependency_history(record, path)) if e["actor"] == "manual"), None)
    if last_edit:
        snapshot.append([last_edit["updated"], last_edit["action"], last_edit["predecessor"]])
    token = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False,
                                      separators=(",", ":")).encode()).hexdigest()
    return token, len(manual), bool(history or last_edit), len(predecessors)


class GanttFeedback:
    def __init__(self, root):
        self.planning = Planning(root)

    def pending(self, plan_id=None):
        members = None
        if plan_id is not None:
            plan = self.planning.view(plan_id)
            if plan["status"] == "closed":
                return
            members = {row["id"] for row in plan["items"]}
        for path, _, record, _ in self.planning._entries("task"):
            if members is not None and record["id"] not in members:
                continue
            relative = path.relative_to(self.planning.root).as_posix()
            token, manual_count, has_history, predecessor_count = _snapshot(record, relative)
            previous = record.get("gantt_reviewed")
            dates = {key: record.get(key) for key in ("start", "due", "forecast", "completed")}
            kind = record.get("task_kind", "task")
            if (previous or manual_count or has_history or any(dates.values()) or predecessor_count
                    or kind == "milestone") and previous != token:
                yield {"id": record["id"], "title": record["title"], "path": relative,
                       **dates, "manualEntries": manual_count, "kind": kind, "predecessorCount": predecessor_count,
                       "firstReview": previous is None, "token": token}

    def acknowledge(self, task_id, token):
        with storage._write_lock(self.planning.root):
            path, before, record, header = self.planning._find("task", task_id)
            current, *_ = _snapshot(record, path.relative_to(self.planning.root).as_posix())
            if token != current:
                raise storage.ConflictError("После чтения изменились даты, зависимости или ручные уточнения. Сверьте их снова.")
            changed = record.get("gantt_reviewed") != token
            if changed:
                header = storage._patch_header(header, record, {"gantt_reviewed": token})
                storage.replace_bytes(path, before, header + record["body"].encode())
            return {"id": task_id, "reviewed": True, "changed": changed}
