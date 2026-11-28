"""Move cold, dated project history into immutable quarterly Markdown files."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re

from . import archive_reader, evidence, project_updates, storage
from .archive_reader import ORIGIN, POINTER, original_lines
from .markdown_sections import fenced_lines, headings


ARCHIVE_SECTION = "## Архив истории"
_LEGACY = ("Журнал", "История статусов")
_NATIVE = "Последнее"
_DATE = re.compile(r"^[*_]*(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}\.\d{1,2}\.\d{2})(?!\d)")
_BULLET = re.compile(r"^[-*+]\s+(.*)$")
_BLOCK_ID = re.compile(r"^\s*\^[\w-]+\s*$")
_TASK_MARKER = re.compile(r"^( {0,3}[-+*] {1,4})\[[ xX]\](?=[ \t]+\S)", re.M)
_WIKI = re.compile(r"!?\[\[([^\[\]\n]+)\]\]")
_EXTERNAL = re.compile(r"!?\[[^\[\]\n]*\]\(<?(?:https?://|mailto:)[^\n()]+>?\)", re.I)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _dated(label):
    match = _DATE.match(label)
    if match:
        try:
            value = match[1]
            if "." in value:
                day, month, year = map(int, value.split("."))
                return date(year + 2000 if year < 100 else year, month, day)
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


@dataclass
class Entry:
    section: str
    start: int  # inclusive, zero-based, measured from the first file line
    stop: int   # exclusive
    day: date
    native: dict = None
    linked_block: bool = False
    footer_line: int = None  # One-based, confirmed to belong to the project.


def _legacy_entries(lines, code, heading_at, start, stop, section):
    """Only H3 snapshots and top-level bullets own their subordinate Markdown."""
    # Obsidian can name a whole list on a separate ^block-id line after it.
    # All items then contribute to one existing link, including items separated by blanks.
    linked_items = set()
    list_items = []
    for j in range(start, stop):
        line = lines[j]
        if j in heading_at:
            list_items.clear()
        elif j not in code and _BLOCK_ID.fullmatch(line):
            linked_items.update(list_items)
            list_items.clear()
        elif j not in code and _BULLET.match(line):
            list_items.append(j)
        elif (line.strip() and not line.startswith((" ", "\t")) and j not in code
              and (j == start or not lines[j-1].strip())):
            list_items.clear()
    i = start
    while i < stop:
        if i in code:
            i += 1
            continue
        heading = heading_at.get(i)
        bullet = _BULLET.match(lines[i])
        if not (heading or bullet):
            i += 1
            continue
        begin = i
        i += 1
        if heading:
            # An undated H3 can also own dated bullets. Keep its whole subtree.
            level, title, i = heading
            while i < stop:
                child = heading_at.get(i)
                if child and child[0] <= level:
                    break
                i += 1
            day = _dated(title) if level == 3 else None
        else:
            day = _dated(bullet[1])
            while i < stop:
                if i not in code and (i in heading_at or _BULLET.match(lines[i])):
                    break
                # A separate unindented paragraph after a blank is not part of a list item.
                if (i not in code and i > begin + 1 and not lines[i-1].strip()
                        and lines[i].strip() and not lines[i].startswith((" ", "\t"))):
                    break
                i += 1
        end = i
        while end > begin + 1 and not lines[end-1].strip():
            end -= 1
        if day and not (heading and begin+1 < end and
                        lines[begin+1].startswith("[Открыть запись в архиве](") and
                        "Archive/ProjectHistory/" in lines[begin+1]):
            yield Entry(section, begin, end, day, linked_block=begin in linked_items)


def _entries(record, lines):
    plain = [line.rstrip("\r\n") for line in lines]
    code, _ = fenced_lines(plain, comments=True)
    heading_at = headings(plain, start=record["body_start"]-1)
    heads = [(i, level, title, body) for i, (level, title, body) in heading_at.items() if level <= 2]
    duplicates = Counter(title for _, level, title, _ in heads if level == 2)
    native_section = None
    for pos, (begin, level, section, body) in enumerate(heads):
        end = heads[pos+1][0] if pos+1 < len(heads) else len(lines)
        if level != 2 or duplicates[section] != 1:
            continue
        if section in _LEGACY:
            yield from _legacy_entries(plain, code, heading_at, body, end, section)
        if section == _NATIVE:
            native_section = (body, end)
    if native_section is None:
        return
    _, rows, _ = project_updates._layout(record)
    for row, begin, stop in rows:
        if row["field"] != "event" or not row["date"]:
            continue
        start = begin + record["body_start"] - 1
        finish = stop + record["body_start"] - 1
        if not (native_section[0] <= start < finish <= native_section[1]):
            continue
        if not plain[start].startswith(project_updates._ENTRY):
            continue
        meta = json.loads(plain[start][len(project_updates._ENTRY):-4])
        # Hand-edited managed blocks remain in the working card.
        if not row["updated"] or row["updated"] != meta.get("updated"):
            continue
        after = finish
        while after < native_section[1] and not plain[after].strip():
            after += 1
        linked = after < native_section[1] and after not in code and _BLOCK_ID.fullmatch(plain[after])
        yield Entry(_NATIVE, start, finish, date.fromisoformat(row["date"]), row, bool(linked))


def _has_fragment(path, fragment, cache):
    if path.suffix.lower() != ".md" or not fragment:
        return False
    key = ("archive-link-fragments", path)
    if key not in cache:
        lines = path.read_text(encoding="utf-8").splitlines()
        start = 0
        if lines and lines[0] == "---":
            start = next((i+1 for i, line in enumerate(lines[1:], 1) if line in ("---", "...")), len(lines))
        code, _ = fenced_lines(lines, comments=True)
        targets = Counter()
        for _, title, _ in headings(lines, start=start).values():
            # Markup inside a heading may display differently in Obsidian.
            if not re.search(r"[*_\x60\[\]<>\\~]|&[#\w]+;", title):
                targets[title] += 1
        for i in range(start, len(lines)):
            block = re.search(r"(?:^|\s)(\^[A-Za-z0-9-]+)\s*$", lines[i])
            if i not in code and block:
                targets[block[1]] += 1
        cache[key] = targets
    return cache[key][fragment] == 1


def _portable(root, text, cache):
    """Ambiguous Markdown stays in place. Absolute vault wikilinks keep their target."""
    if re.search(r"(?:^|\s)\^[\w-]+\s*$|<[a-z][^>]*\b(?:id|name)\s*=", text, re.I | re.M):
        return False  # A saved Obsidian block link or HTML fragment points here.
    lines = text.splitlines()
    _, open_fence = fenced_lines(lines, comments=True)
    if open_fence != len(lines):
        return False
    for match in _WIKI.finditer(text):
        target, marker, fragment = match[1].split("|", 1)[0].partition("#")
        parts = target.split("/")
        if len(parts) < 2 or any(not p or p.startswith(".") or "\\" in p for p in parts):
            return False
        try:
            path = storage._inside(root, target)
            if not path.is_file():
                path = storage._inside(root, target + ".md")
            if not path.is_file() or (marker and not _has_fragment(path, fragment, cache)):
                return False
        except (ValueError, OSError):
            return False
    # Only the leading task checkbox is literal. Inspect every link in its label.
    remaining = _TASK_MARKER.sub(r"\1", text)
    remaining = _EXTERNAL.sub("", _WIKI.sub("", remaining))
    # A reference-style link or local embed may resolve against another definition
    # or directory after moving. Keep such entries, including bare fragment links.
    return not re.search(r"\[|\]\(|\b(?:href|src)\s*=|^[ \t]*!include\b", remaining, re.I | re.M)


def _render_entry(root, source, parent, entry, raw, cache):
    if entry.linked_block:
        return None
    if entry.native is None:
        return list(raw) if _portable(root, "".join(raw), cache) else None
    row = entry.native
    if not _portable(root, row["text"], cache):
        return None
    old_header = raw[0].rstrip("\r\n")
    meta = json.loads(old_header[len(project_updates._ENTRY):-4])
    footer = meta.get("footer", "")
    if not row["sources"]:
        return list(raw)
    try:
        references = [evidence.resolve_source(root, item, records=cache, allow_originals=True)
                      for item in row["sources"]]
    except (ValueError, OSError):
        return None

    def source_footer(refs, base):
        links = [storage.markdown_link(f"Источник, строка {ref['line']}", root / ref["path"], base)
                 for ref in refs]
        return " · ".join(filter(None, [row["date"], *links]))

    if footer != source_footer(row["sources"], source.parent) or footer != raw[-2].rstrip("\r\n"):
        return None
    new_footer = source_footer(references, parent)
    meta.update(footer=new_footer, digest=project_updates._digest(row["text"] + "\0" + new_footer))
    rendered = list(raw)
    rendered[0] = project_updates._ENTRY + storage.comment_json(meta) + " -->" + raw[0][len(old_header):]
    rendered[-2] = new_footer + raw[-2][len(footer):]
    return rendered


def _archive(path, record, relative, entries, lines, lineage):
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    output = ["", f"# {record['title']} · {path.name[:7]}{newline}"]
    old_lines = {}
    spans = []
    for section in (*_LEGACY, _NATIVE):
        selected = [(entry, rendered) for entry, rendered in entries if entry.section == section]
        if not selected:
            continue
        output += [newline, f"## {section}{newline}"]
        for entry, rendered in selected:
            output.append(newline)
            raw = lines[entry.start:entry.stop]
            # Isolate navigation anchors from the original byte spans. Old links
            # to the heading line (including its Markdown) still verify unchanged.
            anchors = {body: i for i, (_, _, body) in headings(raw).items()} if section != _NATIVE else {}
            begin = 0
            for end in sorted(set(anchors) | {len(raw)}):
                start = len(output) + 1
                for offset in range(begin, end):
                    old, new = raw[offset], rendered[offset]
                    if not new.endswith(("\n", "\r")):
                        new += newline
                    if old != new:
                        old_lines[str(len(output)+1)] = old
                    output.append(new)
                spans.append({"section": section, "start": entry.start+begin+1, "end": entry.start+end,
                              "archiveStart": start, "sha256": _hash("".join(raw[begin:end]).encode())})
                if end in anchors:
                    output.extend([newline, f"^sb-history-{entry.start+anchors[end]+1}" + newline, newline])
                begin = end
        if section == _NATIVE:
            output.append(project_updates._SECTION_END + newline)
    meta = {"id": record["id"], "path": relative, "revision": record["revision"],
            "ranges": spans, "originalLines": old_lines}
    if lineage is not None:
        meta.update(lineage)
    output[0] = ORIGIN + storage.comment_json(meta) + " -->" + newline
    return "".join(output).encode()


def _verify(data, record, source_lines):
    archived = data.decode().splitlines(keepends=True)
    meta = json.loads(archived[0].rstrip("\r\n")[len(ORIGIN):-4])
    if (meta["id"], meta["revision"]) != (record["id"], record["revision"]):
        raise storage.ConflictError("Архив относится к другой версии карточки.")
    original = original_lines(archived, meta)
    for span in meta["ranges"]:
        start, end, archived_start = span["start"] - 1, span["end"], span["archiveStart"] - 1
        moved = original[archived_start:archived_start + end-start]
        if moved != source_lines[start:end] or _hash("".join(moved).encode()) != span["sha256"]:
            raise storage.ConflictError("Строки архива отличаются от исходной карточки.")


def _receipt(root, previous, folder):
    archives = []
    for item in previous:
        rel = Path(item["path"])
        if rel.parent != folder or not re.fullmatch(r"[0-9]{4}-Q[1-4]-[0-9a-f]{64}\.md", rel.name):
            raise storage.ConflictError("Некорректный путь подтверждённого архива.")
        path = storage._inside(root, rel)
        if not path.is_file() or _hash(path.read_bytes()) != item["sha256"]:
            raise storage.ConflictError("Подтверждённый архив истории изменился.")
        archives.append({"path": rel.as_posix(), "quarter": rel.name[:7], "entries": item["entries"]})
    if len(previous) != previous[0]["parts"] or len({a["path"] for a in archives}) != len(archives):
        raise storage.ConflictError("В карточке сохранилась только часть ссылок на архив.")
    return archives


def _reviewed_entries(root, source, record, lines, entries, review):
    """Bind an operator's link decisions to exact source bytes before moving anything."""
    if (not isinstance(review, dict) or not set(review) <= {"revision", "sourceFooter", "wikiLink"}
            or not set(review) & {"sourceFooter", "wikiLink"}):
        raise ValueError("Укажите ревизию и проверенную ссылку или источник карточки.")
    if review.get("revision") != record["revision"]:
        raise storage.ConflictError("Проверенные ссылки относятся к другой версии карточки.")
    replacement = None
    for name, fields in (("sourceFooter", {"line", "target"}), ("wikiLink", {"line", "old", "new"})):
        if name not in review:
            continue
        item = review[name]
        if not isinstance(item, dict) or set(item) != fields or type(item["line"]) is not int:
            raise ValueError("Некорректные поля проверенной ссылки.")
        number = item["line"]
        entry = next((e for e in entries if e.native is None and e.start < number <= e.stop), None)
        if entry is None:
            raise ValueError("Проверенная строка находится вне датированной записи истории.")
        line = lines[number-1].rstrip("\r\n")
        if name == "sourceFooter":
            target = item["target"]
            if (not isinstance(target, str) or not target or "\\" in target or Path(target).is_absolute()
                    or any(not p or p.startswith(".") for p in target.split("/"))):
                raise ValueError("Укажите путь к источнику внутри хранилища.")
            path = storage._inside(root, target)
            label = re.fullmatch(r"\[([^\[\]]+)\]\([^()]+\)", line)
            following = next((i for i in range(number, len(lines)) if lines[i].strip()), None)
            if following is not None and headings(lines, start=record["body_start"]-1).get(following, (7,))[0] > 2:
                raise ValueError("Источник всей карточки должен завершать раздел истории.")
            if (not label or not path.is_file() or line != storage.markdown_link(label[1], path, source.parent)
                    or number != entry.stop or number < entry.start + 3 or lines[number-2].strip()
                    or not lines[entry.start].startswith("### ")):
                raise ValueError("Конец снимка не совпадает с отдельной ссылкой на источник карточки.")
            entry.footer_line = number
            entry.stop = number - 1
            while entry.stop > entry.start+1 and not lines[entry.stop-1].strip():
                entry.stop -= 1
        else:
            old, new = item["old"], item["new"]
            if (not all(isinstance(value, str) and _WIKI.fullmatch(value) for value in (old, new))
                    or old == new or [m[0] for m in _WIKI.finditer(line)].count(old) != 1
                    or not _portable(root, new, {})):
                raise ValueError("Проверьте исходную вики-ссылку и точный адрес назначения.")
            replacement = (number, old, new)
    return replacement


