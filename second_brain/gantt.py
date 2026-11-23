"""Read plan timelines and edit sourced row details on their canonical Markdown task."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re
import uuid

from . import storage
from .evidence import linked_source, verify_sources
from .planning import Planning, TASK_SCALARS, validate_due
from .schedule import schedule_history


_HEADINGS = {"brief": "Коротко", "latest": "Последнее"}
_OPEN, _END = "<!-- brain-gantt: ", "<!-- /brain-gantt -->"
_REFS = "<!-- brain-gantt-references -->"
_CHANGE = "<!-- brain-gantt-change: "
_MAX_LATEST, _MAX_HISTORY = 12, 5


def _payload(line, prefix):
    try:
        if not line.endswith(" -->"):
            raise ValueError
        value = json.loads(line[len(prefix):-4])
        if not isinstance(value, dict):
            raise ValueError
        return value
    except ValueError as error:
        raise storage.ConflictError("Повреждена служебная отметка Ганта в Markdown.") from error


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _text(lines):
    return "\n".join(line.rstrip("\r\n") for line in lines).strip()


def _source_keys(sources):
    # A file rename or inserted lines leave the cited passage and its stable ID intact.
    return {(s["id"], s["ref"], s["quote"]) for s in sources}


def _window(lines, field):
    title = "## " + _HEADINGS[field]
    starts = [n for n, line in enumerate(lines) if line.rstrip() == title]
    if len(starts) > 1:
        raise storage.ConflictError(f"В карточке несколько разделов «{_HEADINGS[field]}».")
    if not starts:
        return None
    begin = starts[0] + 1
    end = next((n for n in range(begin, len(lines)) if re.match(r"^#{1,2}\s", lines[n])), len(lines))
    return begin, end


def _empty():
    return {"text": "", "author": None, "manual": False, "updated": None, "sources": []}


def _entries(lines, field, bounds):
    """Read managed text and ordinary owner-written paragraphs under the same heading."""
    if bounds is None:
        return []
    pos, end = bounds
    entries = []
    freeform_count = 0
    while pos < end:
        if not lines[pos].strip():
            pos += 1
            continue
        begin = pos
        line = lines[pos].rstrip("\r\n")
        if line.startswith(_OPEN):
            meta = _payload(line, _OPEN)
            refs = next((n for n in range(pos + 1, end) if lines[n].rstrip() == _REFS), end)
            close = next((n for n in range(refs + 1, end) if lines[n].rstrip() == _END), end)
            if close == end or any(l.startswith(_OPEN) or l.rstrip() == _END for l in lines[pos+1:refs]):
                raise storage.ConflictError(f"Проверьте границы текста в разделе «{_HEADINGS[field]}».")
            text = _text(lines[pos + 1:refs])
            footer = _text(lines[refs + 1:close])
            modified = _digest(text + "\0" + footer) != meta.get("digest")
            references_edited = meta.get("footerDigest") is not None and _digest(footer) != meta["footerDigest"]
            try:
                author = "manual" if modified else meta["author"]
                if author not in ("manual", "ai"):
                    raise ValueError
                entry = {"text": text, "author": author, "manual": author == "manual",
                         "updated": None if modified else meta["updated"],
                         "sources": [] if modified else [linked_source(s) for s in meta["sources"]]}
                if not modified and author == "ai" and meta.get("acceptedByOwner") is True:
                    entry.update(manual=True, acceptedByOwner=True)
                if field == "latest":
                    entry.update(id=meta["id"], date=validate_due(meta.get("date")))
                    if not isinstance(entry["id"], str) or not entry["id"]:
                        raise ValueError
                if references_edited:
                    # The footer is visible in Obsidian. Preserve hand-edited references as prose.
                    date_part, _, refs = footer.partition(" · ")
                    if field == "latest":
                        try:
                            entry["date"] = validate_due(date_part or None)
                        except ValueError:
                            entry["date"], refs = None, footer
                    else:
                        refs = footer
                    if refs:
                        entry["text"] = "\n\n".join(filter(None, (text, refs)))
            except (KeyError, TypeError, ValueError) as error:
                raise storage.ConflictError("Проверьте служебные поля Ганта в Markdown.") from error
            pos = close + 1
        else:
            while pos < end and not lines[pos].startswith(_OPEN):
                if lines[pos].strip() in (_END, _REFS):
                    raise storage.ConflictError("Проверьте границы полей Ганта в Markdown.")
                pos += 1
            text = _text(lines[begin:pos])
            entry = {**_empty(), "text": text, "author": "manual", "manual": True}
            if field == "latest":
                freeform_count += 1
                entry.update(id=f"markdown-{freeform_count}", date=None)
            # Trailing blank lines belong to the document layout.
            while pos > begin and not lines[pos-1].strip():
                pos -= 1
        entries.append((entry, begin, pos))
    if field == "brief" and len(entries) > 1:
        raise storage.ConflictError("Проверьте отдельные фрагменты в разделе «Коротко».")
    if field == "latest" and len({e["id"] for e, _, _ in entries}) != len(entries):
        raise storage.ConflictError("В разделе «Последнее» повторяется ID записи.")
    return entries


def _block(entry, root, task_path, newline):
    meta = {key: value for key, value in entry.items() if key not in ("text", "manual")}
    meta["sources"] = [{k: v for k, v in source.items() if k != "href"} for source in meta["sources"]]
    refs = [storage.markdown_link(f"{PurePosixPath(s['path']).stem} · {s['line']}", root / s["path"], task_path.parent)
            for s in meta["sources"]]
    footer = " · ".join(filter(None, [meta.get("date"), *refs]))
    meta["digest"] = _digest(entry["text"] + "\0" + footer)
    meta["footerDigest"] = _digest(footer)
    lines = [_OPEN + storage.comment_json(meta) + " -->", *entry["text"].split("\n"), _REFS]
    if footer:
        lines.append(footer)
    return newline.join([*lines, _END, ""])


def _changes(lines):
    return [_payload(line.strip(), _CHANGE) for line in lines if line.startswith(_CHANGE)]


def _deleted():
    # The owner removed a previously recorded block using a Markdown editor.
    return {**_empty(), "author": "manual", "manual": True, "deleted": True}


def _row_fields(record, *, all_entries=False):
    lines = record["body"].splitlines(keepends=True)
    changes = _changes(lines)
    brief = _entries(lines, "brief", _window(lines, "brief"))
    if brief:
        summary = brief[0][0]
    elif any(c.get("field") == "brief" for c in changes):
        summary = _deleted()
    else:
        summary = _empty()
    latest = [entry for entry, _, _ in _entries(lines, "latest", _window(lines, "latest"))]
    latest.sort(key=lambda entry: entry["date"] or "", reverse=True)
    total = len(latest)
    if all_entries:
        known = {e["id"] for e in latest}
        for change in reversed(changes):
            identifier = change.get("entryId")
            if change.get("field") == "latest" and identifier and identifier not in known:
                latest.append({**_deleted(), "id": identifier, "date": None})
                known.add(identifier)
    else:
        latest = latest[:_MAX_LATEST]
    return {"brief": summary, "latest": latest, "latestTotal": total,
            "history": changes[-_MAX_HISTORY:][::-1], "historyTotal": len(changes)}


def _write_history(lines, change, newline):
    entry = _CHANGE + storage.comment_json(change) + " -->" + newline
    last = next((n for n in reversed(range(len(lines))) if lines[n].startswith(_CHANGE)), None)
    if last is None:
        lines.append(newline + "## История Ганта" + newline + newline + entry)
    else:
        lines.insert(last + 1, entry)


class Gantt:
    def __init__(self, root):
        self.planning = Planning(root)
        self.root = self.planning.root

    def view(self, plan_id):
        plan = self.planning.view(plan_id)
        if plan["status"] == "active":
            self.planning._group_titles(plan)
        return plan

    def detail(self, task_id, *, all_entries=False):
        path, _, record, _ = self.planning._find("task", task_id)
        return self._detail(path, record, all_entries=all_entries)

    def _detail(self, path, record, *, all_entries=False):
        dates = schedule_history(record, path.relative_to(self.root).as_posix())
        return {**self.planning._task_summary(path, record, self.planning._project_directions([record])),
                "scheduleHistory": dates[-8:][::-1], "scheduleHistoryTotal": len(dates),
                **_row_fields(record, all_entries=all_entries)}

    def edit(self, data, *, accepted_manual_revision=None):
        if not isinstance(data, dict) or data.get("actor") not in ("ai", "manual"):
            raise ValueError("Укажите автора правки: manual или ai.")
        field, actor = data.get("field"), data["actor"]
        if field not in (*_HEADINGS, "start"):
            raise ValueError("Выберите поле brief, latest или start.")
        task_id = data.get("id")
        with storage._write_lock(self.root):
            path, before, record, header = self.planning._find("task", task_id)
            if record["revision"] != data.get("revision"):
                raise storage.ConflictError("Карточка изменилась. Перечитайте выбранную задачу.")
            if accepted_manual_revision is not None and (
                actor != "ai" or field == "start" or accepted_manual_revision != record["revision"]
            ):
                raise storage.ConflictError("Согласие владельца должно относиться к текущей версии исправляемого текста.")
            current = self._detail(path, record)
            newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
            lines = record["body"].splitlines(keepends=True)
            updated = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            if field == "start":
                if actor != "manual" or "value" not in data:
                    raise ValueError("Укажите начало задачи вручную, датой или null.")
                start = validate_due(data["value"])
                if start and current["kind"] == "milestone":
                    raise ValueError("У вехи одна точка на шкале. Укажите её срок или прогноз.")
                due = validate_due(record.get("due"))
                if start and due and start > due:
                    raise ValueError("Начало задачи позже её срока.")
                if start == record.get("start"):
                    return current
                header = storage._patch_header(header, record, {"start": start})
                change = {"before": record.get("start"), "after": start}
            else:
                change = self._write_entry(path, data, lines, newline, updated, accepted_manual_revision)
            if change is None:
                return current
            _write_history(lines, {**change, "field": field, "actor": actor, "updated": updated}, newline)
            after = header + "".join(lines).encode()
            saved, _ = storage._parse_record(path, after, kinds=("task",), optional_scalars=TASK_SCALARS)
            result = self._detail(path, saved)
            storage.replace_bytes(path, before, after)
            return result

    def _write_entry(self, path, data, lines, newline, updated, accepted_manual_revision):
        field, actor, entry_id = data["field"], data["actor"], data.get("entryId")
        text = data.get("text")
        if not isinstance(text, str):
            raise ValueError("Введите текст правки.")
        text = text.replace("\r\n", "\n").strip()
        if (len(text) > 2400 or ("\0" in text or "\r" in text) or re.search(r"^#{1,2}\s|<!--\s*/?brain-", text, re.M)
                or (not text and (field == "latest" or actor == "ai"))):
            raise ValueError("Введите до 2400 символов без служебных меток или заголовков верхнего уровня.")
        bounds = _window(lines, field)
        entries = _entries(lines, field, bounds)
        old = None
        previous = _empty()
        if field == "brief":
            if entry_id is not None:
                raise ValueError("ID события допустим только для поля latest.")
            old = entries[0] if entries else None
            if not old and any(c.get("field") == "brief" for c in _changes(lines)):
                previous = _deleted()
        elif entry_id is not None:
            old = next((row for row in entries if row[0]["id"] == entry_id), None)
            if not old:
                if not any(c.get("field") == "latest" and c.get("entryId") == entry_id for c in _changes(lines)):
                    raise storage.ConflictError("Событие не найдено. Перечитайте карточку.")
                previous = _deleted()
        if old:
            previous = old[0]
        accepted = accepted_manual_revision is not None
        if accepted and not previous["manual"]:
            raise storage.ConflictError("У этой записи нет защищённой правки для согласования.")
        if actor == "ai" and previous["manual"] and not accepted:
            raise storage.ConflictError("Поле защищено решением владельца. Согласуйте новую формулировку.")
        if actor == "manual" and "sources" not in data:
            # Existing evidence remains traceable through its digest even after a note moves.
            sources = [{k: v for k, v in src.items() if k != "href"} for src in previous["sources"]]
        else:
            sources = verify_sources(self.root, data.get("sources", []))
        if actor == "ai" and not sources:
            raise ValueError("Для записи ИИ требуется ссылка на строку источника.")
        entry = {"text": text, "author": actor, "manual": actor == "manual", "sources": sources, "updated": updated}
        if accepted:
            entry.update(manual=True, acceptedByOwner=True)
        if field == "latest":
            entry.update(id=entry_id or str(uuid.uuid4()), date=validate_due(data.get("date", previous.get("date"))))
        # Applying the same values at a fresh revision must not duplicate events or history.
        def same_as(candidate):
            return (candidate["text"] == text and candidate["author"] == actor
                    and candidate.get("date") == entry.get("date") and _source_keys(candidate["sources"]) == _source_keys(sources))

        candidates = [previous] if old or entry_id is not None else [e for e, _, _ in entries]
        if any(same_as(candidate) for candidate in candidates):
            return None
        if actor == "ai" and field == "latest" and entry_id is None:
            protected = {e["id"] for e, _, _ in entries if e["manual"]}
            live = {e["id"] for e, _, _ in entries}
            for change in _changes(lines):
                if (change.get("field") == "latest" and (change["entryId"] in protected or change["entryId"] not in live)
                        and change["after"] == text and change.get("date") == entry.get("date")
                        and _source_keys(change.get("sources", [])) == _source_keys(sources)):
                    raise storage.ConflictError("Владелец исправил или удалил это событие.")
        block = _block(entry, self.root, path, newline)
        if old:
            lines[old[1]:old[2]] = block.splitlines(keepends=True)
        elif bounds:
            lines.insert(bounds[0], newline + block)
        else:
            lines.append(newline + "## " + _HEADINGS[field] + newline + newline + block)
        change = {"entryId": entry.get("id"), "before": previous["text"], "after": text,
                  "beforeAuthor": previous["author"], "beforeManual": previous["manual"],
                  "beforeSources": previous["sources"], "sources": sources,
                  "beforeDate": previous.get("date"), "date": entry.get("date")}
        if accepted:
            change.update(acceptedByOwner=True, acceptedManualRevision=accepted_manual_revision)
        return change
