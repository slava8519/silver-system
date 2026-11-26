"""Refresh project events and summaries from verified, explicitly attributed vault records."""

from collections import defaultdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from . import evidence, project_updates as updates, storage
from .batch import Batch
from .routing import Routing


_DECISION = re.compile(r"^(?:[-*] )?(?:(\d{4}-\d{2}-\d{2})[. ·:—-]+)?"
                       r"(?:Решили\b|Согласовали\b|Утвердили\b|(?P<label>Решение\s*[:—]))(?!\s+(?:бы|ли)\b)", re.I)
_PASSIVE = re.compile(r"^(?:[-*] )?(?:(\d{4}-\d{2}-\d{2})[. ·:—-]+)?"
                      r"(?:(?:для|к|по|на|в) (?:[\w-]+ ){1,6}|"
                      r"(?:порядок|сроки?|дата|формат|критерии|условия|решение) (?:[\w-]+ ){0,5})"
                      r"(?:согласован[аоы]?|утвержд[её]н[аоы]?|принят[аоы]?)\b", re.I)
_UNCERTAIN = re.compile(r"\b(?:не|ни|ли|бы|если|может|мог\w*|буд\w*|якобы|возможно|вероятно|"
                        r"пример\w*|предполож\w*|чернов\w*|ожида\w*)\b", re.I)
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:\s+|$)(.*)$")
_LINK = re.compile(r"(?<!!)\[([^\]\n]+)\]\((?:<([^>\n]+)>|([^()\s]+))\)")
_WIKI = re.compile(r"(?<!!)\[\[([^|#\[\]\n]+)(?:\|([^\[\]\n]+))?\]\]")
_TENTATIVE = re.compile(r"\b(?:предлож|пример|вариант|образец|образц|шаблон|гипотез|иде[яию]|вопрос|"
                        r"чернов|предполож|обсужд|возможн|кандидат|proposa|example|draft|template)", re.I)
_LABELS = {"status": "Статус", "next_step": "Следующий шаг", "due": "Срок"}
_MARK = b"# project-refresh-event "
_SUMMARY = b"# project-refresh-brief "
_INLINE_MARK = re.compile(rb"  (# project-refresh-(?:event|brief) [a-f0-9]{64})(?=\s|$)")


def _header_marks(header, record):
    lines = header.splitlines()
    version = lines[record["field_lines"]["schema_version"] - 1]
    return set(lines) | set(_INLINE_MARK.findall(version))


def _marked_header(header, record, marker):
    """Keep every line in place, including standalone markers in older cards."""
    lines = header.splitlines(keepends=True)
    if marker.startswith(_SUMMARY):
        for i, row in enumerate(lines):
            if row.startswith(_SUMMARY):
                lines[i] = marker + row[len(row.rstrip(b"\r\n")):]
                return b"".join(lines)
    i = record["field_lines"]["schema_version"] - 1
    row = lines[i].rstrip(b"\r\n")
    newline = lines[i][len(row):]
    if marker.startswith(_SUMMARY):
        row = _INLINE_MARK.sub(lambda m: b"" if m[1].startswith(_SUMMARY) else m[0], row)
    lines[i] = row + b"  " + marker + newline
    return b"".join(lines)


def _hash(value):
    return sha256(value.encode("utf-8")).hexdigest()