def rotate(root, project_id, *, as_of=None, keep_days=60, min_kept=5, apply=None, reviewed_links=None):
    """Preview one project or publish exactly the preview revision under an exclusive lock."""
    if type(keep_days) is not int or keep_days < 0 or type(min_kept) is not int or min_kept < 0:
        raise ValueError("Возраст и число сохраняемых записей должны быть неотрицательными целыми.")
    if apply is not None and (not isinstance(apply, str) or not re.fullmatch(r"[0-9a-f]{64}", apply)):
        raise ValueError("Для применения укажите ревизию предпросмотра.")
    if isinstance(as_of, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            raise ValueError("Укажите дату проверки в формате ГГГГ-ММ-ДД.")
        as_of = date.fromisoformat(as_of)
    day = date.today() if as_of is None else as_of
    if type(day) is not date:
        raise ValueError("Укажите дату проверки в формате ГГГГ-ММ-ДД.")
    root = Path(root).resolve()
    with storage._vault_lock(root, write=apply is not None):
        source, original, record, _ = project_updates._project(root, project_id)
        relative = source.relative_to(root).as_posix()
        folder = archive_reader.BASE / archive_reader.owner_folder(project_id)
        lines = original.decode().splitlines(keepends=True)
        active = archive_reader.pointers(record)
        for rel in active:
            # Also reject a new rotation if an older referenced quarter was edited or lost.
            archive_reader.read_path(root, rel, owner=(source, record))
        previous = [item for item in active.values() if apply and item.get("plan") == apply]
        result = {"id": project_id, "path": relative, "asOf": day.isoformat(), "applied": False,
                  "archives": [], "moved": 0, "skippedLinks": 0}
        if previous:
            archives = _receipt(root, previous, folder)
            return {**result, "applied": True, "alreadyApplied": True, "revision": apply,
                    "moved": sum(item["entries"] for item in archives), "archives": archives}

        sections = defaultdict(list)
        candidates = list(_entries(record, lines))
        replacement = None
        if reviewed_links is not None:
            replacement = _reviewed_entries(root, source, record, lines, candidates, reviewed_links)
        for entry in candidates:
            sections[entry.section].append(entry)
        quarters = defaultdict(list)
        cache = {}
        parent = storage._inside(root, folder)
        for entries in sections.values():
            dated = sorted(entries, key=lambda item: item.day, reverse=True)
            for entry in dated[min_kept:]:
                if (day - entry.day).days <= keep_days:
                    continue
                raw = lines[entry.start:entry.stop]
                if replacement and entry.start < replacement[0] <= entry.stop:
                    number, old, new = replacement
                    index = number - entry.start - 1
                    raw[index] = _WIKI.sub(lambda m: new if m[0] == old else m[0], raw[index])
                rendered = _render_entry(root, source, parent, entry, raw, cache)
                if rendered is None:
                    result["skippedLinks"] += 1
                    continue
                quarter = f"{entry.day.year:04d}-Q{(entry.day.month-1)//3+1}"
                quarters[quarter].append((entry, rendered))
        for quarter, entries in sorted(quarters.items()):
            entries.sort(key=lambda item: item[0].start)
            rel = folder / f"{quarter}-{record['revision']}.md"
            result["archives"].append({"path": rel.as_posix(), "quarter": quarter, "entries": len(entries)})
            result["moved"] += len(entries)
        archives = []
        if quarters:
            body, pointer_at, retained = _project_layout(root, source, lines, quarters, result["archives"])
            lineage = {"retained": retained, "beforeContent": archive_reader.content_revision("".join(lines)),
                       "afterContent": archive_reader.content_revision("".join(body))}
            for item in result["archives"]:
                rel = Path(item["path"])
                data = _archive(rel, record, relative, quarters[item["quarter"]], lines, lineage)
                _verify(data, record, lines)
                archives.append((rel, data))
                lineage = None  # One shared map per original project revision.
        verdict = [record["revision"], relative, day.isoformat(), keep_days, min_kept,
                   [(rel.as_posix(), _hash(data)) for rel, data in archives]]
        token = _hash(storage.comment_json(verdict).encode())
        result["revision"] = token
        if apply is None:
            return result
        if token != apply:
            raise storage.ConflictError("План архивации изменился. Получите новый предпросмотр.")
        if archives:
            _publish(root, source, record, original, lines, archives, result, body, pointer_at)
        return {**result, "applied": True}


def _publish(root, source, record, original, lines, archives, result, body, pointer_at):
    # Archives are inert until the project points at them. An interrupted publisher
    # can reuse exactly matching archives, while working bytes stay authoritative.
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    pointers = []
    for rel, data in archives:
        target = storage._inside(root, rel)
        if target.exists() and (not target.is_file() or target.read_bytes() != data):
            raise storage.ConflictError(f"Архив {rel} уже существует с другим содержимым.")
    for item, (rel, data) in zip(result["archives"], archives):
        target = storage._inside(root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            storage.replace_bytes(target, None, data)
        stored = target.read_bytes()
        if stored != data:
            raise storage.ConflictError(f"Архив {rel} уже существует с другим содержимым.")
        _verify(stored, record, lines)
        pointer = {"path": rel.as_posix(), "sha256": _hash(data), "plan": result["revision"],
                   "entries": item["entries"], "parts": len(archives)}
        pointers += [storage.markdown_link(item["quarter"], target, source.parent) + newline,
                     POINTER + storage.comment_json(pointer) + " -->" + newline]

    body[pointer_at:pointer_at+len(pointers)] = pointers
    storage.replace_bytes(source, original, "".join(body).encode())


def _project_layout(root, source, lines, quarters, archives):
    """Build the exact working file, reserving slots for its signed quarter pointers."""
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    remaining = list(enumerate(lines, 1))
    destinations = {item["quarter"]: root / item["path"] for item in archives}
    moved = [(entry, destinations[quarter]) for quarter, entries in quarters.items() for entry, _ in entries]
    for entry, dest in sorted(moved, key=lambda item: item[0].start, reverse=True):
        if entry.footer_line:
            at = entry.footer_line - 1
            titles = {title.casefold() for _, title, _ in headings(lines).values()}
            label = "Источник карточки"
            suffix = 2
            while label.casefold() in titles:
                label = f"Источник карточки ({suffix})"
                suffix += 1
            remaining[at:at] = [(None, "## " + label + newline), (None, newline)]
        # Keep original Obsidian heading anchors next to their precise archive links.
        stubs = []
        raw = lines[entry.start:entry.stop]
        for i, (_, _, body) in headings(raw).items():
            link = storage.markdown_link("Открыть запись в архиве", dest, source.parent)
            stubs.extend((None, line.rstrip("\r\n") + newline) for line in raw[i:body])
            stubs.append((None, link[:-1] + f"#^sb-history-{entry.start+i+1})" + newline))
        remaining[entry.start:entry.stop] = stubs
    plain = [text.rstrip("\r\n") for _, text in remaining]
    code, append_at = fenced_lines(plain, comments=True)
    heading = next((i for i, line in enumerate(plain) if i not in code and line == ARCHIVE_SECTION), None)
    if heading is not None:
        append_at = heading + 1
    if append_at and not remaining[append_at-1][1].endswith(("\r", "\n")):
        # Terminate the preceding physical line before inserting the new section or link.
        _, text = remaining[append_at-1]
        remaining[append_at-1] = (None, text + newline)
    if heading is None:
        remaining[append_at:append_at] = [(None, newline), (None, ARCHIVE_SECTION + newline), (None, newline)]
        append_at += 3
    pointer_lines = []
    for item in archives:
        link = storage.markdown_link(item["quarter"], root / item["path"], source.parent)
        pointer_lines.extend([(None, link + newline), (None, POINTER + newline)])
    remaining[append_at:append_at] = pointer_lines

    retained = []
    for position, (original, _) in enumerate(remaining, 1):
        if original is None:
            continue
        if (retained and original == retained[-1]["end"] + 1
                and position == retained[-1]["currentStart"] + original - retained[-1]["start"]):
            retained[-1]["end"] = original
        else:
            retained.append({"start": original, "end": original, "currentStart": position})
    for span in retained:
        span["sha256"] = _hash("".join(lines[span["start"]-1:span["end"]]).encode())
    return [text for _, text in remaining], append_at, retained
