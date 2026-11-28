"""Vault-native report issues, calendar series and append-only template versions."""

import calendar
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import quote
import uuid

from . import brief, report_sources, storage
from .markdown_sections import fenced_lines
from .report_defaults import BUILTIN_TEMPLATES
from .weekly_report import _issue_catalog

TEMPLATES = "Settings/Templates/Reports"
SERIES = "Settings/Formats/ReportSeries"
_CADENCE_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14}
_CADENCE_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}
_FOOTER = "<!-- second-brain:report-evidence -->"
_END = "<!-- /second-brain:report-evidence -->"
_SOURCE = "<!-- report-source "
_TEMPLATE_FIELDS = ("version", "audience", "guidance", "parent_revision")
_ISSUE_FIELDS = ("template_id", "template_version", "template_revision", "series_id", "period_start",
                 "period_end", "created_at", "as_of", "release_date",
                 "editorial_status", "finalized_at", "publication_status", "draft")
_SERIES_FIELDS = ("template_id", "cadence", "period_start")


def _text(value, name, maximum, *, line=False, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"Проверьте поле «{name}» (до {maximum} символов).")
    if "\x00" in value or (line and value.splitlines() != [value] and value):
        raise ValueError(f"Поле «{name}» должно содержать обычный текст" + (" в одну строку." if line else "."))
    return value.strip() if line else value.strip("\r\n")


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}", value):
        raise ValueError("Проверьте идентификатор.")
    return value


def _version(value):
    if type(value) is int and 1 <= value <= 999999999:
        return value
    if isinstance(value, str) and re.fullmatch("[1-9][0-9]{0,8}", value):
        return int(value)
    raise ValueError("Номер версии должен быть положительным целым числом.")


def _request_id(kind, token):
    if token is None:
        return str(uuid.uuid4())
    token = _text(token, "Идентификатор запроса", 128, line=True)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"second-brain/{kind}/{token}"))


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _bytes(fields, body):
    header = "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in fields.items() if v is not None)
    return ("---\n" + header + "---\n" + body).encode()


def _page(items, limit, offset):
    if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
        raise ValueError("Укажите размер страницы от 1 до 20 и неотрицательное смещение.")
    page = brief.page(items, limit, offset)
    return {**page, "limit": limit, "offset": offset, "nextOffset": offset + limit if page["truncated"] else None}


def _template_bytes(identifier, version, values, parent=None, creation_hash=None):
    fields = {"type": "report_template", "schema_version": 1, "id": identifier, "version": version,
              **{k: values[k] for k in ("title", "audience", "guidance")},
              "parent_revision": parent, "creation_hash": creation_hash}
    return _bytes(fields, values["body"].rstrip("\r\n") + "\n")


def _template_path(root, identifier, version):
    return storage._inside(root, Path(TEMPLATES) / _id(identifier) / f"v{_version(version)}.md")


def _template_origin(identifier):
    return "builtin" if identifier in BUILTIN_TEMPLATES else "custom"


def _template_record(root, identifier, version):
    path = _template_path(root, identifier, version)
    if path.is_file():
        data = path.read_bytes()
        relative = path.relative_to(root).as_posix()
    elif identifier in BUILTIN_TEMPLATES and version == 1:
        data = _template_bytes(identifier, 1, BUILTIN_TEMPLATES[identifier])
        relative = None
    else:
        raise FileNotFoundError("Версия шаблона не найдена.")
    rec, _ = storage._parse_record(path, data, kinds=("report_template",), optional_scalars=_TEMPLATE_FIELDS)
    if rec["id"] != identifier or _version(rec.get("version")) != version:
        raise storage.ConflictError(f"Свойства версии не соответствуют файлу: {path.name}.")
    return {"id": identifier, "origin": _template_origin(identifier),
            "title": rec["title"], "version": version, "revision": rec["revision"],
            "audience": rec.get("audience", ""), "guidance": rec.get("guidance", ""),
            "body": rec["body"].rstrip("\r\n"), "path": relative}, rec


def _versions(root, identifier):
    directory = _template_path(root, identifier, 1).parent
    versions = {1} if identifier in BUILTIN_TEMPLATES else set()
    if directory.is_dir():
        for item in directory.iterdir():
            match = re.fullmatch(r"v([1-9][0-9]{0,8})\.md", item.name)
            if match:
                versions.add(int(match[1]))
    if not versions:
        raise FileNotFoundError("Шаблон не найден.")
    return sorted(versions, reverse=True)


