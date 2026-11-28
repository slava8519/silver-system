"""Read only quarterly history parts linked and authenticated by their owning project."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import quote, unquote

from . import storage
from .markdown_sections import fenced_lines


BASE = Path("Archive/ProjectHistory")
_PART = re.compile(r"[0-9]{4}-Q[1-4]-[0-9a-f]{64}\.md\Z")
POINTER = "<!-- brain-history-archive: "
ORIGIN = "<!-- brain-history-origin: "


def content_revision(raw):
    """Fingerprint all text and coordinates without depending on the archive's own digest."""
    lines = raw.splitlines(keepends=True)
    return sha256("".join(POINTER + line[len(line.rstrip("\r\n")):] if line.startswith(POINTER) else line
                          for line in lines).encode()).hexdigest()


def owner_folder(identifier):
    encoded = quote(identifier, safe="")
    return "%2E" + encoded[1:] if encoded.startswith(".") else encoded


def archive_path(relative):
    path = Path(relative)
    return (len(path.parts) == 4 and path.parent.parent == BASE and
            owner_folder(unquote(path.parent.name)) == path.parent.name
            and _PART.fullmatch(path.name) is not None)


def pointers(project):
    if POINTER not in project["body"]:
        return {}
    result = {}
    counts = Counter()
    lines = project["body"].splitlines()
    code, _ = fenced_lines(lines)
    for i, line in enumerate(lines):
        if i in code or not line.startswith(POINTER):
            continue
        try:
            if not line.endswith(" -->"):
                raise ValueError
            item = json.loads(line[len(POINTER):-4])
            relative = item["path"]
            if (not isinstance(relative, str) or not archive_path(relative) or Path(relative).as_posix() != relative
                    or Path(relative).parent.name != owner_folder(project["id"]) or relative in result
                    or not re.fullmatch("[0-9a-f]{64}", item["sha256"])
                    or not re.fullmatch("[0-9a-f]{64}", item["plan"])
                    or any(type(item[k]) is not int or item[k] < 1 for k in ("parts", "entries"))):
                raise ValueError
            result[relative] = item
            counts[item["plan"]] += 1
        except (ValueError, KeyError, TypeError) as exc:
            raise storage.ConflictError("Проверьте ссылку на квартальный архив в карточке проекта.") from exc
    if any(counts[item["plan"]] != item["parts"] for item in result.values()):
        raise storage.ConflictError("В карточке сохранилась только часть ссылок на архив.")
    return result


def original_lines(lines, meta):
    """Restore bytes of citation lines before resolving old source hashes."""
    result = list(lines)
    for key, value in meta.get("originalLines", {}).items():
        number = int(key)
        if not (1 <= number <= len(result)) or not isinstance(value, str) or len(value.splitlines()) != 1:
            raise ValueError("Некорректная карта исходных строк архива.")
        result[number-1] = value
    return result


def original_citation(record, number, ref):
    """Return the authenticated pre-rotation wording when this hash requests it."""
    if not ref:
        return None
    original = record.get("archiveOrigin", {}).get("originalLines", {}).get(str(number))
    if original is not None:
        text = original.rstrip("\r\n")
        if sha256(text.encode()).hexdigest() == ref:
            return text
    return None


def _read(root, owner_path, owner, relative, pointer):
    raw = storage._inside(root, relative).read_bytes()
    digest = sha256(raw).hexdigest()
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    try:
        if digest != pointer["sha256"] or not lines:
            raise ValueError
        header = lines[0].rstrip("\r\n")
        if not header.startswith(ORIGIN) or not header.endswith(" -->"):
            raise ValueError
        origin = json.loads(header[len(ORIGIN):-4])
        part = Path(relative)
        if origin["id"] != owner["id"] or origin["revision"] != part.stem[8:]:
            raise ValueError
        original = original_lines(lines, origin)
        occupied = set()
        for span in origin["ranges"]:
            a, b, at = (span[k] for k in ("start", "end", "archiveStart"))
            if any(type(n) is not int for n in (a, b, at)) or a < 1 or b < a or at < 2 or at+b-a > len(lines):
                raise ValueError
            segment = range(at, at+b-a+1)
            if occupied.intersection(segment) or sha256("".join(original[at-1:at+b-a]).encode()).hexdigest() != span["sha256"]:
                raise ValueError
            occupied.update(segment)
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise storage.ConflictError(f"Не удалось подтвердить содержимое квартального архива: {relative}") from exc
    return {"id": f"history:{owner['id']}:{part.stem}", "type": "project_history",
            "title": f"{owner['title']} · {part.name[:7]}", "aliases": [owner["title"], *owner["aliases"]],
            "project_id": owner["id"], "direction": owner.get("direction"), "revision": digest,
            "path": relative, "originPath": owner_path.relative_to(root).as_posix(), "archiveOrigin": origin,
            "raw": text, "body": "".join(lines[1:]), "body_start": 2, "field_lines": {}}


