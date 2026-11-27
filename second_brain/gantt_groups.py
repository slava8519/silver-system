"""Sourced briefs and journals belonging to individual Gantt groups and their snapshots."""

from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import quote, unquote

from . import brief, storage
from .cycles import closed_metadata, frozen_rows
from .gantt import _empty, _row_fields, _write_history, write_entry
from .okr import KeyResults


_KINDS = {"plan", "objective", "key_result", "group"}
_PREFIX = "gantt-group:"


def _arguments(kind, identifier, limit, offset):
    if not isinstance(kind, str) or kind not in _KINDS or not isinstance(identifier, str) or not identifier:
        raise ValueError("Укажите вид и ID выбранной строки Ганта.")
    if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
        raise ValueError("Укажите размер страницы от 1 до 20 и неотрицательное смещение.")


def _fields(record):
    return _row_fields(record, all_entries=True, all_history=True, with_lines=True)


def _updated(fields):
    # Native edits in Obsidian have no verified time. The file mtime also covers unrelated changes.
    if any(item["manual"] and not item["updated"] for item in [fields["brief"], *fields["latest"]]):
        return None
    changes = fields["history"]
    return changes[0]["updated"] if changes else None


def _display(fields, *, offset, limit, manual=False):
    events = (item for item in fields["latest"] if (item["manual"] if manual else not item.get("deleted")))
    page = brief.page(events, limit, offset)
    return {"brief": fields["brief"] if not manual or fields["brief"]["manual"] else None,
            "updated": _updated(fields), "latest": page["items"], "latestTotal": page["total"],
            "historyTotal": fields["historyTotal"], "offset": offset, "limit": limit, "truncated": page["truncated"]}


def _metadata(root, kind, identifier, plan_id, path, record):
    return {"id": identifier, "kind": kind, "planId": plan_id, "title": record["title"],
            "path": path.relative_to(root).as_posix(), "revision": record["revision"]}