def _freeze_builtin(root, identifier):
    if identifier in BUILTIN_TEMPLATES:
        path = _template_path(root, identifier, 1)
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            storage.replace_bytes(path, None, _template_bytes(identifier, 1, BUILTIN_TEMPLATES[identifier]))


def get_template(root, id, version=None):
    root = Path(root).resolve()
    with storage._vault_lock(root, write=False):
        versions = _versions(root, id)
        chosen = versions[0] if version is None else _version(version)
        detail = _template_record(root, id, chosen)[0]
        history = []
        for n in versions:
            if n == chosen:
                title = detail["title"]
            else:
                path = _template_path(root, id, n)
                if path.is_file():
                    record, _ = storage._parse_record(path, storage.read_header(path), kinds=("report_template",))
                    title = record["title"]
                else:
                    title = BUILTIN_TEMPLATES[id]["title"]
            history.append({"version": n, "title": title})
        return {**detail, "history": history}


def list_templates(root, limit=20, offset=0, *, origin=None, match=None):
    root = Path(root).resolve()
    if origin not in (None, "builtin", "custom"):
        raise ValueError("Выберите происхождение шаблона: builtin или custom.")
    term = _text(match, "Поиск шаблона", 180, line=True, required=False).casefold() if match is not None else ""
    with storage._vault_lock(root, write=False):
        folder = storage._inside(root, TEMPLATES)
        ids = dict.fromkeys(BUILTIN_TEMPLATES)
        if folder.is_dir():
            for path in sorted(folder.iterdir()):
                if path.is_dir() and not path.is_symlink() and re.fullmatch(r"[\w-]+", path.name, re.ASCII):
                    if any(re.fullmatch(r"v[1-9][0-9]*\.md", p.name) for p in path.iterdir()):
                        ids[path.name] = None
        rows = []
        for identifier in ids:
            if origin is not None and _template_origin(identifier) != origin:
                continue
            version = _versions(root, identifier)[0]
            template = _template_record(root, identifier, version)[0]
            if term and not any(term in template[k].casefold() for k in ("title", "audience")):
                continue
            rows.append({k: template[k] for k in ("id", "origin", "title", "audience", "version", "revision", "path")})
        return _page(rows, limit, offset)


def save_template(root, *, title, audience, guidance, body, id=None, revision=None, request_id=None):
    root = Path(root).resolve()
    values = {"title": _text(title, "Название", 180, line=True),
              "audience": _text(audience, "Для кого", 600, line=True, required=False),
              "guidance": _text(guidance, "Редакционные правила", 16000, required=False),
              "body": _text(body, "Структура", 120000)}
    identifier = _request_id("report-template", request_id) if id is None else _id(id)
    with storage._write_lock(root):
        if id is None:
            if revision is not None:
                raise ValueError("Для правки существующего шаблона нужен его ID.")
            version = 1
            path = _template_path(root, identifier, version)
            if path.exists():
                _, stored = _template_record(root, identifier, 1)
                if stored.get("creation_hash") != _hash(values):
                    raise storage.ConflictError("Запрос создания шаблона уже использован с другими данными.")
                return get_template(root, identifier, 1)
        else:
            previous = _versions(root, identifier)[0]
            current, stored = _template_record(root, identifier, previous)
            identical = all(current[k] == v for k, v in values.items())
            if revision != current["revision"]:
                if revision and revision == stored.get("parent_revision") and identical:
                    return get_template(root, identifier, previous)
                raise storage.ConflictError("Шаблон изменился. Перечитайте последнюю версию перед сохранением.")
            if identical:
                return get_template(root, identifier, previous)
            _freeze_builtin(root, identifier)
            version = previous + 1
            path = _template_path(root, identifier, version)
            if _template_record(root, identifier, previous)[0]["revision"] != revision:
                raise storage.ConflictError("Версия на диске изменилась во время сохранения.")
        path.parent.mkdir(parents=True, exist_ok=True)
        storage.replace_bytes(path, None, _template_bytes(identifier, version, values, revision,
                                                         _hash(values) if id is None else None))
        return get_template(root, identifier, version)


