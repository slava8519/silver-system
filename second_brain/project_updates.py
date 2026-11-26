"""Project summaries and events in the project's own Markdown, with line-level evidence."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from . import evidence, storage
from .inbox import Inbox
from .markdown_sections import FENCE, fenced_lines
from .planning import validate_due


_HEADINGS = {"brief": "## Коротко", "event": "## Последнее"}
_ENTRY = "<!-- brain-project-entry: "
_LINKS = "<!-- brain-project-links -->"
_END = "<!-- /brain-project-entry -->"
_SECTION_END = "<!-- /brain-project-section -->"
_HISTORY = "<!-- brain-project-brief-change: "
_HISTORY_TITLE = "## История сводки проекта"
_ORIGINAL = re.compile(r"Sources/Originals/([0-9a-f]{64})\.(?:txt|md)\Z")


def _digest(text):
    return sha256(text.encode()).hexdigest()


def _source(root, item, *, resolve=False):
    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
        raise ValueError("Укажите путь, строку и цитату источника проекта.")
    match = _ORIGINAL.fullmatch(item["path"])
    if match:
        identifier = match[1]
        number = item.get("line")
        if type(number) is not int or number < 1:
            raise ValueError("Укажите положительный номер строки оригинала.")
        line = Inbox(root).evidence(identifier, number)
        quote = item.get("quote")
        fingerprint = _digest(line["text"])
        if (line["path"] != item["path"] or not isinstance(quote, str) or not quote.strip()
                or len(quote) > 600 or quote not in line["text"] or item.get("ref", fingerprint) != fingerprint
                or item.get("id", identifier) != identifier):
            raise storage.ConflictError("Проверьте исходный фрагмент и точную цитату.")
        result = {"id": identifier, "path": item["path"], "line": number, "quote": quote, "ref": fingerprint}
        if resolve:
            result["href"] = f"/original/{identifier}?start={number}"
        return result
    if resolve:
        return evidence.resolve_source(root, item)
    if not isinstance(item.get("quote"), str) or not item["quote"].strip():
        raise ValueError("Укажите точную цитату источника.")
    return evidence.verify_source_line(root, item)[0]


def _sources(root, values, actor):
    if not isinstance(values, list) or len(values) > 8 or (actor == "ai" and not values):
        raise ValueError("Для записи ИИ укажите от одной до восьми цитат источников.")
    verified = []
    for value in values:
        source = _source(root, value)
        if source not in verified:
            verified.append(source)
    return verified


def _row(text, field, line, end, **fields):
    return {"field": field, "text": text, "line": line, "end": end,
            "author": "manual", "manual": True, "date": None,
            "sources": [], "updated": None, **fields}


def _sections(lines):
    """Headings in examples and ambiguous legacy headings are ordinary Markdown."""
    headings = {title: field for field, title in _HEADINGS.items()}
    sections = {}
    code, _ = fenced_lines(lines)
    for i, line in enumerate(lines):
        if i not in code and line in headings:
            field = headings[line]
            sections[field] = None if field in sections else i
    return sections, code


def _entry(lines, begin, field, base, code):
    line = lines[begin]
    meta = json.loads(line[len(_ENTRY):-4])
    refs = lines.index(_LINKS, begin + 1)
    close = lines.index(_END, refs + 1)
    if (not line.endswith(" -->") or meta["author"] not in ("ai", "manual")
            or not isinstance(meta["sources"], list) or len(meta["sources"]) > 8
            or close in code or any(n in code or lines[n].startswith((_ENTRY, _END, _SECTION_END, "## ", "# "))
                                    for n in range(begin+1, close))):
        raise ValueError
    for source in meta["sources"]:
        if (not isinstance(source, dict) or type(source["line"]) is not int or source["line"] < 1
                or not all(isinstance(source[key], str) for key in ("quote", "path", "id", "ref"))):
            raise ValueError
    text = "\n".join(lines[begin+1:refs]).strip()
    footer = "\n".join(lines[refs+1:close])
    modified = meta["digest"] != _digest(text + "\0" + footer)
    attribution = {}
    if not modified:
        attribution = {"author": meta["author"], "manual": meta["author"] == "manual",
                       "updated": meta["updated"], "sources": meta["sources"]}
        if meta.get("acceptedByOwner") is True:
            attribution.update(manual=True, acceptedByOwner=True)
    date = None if modified and footer != meta.get("footer") else validate_due(meta.get("date"))
    row = _row(text, field, base+begin+1, base+max(begin+1, refs-1), date=date, **attribution)
    return row, refs, close


def _layout(record, *, strict=False, warnings=None):
    """Ranges are zero-based; public citation coordinates are absolute, one-based."""
    lines = record["body"].splitlines()
    base = record["body_start"]
    headings = {title: field for field, title in _HEADINGS.items()}
    sections, code = _sections(lines)
    entries, hidden = [], set()
    field = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if i in code:
            i += 1
            continue
        if line in headings:
            candidate = headings[line]
            field = candidate if sections[candidate] == i else None
            if field:
                hidden.add(base + i)
        elif line.startswith(_HISTORY) or line == _HISTORY_TITLE:
            hidden.add(base + i)
            field = None
        elif line == _SECTION_END:
            hidden.add(base + i)
            field = None
        elif line.startswith(("# ", "## ")):
            field = None
        elif (not field and line.startswith(_ENTRY)) or line == _END:
            hidden.add(base + i)
        elif line == _LINKS:
            # The owner may have renamed the enclosing section. Its ordinary
            # prose is still visible; service metadata and link footers are not.
            hidden.add(base + i)
            if i+2 < len(lines) and lines[i+2] == _END:
                i += 1
                hidden.add(base + i)
        elif field and line.startswith(_ENTRY):
            begin = i
            try:
                entry, refs, close = _entry(lines, begin, field, base, code)
            except (IndexError, KeyError, TypeError, ValueError) as error:
                message = f"Проверьте разметку истории проекта в строке {base+begin}."
                if strict:
                    raise storage.ConflictError(message) from error
                if warnings is not None and len(warnings) < 5:
                    warnings.append(message)
                # Leave an unverified block out of search and continue with the
                # next independent entry or heading. Keep its original bytes.
                i += 1
                while i < len(lines) and not lines[i].startswith((_ENTRY, _SECTION_END, "# ", "## ")):
                    i += 1
                    if lines[i-1] == _END:
                        break
                hidden.update(range(base + begin, base + i))
                continue
            entries.append((entry, begin, close+1))
            hidden.add(base + begin)
            hidden.update(range(base+refs, base+close+1))
            i = close
        elif field and line.strip():
            begin = i
            while i + 1 < len(lines) and i+1 not in code and lines[i+1].strip() and not lines[i+1].startswith(
                    ("# ", "## ", "<!-- brain-project", _END, _SECTION_END)):
                i += 1
            text = "\n".join(lines[begin:i+1]).strip()
            entries.append((_row(text, field, base+begin, base+i), begin, i+1))
        i += 1
    return sections, entries, hidden


def projection(record):
    _, entries, hidden = _layout(record)
    return [row for row, _, _ in entries if row["text"]], hidden


def _current(entries):
    summaries = [entry for entry, _, _ in entries if entry["field"] == "brief"]
    if len(summaries) > 1:
        # Several hand-written paragraphs are one owner-authored summary.
        first, last = summaries[0], summaries[-1]
        return _row("\n\n".join(s["text"] for s in summaries), "brief", first["line"], last["end"])
    return next(iter(summaries), None)


def read_record_updates(root, path, record, limit=5, offset=0, *, start=None, end=None):
    if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
        raise ValueError("Укажите размер страницы от 1 до 20 и неотрицательное смещение.")
    warnings = []
    _, entries, _ = _layout(record, warnings=warnings)
    summary = _current(entries)
    events = [entry for entry, _, _ in entries if entry["field"] == "event" and entry["text"]]
    if start or end:
        events = [event for event in events if event["date"] and (not start or start <= event["date"])
                  and (not end or event["date"] <= end)]
    # New entries are inserted at the start of the section. A stable sort keeps
    # entry order for two events on the same date, including two undated events.
    events.sort(key=lambda event: event["date"] or "", reverse=True)

    def resolved(entry):
        sources = []
        for item in entry["sources"]:
            try:
                sources.append({**_source(root, item, resolve=True), "available": True})
            except (ValueError, OSError):
                sources.append({**{k: item[k] for k in ("path", "line", "quote")}, "available": False})
        return {**entry, "sources": sources}

    return {"id": record["id"], "path": path.relative_to(root).as_posix(), "revision": record["revision"],
            "brief": resolved(summary) if summary and summary["text"] else None,
            "events": [resolved(e) for e in events[offset:offset+limit]], "total": len(events),
            "offset": offset, "truncated": offset+limit < len(events), **({"warnings": warnings} if warnings else {})}


def _project(root, identifier):
    path = storage.find_record(root, identifier)
    before = path.read_bytes()
    record, header = storage._parse_record(path, before)
    if record["type"] != "project" or record["id"] != identifier:
        raise ValueError("Укажите ID существующего проекта.")
    return path, before, record, header


def read_updates(root, project_id, limit=5, offset=0):
    root = Path(root).resolve()
    with storage._vault_lock(root, write=False):
        path, _, record, _ = _project(root, project_id)
        return read_record_updates(root, path, record, limit, offset)


def _text(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 2400
            or value.splitlines() != [value] or "\0" in value
            or value.strip().startswith(("# ", "## ")) or FENCE.match(value.strip()) or "<!--" in value):
        raise ValueError("Введите одну фразу до 2400 символов без служебной разметки.")
    return value.strip()


def _same(entry, text, actor, date, sources):
    def keys(items):
        return {(s["id"], s["ref"], s["quote"]) for s in items}
    return (entry["text"] == text and entry["author"] == actor and entry["date"] == date
            and keys(entry["sources"]) == keys(sources))


def _apply(root, path, record, *, actor, sources, date=None, text=None, summary=None,
           inbox=False, accepted=False):
    if actor not in ("ai", "manual") or (text is None and summary is None):
        raise ValueError("Укажите событие или сводку и автора записи.")
    sources = _sources(root, sources, actor)
    date = validate_due(date)
    if date and actor == "ai":
        evidence.require_event_date(date, sources)
    sections, entries, hidden = _layout(record, strict=True)
    for source in sources:
        if source["id"] == record["id"] and (source["line"] in hidden or any(
                row["line"] <= source["line"] <= row["end"] for row, _, _ in entries)):
            raise ValueError("Для проектной записи нужно независимое основание. Сошлитесь на исходное решение или заметку.")
    body = record["body"]
    newline = "\r\n" if "\r\n" in body or (not body and path.read_bytes().startswith(b"---\r\n")) else "\n"
    lines = body.splitlines(keepends=True)
    current = _current(entries)
    events = [entry for entry, _, _ in entries if entry["field"] == "event"]
    status = "unchanged"
    stamp = datetime.now(timezone.utc).isoformat()
    changes = {}
    if summary is not None:
        summary = _text(summary)
        protected = (current["manual"] if current else any(
            s.startswith(_HISTORY) and record["body_start"] + i in hidden for i, s in enumerate(lines)))
        ambiguous = "brief" in sections and sections["brief"] is None
        newest = max((e["date"] for e in events if e["date"]), default=None)
        if accepted and not protected:
            raise storage.ConflictError("У этой сводки нет ручной правки для согласования.")
        if ambiguous or (actor == "ai" and protected and not accepted):
            if not inbox:
                raise storage.ConflictError("Сверьте ручную формулировку и единственный раздел «Коротко» в проекте.")
            status = "manual_preserved"
        elif inbox and current and newest and (date or "") < newest:
            status = "older_event"
        elif not current or not _same(current, summary, actor, date, sources):
            changes["brief"] = summary
            status = "updated"
    if text is not None:
        text = _text(text)
        if "event" in sections and sections["event"] is None:
            raise storage.ConflictError("В проекте несколько разделов «Последнее». Уточните заголовки в Markdown.")
        if not any(_same(entry, text, actor, date, sources) for entry in events):
            changes["event"] = text
    if not changes:
        return body, status

    # Edit existing sections from bottom to top so the parsed line offsets stay valid.
    missing = []
    for field in sorted(changes, key=lambda f: sections.get(f, -1), reverse=True):
        value = changes[field]
        footer = " · ".join(filter(None, [date, *(storage.markdown_link(
            f"Источник, строка {s['line']}", root / s["path"], path.parent) for s in sources)]))
        meta = {"author": actor, "updated": stamp, "date": date, "sources": sources,
                "footer": footer, "digest": _digest(value + "\0" + footer)}
        if accepted and field == "brief":
            meta["acceptedByOwner"] = True
        block = [_ENTRY + storage.comment_json(meta) + " -->", value, _LINKS]
        if footer:
            block.append(footer)
        block.append(_END)
        block = [line + newline for line in block]
        if field not in sections:
            missing.extend([newline + _HEADINGS[field] + newline, newline, *block, _SECTION_END + newline])
        elif field == "brief" and current:
            spans = [(a, b) for e, a, b in entries if e["field"] == field]
            for a, b in reversed(spans[1:]):
                del lines[a:b]
            a, b = spans[0]
            lines[a:b] = block
        else:
            pos = sections[field] + 1
            lines[pos:pos] = [newline, *block]
    if "brief" in changes:
        previous = None if not current else {k: current[k] for k in ("text", "author", "sources", "date")}
        change = {"before": previous, "after": summary, "actor": actor, "updated": stamp}
        if accepted:
            change.update(acceptedByOwner=True, acceptedManualRevision=record["revision"])
        if not any(s == _HISTORY_TITLE and record["body_start"]+i in hidden
                   for i, s in enumerate(body.splitlines())):
            missing.extend([newline, _HISTORY_TITLE + newline])
        missing.append(_HISTORY + storage.comment_json(change) + " -->" + newline)
    if missing:
        _, append_at = fenced_lines(lines)
        if append_at and not lines[append_at-1].endswith(("\n", "\r")):
            missing.insert(0, newline)
        lines[append_at:append_at] = missing
    return "".join(lines), status


def inbox_update(root, path, record, *, source, date=None, text=None, summary=None):
    """Prepare both changes as one body under the importer's revision and write lock."""
    return _apply(root, path, record, actor="ai", sources=[source], date=date, text=text,
                  summary=summary, inbox=True)


