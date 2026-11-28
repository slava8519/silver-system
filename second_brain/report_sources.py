"""Line-level evidence for reports, including plain Markdown in older vaults."""

from pathlib import PurePosixPath, PureWindowsPath
import re

from . import archive_reader, evidence, storage
from .query import _line_ref, _state_sources


_PROJECT_FIELD_PREFIXES = tuple(key + ":" for key in storage.PROJECT_EVIDENCE_FIELDS)


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError("Укажите путь к Markdown-файлу внутри хранилища.")
    path = PurePosixPath(value)
    if evidence.is_original_path(value):
        return path
    if (path.is_absolute() or PureWindowsPath(value).drive or path.as_posix() != value
            or any(part.startswith(".") or ":" in part for part in path.parts) or path.suffix.lower() != ".md"):
        raise ValueError("Укажите относительный путь к Markdown-файлу хранилища без скрытых папок.")
    return path


def _identity(lines):
    rows = iter(lines)
    if next(rows, "").rstrip("\r\n") != "---":
        return None
    for row in rows:
        if row.rstrip("\r\n") in ("---", "..."):
            break
        if row.startswith("id:"):
            value = storage._scalar(row[3:])
            return value if isinstance(value, str) and value else None
    return None


def _read(root, relative, *, originals=None):
    relative = _relative(relative)
    if archive_reader.archive_path(relative):
        return archive_reader.read_path(root, relative)["raw"].splitlines()
    if evidence.is_original_path(relative.as_posix()):
        return evidence.original_lines(root, relative, cache=originals)
    path = storage._inside(root, relative)
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
    originals = {}
    for item in provided:
        if not isinstance(item, dict):
            raise ValueError("Для основания укажите путь и номер строки.")
        relative = item.get("path")
        try:
            lines = _read(root, relative, originals=originals)
        except FileNotFoundError as error:
            raise ValueError(f"Не удалось найти источник «{relative}». Проверьте путь от корня хранилища.") from error
        number = item.get("line")
        if (type(number) is int and number > 0 and item.get("id") and item.get("ref") and item.get("quote")
                and any(s.startswith(archive_reader.POINTER) for s in lines)
                and _project_field_refs(root, relative, lines) is not None):
            project = storage.read_record(root / relative)
            if (item["id"] == project["id"] and number >= project["body_start"]
                    and item.get("archivePlan") != archive_reader.latest_plan(project)):
                checked = resolve(root, item, "")
                if not checked["available"]:
                    raise storage.ConflictError("Строка источника изменилась. Перечитайте основание.")
                relative, number = checked["path"], checked["line"]
                if archive_reader.archive_path(relative):
                    item = {**item, "id": archive_reader.read_path(root, relative)["id"]}
                lines = _read(root, relative, originals=originals)
        if type(number) is not int or not 1 <= number <= len(lines):
            raise ValueError(f"Строка источника не найдена: {relative}.")
        text = lines[number - 1]
        original = evidence.is_original_path(relative)
        identity = PurePosixPath(relative).stem if original else _identity(lines)
        if archive_reader.archive_path(relative):
            archived = archive_reader.read_path(root, relative)
            if number not in archive_reader.visible_lines(archived):
                raise ValueError("Укажите строку записи в квартальном архиве.")
            identity = archived["id"]
            original_text = archive_reader.original_citation(archived, number, item.get("ref"))
            if original_text is not None:
                text = original_text
        quote = item.get("quote", text.strip())
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 600 or quote not in text:
            raise ValueError(f"Нужна точная цитата до 600 символов: {relative}, строка {number}.")
        line_ref = _line_ref(text)
        supplied_id = item.get("id", identity)
        if supplied_id != identity and not (original and supplied_id and supplied_id == _identity(lines)):
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
        if (identity and scope != "field" and any(s.startswith(archive_reader.POINTER) for s in lines)
                and _project_field_refs(root, relative, lines) is not None):
            project = storage.read_record(root / relative)
            if number >= project["body_start"]:
                plan = archive_reader.latest_plan(project)
                if plan:
                    source["archivePlan"] = plan
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


def _locate(root, saved, originals):
    relative = _relative(saved["path"])
    if archive_reader.archive_path(relative):
        part = archive_reader.read_path(root, relative)
        if saved.get("id") and saved["id"] != part["id"]:
            raise storage.ConflictError("ID квартального архива изменился.")
        lines = part["raw"].splitlines()
        number = saved["line"]
        original = archive_reader.original_citation(part, number, saved["ref"])
        if original is not None and number in archive_reader.visible_lines(part):
            lines[number-1] = original
        return saved["path"], lines
    if evidence.is_original_path(saved["path"]):
        lines = evidence.original_lines(root, relative, cache=originals)
        # Earlier Markdown reports used the original's embedded card ID, if present.
        if saved.get("id") and saved["id"] != relative.stem and saved["id"] != _identity(lines):
            raise storage.ConflictError("ID источника не соответствует сохранённому оригиналу.")
        return saved["path"], lines
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
    folder = relative.parts[0]
    folders = storage.KINDS.values() if folder in storage.KINDS.values() else (folder,)
    for candidate in storage.iter_markdown_paths(root, folders=folders):
        name = candidate.relative_to(root)
        if any(part.startswith(".") for part in name.parts):
            continue
        try:
            with candidate.open(encoding="utf-8-sig") as stream:
                identifier = _identity(stream)
            if identifier == saved["id"]:
                matches.append(name.as_posix())
                if len(matches) > 1:
                    break
        except (ValueError, OSError):
            continue
    if len(matches) != 1:
        raise FileNotFoundError(relative)
    path = matches[0]
    lines = _read(root, path)
    if _identity(lines) != saved["id"]:
        raise FileNotFoundError(relative)
    return path, lines


def resolve(root, saved, href, *, context=False, originals=None):
    """Keep the original quote on a missing, edited or ambiguously moved line."""
    result = {**saved, "available": False, "href": href}
    if context:
        result["lines"] = []
    try:
        path, lines = _locate(root, saved, originals)
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
        project = None
        if fields is not None and scope != "field" and saved.get("id") and any(
                s.startswith(archive_reader.POINTER) for s in lines):
            project = storage.read_record(root / path, include_raw=True)
        prior_rotation = (project is not None and number >= project["body_start"]
                          and archive_reader.latest_plan(project) != saved.get("archivePlan"))
        if prior_rotation:
            matches = [n for n in range(project["body_start"], len(lines) + 1) if matches_line(n)]
            selected, selected_record, number = archive_reader.citation_line(
                root, root / path, project, number, saved["ref"], matches, saved.get("archivePlan"))
            if selected_record["type"] == "project_history":
                visible = archive_reader.visible_lines(selected_record)
                original = archive_reader.original_citation(selected_record, number, saved["ref"])
                if original is not None:
                    visible[number] = original
                text = visible[number]
                if saved["quote"] not in text or (scope is None and text.startswith(_PROJECT_FIELD_PREFIXES)):
                    return result
                result.update(path=selected.relative_to(root).as_posix(), line=number, available=True)
                if context:
                    result["lines"] = [{"n": n, "text": visible[n]} for n in range(number-2, number+3) if n in visible]
                return result
        elif not 1 <= number <= len(lines) or not matches_line(number):
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