def _records(root, folder, kind, fields):
    """List compact headers without opening issue prose or mistaking a free-form note for a report."""
    rows = {}
    for path in storage.iter_markdown_paths(root, folders=(folder,)):
        data = storage.read_header(path)
        if not re.search(rb"(?m)^type:[ \t]*[\"']?" + kind.encode() + rb"[\"']?[ \t]*(?:#.*)?\r?$", data):
            continue
        rec, _ = storage._parse_record(path, data, kinds=(kind,), optional_scalars=fields)
        if rec["id"] in rows:
            raise storage.ConflictError(f"Один ID найден в нескольких файлах: {rec['id']}.")
        rows[rec["id"]] = {**rec, "path": path.relative_to(root).as_posix()}
    return rows


def _day(value):
    day = storage._iso_date(value)
    if day is None:
        raise ValueError("Укажите календарную дату в формате ГГГГ-ММ-ДД.")
    return day


def _boundary(anchor, cadence, index):
    try:
        if cadence in _CADENCE_DAYS:
            return anchor + timedelta(days=index * _CADENCE_DAYS[cadence])
        month_number = anchor.year * 12 + anchor.month - 1 + _CADENCE_MONTHS[cadence] * index
        year, month = divmod(month_number, 12)
        return date(year, month + 1, min(anchor.day, calendar.monthrange(year, month + 1)[1]))
    except (OverflowError, KeyError, ValueError):
        raise ValueError("Период серии выходит за доступный диапазон календаря.")


def _period_index(anchor, cadence, start):
    if cadence in _CADENCE_DAYS:
        index = (start - anchor).days // _CADENCE_DAYS[cadence]
    else:
        months = (start.year - anchor.year) * 12 + start.month - anchor.month
        index = months // _CADENCE_MONTHS[cadence]
    if index < 0 or _boundary(anchor, cadence, index) != start:
        raise ValueError("Начало периода должно совпадать с календарём серии.")
    return index


def _series_view(row, issues):
    cadence = row["cadence"]
    if cadence not in (*_CADENCE_DAYS, *_CADENCE_MONTHS):
        raise ValueError(f"Проверьте периодичность серии в {row['path']}.")
    anchor = _day(row["period_start"])
    starts = {r.get("period_start") for r in issues.values() if r.get("series_id") == row["id"]}
    index = 0
    while _boundary(anchor, cadence, index).isoformat() in starts:
        index += 1
    return {"id": row["id"], "title": row["title"], "templateId": row["template_id"], "cadence": cadence,
            "start": row["period_start"], "nextStart": _boundary(anchor, cadence, index).isoformat(),
            "nextEnd": (_boundary(anchor, cadence, index + 1) - timedelta(days=1)).isoformat(), "path": row["path"]}


def list_series(root, limit=20, offset=0, due_on=None):
    root = Path(root).resolve()
    if due_on is not None:
        due_on = _day(due_on).isoformat()
    with storage._vault_lock(root, write=False):
        records = _records(root, SERIES, "report_series", _SERIES_FIELDS)
        issues = _records(root, "Reports", "report_issue", _ISSUE_FIELDS)
        rows = [_series_view(row, issues) for row in sorted(records.values(), key=lambda r: r["title"].casefold())]
        return _page((r for r in rows if due_on is None or r["nextEnd"] <= due_on), limit, offset)


def get_series(root, id):
    root = Path(root).resolve()
    with storage._vault_lock(root, write=False):
        row = _records(root, SERIES, "report_series", _SERIES_FIELDS).get(_id(id))
        if row is None:
            raise FileNotFoundError("Серия не найдена.")
        return _series_view(row, _records(root, "Reports", "report_issue", _ISSUE_FIELDS))


def create_series(root, *, title, template_id, cadence, start, request_id=None):
    root = Path(root).resolve()
    title = _text(title, "Название", 180, line=True)
    if not isinstance(cadence, str) or cadence not in (*_CADENCE_DAYS, *_CADENCE_MONTHS):
        raise ValueError("Выберите периодичность серии.")
    _boundary(_day(start), cadence, 1)
    identifier = _request_id("report-series", request_id)
    key = _hash([title, _id(template_id), cadence, start])
    with storage._write_lock(root):
        records = _records(root, SERIES, "report_series", _SERIES_FIELDS)
        if identifier in records:
            if records[identifier].get("creation_hash") != key:
                raise storage.ConflictError("Запрос создания серии уже использован с другими данными.")
            return get_series(root, identifier)
        _template_record(root, template_id, _versions(root, template_id)[0])
        _freeze_builtin(root, template_id)
        storage._inside(root, SERIES).mkdir(parents=True, exist_ok=True)
        fields = {"type": "report_series", "schema_version": 1, "id": identifier, "title": title,
                  "template_id": template_id, "cadence": cadence, "period_start": start, "creation_hash": key}
        storage._publish_record(root, fields, "# " + storage.markdown_label(title) + "\n", kinds={"report_series": SERIES})
        return get_series(root, identifier)