def _receipts(root, issues):
    grouped = defaultdict(list)
    folder = storage._inside(root, "Sources/Processed")
    if not folder.is_dir():
        return grouped
    for digest, jobs, questions in Batch(root)._receipts():
        if any(q.get("status") == "damaged" for q in questions.values()):
            issues.append({"path": f"Sources/Processed/{digest}.md", "reason": "Повреждена квитанция разбора."})
            continue
        for job in jobs.values():
            if not job["done"]:
                continue
            for index, result in job["steps"].items():
                kind = result["kind"]
                if kind not in ("project", "project_event"):
                    continue
                action = job["actions"][index]
                if (action["kind"] != kind or action.get("project") != result["project"]
                        or action.get("visibility", "shared") != "shared"):
                    continue
                source = {"id": digest, "path": job["source_path"], "line": action["line"], "quote": action["quote"]}
                if kind == "project_event":
                    key = _hash(json.dumps([digest, action["line"], action["quote"], result["project"]]))
                    if result["eventId"] != key:
                        continue
                    item = {"source": source, "text": action.get("text") or action["quote"],
                            "date": action.get("date"), "inboxMarker": "# inbox-event " + key + " ",
                            "summary": action.get("summary") if result.get("summaryStatus") == "updated" else None}
                else:
                    field, value = result["field"], result["value"]
                    if (field not in _LABELS or field != action.get("field") or value != action.get("value")
                            or not value or value not in action["quote"]):
                        continue
                    # The field value was published; the rest of the source line may be personal.
                    source["quote"] = value
                    key = _hash(json.dumps([digest, action["line"], action.get("direction"), result["project"], field]))
                    item = {"source": source, "text": f"{_LABELS[field]} — {value}", "date": None,
                            "fieldMarker": f"# inbox-change {key} {_hash(value)} "}
                grouped[result["project"]].append({**item, "direction": action.get("direction"), "kind": kind})
    return grouped


def _decisions(record, path, root):
    managed = set()
    if record["type"] == "project":
        _, rows, hidden = updates._layout(record, strict=True)
        managed.update(hidden)
        for row, _, _ in rows:
            managed.update(range(row["line"], row["end"] + 1))
    lines = record["body"].splitlines()
    _, code = updates._sections(lines)
    tentative_level = None
    for i, text in enumerate(lines):
        if i in code:
            continue
        heading = _HEADING.match(text)
        if heading:
            level = len(heading[1])
            if tentative_level and level <= tentative_level:
                tentative_level = None
            if not tentative_level and _TENTATIVE.search(heading[2]):
                tentative_level = level
            continue
        line = record["body_start"] + i
        plain = _LINK.sub(lambda m: m[1], text)
        plain = _WIKI.sub(lambda m: m[2] or Path(m[1]).name.removesuffix(".md"), plain)
        match = _DECISION.match(plain)
        if match and match["label"] and _UNCERTAIN.search(plain):
            continue
        if not match and not _UNCERTAIN.search(plain):
            match = _PASSIVE.match(plain)
        if tentative_level or not match or line in managed or "?" in plain or len(text) > 600 or "`" in text:
            continue
        quote = text.strip()
        source = {"id": record["id"], "path": path.relative_to(root).as_posix(),
                  "line": line, "quote": quote, "ref": _hash(text)}
        # Keep an independent line in this same file uniquely attributable after a move.
        yield {"source": source, "text": f"«{plain.strip()}»", "date": match[1]}


def _linked_notes(root, projects, issues):
    by_path = {p: key for key, copies in projects.items() for p, _, _ in copies}
    project_stems = {p.stem.casefold() for p in by_path}
    filenames = None
    grouped = defaultdict(list)
    for path in storage.iter_markdown_paths(root, folders=[storage.KINDS["note"]]):
        try:
            record = storage.read_record(path)
            if record["type"] != "note":
                continue
            for item in _decisions(record, path, root):
                source = item["source"]
                targets = set()
                for link in _LINK.finditer(source["quote"]):
                    url = urlsplit(link[2] or link[3])
                    relative = Path(unquote(url.path))
                    if url.scheme or url.netloc or relative.is_absolute() or not url.path:
                        continue
                    target = (path.parent / relative).resolve()
                    if target in by_path:
                        targets.add(by_path[target])
                    elif "Projects" in relative.parts:
                        targets.add(None)
                for link in _WIKI.finditer(source["quote"]):
                    name = link[1].strip().removesuffix(".md")
                    if "/" in name:
                        target = (root / (name + ".md")).resolve()
                        if name.startswith("Projects/"):
                            targets.add(by_path.get(target))
                    elif name.casefold() in project_stems:
                        if filenames is None:
                            # A bare Obsidian link can also name a note, plan or attachment.
                            filenames = defaultdict(list)
                            for folder, dirs, files in os.walk(root):
                                dirs[:] = [d for d in dirs if not d.startswith(".") and not (Path(folder) / d).is_symlink()]
                                for file in files:
                                    if not file.startswith("."):
                                        filenames[Path(file).stem.casefold()].append(Path(folder) / file)
                        matches = filenames[name.casefold()]
                        targets.add(by_path.get(matches[0]) if len(matches) == 1 else None)
                if len(targets) == 1 and None not in targets:
                    key, = targets
                    item["stableKey"] = [record["id"], item["text"], item["date"]]
                    grouped[key].append(item)
                elif targets:
                    issues.append({"path": source["path"], "reason": f"Сверьте адресата решения в строке {source['line']}."})
        except storage.UnmanagedMarkdown:
            continue
        except (OSError, ValueError) as error:
            issues.append({"path": path.relative_to(root).as_posix(), "reason": str(error)})
    return grouped


