"""Arrival dates and a compact, source-linked history of distinct Inbox materials."""

from collections import Counter, defaultdict
from datetime import date, datetime
from functools import lru_cache
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode

from . import brief, storage
from .inbox import Inbox, EXTENSIONS, _ARCHIVE_SUFFIX, _file_digest


KINDS = ("meeting", "report", "thread", "recipe", "image", "attachment", "other")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
_DATE = re.compile(r"(?<!\d)(\d{1,2}) (" + "|".join(_MONTHS) + r") (\d{4})(?!\d)")
_MEETING_DATE_PREFIX = re.compile(
    r'^(?:транскрипция записи\s*(?:["«].*["»]\s*)?|.{0,30}начало встречи \(календарь\):\s*)', re.I)
_PATTERNS = (
    ("thread", r"\b(?:тред\w*|перепис\w*|thread|чат)\b"),
    ("meeting", r"\b(?:созвон\w*|синк\w*|план[её]рк\w*|транскрип\w*|расшифровк\w*|протокол\w*|встреч[аиу]|transcript|meeting|sync)\b"),
    ("report", r"\b(?:отч[её]т\w*|дайджест\w*|report|digest)\b"),
    ("recipe", r"\b(?:рецепт\w*|формул[аыу]|recipe)\b"),
)
_UNDATED = "undated"


def _folder_dates(files):
    """Finder's date added to the containing directory. mtime/birthtime can predate arrival."""
    result = {}
    if sys.platform != "darwin":
        return result
    files = list(files)
    for offset in range(0, len(files), 64):
        chunk = files[offset:offset + 64]
        try:
            output = subprocess.run(
                ["/usr/bin/mdls", "-raw", "-name", "kMDItemDateAdded", *chunk],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=True,
            ).stdout.split(b"\0")
        except (OSError, subprocess.SubprocessError):
            continue
        if len(output) != len(chunk):
            continue
        for path, raw in zip(chunk, output):
            try:
                result[path] = datetime.strptime(raw.decode("ascii"), "%Y-%m-%d %H:%M:%S %z").astimezone()
            except ValueError:
                pass
    return result


def record_arrival(root, path, digest, *, received_at=None, basis="capture"):
    """Keep the first recorded intake date. Called under the writer's vault lock."""
    if not _DIGEST.fullmatch(digest):
        raise ValueError("Некорректный SHA-256 входящего.")
    if basis == "capture" and storage._inside(root, f"Sources/Processed/{digest}.md").is_file():
        return  # A new upload cannot date an earlier review, including an unfinished one.
    folder = storage._inside(root, "Sources/Arrivals")
    folder.mkdir(parents=True, exist_ok=True)
    record = storage._inside(folder, digest + ".md")
    if record.exists():
        return
    received_at = received_at or datetime.now().astimezone()
    body = ("---\ntype: inbox_arrival\nschema_version: 1\n"
            f'source_id: "{digest}"\ninbox_path: {json.dumps(path, ensure_ascii=False)}\n'
            f'received_at: "{received_at.isoformat(timespec="seconds")}"\ndate_basis: {basis}\n---\n')
    storage.replace_bytes(record, None, body.encode())


def remember_inbox_date(root, incoming, digest):
    """Preserve a known folder-arrival date before the queue copy is moved."""
    if storage._inside(root, f"Sources/Arrivals/{digest}.md").exists():
        return
    stamp = _folder_dates([incoming]).get(incoming)
    receipt = storage._inside(root, f"Sources/Processed/{digest}.md")
    if stamp and _same_arrival(receipt, incoming.relative_to(root), stamp):
        record_arrival(root, incoming.relative_to(root).as_posix(), digest, received_at=stamp, basis="folder")


def _metadata(path):
    values = {}
    for row in storage.read_header(path).decode().splitlines()[1:-1]:
        field, _, value = row.partition(":")
        if field in {"type", "source_id", "inbox_path", "received_at", "date_basis"}:
            values[field] = storage._scalar(value)
    return values


def _same_arrival(receipt, name, stamp):
    # An old review followed by a new copy, possibly under another name,
    # supplies no evidence for the intake date of the originally reviewed file.
    if not receipt.exists():
        return True
    return (stamp.timestamp() <= receipt.stat().st_mtime and
            _metadata(receipt).get("inbox_path") == name.as_posix())


def _headers(root, relative, kind):
    folder = storage._inside(root, relative)
    for path in sorted(folder.glob("*.md")):
        if path.is_symlink() or not _DIGEST.fullmatch(path.stem) or not path.is_file():
            continue
        try:
            values = _metadata(path)
            if values.get("type") != kind or values.get("source_id") != path.stem:
                continue
            name = Path(values["inbox_path"])
            if name.parts[:1] != ("Inbox",) or len(name.parts) < 2 or any(p.startswith(".") for p in name.parts):
                continue
            yield path.stem, name, values
        except (KeyError, ValueError, TypeError, FileNotFoundError):
            continue


@lru_cache(maxsize=2048)
def _active_digest(path, fingerprint):
    # The metadata only keys the cache. The content hash identifies the source.
    return _file_digest(path)[0]


