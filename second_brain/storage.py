"""File-backed records for a Second Brain vault."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import uuid
from contextlib import contextmanager, suppress
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import quote


KINDS = {"direction": "Directions", "note": "Notes", "project": "Projects"}
STATE_FIELDS = ("status", "due", "next_step")
PROJECT_EVIDENCE_FIELDS = (*STATE_FIELDS, "owner")
EDITABLE = {"title", "aliases", "direction", "project_start", *STATE_FIELDS}
FOLDERS = (
    "Inbox", *KINDS.values(), "People", "Plans", "Tasks", "Reports", "Sources/Originals",
    "Sources/Processed", "Archive", "Settings/Formats", "Settings/Templates",
)
START_PAGES = {
    "Home.md": (
        "# Мой второй мозг\n\n[Первый шаг](Start-Here.md)\n\n"
        "[Направления](Directions/) · [Заметки](Notes/) · [Проекты](Projects/)\n"
    ),
    "Start-Here.md": (
        "# С чего начать\n\n"
        "Выберите постоянную область своей жизни или работы и попросите ИИ "
        "создать направление. Сохраните одно наблюдение своими словами. "
        "Можно добавить другое название, по которому вы его вспомните.\n\n"
        "Откройте новый чат и попросите найти наблюдение со ссылкой на строки "
        "в файле. Следующее направление можно добавить тем же способом.\n"
    ),
}


class ConflictError(ValueError):
    """The record changed since the caller read it."""


class UnmanagedMarkdown(ValueError):
    """An ordinary Markdown file without a managed record."""


def _inside(root, relative):
    path = root
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"Символическая ссылка в пути хранилища: {path}")
    return path


@contextmanager
def _staged(path, data):
    fd, name = tempfile.mkstemp(prefix=".brain-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        yield Path(name)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(name)


def replace_bytes(path, before, after):
    """Atomically publish a new file or replace a known, unchanged version."""
    if before == after:
        return
    with _staged(path, after) as staged:
        if before is None:
            try:
                os.link(staged, path)
            except FileExistsError as error:
                raise ConflictError("Файл уже создан. Перечитайте его.") from error
            return
        if path.is_symlink() or not path.is_file() or path.read_bytes() != before:
            raise ConflictError("Файл изменился. Перечитайте его.")
        staged.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(staged, path)


def comment_json(obj):
    # Source quotes and owner text can themselves contain an HTML comment terminator.
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e")


def markdown_label(text):
    # Keep imported text on one Markdown line even when a title contains markup.
    return re.sub(r"([\\\[\]*_`])", r"\\\1", escape(" ".join(text.splitlines()), quote=False))


def markdown_link(label, path, base):
    relative = Path(os.path.relpath(path, start=base)).as_posix()
    return f"[{markdown_label(label)}]({quote(relative)})"


def id_marker(entity, identifier):
    return f"<!-- {entity}-id: {quote(identifier, safe='')} -->"


def initialize(root: Path):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for folder in FOLDERS:
        _inside(root, folder).mkdir(parents=True, exist_ok=True)
    for filename, content in START_PAGES.items():
        path = _inside(root, filename)
        if path.exists():
            if not path.is_file():
                raise ValueError(f"Ожидался файл: {path}")
            continue
        with _staged(path, content.encode()) as staged:
            with suppress(FileExistsError):
                os.link(staged, path)
    return root


def _scalar(value):
    """Read simple YAML strings used for the record's own fields."""
    value = value.strip()
    if value.startswith('"'):
        parsed, pos = json.JSONDecoder().raw_decode(value)
        if not value[pos:].strip() or value[pos:].lstrip().startswith("#"):
            return parsed
        raise ValueError(f"Некорректное строковое поле: {value}")
    if value.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'(?:\s+#.*)?", value)
        if not match:
            raise ValueError(f"Некорректное строковое поле: {value}")
        return match[1].replace("''", "'")
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def _aliases(value):
    """Accept a flat YAML flow sequence of strings; block lists are read below."""
    if (not value.strip() or value.lstrip().startswith("#")
            or re.fullmatch(r"\s*(?:null|~)(?:\s+#.*)?\s*", value, re.I)):
        return []
    sequence = re.fullmatch(r"\s*\[(.*)\]\s*(?:#.*)?", value)
    if not sequence:
        raise ValueError(f"Ожидался список aliases: {value}")
    items = sequence[1].strip()
    aliases = []
    while items:
        item = re.match(r'''("(?:\\.|[^"\\])*"|'(?:''|[^'])*'|[^,'"[\]]+)\s*(,|$)''', items)
        if not item:
            raise ValueError(f"Некорректный алиас: {items}")
        aliases.append(_scalar(item[1]))
        items = items[item.end():].lstrip()
    return aliases