def _field_summary(record, path, root, raw):
    parts, sources = [], []
    lines = raw.splitlines()
    for field, label in _LABELS.items():
        value = record.get(field)
        if not value:
            continue
        line = record["field_lines"][field]
        literal = lines[line - 1].partition(b":")[2]
        comment = storage._comment(literal)
        if comment:
            literal = literal[:-len(comment)]
        quote = literal.decode().strip()
        sources.append({"path": path.relative_to(root).as_posix(), "line": line, "quote": quote})
        parts.append(f"{label} — {value}")
    return ". ".join(parts), sources


def _key(source):
    return source.get("id"), source.get("ref") or source["line"], source["quote"]


def _apply_card(root, path, expected_revision, permissions, receipt_rows, notes, counts):
    before = path.read_bytes()
    record, header = storage._parse_record(path, before)
    first_revision = record["revision"]
    if first_revision != expected_revision:
        raise storage.ConflictError("Проект изменился после чтения. Повторите обновление.")
    rows = [row for row in receipt_rows if row["direction"] == record.get("direction")]
    # Existing reviewed events cover legacy field changes from the same source fragment.
    reviewed = [r for r in rows if r["kind"] == "project_event"]
    candidates = reviewed.copy()
    event_quotes = defaultdict(list)
    for event in reviewed:
        src = event["source"]
        event_quotes[(src["id"], src["line"])].append(src["quote"])
    for row in rows:
        src = row["source"]
        if row["kind"] == "project" and not any(src["quote"] in q for q in event_quotes[(src["id"], src["line"])]):
            candidates.append(row)
    candidates.extend(_decisions(record, path, root))
    candidates.extend(notes)

    def save(body, marker):
        nonlocal before, record, header
        newline = b"\r\n" if header.startswith(b"---\r\n") else b"\n"
        updated = _marked_header(header, record, marker)
        if not updated.endswith((b"\r", b"\n")):
            updated += newline
        after = updated + body.encode("utf-8")
        parsed, new_header = storage._parse_record(path, after)
        updates._layout(parsed, strict=True)
        storage.replace_bytes(path, before, after)
        if record["revision"] == first_revision:
            counts["changed"] += 1
            counts.setdefault("example", {"id": record["id"], "path": path.relative_to(root).as_posix()})
        before, record, header = after, parsed, new_header

    for item in candidates:
        source = item["source"]
        marker = _MARK + _hash(json.dumps(item.get("stableKey") or _key(source), ensure_ascii=False)).encode()
        if marker in _header_marks(header, record) or (item.get("inboxMarker") and item["inboxMarker"].encode() in header):
            continue
        if item.get("fieldMarker") and item["fieldMarker"].encode() not in header:
            continue
        try:
            if source["id"] == record["id"]:
                source = evidence.resolve_source(root, source)
            verified = updates._source(root, source)
        except (OSError, ValueError) as error:
            counts["issues"].append({"path": path.relative_to(root).as_posix(),
                                    "reason": f"Основание {source['path']} · {source['line']}: {error}"})
            continue
        _, entries, _ = updates._layout(record)
        if any(e["field"] == "event" and (any(_key(s) == _key(verified) for s in e["sources"])
               or (e["manual"] and e["text"] == item["text"] and e["date"] == item["date"]))
               for e, _, _ in entries):
            continue
        body, _ = updates.inbox_update(root, path, record, source=verified, date=item["date"], text=item["text"])
        if body != record["body"]:
            save(body, marker)
            counts["eventsAdded"] += 1

    if "summary" not in permissions:
        return
    _, entries, hidden = updates._layout(record)
    current = updates._current(entries)
    deleted = not current and any(s.startswith(updates._HISTORY) and record["body_start"] + i in hidden
                                  for i, s in enumerate(record["body"].splitlines()))
    if deleted or (current and current["manual"]):
        counts["manualPreserved"] += 1
        return
    events = [e for e, _, _ in entries if e["field"] == "event" and e["sources"]]
    newest = max((e["date"] for e in events if e["date"]), default=None)
    latest = [e for e in events if e["date"] == newest] if newest else events
    curated = current and _SUMMARY + _hash(current["text"]).encode() not in _header_marks(header, record)
    if curated and not (current["date"] and newest and newest > current["date"] and len(latest) == 1):
        return
    # A dated new event takes priority over a previously dated brief. Other
    # updates use the current, explicitly recorded project fields first.
    use_event = curated or (current and current["date"] and newest and newest >= current["date"] and len(latest) == 1)
    text, sources = ("", []) if use_event else _field_summary(record, path, root, before)
    summary_date = None
    if not sources:
        if len(latest) != 1:
            return
        event = latest[0]
        sources = [updates._source(root, s, resolve=True) for s in event["sources"]]
        summary_date = event["date"]
        text = next((r["summary"] for r in reviewed if r.get("summary") and any(
            all(r["source"][k] == s[k] for k in ("id", "line", "quote")) for s in sources)), event["text"])
    body, status = updates._apply(root, path, record, actor="ai", sources=sources, date=summary_date, summary=text)
    if status == "updated":
        save(body, _SUMMARY + _hash(text).encode())
        counts["summariesUpdated"] += 1


