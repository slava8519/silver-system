"""Resolve and verify citations to specific lines of vault notes and projects."""

from pathlib import PurePosixPath
import re

from . import query, storage


_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря")
_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})(?!\d)"),
    re.compile(r"(?<!\d)(?P<day>\d{1,2})\s+(?P<month>" + "|".join(_MONTHS) + r")\s+(?P<year>\d{4})(?!\d)", re.I),
)


def date_spans(line):
    dates = {}
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(line):
            month = match["month"].lower()
            number = _MONTHS.index(month) + 1 if month in _MONTHS else int(month)
            dates[match.span()] = f'{match["year"]}-{number:02}-{int(match["day"]):02}'
    return dates


def _source_shape(relative):
    if not isinstance(relative, str) or not relative or "\\" in relative or any(ord(c) < 32 for c in relative):
        raise ValueError("Укажите путь к заметке или проекту внутри хранилища.")
    path = PurePosixPath(relative)
    if (path.as_posix() != relative or len(path.parts) < 2 or path.parts[0] not in ("Notes", "Projects")
            or path.suffix.lower() != ".md" or any(p.startswith(".") for p in path.parts)):
        raise ValueError("Источник должен быть Markdown-карточкой в Notes/ или Projects/.")
    return path


def linked_source(source):
    # Reconstruct links locally even if someone edited metadata in their text editor.
    _source_shape(source["path"])
    if (not isinstance(source["id"], str) or not source["id"] or type(source["line"]) is not int
            or source["line"] < 1 or not re.fullmatch("[0-9a-f]{64}", source["ref"])
            or not isinstance(source["quote"], str)):
        raise ValueError("Неполная ссылка на источник в карточке.")
    return {**source, "href": query._source_link(source, source["line"], source["ref"])}


def verify_source_line(root, item, *, records=None):
    """Read the caller's quote and its complete line from the same file revision."""
    if not isinstance(item, dict):
        raise ValueError("Проверьте формат ссылки на строку.")
    path = storage._inside(root, _source_shape(item.get("path")))
    if records is not None and path in records:
        record = records[path]
    else:
        record = storage.read_record(path, include_raw=True)
        if records is not None:
            records[path] = record
    number = item.get("line")
    if record["type"] not in ("note", "project") or type(number) is not int or number < 1:
        raise ValueError("Укажите положительный номер строки заметки или проекта.")
    if number < record["body_start"]:
        fragment = next((s for _, s in query._state_sources(root, path, record) if s["start"] == number), None)
        if not fragment:
            raise ValueError("В шапке проекта доступны только статус, срок и следующий шаг.")
    else:
        fragment = query.source_fragment(root, path, record, number)
    line_text = next(row["text"] for row in fragment["lines"] if row["n"] == number)
    quote = item.get("quote", line_text.strip())
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 600 or quote not in line_text:
        raise ValueError("Укажите точную цитату из выбранной строки (до 600 символов).")
    if (("ref" in item and item["ref"] != fragment["ref"])
            or ("id" in item and item["id"] != record["id"])):
        raise storage.ConflictError("Строка источника изменилась. Перечитайте основание.")
    source = {"path": path.relative_to(root).as_posix(), "line": number, "id": record["id"],
              "ref": fragment["ref"], "quote": quote}
    return source, line_text


def verify_sources(root, provided):
    if not isinstance(provided, list) or len(provided) > 8:
        raise ValueError("Укажите до восьми ссылок на отдельные строки.")
    sources = []
    for item in provided:
        source, _ = verify_source_line(root, item)
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


def resolve_source(root, reference, *, records=None):
    """Resolve a previously saved passage after a file rename or a line move."""
    ref = linked_source(reference)
    try:
        source, _ = verify_source_line(root, ref, records=records)
        return {**source, "href": ref["href"]}
    except (OSError, ValueError):
        record, fragment = query.WorkspaceQuery(root, include_people=False).open_citation(ref)
        line = next(v["text"] for v in fragment["lines"] if v["n"] == fragment["start"])
        if ref["quote"] not in line:
            raise storage.ConflictError("Цитата в исходной записи изменилась.")
        return {**ref, "path": fragment["path"], "id": record["id"], "line": fragment["start"],
                "href": fragment["href"]}