def write_update(root, project_id, *, field, text, revision, actor="ai", sources=None, date=None,
                 accepted_manual_revision=None):
    if field not in _HEADINGS or (field == "brief" and date is not None):
        raise ValueError("Выберите brief или event. Дата указывается для события.")
    root = Path(root).resolve()
    with storage._write_lock(root):
        path, before, record, header = _project(root, project_id)
        accepted = accepted_manual_revision is not None
        if accepted and (field != "brief" or actor != "ai" or accepted_manual_revision != revision
                         or record["revision"] != revision):
            raise storage.ConflictError("Согласие владельца должно относиться к актуальной версии ручной сводки.")
        body, _ = _apply(root, path, record, actor=actor, sources=[] if sources is None else sources,
                         date=date, accepted=accepted, **{"summary" if field == "brief" else "text": text})
        if body == record["body"]:
            return read_record_updates(root, path, record)
        if record["revision"] != revision:
            raise storage.ConflictError("Проект изменился. Перечитайте карточку и сверьте правку.")
        if not header.endswith((b"\n", b"\r")):
            header += b"\n"
        after = header + body.encode()
        saved, _ = storage._parse_record(path, after)
        _layout(saved, strict=True)
        storage.replace_bytes(path, before, after)
        return read_record_updates(root, path, saved)