class GanttGroups:
    def __init__(self, root):
        self.okr = KeyResults(root)
        self.planning = self.okr.planning
        self.root = self.planning.root

    def _objective_entry(self, goal):
        path = storage._inside(self.root, goal["path"])
        data = path.read_bytes()
        record, header = storage._parse_record(path, data, kinds=("objective",), optional_scalars=("plan_id",))
        if record["revision"] != goal["revision"]:
            raise storage.ConflictError("Цель изменилась. Перечитайте строку.")
        return path, data, record, header

    def _active_entry(self, plan_entry, kind, identifier):
        plan_id = plan_entry[2]["id"]
        if kind == "plan":
            if identifier == plan_id:
                return plan_entry
        elif kind == "group":
            from .gantt_group_cards import group_entry
            return group_entry(self.planning, plan_entry, identifier)
        else:
            goals = {item["id"]: item for item in self.okr._objectives({plan_id})}
            if kind == "objective" and identifier in goals:
                return self._objective_entry(goals[identifier])
            if kind == "key_result":
                matches = [entry for entry in self.okr._entries(goals) if entry[2]["id"] == identifier]
                if matches:
                    return matches[0]
        raise FileNotFoundError("Эта строка не входит в выбранный план.")

    def _members(self, plan_entry):
        from .gantt_group_cards import iter_group_entries
        plan_id = plan_entry[2]["id"]
        yield "plan", plan_id, plan_entry
        goals = {item["id"]: item for item in self.okr._objectives({plan_id})}
        for identifier, goal in goals.items():
            yield "objective", identifier, self._objective_entry(goal)
        if goals:
            for entry in self.okr._entries(goals):
                yield "key_result", entry[2]["id"], entry
        groups = list(iter_group_entries(self.planning, plan_entry))
        if groups:
            from .gantt_group_cards import group_title
            from .plan_tree import outline
            board = self.okr.board(plan_id)
            outside = any(row["kind"] == "outside" for row in outline(board))
            for identifier, entry in groups:
                try:
                    group_title(identifier, board["items"], outside)
                except FileNotFoundError:
                    continue
                yield "group", identifier, entry

    def _contents(self, plan_entry, kind, identifier):
        path, _, plan, _ = plan_entry
        plan_id = plan["id"]
        closed = closed_metadata(plan)
        if closed is None:
            target, data, record, _ = self._active_entry(plan_entry, kind, identifier)
            return {**_metadata(self.root, kind, identifier, plan_id, target, record), "readOnly": False, "stored": data is not None}, _fields(record)
        rows = frozen_rows(plan, closed)
        goals = (closed.get("okr") or {}).get("objectives", [])
        # Validate membership against the sealed plan even if the source card was renamed or removed.
        if kind == "group":
            from .gantt_group_cards import group_title
            from .plan_tree import outline
            outside = identifier == "outside" and any(item["kind"] == "outside" for item in outline({"items": rows, "objectives": goals}))
            title = group_title(identifier, rows, outside)
        elif kind == "plan" and plan_id == identifier:
            title = closed["title"]
        else:
            candidates = []
            if kind == "objective":
                candidates = goals
            elif kind == "key_result":
                candidates = (kr for goal in goals for kr in goal["results"])
            title = next((item["title"] for item in candidates if item["id"] == identifier), None)
        if not title:
            raise FileNotFoundError("В снимке плана нет выбранной строки.")
        saved = closed.get("groupJournals", {}).get(kind, {}).get(identifier)
        identity = {"id": identifier, "kind": kind, "planId": plan_id, "title": title,
                    "revision": closed["revision"], "path": path.relative_to(self.root).as_posix(), "readOnly": True, "stored": True}
        if saved and saved.get("textPath"):
            identity["textPath"] = saved["textPath"]
        return identity, saved or {"brief": _empty(), "latest": [], "history": [], "historyTotal": 0}

    def detail(self, plan_id, kind, identifier, *, limit=5, offset=0, manual=False):
        _arguments(kind, identifier, limit, offset)
        with storage._vault_lock(self.root, write=False):
            entry = self.planning._find("plan", plan_id)
            identity, fields = self._contents(entry, kind, identifier)
            return {**identity, **_display(fields, offset=offset, limit=limit, manual=manual)}

    def history(self, plan_id, kind, identifier, *, limit=5, offset=0):
        _arguments(kind, identifier, limit, offset)
        with storage._vault_lock(self.root, write=False):
            entry = self.planning._find("plan", plan_id)
            identity, fields = self._contents(entry, kind, identifier)
            return {**identity, "updated": _updated(fields), **brief.page(fields["history"], limit, offset), "limit": limit, "offset": offset}

    def edit(self, plan_id, kind, identifier, data, *, accepted_manual_revision=None):
        _arguments(kind, identifier, 5, 0)
        if not isinstance(data, dict) or data.get("field") not in ("brief", "latest") or data.get("actor") not in ("manual", "ai"):
            raise ValueError("Укажите поле brief или latest и автора manual или ai.")
        if (data["actor"] == "ai" and data["field"] == "latest" and data.get("entryId") is None and "date" not in data):
            raise ValueError("Укажите подтверждённую дату события или явно передайте date=null для события без даты.")
        if isinstance(data.get("text"), str) and re.search(r"<!--\s*/?(?:plan-|okr-|task-id:|task-readiness:)", data["text"]):
            raise ValueError("Текст строки не может содержать служебные метки плана или OKR.")
        with storage._write_lock(self.root):
            plan = self.planning._find("plan", plan_id)
            if closed_metadata(plan[2]) is not None:
                raise storage.ConflictError("Закрытый план доступен только для чтения.")
            path, before, record, header = self._active_entry(plan, kind, identifier)
            if record["revision"] != data.get("revision"):
                raise storage.ConflictError("Строка изменилась. Перечитайте её перед сохранением.")
            if accepted_manual_revision is not None and (accepted_manual_revision != record["revision"] or data["actor"] != "ai"):
                raise storage.ConflictError("Согласие владельца должно относиться к текущей версии текста.")
            lines = record["body"].splitlines(keepends=True)
            newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
            updated = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            change = write_entry(self.root, path, data, lines, newline, updated, accepted_manual_revision)
            if change is not None:
                if data["actor"] == "ai" and data["field"] == "brief" and change["after"] != change["before"]:
                    from .gantt_brief_format import validate_group_brief
                    validate_group_brief(change["after"])
                _write_history(lines, {**change, "field": data["field"], "actor": data["actor"], "updated": updated}, newline)
                after = header + "".join(lines).encode()
                saved, _ = storage._parse_record(path, after, kinds=(record["type"],))
                values = _fields(saved)
                if path != plan[0]:
                    if plan[0].read_bytes() != plan[1]:
                        raise storage.ConflictError("План изменился. Перечитайте строку перед сохранением.")
                    # Markdown editors can move an objective to another period, change a group’s membership
                    # or rename its file while this process holds the application lock.
                    checked = self._active_entry(plan, kind, identifier)
                    if checked[0] != path or checked[2]["revision"] != record["revision"]:
                        raise storage.ConflictError("Строка изменилась. Перечитайте её перед сохранением.")
                if before is None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                storage.replace_bytes(path, before, after)
                record = saved
            else:
                values = _fields(record)
            result = _metadata(self.root, kind, identifier, plan_id, path, record)
            return {**result, "readOnly": False, "stored": before is not None or change is not None,
                    **_display(values, limit=5, offset=0)}

    def snapshot(self, plan_entry):
        journals = {}
        for kind, identifier, entry in self._members(plan_entry):
            values = _fields(entry[2])
            if values["brief"]["manual"] or values["brief"]["text"] or values["latest"] or values["history"]:
                # Only the sealed plan is a stable source location after the cycle closes.
                fields = {key: value for key, value in values.items() if key != "latestTotal"}
                fields["textPath"] = entry[0].relative_to(self.root).as_posix()
                fields["brief"].pop("line", None)
                for row in (*fields["latest"], *fields["history"]):
                    row.pop("line", None)
                journals.setdefault(kind, {})[identifier] = fields
        return journals

    def _receipt(self, plan_id, kind, identifier):
        key = hashlib.sha256(json.dumps([plan_id, kind, identifier], ensure_ascii=False).encode()).hexdigest()
        return storage._inside(self.root, f"Settings/Reviews/GanttGroups/{key}.txt")

    def _review_state(self, plan_id, kind, identifier, fields):
        entries = [("brief", fields["brief"]), *(("latest", item) for item in fields["latest"])]
        decisions = []
        for field, item in entries:
            if item["manual"]:
                refs = sorted((s["id"], s["ref"], s["quote"]) for s in item["sources"])
                decisions.append([field, item.get("id"), item.get("date"), item["text"], refs, bool(item.get("deleted"))])
        receipt = self._receipt(plan_id, kind, identifier)
        previous = receipt.read_text(encoding="ascii").strip() if receipt.exists() else None
        key = [plan_id, kind, identifier, decisions]
        digest = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()
        token = _PREFIX + quote(plan_id, safe="") + ":" + kind + ":" + digest
        return token, previous, len(decisions)

    def pending(self, plan_id=None):
        with storage._vault_lock(self.root, write=False):
            rows = []
            plans = [self.planning._find("plan", plan_id)] if plan_id is not None else self.planning._entries("plan")
            for plan_entry in plans:
                plan = plan_entry[2]
                if closed_metadata(plan) is not None:
                    continue
                for kind, identifier, entry in self._members(plan_entry):
                    fields = _fields(entry[2])
                    token, previous, count = self._review_state(plan["id"], kind, identifier, fields)
                    if (count or previous is not None) and token != previous:
                        item = _metadata(self.root, kind, identifier, plan["id"], entry[0], entry[2])
                        brief.clip_fields(item, ("title",))
                        rows.append({**item, "manualEntries": count, "firstReview": previous is None, "token": token})
            return rows

    def acknowledge(self, identifier, token):
        try:
            plan, kind, _ = token.removeprefix(_PREFIX).split(":")
            plan_id = unquote(plan)
            _arguments(kind, identifier, 5, 0)
        except (AttributeError, ValueError) as error:
            raise ValueError("Проверьте отпечаток ручных правок строки Ганта.") from error
        with storage._write_lock(self.root):
            plan = self.planning._find("plan", plan_id)
            if closed_metadata(plan[2]) is not None:
                raise storage.ConflictError("План уже закрыт.")
            _, _, record, _ = self._active_entry(plan, kind, identifier)
            current, previous, count = self._review_state(plan_id, kind, identifier, _fields(record))
            if current != token or (not count and previous is None):
                raise storage.ConflictError("После чтения изменилась строка Ганта. Перечитайте ручные правки.")
            if previous != current:
                path = self._receipt(plan_id, kind, identifier)
                before = path.read_bytes() if path.exists() else None
                path.parent.mkdir(parents=True, exist_ok=True)
                storage.replace_bytes(path, before, (token + "\n").encode("ascii"))
            return {"id": identifier, "reviewed": True, "changed": previous != current}
