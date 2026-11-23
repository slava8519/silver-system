"""Preview and record a change to the single due date shared by a task's plans."""

import hashlib
import json
import re

from . import storage
from .planning import Planning, validate_due


_EMPTY = "без срока"
_DATE = r"(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|без срока)"
_ENTRY = re.compile(rf"- Срок: ({_DATE}) → ({_DATE}) <!-- brain-due:([0-9a-f]{{64}}) -->")


def _history(record, path):
    changes = []
    seen = set()
    for number, line in enumerate(record["body"].splitlines(), record["body_start"]):
        if "<!-- brain-due:" not in line:
            continue
        entry = _ENTRY.fullmatch(line)
        if not entry or entry[3] in seen:
            raise storage.ConflictError(f"Проверьте запись истории срока в {path}, строка {number}.")
        before, after, revision = entry.groups()
        seen.add(revision)
        changes.append({"before": None if before == _EMPTY else validate_due(before),
                        "after": None if after == _EMPTY else validate_due(after),
                        "path": path, "line": number, "revision": revision})
    return changes


def _with_entry(header, record, changes, proposal):
    newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
    header = storage._patch_header(header, record, {"due": proposal["after"]})
    before, after = proposal["before"] or _EMPTY, proposal["after"] or _EMPTY
    entry = f"- Срок: {before} → {after} <!-- brain-due:{proposal['revision']} -->" + newline
    lines = record["body"].splitlines(keepends=True)
    if changes:
        # Keep the log together even when the owner added other sections after it.
        index = changes[-1]["line"] - record["body_start"] + 1
    else:
        index = len(lines)
        entry = newline + "## История сроков" + newline + newline + entry
    if index and not lines[index - 1].endswith(("\n", "\r")):
        entry = newline + entry
    lines.insert(index, entry)
    return header + "".join(lines).encode()


class Deadlines:
    def __init__(self, root):
        self.planning = Planning(root)
        self.root = self.planning.root

    def _preview(self, path, record, header, due):
        before = validate_due(record.get("due"))
        if before is None and "due" in record["field_lines"]:
            raw = header.splitlines()[record["field_lines"]["due"] - 1].decode().partition(":")[2].strip()
            if raw and not raw.startswith("#") and storage._scalar(raw).lower() not in ("~", "null"):
                raise ValueError(f"Проверьте значение поля due в {path}.")
        plans = [{key: plan[key] for key in ("id", "title", "path")}
                 for plan in self.planning.plans_for_task(record["id"])]
        # Membership matters to what the owner previews; unrelated plan text does not.
        snapshot = [record["id"], record["revision"], due, sorted(plan["id"] for plan in plans)]
        revision = hashlib.sha256(json.dumps(snapshot, separators=(",", ":")).encode()).hexdigest()
        return {"id": record["id"], "title": record["title"], "path": path,
                "due": before, "before": before, "after": due, "revision": revision,
                "taskRevision": record["revision"], "plans": plans, "changed": before != due}

    def preview(self, task_id, due):
        due = validate_due(due)
        path, _, record, header = self.planning._find("task", task_id)
        self._check_start(record, due)
        return self._preview(path.relative_to(self.root).as_posix(), record, header, due)

    @staticmethod
    def _check_start(record, due):
        if due and record.get("start") and due < validate_due(record["start"]):
            raise ValueError("Срок раньше начала задачи. Проверьте обе даты.")

    def history(self, task_id):
        path, _, record, _ = self.planning._find("task", task_id)
        return _history(record, path.relative_to(self.root).as_posix())

    def apply(self, task_id, due, revision):
        due = validate_due(due)
        if not isinstance(revision, str) or not re.fullmatch("[0-9a-f]{64}", revision):
            raise ValueError("Укажите версию из предварительного просмотра.")
        with storage._write_lock(self.root):
            file, data, record, header = self.planning._find("task", task_id)
            path = file.relative_to(self.root).as_posix()
            proposal = self._preview(path, record, header, due)
            changes = _history(record, path)
            existing = next((change for change in changes if change["revision"] == revision), None)
            result = {key: proposal[key] for key in ("id", "title", "path", "due", "taskRevision", "plans")}
            if existing and existing["after"] == due:
                return {**result, "changed": False, "replayed": True, "change": existing}
            if revision != proposal["revision"] or existing:
                raise storage.ConflictError("Задача, предложенный срок или состав планов изменились. Повторите предпросмотр.")
            self._check_start(record, due)
            if not proposal["changed"]:
                return {**result, "changed": False, "replayed": False, "change": None}
            updated = _with_entry(header, record, changes, proposal)
            saved, _ = storage._parse_record(file, updated, kinds={"task"})
            change = _history(saved, path)[-1]
            storage.replace_bytes(file, data, updated)
            return {**result, "due": due, "taskRevision": saved["revision"], "changed": True,
                    "replayed": False, "change": change}
