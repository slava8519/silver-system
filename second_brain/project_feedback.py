"""Track owner decisions about the scope of a plan and their review by the agent."""

from datetime import datetime, timezone
import hashlib
import json
import re

from . import brief, storage
from .planning import Planning

_MARK = " <!-- brain-project-change: "
_PREFIX = "plan-projects:"


def append_event(lines, *, project_id, title, present, actor, newline, task_id=None, task_title=None):
    """Insert the audit line in the same plan write as the edited membership."""
    if actor not in ("ai", "manual"):
        raise ValueError("Укажите автора изменения плана.")
    stamp = datetime.now(timezone.utc)
    event = {"action": "include" if present else "exclude", "actor": actor, "updated": stamp.isoformat(),
             "projectId": project_id, "projectTitle": title}
    label = ("Включён проект " if present else "Снято включение проекта ") + storage.markdown_label(title)
    if task_id is not None:
        event.update(action="add-task", taskId=task_id, taskTitle=task_title)
        label = f"Выбрана работа {storage.markdown_label(task_title)} · {storage.markdown_label(title)}"
    author = "владелец" if actor == "manual" else "ИИ"
    entry = f"- {stamp:%Y-%m-%d %H:%M} UTC · {author} · {label}{_MARK}{storage.comment_json(event)} -->{newline}"
    last = next((i for i in reversed(range(len(lines))) if _MARK in lines[i]), None)
    if last is None:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines.append(newline)
        lines.extend([newline, "## История выбора проектов и работ" + newline, newline, entry])
    else:
        if not lines[last].endswith(("\n", "\r")):
            lines[last] += newline
        lines.insert(last + 1, entry)


def _history(record, path):
    for number, line in enumerate(record["body"].splitlines(), record["body_start"]):
        if _MARK not in line:
            continue
        try:
            if not line.startswith("- ") or not line.endswith(" -->"):
                raise ValueError
            event = json.loads(line.rsplit(_MARK, 1)[1][:-4])
            if (event["action"] not in ("include", "exclude", "add-task") or event["actor"] not in ("ai", "manual")
                    or datetime.fromisoformat(event["updated"]).tzinfo is None):
                raise ValueError
            keys = ("projectId", "projectTitle")
            if event["action"] == "add-task":
                keys += ("taskId", "taskTitle")
            if any(not isinstance(event[key], str) or not event[key] for key in keys):
                raise ValueError
        except (ValueError, TypeError, KeyError) as error:
            raise storage.ConflictError(f"Проверьте историю выбора проектов в {path}, строка {number}.") from error
        yield {**event, "path": path, "line": number}


class ProjectFeedback:
    def __init__(self, root):
        self.planning = Planning(root)

    def _receipt(self, plan_id):
        # Acknowledging a closed plan leaves the sealed Markdown and its evidence lines unchanged.
        key = hashlib.sha256(plan_id.encode()).hexdigest()
        return storage._inside(self.planning.root, f"Settings/Reviews/PlanProjects/{key}.txt")

    def _state(self, entry):
        path, _, record, _ = entry
        relative = path.relative_to(self.planning.root).as_posix()
        receipt = self._receipt(record["id"])
        previous = receipt.read_text(encoding="ascii").strip() if receipt.exists() else None
        if previous is not None and not re.fullmatch(_PREFIX + "[0-9a-f]{64}", previous):
            raise storage.ConflictError("Проверьте отметку сверки проектов плана в Settings/Reviews.")
        events = list(_history(record, relative))
        digest = hashlib.sha256(record["id"].encode())
        pending = []
        token = None
        for event in events:
            if event["actor"] != "manual":
                continue
            # Cite the current line, but do not invalidate a receipt when the file is moved.
            fields = {k: v for k, v in event.items() if k not in ("path", "line")}
            digest.update(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode() + b"\n")
            token = _PREFIX + digest.hexdigest()
            pending.append(event)
            if token == previous:
                pending.clear()
        return events, pending, previous, token

    def pending(self, plan_id=None):
        rows = []
        with storage._vault_lock(self.planning.root, write=False):
            entries = [self.planning._find("plan", plan_id)] if plan_id is not None else self.planning._entries("plan")
            for entry in entries:
                if _MARK not in entry[2]["body"]:
                    continue
                _, pending, previous, token = self._state(entry)
                if pending:
                    row = {"kind": "plan-projects", "id": entry[2]["id"], "title": entry[2]["title"],
                           "manualEntries": len(pending), "firstReview": previous is None, "token": token,
                           "path": pending[-1]["path"], "line": pending[-1]["line"]}
                    brief.clip_fields(row, ("title",))
                    rows.append(row)
        return rows

    def history(self, plan_id, *, pending, limit, offset):
        with storage._vault_lock(self.planning.root, write=False):
            entry = self.planning._find("plan", plan_id)
            events, unread, _, token = self._state(entry)
            page = brief.page(reversed(unread if pending else events), limit, offset)
        for item in page["items"]:
            brief.clip_fields(item, [k for k in ("projectTitle", "taskTitle") if k in item])
        return {"id": plan_id, "token": token, **page}

    def acknowledge(self, plan_id, token):
        with storage._write_lock(self.planning.root):
            entry = self.planning._find("plan", plan_id)
            _, _, previous, current = self._state(entry)
            if current is None or current != token:
                raise storage.ConflictError("После чтения изменился выбор проектов или работ. Перечитайте историю плана.")
            changed = previous != current
            if changed:
                receipt = self._receipt(plan_id)
                receipt.parent.mkdir(parents=True, exist_ok=True)
                before = receipt.read_bytes() if receipt.exists() else None
                storage.replace_bytes(receipt, before, (current + "\n").encode("ascii"))
            return {"id": plan_id, "reviewed": True, "changed": changed}