def _editorial_status(row):
    if row.get("publication_status") == "posted":
        return "posted"
    if "editorial_status" in row["field_lines"]:
        status = row.get("editorial_status")
        return status if status in ("draft", "final") else None
    if "publication_status" in row["field_lines"]:
        return "draft" if row.get("publication_status") == "draft" else None
    if (row.get("draft") or "").lower() == "true":
        return "draft"
    return None


def _issue_summary(row):
    status = _editorial_status(row)
    return {"id": row["id"], "title": row["title"], "kind": "report_issue", "path": row["path"],
            "templateId": row["template_id"], "templateVersion": _version(row["template_version"]),
            "seriesId": row.get("series_id"), "start": row.get("period_start"), "end": row.get("period_end"),
            "createdAt": row.get("created_at"), "asOf": row.get("as_of"),
            **({"editorialStatus": status} if status else {}),
            **({"finalizedAt": row["finalized_at"]} if row.get("finalized_at") else {}),
            **({"releaseDate": row["release_date"]} if row.get("release_date") else {})}


def _legacy_summary(row):
    return {**row, "kind": "weekly_review", "templateId": None, "templateVersion": None, "seriesId": None,
            "createdAt": row.get("closedAt"), "asOf": None}


def list_issues(root, *, series_id=None, match=None, status=None, limit=20, offset=0):
    root = Path(root).resolve()
    if status not in (None, "draft", "final", "posted"):
        raise ValueError("Неизвестный статус выпуска.")
    term = _text(match, "Поиск по названию", 180, line=True, required=False).casefold() if match is not None else ""
    with storage._vault_lock(root, write=False):
        rows = _records(root, "Reports", "report_issue", _ISSUE_FIELDS)
        items = [_issue_summary(r) for r in rows.values() if series_id is None or r.get("series_id") == series_id]
        if status is not None:
            items = [item for item in items if item.get("editorialStatus") == status]
        elif series_id is None:
            items.extend(_legacy_summary(r) for r in _issue_catalog(root)[0])
        if term:
            items = [item for item in items if term in item["title"].casefold()]
        items.sort(key=lambda r: (r.get("releaseDate") or r.get("end") or (r.get("createdAt") or "")[:10],
                                  r.get("createdAt") or "", r["id"]),
                   reverse=True)
        return _page(items, limit, offset)


def issue_content(record):
    """Parse one issue's editable Markdown and its stored source metadata."""
    lines = record["body"].splitlines(keepends=True)
    first = record["body_start"]
    code, unclosed = fenced_lines(lines)
    # Releases already in vaults can end the author's text with an open fence.
    # The writer always follows it with its own template link and source appendix.
    appendix = next((i for i in range(len(lines) - 3, unclosed, -1)
                     if lines[i].rstrip("\r\n") == _FOOTER and not lines[i+1].strip()
                     and lines[i+2].startswith("Шаблон выпуска — [")), len(lines))
    start = end = None
    saved = []
    for index, raw in enumerate(lines):
        if index in code and not (start is None and index == appendix or start == appendix and end is None):
            continue
        line = raw.rstrip("\r\n")
        if line == _FOOTER:
            if start is not None:
                raise ValueError(f"Раздел оснований выпуска открыт повторно, строка {first + index}.")
            start = index
        elif line == _END:
            if start is None or end is not None:
                raise ValueError(f"Лишняя закрывающая отметка раздела оснований, строка {first + index}.")
            end = index
        elif start is not None and end is None and line.startswith(_SOURCE):
            try:
                if not line.endswith(" -->"):
                    raise ValueError
                source = json.loads(line[len(_SOURCE):-4])
                report_sources.validate_saved(source)
            except ValueError as error:
                raise ValueError(f"Повреждена запись основания выпуска, строка {first + index}.") from error
            saved.append(source)
    if start is not None and end is None:
        raise ValueError(f"В выпуске не закрыт раздел оснований, строка {first + start}.")
    if start is not None:
        del lines[start:end + 1]
    return "".join(lines).strip("\r\n"), saved


