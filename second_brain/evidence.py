"""Resolve and verify line citations to vault cards and preserved Inbox originals."""

from pathlib import PurePosixPath
import re

from . import query, storage
from .inbox import Inbox


_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря")
_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})(?!\d)"),
    re.compile(r"(?<!\d)(?P<day>\d{1,2})\s+(?P<month>" + "|".join(_MONTHS) + r")\s+(?P<year>\d{4})(?!\d)", re.I),
)
_ORIGINAL = re.compile(r"Sources/Originals/([0-9a-f]{64})\.(?:txt|md)\Z")


def date_spans(line):
    dates = {}
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(line):
            month = match["month"].lower()
            number = _MONTHS.index(month) + 1 if month in _MONTHS else int(month)
            dates[match.span()] = f'{match["year"]}-{number:02}-{int(match["day"]):02}'
    return dates


def _source_shape(relative, allow_originals):
    if not isinstance(relative, str) or not relative or "\\" in relative or any(ord(c) < 32 for c in relative):
        raise ValueError("Укажите путь к заметке или проекту внутри хранилища.")
    path = PurePosixPath(relative)
    if allow_originals and _ORIGINAL.fullmatch(relative):
        return path
    if (path.as_posix() != relative or len(path.parts) < 2 or path.parts[0] not in ("Notes", "Projects")
            or path.suffix.lower() != ".md" or any(p.startswith(".") for p in path.parts)):
        if allow_originals:
            raise ValueError("Укажите Markdown-карточку в Notes/ или Projects/ либо сохранённый текст в Sources/Originals/.")
        raise ValueError("Источник должен быть Markdown-карточкой в Notes/ или Projects/.")
    return path


def linked_source(source, *, allow_originals=False):
    # Reconstruct links locally even if someone edited metadata in their text editor.
    path = _source_shape(source["path"], allow_originals)
    if (not isinstance(source["id"], str) or not source["id"] or type(source["line"]) is not int
            or source["line"] < 1 or not re.fullmatch("[0-9a-f]{64}", source["ref"])
            or not isinstance(source["quote"], str)):
        raise ValueError("Неполная ссылка на источник в карточке.")
    if path.parts[0] == "Sources":
        if source["id"] != path.stem:
            raise ValueError("ID оригинала не совпадает с путём.")
        return {**source, "href": f'/original/{path.stem}?start={source["line"]}'}
    return {**source, "href": query._source_link(source, source["line"], source["ref"])}


def verify_source_line(root, item, *, records=None, allow_originals=False):
    """Read the caller's quote and its complete line from the same file revision."""
    if not isinstance(item, dict):
        raise ValueError("Проверьте формат ссылки на строку.")
    relative = _source_shape(item.get("path"), allow_originals)
    path = storage._inside(root, relative)
    number = item.get("line")
    if type(number) is not int or number < 1:
        raise ValueError("Укажите положительный номер строки источника.")
    if relative.parts[0] == "Sources":
        if records is not None and path in records:
            lines = records[path]
        else:
            original, lines = Inbox(root).original_text(relative.stem)
            if original != path:
                raise storage.ConflictError("Путь не совпадает с сохранённым оригиналом.")
            if records is not None:
                records[path] = lines
        if number > len(lines):
            raise ValueError("Укажите существующую строку оригинала.")
        line_text = lines[number - 1]
        return _verified(item, relative.stem, query._line_ref(line_text), line_text)
    if records is not None and path in records:
        record = records[path]
    else:
        record = storage.read_record(path, include_raw=True)
        if records is not None:
            records[path] = record
    if record["type"] not in ("note", "project"):
        raise ValueError("Укажите строку заметки или проекта.")
    if number < record["body_start"]:
        fragment = next((s for _, s in query._state_sources(root, path, record) if s["start"] == number), None)
        if not fragment:
            raise ValueError("В шапке проекта доступны только статус, срок и следующий шаг.")
    else:
        fragment = query.source_fragment(root, path, record, number)
    line_text = next(row["text"] for row in fragment["lines"] if row["n"] == number)
    return _verified(item, record["id"], fragment["ref"], line_text)


def _verified(item, identifier, fingerprint, line_text):
    quote = item.get("quote", line_text.strip())
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 600 or quote not in line_text:
        raise ValueError("Укажите точную цитату из выбранной строки (до 600 символов).")
    if (("ref" in item and item["ref"] != fingerprint)
            or ("id" in item and item["id"] != identifier)):
        raise storage.ConflictError("Строка источника изменилась. Перечитайте основание.")
    source = {"path": item["path"], "line": item["line"], "id": identifier, "ref": fingerprint, "quote": quote}
    return source, line_text


def verify_sources(root, provided, *, allow_originals=False):
    if not isinstance(provided, list) or len(provided) > 8:
        raise ValueError("Укажите до восьми ссылок на отдельные строки.")
    sources = []
    for item in provided:
        source, _ = verify_source_line(root, item, allow_originals=allow_originals)
        if source not in sources:
            sources.append(source)
    return sources


def dated_quote(date, sources):
    """Find a supplied quotation that states the complete event date."""
    for source in sources:
        if date in date_spans(source["quote"]).values():
            return source["quote"]
    return None


def require_event_date(date, sources):
    if dated_quote(date, sources) is None:
        raise ValueError("Дата события должна быть подтверждена цитатой источника.")


def resolve_source(root, reference, *, records=None, allow_originals=False):
    """Resolve a previously saved passage after a file rename or a line move."""
    ref = linked_source(reference, allow_originals=allow_originals)
    try:
        source, _ = verify_source_line(root, ref, records=records, allow_originals=allow_originals)
        return {**source, "href": ref["href"]}
    except (OSError, ValueError):
        if _ORIGINAL.fullmatch(ref["path"]):
            # An immutable original has no replacement card or moved passage.
            raise
        record, fragment = query.WorkspaceQuery(root, include_people=False).open_citation(ref)
        line = next(v["text"] for v in fragment["lines"] if v["n"] == fragment["start"])
        if ref["quote"] not in line:
            raise storage.ConflictError("Цитата в исходной записи изменилась.")
        return {**ref, "path": fragment["path"], "id": record["id"], "line": fragment["start"],
                "href": fragment["href"]}