def read_path(root, path, *, owner=None):
    root = Path(root).resolve()
    path = Path(path)
    relative = (path.relative_to(root) if path.is_absolute() else path).as_posix()
    if not archive_path(relative):
        raise ValueError("Укажите квартальный архив проекта.")
    if owner is None:
        owner_path = storage.find_record(root, unquote(Path(relative).parent.name))
        owner = (owner_path, storage.read_record(owner_path))
    owner_path, project = owner
    if project["type"] == "project" and not any(part.startswith(".") for part in owner_path.relative_to(root).parts):
        pointer = pointers(project).get(relative)
        if pointer is not None:
            return _read(root, owner_path, project, relative, pointer)
    raise FileNotFoundError("Ссылка на квартальный архив отсутствует в проекте.")


def is_archive_id(identifier):
    if not identifier.startswith("history:"):
        return False
    owner, _, stem = identifier[len("history:"):].rpartition(":")
    return bool(owner and _PART.fullmatch(stem + ".md"))


def by_id(root, identifier):
    project_id, stem = identifier[len("history:"):].rsplit(":", 1)
    return read_path(root, BASE / owner_folder(project_id) / (stem + ".md"))


def parts(root, path, project):
    for relative, pointer in pointers(project).items():
        yield root / relative, _read(root, path, project, relative, pointer)


def visible_lines(record):
    from .project_updates import projection
    _, hidden = projection(record)
    lines = record["body"].splitlines()
    first = record["body_start"]
    result = {}
    for span in record["archiveOrigin"]["ranges"]:
        at = span["archiveStart"]
        for n in range(at, at + span["end"] - span["start"] + 1):
            if n not in hidden:
                result[n] = lines[n - first]
    return result


def latest_plan(project):
    """New rotations insert their pointers before the previously published parts."""
    first = next(iter(pointers(project).values()), {})
    return first.get("plan")


def _retained_line(span, number, candidates, raw):
    """Locate a retained passage by hashing its complete, unmodified original range."""
    offset = number - span["start"]
    length = span["end"] - span["start"] + 1
    positions = []
    for candidate in candidates:
        start = candidate - 1 - offset
        if start >= 0 and start + length <= len(raw):
            digest = sha256("".join(raw[start:start + length]).encode()).hexdigest()
            if digest == span["sha256"]:
                positions.append(candidate)
    if len(positions) > 1:
        raise storage.ConflictError("Строка источника повторяется в одинаковых фрагментах карточки.")
    return positions[0] if len(positions) == 1 else None


