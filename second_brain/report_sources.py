"""Line-level evidence for reports, including plain Markdown in older vaults."""

from pathlib import PurePosixPath, PureWindowsPath
import re

from . import storage
from .query import _line_ref


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


def validate_saved(item):
    if not isinstance(item, dict):
        raise ValueError("Повреждён раздел оснований выпуска.")
    _relative(item.get("path"))
    if (type(item.get("line")) is not int or item["line"] < 1
            or not isinstance(item.get("ref"), str) or not re.fullmatch("[0-9a-f]{64}", item["ref"])
            or not isinstance(item.get("quote"), str) or not 0 < len(item["quote"]) <= 600
            or "claim" in item and not isinstance(item["claim"], str)
            or "id" in item and not isinstance(item["id"], str)):
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
        identity, ref = _identity(lines), _line_ref(text)
        if item.get("id", identity) != identity:
            raise storage.ConflictError("Карточка источника изменилась. Перечитайте основание.")
        if item.get("ref", ref) != ref:
            # Existing search citations for project state use a field-specific fingerprint.
            key = text.partition(":")[0]
            if (not relative.startswith("Projects/") or key not in storage.STATE_FIELDS
                    or item["ref"] != _line_ref(f"project-field\0{key}\0{text}")):
                raise storage.ConflictError("Строка источника изменилась. Перечитайте основание.")
        source = {"path": relative, "line": number, "quote": quote, "ref": ref}
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
        number = saved["line"]
        if not 1 <= number <= len(lines) or _line_ref(lines[number - 1]) != saved["ref"]:
            matches = [n for n, text in enumerate(lines, 1) if _line_ref(text) == saved["ref"]]
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
