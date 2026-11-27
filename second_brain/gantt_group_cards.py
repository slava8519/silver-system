"""Markdown for a named grouping of work within one plan."""
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from . import storage
from .okr import KeyResults
from .plan_tree import outline


GROUP_FIELDS = ("plan_id", "group_key")
GROUP_FOLDER = "Plans/Groups"
# Imported work journals also record regenerated summaries and administrative changes.
_JOURNAL_MAINTENANCE = {"сводка", "влияние пересчитано", "нагрузка применена", "правки возвращены"}


def group_title(identifier, rows, has_outside):
    if identifier == "outside" and has_outside:
        return "Другие работы плана"
    kind, separator, value = identifier.partition("/")
    if separator and kind in ("project", "direction"):
        for row in rows:
            if (row.get(kind) or "") == value:
                return row.get(kind + "Title") or ("Без проекта" if kind == "project" else "Без направления")
    raise FileNotFoundError("Такая группа не входит в план.")


def _directory(plan_id):
    # Imported record IDs are opaque and may contain filename punctuation.
    return Path(GROUP_FOLDER) / str(uuid5(NAMESPACE_URL, "second-brain/plan/" + plan_id))


def _identifier(plan_id, key):
    return str(uuid5(NAMESPACE_URL, json.dumps(["second-brain/gantt-group", plan_id, key], ensure_ascii=False)))


def iter_group_entries(planning, plan_entry):
    plan_id = plan_entry[2]["id"]
    seen = set()
    for path in storage.iter_markdown_paths(planning.root, folders=(_directory(plan_id),)):
        data = path.read_bytes()
        try:
            record, header = storage._parse_record(path, data, kinds=("gantt_group",), optional_scalars=GROUP_FIELDS)
        except storage.UnmanagedMarkdown:
            continue
        key = record.get("group_key")
        if not key or record.get("plan_id") != plan_id or record["id"] != _identifier(plan_id, key) or key in seen:
            raise storage.ConflictError(f"Проверьте идентификатор группы плана в {path.name}.")
        seen.add(key)
        yield key, (path, data, record, header)


def group_entry(planning, plan_entry, identifier):
    plan = plan_entry[2]
    board = KeyResults(planning.root).board(plan["id"])
    has_outside = identifier == "outside" and any(row["kind"] == "outside" for row in outline(board))
    title = group_title(identifier, board["items"], has_outside)
    for key, entry in iter_group_entries(planning, plan_entry):
        if key == identifier:
            return entry
    stable_id = _identifier(plan["id"], identifier)
    path = storage._inside(planning.root, _directory(plan["id"]) / (stable_id + ".md"))
    if path.exists():
        raise storage.ConflictError("Путь карточки группы занят другим файлом.")
    fields = {"type": "gantt_group", "schema_version": 1, "id": stable_id, "title": title,
              "plan_id": plan["id"], "group_key": identifier}
    origin = storage.markdown_link(plan["title"], plan_entry[0], path.parent)
    text = "---\n" + "".join(k + ": " + json.dumps(v, ensure_ascii=False) + "\n" for k, v in fields.items())
    text += "---\n\n# " + title + "\n\n" + origin + "\n"
    record, header = storage._parse_record(path, text.encode(), kinds=("gantt_group",), optional_scalars=GROUP_FIELDS)
    return path, None, record, header


def work_events(root, plan_id, kind, identifier, *, limit, offset):
    """A bounded, attributed view of the work below a row, independent of its own journal."""
    from . import brief
    from .gantt import _entries

    if not isinstance(identifier, str) or not identifier:
        raise ValueError("Укажите ID выбранной строки.")
    service = KeyResults(root)
    with storage._vault_lock(service.root, write=False):
        board = service.board(plan_id)
        if board["historical"]:
            raise ValueError("В закрытом плане доступны только события, сохранённые в снимке.")
        rows = board["items"]
        tree = outline(board)
        if kind == "group" and identifier != "outside":
            group_title(identifier, rows, False)
            field, value = identifier.split("/", 1)
            members = {r["id"] for r in rows if (r.get(field) or "") == value}
        elif kind == "plan" and identifier == plan_id:
            members = {r["id"] for r in rows}
        else:
            key = {"objective": "objective/" + identifier, "key_result": "result/" + identifier,
                   "group": "outside" if identifier == "outside" else None}.get(kind)
            if not key or not any(item["key"] == key for item in tree):
                raise FileNotFoundError("Эта строка не входит в выбранный план.")
            descendants = {key}
            members = set()
            for item in tree:
                if item["parentKey"] in descendants:
                    descendants.add(item["key"])
                    if item["kind"] == "task" and not item.get("contextOnly"):
                        members.add(item["id"])
        entries = []
        for row in rows:
            if row["id"] not in members or row.get("missing"):
                continue
            path = storage._inside(service.root, row["path"])
            record, _ = storage._parse_record(path, path.read_bytes(), kinds=("task",))
            if record["id"] != row["id"] or record["revision"] != row["revision"]:
                raise storage.ConflictError("Состав работ изменился. Обновите Гант.")
            events = _entries(record["body"].splitlines(keepends=True), "latest", base=record["body_start"])
            for event, _, _ in events:
                operation, separator, _ = event["text"].partition(" · ")
                if separator and operation.casefold() in _JOURNAL_MAINTENANCE:
                    continue
                entries.append({**event, "taskId": row["id"], "taskTitle": row["title"], "taskPath": row["path"]})
        entries.sort(key=lambda event: event.get("date") or "", reverse=True)
        return brief.page(entries, limit, offset)