def _extract_issue(root, id):
    row = _records(root, "Reports", "report_issue", _ISSUE_FIELDS).get(_id(id))
    if row is None:
        for issue in _issue_catalog(root)[0]:
            if issue["id"] == id:
                path = storage._inside(root, issue["path"])
                record, _ = storage._parse_record(path, path.read_bytes(), kinds=("weekly_review",))
                return {**_legacy_summary(issue), "revision": record["revision"], "body": record["body"].strip("\r\n")}, []
        raise FileNotFoundError("Выпуск не найден.")
    path = storage._inside(root, row["path"])
    record, _ = storage._parse_record(path, path.read_bytes(), kinds=("report_issue",), optional_scalars=_ISSUE_FIELDS)
    body, saved = issue_content(record)
    return {**_issue_summary({**record, "path": row["path"]}), "body": body,
            "revision": record["revision"], "templateRevision": record.get("template_revision")}, saved


def _source_href(identifier, index):
    return f"/report-source/{quote(identifier, safe='')}?index={index}#cited-line"


def get_issue(root, id):
    root = Path(root).resolve()
    with storage._vault_lock(root, write=False):
        issue, saved = _extract_issue(root, id)
        originals = {}
        issue["sources"] = [report_sources.resolve(root, s, _source_href(id, i), originals=originals)
                            for i, s in enumerate(saved)]
        if issue["kind"] == "report_issue":
            try:
                path = _template_path(root, issue["templateId"], issue["templateVersion"])
                issue["templateChanged"] = sha256(path.read_bytes()).hexdigest() != issue["templateRevision"]
            except (OSError, ValueError):
                issue["templateChanged"] = True
        return issue


def read_issue_source(root, id, index):
    root = Path(root).resolve()
    with storage._vault_lock(root, write=False):
        _, saved = _extract_issue(root, id)
        if type(index) is not int or not 0 <= index < len(saved):
            raise FileNotFoundError("Основание выпуска не найдено.")
        return report_sources.resolve(root, saved[index], _source_href(id, index), context=True)


def _render_issue(root, body, template, sources):
    folder = storage._inside(root, "Reports")
    link = storage.markdown_link(template["title"] + f" · v{template['version']}", root / template["path"], folder)
    lines = [body, "", _FOOTER, "", "Шаблон выпуска — " + link, ""]
    if sources:
        lines += ["## Основания выпуска", ""]
    for item in sources:
        label = f"{item['path']} · строка {item['line']}"
        link = storage.markdown_link(label, root / item["path"], folder)
        claim = storage.markdown_label(item["claim"]) + " — " if item.get("claim") else ""
        lines += [f"- {claim}«{storage.markdown_label(item['quote'])}» · {link}",
                  _SOURCE + storage.comment_json(item) + " -->"]
    return "\n".join([*lines, "", _END, ""])


