"""Line-level evidence for reports, including plain Markdown in older vaults."""

from pathlib import PurePosixPath, PureWindowsPath
import re

from . import storage
from .query import _line_ref, _state_sources


_PROJECT_FIELD_PREFIXES = tuple(key + ":" for key in storage.STATE_FIELDS)


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError("Укажите путь к Markdown-файлу внутри хранилища.")
    path = PurePosixPath(value)
    if (path.is_absolute() or PureWindowsPath(value).drive or path.as_posix() != value
            or any(part.startswith(".") or ":" in part for part in path.parts) or path.suffix.lower() != ".md"):
        raise ValueError("Укажите относительный путь к Markdown-файлу хранилища без скрытых папок.")
    return path


def _identity(lines):
    if not lines or lines[0] != "---":
        return None
    for row in lines[1:]:
        if row in ("---", "..."):
            break
        if row.startswith("id:"):
            value = storage._scalar(row[3:])
            return value if isinstance(value, str) and value else None
    return None


def _read(root, relative):
    path = storage._inside(root, _relative(relative))
    if not path.is_file():
        raise FileNotFoundError(f"Источник недоступен: {relative}")
    return path.read_text(encoding="utf-8-sig").splitlines()


def _project_field_refs(root, relative, lines):
    """Use the same public field lines and fingerprints as a project-state citation."""
    if not lines or lines[0] != "---":
        return None
    end = next((n for n in range(1, len(lines)) if lines[n] in ("---", "...")), None)
    if end is None:
        return None
    header = "\n".join(lines[:end + 1]) + "\n"
    path = root / relative
    try:
        record, _ = storage._parse_record(path, header.encode(), kinds=("project",))
    except ValueError:
        # Free-form, unmanaged Markdown is a supported report source.
        return None
    record["raw"] = header
    return {source["start"]: source["ref"] for _, source in _state_sources(root, path, record)}


def validate_saved(item):
    if not isinstance(item, dict):
        raise ValueError("Повреждён раздел оснований выпуска.")
    _relative(item.get("path"))
    if (type(item.get("line")) is not int or item["line"] < 1
            or not isinstance(item.get("ref"), str) or not re.fullmatch("[0-9a-f]{64}", item["ref"])
            or not isinstance(item.get("quote"), str) or not 0 < len(item["quote"]) <= 600
            or "claim" in item and not isinstance(item["claim"], str)
            or "id" in item and not isinstance(item["id"], str)
            or "scope" in item and item["scope"] not in ("field", "text")):
        raise ValueError("Проверьте ссылку и цитату в разделе оснований выпуска.")


def verify(root, provided):
    if provided is None:
        return []
    if not isinstance(provided, list) or len(provided) > 60:
        raise ValueError("Укажите до 60 ссылок на строки источников.")
    result = []
    for item in provided:
        if not isinstance(item, dict):
            raise ValueError("Для основания укажите путь и номер строки.")
        relative = item.get("path")
        try:
            lines = _read(root, relative)
        except FileNotFoundError as error:
            raise ValueError(f"Не удалось найти источник «{relative}». Проверьте путь от корня хранилища.") from error
        number = item.get("line")
        if type(number) is not int or not 1 <= number <= len(lines):
            raise ValueError(f"Строка источника не найдена: {relative}.")
        text = lines[number - 1]
        quote = item.get("quote", text.strip())
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 600 or quote not in text:
            raise ValueError(f"Нужна точная цитата до 600 символов: {relative}, строка {number}.")
        identity, line_ref = _identity(lines), _line_ref(text)
        if item.get("id", identity) != identity:
            raise storage.ConflictError("Карточка источника изменилась. Перечитайте основание.")
        field_ref = scope = None
        if text.startswith(_PROJECT_FIELD_PREFIXES):
            fields = _project_field_refs(root, relative, lines)
            if fields is not None:
                field_ref = fields.get(number)
            scope = "field" if field_ref else "text"
        ref = field_ref or line_ref
        if item.get("ref", ref) not in (ref, line_ref) or item.get("scope", scope) != scope:
            raise storage.ConflictError("Строка источника изменилась. Перечитайте основание.")
        source = {"path": relative, "line": number, "quote": quote, "ref": ref}
        if scope:
            source["scope"] = scope
        if identity:
            source["id"] = identity
        claim = item.get("claim", "")
        if not isinstance(claim, str) or len(claim) > 600:
            raise ValueError("Связь источника с тезисом опишите текстом до 600 символов.")
        if claim.strip():
            source["claim"] = claim.strip()
        if source not in result:
            result.append(source)
    return result


def _locate(root, saved):
    relative = _relative(saved["path"])
    try:
        lines = _read(root, relative.as_posix())
        if not saved.get("id") or _identity(lines) == saved["id"]:
            return relative.as_posix(), lines
    except FileNotFoundError:
        pass
    # An unmanaged Markdown file has no portable identity after a rename.
    if not saved.get("id") or len(relative.parts) < 2:
        raise FileNotFoundError(relative)
    matches = []
    for candidate in storage.iter_markdown_paths(root, folders=[relative.parts[0]]):
        name = candidate.relative_to(root)
        if any(part.startswith(".") for part in name.parts):
            continue
        try:
            lines = candidate.read_text(encoding="utf-8-sig").splitlines()
            if _identity(lines) == saved["id"]:
                matches.append((name.as_posix(), lines))
        except (ValueError, OSError):
            continue
    if len(matches) != 1:
        raise FileNotFoundError(relative)
    return matches[0]


def resolve(root, saved, href, *, context=False):
    """Keep the original quote on a missing, edited or ambiguously moved line."""
    result = {**saved, "available": False, "href": href}
    if context:
        result["lines"] = []
    try:
        path, lines = _locate(root, saved)
        scope = saved.get("scope")
        fields = _project_field_refs(root, path, lines)
        legacy_project = scope is None and saved.get("id") and saved["path"].startswith("Projects/")

        def matches_line(n):
            text = lines[n - 1]
            if scope == "field":
                return fields is not None and fields.get(n) == saved["ref"]
            if text.startswith(_PROJECT_FIELD_PREFIXES) and (fields is not None or legacy_project):
                # Old issues stored a plain hash for fields and prose alike. A line
                # resembling project state can only confirm such a field in its header.
                if scope == "text" and n in fields:
                    return False
                if scope is None and (fields is None or n not in fields):
                    return False
            return _line_ref(text) == saved["ref"]

        number = saved["line"]
        if not 1 <= number <= len(lines) or not matches_line(number):
            matches = [n for n in range(1, len(lines) + 1) if matches_line(n)]
            if len(matches) != 1:
                return result
            number = matches[0]
        if saved["quote"] not in lines[number - 1]:
            return result
        result.update(path=path, line=number, available=True)
        if context:
            result["lines"] = [{"n": i + 1, "text": lines[i]}
                               for i in range(max(0, number - 3), min(len(lines), number + 2))]
    except (ValueError, TypeError, OSError):
        pass
    return result