def _record_content(data, body):
    return {"body": body.decode("utf-8"), "revision": hashlib.sha256(data).hexdigest()}


def _parse_record(path, data, *, kinds=KINDS, optional_scalars=()):
    lines = data.splitlines(keepends=True)
    if not lines or lines[0].rstrip(b"\r\n") != b"---":
        raise UnmanagedMarkdown(path)
    end = next((i for i in range(1, len(lines))
                if lines[i].rstrip(b"\r\n") in (b"---", b"...")), None)
    header_lines = lines[1:end]
    if not any(line.startswith(b"id:") for line in header_lines):
        raise UnmanagedMarkdown(path)
    types = [_scalar(line[5:].decode("utf-8")) for line in header_lines if line.startswith(b"type:")]
    if not any(kind in kinds for kind in types):
        raise UnmanagedMarkdown(path)
    if end is None:
        raise ValueError(f"Не закрыта YAML-шапка: {path}")
    if "project" in types:
        optional_scalars = (*optional_scalars, *PROJECT_EVIDENCE_FIELDS)
    fields = {}
    field_lines = {}
    key = None
    for number, raw in enumerate(header_lines, 2):
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if key == "aliases" and re.match(r"^\s*-\s+", line):
            fields[key].append(_scalar(line.lstrip()[1:]))
            continue
        if line[0].isspace():
            continue
        key, sep, value = line.partition(":")
        if not sep or key not in {"id", "type", "schema_version", "creation_hash", *EDITABLE, *optional_scalars}:
            continue
        if key == "project_start" and "project" not in types:
            continue
        if key in field_lines:
            raise ValueError(f"Повтор поля {key}: {path}")
        field_lines[key] = number
        if key == "aliases":
            fields[key] = _aliases(value)
        elif key in (*STATE_FIELDS, "direction", "project_start", *optional_scalars):
            # These optional fields were user-owned in older records. Keep complex
            # YAML values opaque; only show actual one-line scalars in the overview.
            value = value.strip()
            if value and not value.startswith(("#", "|", ">", "[", "{", "!", "*", "&")):
                scalar = _scalar(value)
                if isinstance(scalar, str) and (value[0] in "'\"" or scalar.lower() not in ("null", "~")):
                    fields[key] = scalar
        else:
            fields[key] = _scalar(value)
    if fields.get("type") not in kinds or not fields.get("id"):
        raise UnmanagedMarkdown(path)
    if fields.get("schema_version") != "1" or not fields.get("title"):
        raise ValueError(f"Неподдерживаемая версия или пустое название записи: {path}")
    fields["schema_version"] = 1
    fields.setdefault("aliases", [])
    fields.setdefault("direction", None)
    fields.update(field_lines=field_lines, body_start=end + 2, **_record_content(data, b"".join(lines[end + 1:])))
    return fields, b"".join(lines[:end + 1])


def read_record(path: Path, *, include_raw=False) -> dict:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Ожидался обычный файл: {path}")
    data = path.read_bytes()
    record, _ = _parse_record(path, data)
    if include_raw:
        record["raw"] = data.decode("utf-8")
    return record


def read_header(path):
    """Read the complete frontmatter without loading the Markdown body."""
    with path.open("rb") as stream:
        return read_header_from(stream)