def _kind(name, heading):
    def detect(text):
        for kind, pattern in _PATTERNS:
            if re.search(pattern, text, re.I):
                return kind
        return None

    # A transcript export identifies itself even when saved under its meeting topic.
    if re.match(r"^транскрипция записи\b", heading, re.I):
        return "meeting"
    named = detect(name.stem.replace("_", " "))
    if named:
        return named
    heading_kind = detect(heading)
    if heading_kind:
        return heading_kind
    if name.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".tif", ".tiff"}:
        return "image"
    return "other" if name.suffix.lower() in EXTENSIONS else "attachment"


def _describe(name, source):
    lines = []
    if source and source.suffix.lower() in EXTENSIONS:
        with source.open("rb") as stream:
            lines = stream.read(4096).decode("utf-8-sig", errors="replace").splitlines()
    heading = next((line.strip(" #*\t") for line in lines if line.strip()), "")[:300]
    kind = _kind(name, heading)
    details = {"title": name.name, "kind": kind}
    if kind == "meeting":
        for line in lines[:8]:
            prefix = _MEETING_DATE_PREFIX.match(line)
            if not prefix:
                continue
            match = _DATE.match(line.lower(), prefix.end())
            if match:
                try:
                    details["meetingDate"] = date(int(match[3]), _MONTHS.index(match[2]) + 1, int(match[1])).isoformat()
                    break
                except ValueError:
                    continue
    return details


def _materials(root):
    service = Inbox(root)
    names = {}
    arrivals = {}
    for digest, name, fields in _headers(root, "Sources/Arrivals", "inbox_arrival"):
        try:
            stamp = datetime.fromisoformat(fields["received_at"])
            if stamp.utcoffset() is None:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        arrivals[digest] = (stamp, fields.get("date_basis", "capture"))
        names[digest] = name
    reviewed = set()
    for digest, name, _ in _headers(root, "Sources/Processed", "processed_source"):
        names.setdefault(digest, name)
        reviewed.add(digest)
    originals = {}
    for path in storage._inside(root, "Sources/Originals").iterdir():
        if path.stem in reviewed and _ARCHIVE_SUFFIX.fullmatch(path.suffix):
            # A duplicate or unsafe original cannot provide a unique document link.
            unique = path.stem not in originals and not path.is_symlink() and path.is_file()
            originals[path.stem] = path if unique else None
    active = {}
    for path in service._files():
        try:
            info = path.stat()
            digest = _active_digest(path, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        except FileNotFoundError:
            continue
        names.setdefault(digest, path.relative_to(root))
        if digest not in active or path.relative_to(root) == names[digest]:
            active[digest] = path
    external_dates = _folder_dates(p for d, p in active.items() if d not in arrivals and p.relative_to(root) == names[d])
    for digest, name in names.items():
        if name.parts[:2] == ("Inbox", "Transcripts") and _DIGEST.fullmatch(name.stem):
            continue  # The batch-generated transcript accompanies the original attachment.
        current = active.get(digest)
        source = originals.get(digest) or current
        try:
            item = _describe(name, source)
        except FileNotFoundError:  # An external editor has moved the file during this view.
            continue
        stamp, basis = arrivals.get(digest, (None, None))
        if stamp:
            item["dateSource"] = f"Sources/Arrivals/{digest}.md"
        elif current in external_dates and _same_arrival(service._receipt(digest), current.relative_to(root),
                                                        external_dates[current]):
            stamp, basis = external_dates[current], "folder"
            item["dateSource"] = current.relative_to(root).as_posix()
        item["day"] = stamp.date().isoformat() if stamp else None
        if stamp:
            item["dateBasis"] = basis
        if source:
            item["path"] = source.relative_to(root).as_posix()
            if originals.get(digest):
                route = "/original/" if source.suffix.lower() in EXTENSIONS else "/attachment/"
                item["href"] = route + digest
            elif source.suffix.lower() in EXTENSIONS:
                item["href"] = "#inbox?" + urlencode({"path": item["path"]})
        yield item


def inbox_activity(root, *, kind=None, day=None, limit=3, offset=0):
    if kind and kind not in KINDS:
        raise ValueError("Неизвестный вид входящего.")
    if day and day != _UNDATED:
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError
        except ValueError:
            raise ValueError("Укажите день в формате ГГГГ-ММ-ДД или undated.") from None
    if not 1 <= limit <= 20 or offset < 0:
        raise ValueError("Укажите от 1 до 20 результатов и неотрицательное смещение.")
    root = Path(root).resolve()
    with storage._vault_lock(root, write=False):
        rows = [r for r in _materials(root) if not kind or r["kind"] == kind]
    # The first examples include the newest dated meeting, even in a bulk upload.
    rows.sort(key=lambda r: r["title"].casefold())
    rows.sort(key=lambda r: r.get("meetingDate", ""), reverse=True)
    if day:
        return brief.page((r for r in rows if (r["day"] or _UNDATED) == day), limit, offset)
    days = defaultdict(list)
    for item in rows:
        days[item["day"] or ""].append(item)
    result = brief.page(({"day": key or None, "count": len(days[key]),
                          "kinds": dict(Counter(r["kind"] for r in days[key]).most_common()),
                          "example": days[key][0]["title"]}
                         for key in sorted(days, reverse=True)), limit, offset)
    result["latestMeeting"] = next((row for row in rows if row.get("meetingDate")), None)
    return result