def refresh_projects(root):
    """Called under the vault write lock. Each replacement checks the project revision."""
    root = Path(root).resolve()
    counts = {"examined": 0, "changed": 0, "eventsAdded": 0, "summariesUpdated": 0,
              "manualPreserved": 0, "issues": []}
    receipts = _receipts(root, counts["issues"])
    routing = Routing(root)
    projects = defaultdict(list)
    for path in storage.iter_markdown_paths(root, folders=[storage.KINDS["project"]]):
        try:
            record = storage.read_record(path)
            if record["type"] == "project":
                counts["examined"] += 1
                projects[record["id"]].append((path, record.get("direction"), record["revision"]))
        except storage.UnmanagedMarkdown:
            continue
        except (OSError, ValueError) as error:
            counts["issues"].append({"path": path.relative_to(root).as_posix(), "reason": str(error)})
    notes = _linked_notes(root, projects, counts["issues"])
    for key, copies in projects.items():
        if len(copies) > 1:
            counts["issues"].extend({"path": p.relative_to(root).as_posix(), "reason": "ID проекта повторяется."}
                                    for p, _, _ in copies)
            continue
        path, direction, revision = copies[0]
        try:
            permissions = {"event", "summary"}
            if direction:
                rule = routing._rule(routing._direction(direction))
                if rule.get("error"):
                    raise ValueError(rule["error"])
                permissions = set(rule["project_fields"])
            if "event" in permissions:
                _apply_card(root, path, revision, permissions, receipts[key], notes[key], counts)
        except (OSError, ValueError) as error:
            counts["issues"].append({"path": path.relative_to(root).as_posix(), "reason": str(error)})
    return counts