def read_header_from(stream):
    first = stream.readline()
    if first.rstrip(b"\r\n") != b"---":
        return b""
    header = [first]
    for line in stream:
        header.append(line)
        if line.rstrip(b"\r\n") in (b"---", b"..."):
            break
    return b"".join(header)


def iter_markdown_paths(root: Path, *, folders=None):
    root = Path(root).resolve()
    for folder in KINDS.values() if folders is None else folders:
        start = _inside(root, folder)
        if not start.is_dir():
            continue
        for current, directories, files in os.walk(start):
            directories[:] = sorted(d for d in directories if not (Path(current) / d).is_symlink())
            for name in sorted(files):
                path = Path(current) / name
                if path.suffix.lower() != ".md" or path.is_symlink() or not path.is_file():
                    continue
                yield path


def iter_records(root: Path, *, direction_ids=None):
    """When scoped, load bodies only for records belonging to the requested directions."""
    allowed = frozenset(direction_ids) if direction_ids is not None else None
    for path in iter_markdown_paths(root):
        if allowed is not None:
            header = read_header(path)
            field = b"id:" if _header_has(header, b"type:", "direction") else b"direction:"
            if not any(_header_has(header, field, identifier) for identifier in allowed):
                continue
        try:
            record = read_record(path)
        except UnmanagedMarkdown:
            continue
        if allowed is None or (record["id"] if record["type"] == "direction" else record["direction"]) in allowed:
            yield path, record


def _header_has(header, field, value):
    for line in header.splitlines():
        if line.startswith(field):
            try:
                if _scalar(line[len(field):].decode("utf-8")) == value:
                    return True
            except ValueError:
                continue
    return False


def direction_headers(root):
    """Read only direction cards, including ones moved within managed record folders."""
    seen = set()
    for path in iter_markdown_paths(root):
        header = read_header(path)
        if not _header_has(header, b"type:", "direction"):
            continue
        try:
            record, _ = _parse_record(path, header, kinds=("direction",))
        except UnmanagedMarkdown:
            continue
        if record["id"] in seen:
            raise ValueError("Один ID направления найден в нескольких карточках.")
        seen.add(record["id"])
        yield record


def find_record(root: Path, record_id: str) -> Path:
    from . import search_index
    root = Path(root).resolve()
    with search_index.optional(root, allow_sync=False) as db:
        if db is not None:
            row = search_index.find(db, record_id)
            path = search_index.record_path(root, row["path"])
            path = _inside(root, path.relative_to(root))
            if path.is_file() and _header_has(read_header(path), b"id:", record_id):
                return path
    # Missing or stale cache, or an editor moved the selected card during the lookup.
    matches = []
    for path in iter_markdown_paths(root):
        if not _header_has(read_header(path), b"id:", record_id):
            continue
        try:
            record = read_record(path)
        except UnmanagedMarkdown:
            continue
        if record["id"] == record_id:
            matches.append(path)
    if not matches:
        raise FileNotFoundError(f"Запись не найдена: {record_id}")
    if len(matches) != 1:
        raise ValueError(f"Один ID найден в нескольких файлах: {record_id}")
    return matches[0]