def citation_line(root, path, project, number, ref, candidates, archive_plan=None):
    """Follow old coordinates through rotations, including unchanged duplicate sentences."""
    groups = {}
    for relative, pointer in pointers(project).items():
        groups.setdefault(pointer["plan"], []).append((relative, pointer))
    if archive_plan and archive_plan not in groups:
        raise storage.ConflictError("Не удалось подтвердить версию истории для ссылки.")
    pending = []
    for plan, files in groups.items():
        if plan == archive_plan:
            break
        pending.append(files)
    if len(pending) > 1:
        # Later user edits can put a different occurrence at the projected
        # coordinate. Reconstruct the first applicable revision across all moves.
        oldest = Path(pending[-1][0][0]).stem[8:]
        located = revision_line(root, path, project, oldest, number)
        if located is None:
            raise storage.ConflictError("Не удалось подтвердить исходный фрагмент после нескольких архиваций.")
        text, record, n = located
        if sha256(text.rstrip("\r\n").encode()).hexdigest() == ref:
            if record is project and n in candidates:
                return path, project, n
            if record["type"] == "project_history" and n in visible_lines(record):
                return root / record["path"], record, n
        raise storage.ConflictError("Строка источника изменилась. Задайте вопрос ещё раз.")
    possible = []
    retained_coordinate = False
    raw = project["raw"].splitlines(keepends=True)
    for files in reversed(pending):
        retained = None
        expected_content = None
        for relative, pointer in files:
            record = _read(root, path, project, relative, pointer)
            origin = record["archiveOrigin"]
            for span in origin.get("retained", []):
                if span["start"] <= number <= span["end"]:
                    retained = span
                    expected_content = origin.get("afterContent")
                    break
            visible = visible_lines(record)
            for span in origin["ranges"]:
                at, start, end = (span[k] for k in ("archiveStart", "start", "end"))
                for offset in range(end - start + 1):
                    n = at + offset
                    if n not in visible:
                        continue
                    # A citation to the working project predates archive-only link rewrites.
                    before = origin.get("originalLines", {}).get(str(n), visible[n]).rstrip("\r\n")
                    if sha256(before.encode()).hexdigest() == ref:
                        location = (root / relative, record, n)
                        if start + offset == number:
                            return location
                        possible.append(location)
        if retained:
            translated = retained["currentStart"] + number - retained["start"]
            if (translated in candidates and expected_content
                    and content_revision(project["raw"]) == expected_content):
                return path, project, translated
            active = _retained_line(retained, number, candidates, raw)
            if active is not None:
                return path, project, active
            # This coordinate survived this rotation. Identical sentences already
            # archived by that time cannot confirm a later edit to its passage.
            possible.clear()
            retained_coordinate = True
            number += retained["currentStart"] - retained["start"]
    if retained_coordinate:
        raise storage.ConflictError("Строка источника изменилась. Задайте вопрос ещё раз.")
    if number in candidates:
        return path, project, number
    possible.extend((path, project, n) for n in candidates)
    if len(possible) != 1:
        raise storage.ConflictError("Строка источника изменилась. Задайте вопрос ещё раз.")
    return possible[0]


def _retained_start(lines, span, intact):
    """Find the authenticated original passage in a possibly edited, sparse revision."""
    width = span["end"] - span["start"] + 1
    if width < 1 or width > len(lines):
        return None

    def matches(at):
        try:
            text = "".join(lines[i][0] for i in range(at, at + width))
        except KeyError:
            return False
        return sha256(text.encode()).hexdigest() == span["sha256"]

    predicted = span["currentStart"]
    found = predicted if matches(predicted) else None
    if intact:
        return found
    for at in lines:
        if at == predicted or (found is not None and lines[at][0] != lines[found][0]):
            continue
        if matches(at):
            if found is not None:
                return None
            found = at
    return found


def revision_line(root, path, project, revision, number):
    """Reconstruct the cited source coordinate, keeping its actual present-day location."""
    groups = {}
    for relative, pointer in pointers(project).items():
        groups.setdefault(Path(relative).stem[8:], []).append((relative, pointer))
    if revision not in groups:
        return None
    active = {n: (text, project, n) for n, text in enumerate(project["raw"].splitlines(keepends=True), 1)}
    content = content_revision(project["raw"])
    # New rotations insert their pointers ahead of the previous parts. Reconstruct
    # newer original revisions first so a retained passage can span later moves.
    for original_revision, files in groups.items():
        previous = {}
        prior_content = None
        for relative, pointer in files:
            part = _read(root, path, project, relative, pointer)
            origin = part["archiveOrigin"]
            raw = original_lines(part["raw"].splitlines(keepends=True), origin)
            for span in origin["ranges"]:
                for n in range(span["start"], span["end"] + 1):
                    at = span["archiveStart"] + n - span["start"]
                    previous[n] = (raw[at - 1], part, at)
            retained = origin.get("retained", [])
            intact = content is not None and content == origin.get("afterContent")
            if intact:
                prior_content = origin.get("beforeContent")
            copies = Counter(span["sha256"] for span in retained)
            for span in retained:
                if original_revision == revision and not span["start"] <= number <= span["end"]:
                    continue
                if not intact and copies[span["sha256"]] > 1:
                    continue
                start = _retained_start(active, span, intact)
                if start is not None:
                    for n in range(span["start"], span["end"] + 1):
                        previous[n] = active[start + n - span["start"]]
        if original_revision == revision:
            return previous.get(number)
        active = previous
        content = prior_content
    return None