def save_issue(root, *, title, body, template_id, template_version, sources=None, series_id=None,
               start=None, end=None, request_id=None, template_revision=None, as_of=None, draft=False):
    root = Path(root).resolve()
    if type(draft) is not bool:
        raise ValueError("Для поля draft укажите true или false.")
    title = _text(title, "Название", 180, line=True)
    body = _text(body, "Текст выпуска", 120000)
    body_lines = body.splitlines()
    code, _ = fenced_lines(body_lines)
    if any(n not in code and (_FOOTER in line or _END in line) for n, line in enumerate(body_lines)):
        raise ValueError("В текст выпуска попала служебная разметка источников.")
    if not any(line.strip() and not re.match(r"^\s*#{1,6}(?:\s|$)", line) for line in body.splitlines()):
        raise ValueError("Добавьте текст выпуска под заголовками.")
    if start is not None or end is not None or series_id is not None:
        first, last = _day(start), _day(end)
        if first > last:
            raise ValueError("Конец периода должен быть не раньше начала.")
    version = _version(template_version)
    identifier = _request_id("report-issue", request_id)
    if series_id is not None:
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"second-brain/report-period/{_id(series_id)}/{start}"))
    fingerprint = [title, body, _id(template_id), version, sources or [], series_id, start, end]
    if as_of is not None:
        as_of = _day(as_of).isoformat()
        fingerprint.append(as_of)
    if draft:
        fingerprint.append({"draft": True})
    key = _hash(fingerprint)
    with storage._write_lock(root):
        folder = storage._inside(root, "Reports")
        if not folder.is_dir():
            raise ValueError("В выбранном хранилище нужна папка Reports.")
        rows = _records(root, "Reports", "report_issue", _ISSUE_FIELDS)
        _, claimed = _issue_catalog(root)
        old = rows.get(identifier)
        if old:
            if len(claimed.get(identifier, ())) != 1 or old.get("creation_hash") != key:
                raise storage.ConflictError("Выпуск для этого периода или запроса уже существует с другими данными.")
            return {**get_issue(root, identifier), "replayed": True}
        if claimed.get(identifier):
            raise storage.ConflictError("Этот ID уже записан в Reports. Проверьте свойства существующего выпуска.")
        if series_id is not None:
            series = _records(root, SERIES, "report_series", _SERIES_FIELDS).get(series_id)
            if series is None:
                raise FileNotFoundError("Серия не найдена.")
            if series["template_id"] != template_id:
                raise ValueError("Выберите шаблон этой серии.")
            anchor, cadence = _day(series["period_start"]), series["cadence"]
            index = _period_index(anchor, cadence, first)
            if last != _boundary(anchor, cadence, index + 1) - timedelta(days=1):
                raise ValueError("Конец периода должен совпадать с календарём серии.")
        selected = _template_record(root, template_id, version)[0]
        if template_revision is not None and template_revision != selected["revision"]:
            raise storage.ConflictError("Файл выбранной версии шаблона изменился. Проверьте текст перед сохранением.")
        if body.strip() == selected["body"].strip():
            raise ValueError("Заполните шаблон текстом выпуска.")
        evidence = report_sources.verify(root, sources)
        _freeze_builtin(root, template_id)
        template = _template_record(root, template_id, version)[0]
        if template["revision"] != selected["revision"]:
            raise storage.ConflictError("Версия шаблона на диске изменилась во время подготовки выпуска.")
        now = datetime.now(timezone.utc).isoformat()
        fields = {"type": "report_issue", "schema_version": 1, "id": identifier, "title": title,
                  "template_id": template_id, "template_version": version, "template_revision": template["revision"],
                  "created_at": now, "creation_hash": key, "editorial_status": "draft" if draft else "final",
                  **({} if draft else {"finalized_at": now}),
                  **({"as_of": as_of} if as_of is not None else {}),
                  **({"series_id": series_id} if series_id else {}),
                  **({"period_start": start, "period_end": end} if start is not None else {})}
        storage._publish_record(root, fields, _render_issue(root, body, template, evidence),
                                kinds={"report_issue": "Reports"})
        return {**get_issue(root, identifier), "replayed": False}


def finalize_issue(root, id, *, revision):
    """Approve the draft revision the owner read, preserving the issue and its source appendix."""
    root = Path(root).resolve()
    _id(id)
    with storage._write_lock(root):
        matches = [path for path in storage.iter_markdown_paths(root, folders=("Reports",))
                   if storage._header_has(storage.read_header(path), b"id:", id)]
        if not matches:
            raise FileNotFoundError("Черновик выпуска не найден.")
        if len(matches) != 1:
            raise storage.ConflictError("Один ID найден в нескольких файлах отчётов.")
        path = matches[0]
        data = path.read_bytes()
        try:
            record, header = storage._parse_record(path, data, kinds=("report_issue",), optional_scalars=_ISSUE_FIELDS)
        except storage.UnmanagedMarkdown:
            raise ValueError("Финализировать можно только черновик выпуска по шаблону.")
        if record["id"] != id or record["revision"] != revision:
            raise storage.ConflictError("Выпуск изменился. Перечитайте текст перед финализацией.")
        receipt = {"id": id, "path": path.relative_to(root).as_posix(), "revision": revision}
        status = _editorial_status(record)
        if status == "final":
            return {**receipt, "editorialStatus": status, "finalizedAt": record.get("finalized_at")}
        if status != "draft":
            raise ValueError("Финализировать можно выпуск со статусом «Черновик».")
        storage.editable_scalar(record, header, "editorial_status")
        if storage.editable_scalar(record, header, "finalized_at"):
            raise ValueError("В черновике уже задано время финализации. Сверьте свойства выпуска.")
        now = datetime.now(timezone.utc).isoformat()
        fields = {"editorial_status": "final", "finalized_at": now}
        updated = storage._patch_header(header, record, fields) + data[len(header):]
        storage.replace_bytes(path, data, updated)
        return {**receipt, "editorialStatus": "final", "finalizedAt": now, "revision": sha256(updated).hexdigest()}