def _filename(title):
    # Keep user-facing titles intact in YAML, including punctuation unsafe in filenames.
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title).strip(" .")
    safe = safe.encode("utf-8")[:166].decode("utf-8", errors="ignore").rstrip(" .") or "Record"
    if re.match(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", safe, re.I):
        safe = "_" + safe
    return safe


def _validate_fields(values):
    if "title" in values and not (isinstance(values["title"], str) and values["title"].strip()):
        raise ValueError("Укажите непустое название записи.")
    if "body" in values and not isinstance(values["body"], str):
        raise ValueError("Текст записи должен быть строкой.")
    if "aliases" in values:
        aliases = values["aliases"]
        if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise ValueError("Алиасы должны быть списком непустых строк.")
    for key in ("direction", "project_start", *STATE_FIELDS):
        value = values.get(key)
        if value is not None and not (isinstance(value, str) and value.strip() and value.splitlines() == [value]):
            raise ValueError(f"Поле {key} должно содержать одну непустую строку.")


def _iso_date(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def _validate_project_dates(kind, record, changes):
    if kind != "project":
        if "project_start" in changes:
            raise ValueError("Согласованное начало указывается только в карточке проекта.")
        return
    if not any(k in changes and changes[k] != record.get(k) for k in ("project_start", "due")):
        return
    start = _iso_date(changes.get("project_start", record.get("project_start")))
    due = _iso_date(changes.get("due", record.get("due")))
    if changes.get("project_start") is not None and changes["project_start"] != record.get("project_start") and start is None:
        raise ValueError("Согласованное начало проекта должно быть календарной датой ГГГГ-ММ-ДД.")
    if start and due and start > due:
        raise ValueError("Согласованное начало проекта позже его срока.")


def _check_direction(root, kind, direction):
    if direction is not None:
        if kind == "direction" or read_record(find_record(root, direction))["type"] != "direction":
            raise ValueError("Связь должна указывать на направление для заметки или проекта.")


def create(root: Path, kind: str, title: str, body: str = "", aliases=None, direction=None,
           *, status=None, due=None, next_step=None, project_start=None, request_id=None) -> Path:
    root = Path(root).resolve()
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("Укажите тип записи.")
    fields = {"type": kind, "schema_version": 1, "title": title, "aliases": [] if aliases is None else aliases}
    optional = {"direction": direction, "status": status, "due": due, "next_step": next_step,
                "project_start": project_start}
    fields.update({key: value for key, value in optional.items() if value is not None})
    _validate_fields({**fields, "body": body})
    _validate_project_dates(kind, {}, fields)
    if request_id is None:
        fields["id"] = str(uuid.uuid4())
        return _publish_record(root, fields, body)
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise ValueError("Некорректный идентификатор запроса.")

    # A retry locates the original file by ID even after a rename or an edit.
    # Both the initial request fingerprint and its data are published in one link.
    fields["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "second-brain/create/" + request_id))
    fields["creation_hash"] = hashlib.sha256(json.dumps([fields, body], sort_keys=True).encode()).hexdigest()
    with _write_lock(root):
        try:
            previous = find_record(root, fields["id"])
        except FileNotFoundError:
            return _publish_record(root, fields, body)
        if read_record(previous).get("creation_hash") != fields["creation_hash"]:
            raise ConflictError("Идентификатор запроса уже использован с другими данными.")
        return previous


def _publish_record(root, fields, body, *, kinds=KINDS):
    kind = fields["type"]
    _check_direction(root, kind, fields.get("direction"))
    header = "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in fields.items())
    data = ("---\n" + header + "---\n" + body).encode("utf-8")
    stem = _filename(fields["title"])
    path = _inside(root, Path(kinds[kind]) / f"{stem}.md")
    with _staged(path, data) as staged:
        try:
            os.link(staged, path)
        except FileExistsError:
            path = path.with_name(f"{stem} ({fields['id']}).md")
            os.link(staged, path)
    return path


_HELD_LOCKS = threading.local()


@contextmanager
def _vault_lock(root, *, write):
    # A separate lock inode survives replacement of the record; the OS releases
    # the lock on process exit, even if the process is killed.
    lock = _inside(root, ".second-brain.lock")
    held = getattr(_HELD_LOCKS, "paths", {})
    if lock in held:
        if write and not held[lock]:
            raise RuntimeError("Нельзя изменять карточку во время чтения.")
        yield
        return
    mode = os.O_CREAT | os.O_RDWR if write else os.O_RDONLY
    try:
        fd = os.open(lock, mode | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileNotFoundError:
        if write:
            raise
        # Read-only and older vaults need no new file just to show their records.
        yield
        return
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_LOCK if write else msvcrt.LK_RLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        _HELD_LOCKS.paths = {**held, lock: write}
        yield
    finally:
        _HELD_LOCKS.paths = held
        os.close(fd)


@contextmanager
def _write_lock(root):
    with _vault_lock(root, write=True):
        from .plan_transaction import recover
        recover(root)
        yield


_QUOTES_OR_COMMENT = re.compile(rb'''"(?:\\.|[^"\\])*"|'(?:''|[^'])*'|(?:^|\s)(#.*)$''')


def editable_scalar(record, header, field):
    """Read an optional scalar for editing, refusing opaque YAML owned by the user."""
    value = record.get(field)
    number = record["field_lines"].get(field)
    if number is None:
        return value
    lines = header.decode().splitlines()
    raw = lines[number - 1].partition(":")[2].strip()
    if value is None and raw and not raw.startswith("#") and not re.fullmatch(r"(?:null|~)(?:\s+#.*)?", raw, re.I):
        raise ValueError(f"Для изменения сложного поля {field} откройте Markdown.")
    following = next((line for line in lines[number:] if line.strip() and not line.lstrip().startswith("#")), "")
    if following.startswith((" ", "\t", "- ")) or following == "-":
        raise ValueError(f"Для изменения многострочного поля {field} откройте Markdown.")
    return value


def _comment(value):
    for match in _QUOTES_OR_COMMENT.finditer(value.rstrip(b"\r\n")):
        if match[1]:
            return match[1]
    return b""


def _patch_header(header, record, changes):
    """Rewrite only requested fields; leave order, unknown YAML and comments intact."""
    if not changes:
        return header
    lines = header.splitlines(keepends=True)
    newline = b"\r\n" if lines[0].endswith(b"\r\n") else b"\n"
    output = [lines[0]]
    pending = dict(changes)
    by_line = {n: k for k, n in record["field_lines"].items()}
    active = None

    def write_field(key, comment=b""):
        value = pending.pop(key)
        if value is not None:
            line = f"{key}: {json.dumps(value, ensure_ascii=False)}".encode()
            output.append(line + (b"  " + comment if comment else b"") + newline)
        elif comment:
            output.append(comment + newline)

    for number, raw in enumerate(lines[1:-1], 2):
        key = by_line.get(number)
        if key in changes:
            active = key
            write_field(key, _comment(raw.partition(b":")[2]))
        elif not raw.strip() or raw.lstrip().startswith(b"#"):
            output.append(raw)
        elif active == "aliases" and re.match(rb"^\s*-\s", raw):
            comment = _comment(raw)
            if comment:
                output.append(comment + newline)
        elif active and raw[:1].isspace():
            raise ValueError(f"Для изменения многострочного поля {active} откройте Markdown.")
        else:
            active = None
            output.append(raw)
    for key in list(pending):
        write_field(key)
    return b"".join([*output, lines[-1]])


def edit(root: Path, record_id: str, revision: str, changes: dict) -> Path:
    """Apply an explicit patch at a known revision, without renaming its file."""
    root = Path(root).resolve()
    if changes.keys() - (EDITABLE | {"body"}):
        raise ValueError("В изменении есть неподдерживаемые поля.")
    _validate_fields(changes)
    with _write_lock(root):
        path = find_record(root, record_id)
        data = path.read_bytes()
        record, header = _parse_record(path, data)
        if record["id"] != record_id or record["revision"] != revision:
            raise ConflictError(f"Файл изменился. Перечитайте запись: {path}")
        for key in changes.keys() & {"direction", "project_start", *STATE_FIELDS}:
            editable_scalar(record, header, key)
        _validate_project_dates(record["type"], record, changes)
        if "direction" in changes and changes["direction"] != record["direction"]:
            _check_direction(root, record["type"], changes["direction"])
        metadata = {key: value for key, value in changes.items()
                    if key != "body" and value != record.get(key)}
        body = changes.get("body", record["body"])
        if not metadata and body == record["body"]:
            return path
        header = _patch_header(header, record, metadata)
        if body and not header.endswith((b"\r", b"\n")):
            header += b"\n"
        with _staged(path, header + body.encode("utf-8")) as staged:
            staged.chmod(stat.S_IMODE(path.stat().st_mode))
            if path.is_symlink() or path.read_bytes() != data:
                raise ConflictError(f"Файл изменился. Перечитайте запись: {path}")
            os.replace(staged, path)
    return path


def update(root: Path, record_id: str, body: str, revision: str) -> Path:
    return edit(root, record_id, revision, {"body": body})
