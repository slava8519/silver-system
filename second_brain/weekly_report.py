"""Publish a weekly review as one vault note with a citation for each finding."""

from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
import re
from urllib.parse import quote, urlsplit
import uuid
from zoneinfo import ZoneInfo

from . import brief, storage
from .planning import Planning
from .weekly_evidence import _period, build_weekly_evidence


_REPORT_TYPE = "weekly_review"
_REPORT_FIELDS = ("plan_id", "period_start", "period_end", "timezone")
_FORMAT_PATH = "Settings/Formats/Weekly.md"
_KIND_LABELS = {
    "event": "События недели",
    "change": "С прошлого выпуска",
    "snapshot": "На момент закрытия",
}
_DATE_LABELS = {
    "start": "начало",
    "due": "согласованный срок",
    "forecast": "прогноз",
    "completed": "факт завершения",
}
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря")


def _issue_catalog(root, plan_id=None):
    """Read report headers; leave free-form reports and owner-edited bodies alone."""
    root = Path(root).resolve()
    issues = []
    claimed = defaultdict(set)
    for path in storage.iter_markdown_paths(root, folders=("Reports",)):
        with path.open("rb") as stream:
            first = stream.readline()
            if first.rstrip(b"\r\n") != b"---":
                continue
            header = [first]
            for line in stream:
                header.append(line)
                if line.rstrip(b"\r\n") in (b"---", b"..."):
                    break
        raw = b"".join(header)
        # The same report ID stays reserved when its owner renames the note or
        # edits another YAML field into a value the report reader cannot parse.
        for identity in re.findall(rb'''(?m)^id:[ \t]*["']?([a-f0-9-]{36})["']?[ \t]*(?:#.*)?\r?$''', raw):
            claimed[identity.decode("ascii")].add(path.relative_to(root).as_posix())
        if not re.search(rb'''(?m)^type:[ \t]*["']?weekly_review["']?[ \t]*(?:\#.*)?\r?$''', raw):
            continue
        try:
            record, _ = storage._parse_record(path, raw, kinds=(_REPORT_TYPE,),
                                              optional_scalars=(*_REPORT_FIELDS, "closed_at"))
            start, end = (date.fromisoformat(record[key]).isoformat() for key in ("period_start", "period_end"))
            if record.get("closed_at"):
                close_time = record["closed_at"]
            else:
                # Earlier issues derive the cutoff from their already closed plan.
                close_time = Planning(root).view(record["plan_id"])["closedAt"]
            closed_at = datetime.fromisoformat(close_time)
            if not closed_at.tzinfo or end < start:
                continue
        except (ValueError, TypeError, KeyError, FileNotFoundError):
            continue
        if plan_id is not None and record.get("plan_id") != plan_id:
            continue
        if any(not record.get(key) for key in _REPORT_FIELDS):
            continue
        issues.append({"id": record["id"], "planId": record["plan_id"], "title": record["title"],
                       "path": path.relative_to(root).as_posix(), "start": start,
                       "end": end, "timezone": record["timezone"], "closedAt": closed_at.isoformat()})
    issues.sort(key=lambda item: (item["end"], item["start"], item["path"]), reverse=True)
    return issues, claimed


def published_issues(root, plan_id=None):
    return _issue_catalog(root, plan_id)[0]


def _format(root):
    result = {"title": "Недельный обзор", "group_by": "direction"}
    path = storage._inside(root, _FORMAT_PATH)
    if not path.exists():
        return result
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError(f"Добавьте блок свойств в начале {_FORMAT_PATH}.")
    seen = set()
    for line in lines[1:lines.index("---", 1)]:
        key, _, value = line.partition(":")
        if key in result:
            if key in seen:
                raise ValueError(f"Поле {key} повторяется в {_FORMAT_PATH}.")
            result[key] = storage._scalar(value)
            seen.add(key)
    if result["group_by"] not in ("direction", "kind"):
        raise ValueError(f"Для group_by в {_FORMAT_PATH} укажите direction или kind.")
    title = result["title"]
    if not isinstance(title, str) or not title.strip() or len(title) > 150 or len(title.splitlines()) != 1:
        raise ValueError(f"Укажите название до 150 символов в поле title в {_FORMAT_PATH}.")
    return result


def _origin(value):
    if value is None:
        return None
    uri = urlsplit(value)
    if (uri.scheme not in ("http", "https") or not uri.hostname or uri.path not in ("", "/")
            or uri.query or uri.fragment or not re.fullmatch(r"[\w.\-:\[\]]+", uri.netloc)):
        raise ValueError("Для --gantt-url укажите адрес рабочей оболочки, например http://127.0.0.1:8766/.")
    # Accessing port validates an optional numeric port and its range.
    _ = uri.port
    return f"{uri.scheme}://{uri.netloc}"


def _prior_issue(root, issues, current, selection):
    cutoff = datetime.fromisoformat(current["closedAt"])
    earlier = [issue for issue in issues if issue["end"] < current["start"] and issue["timezone"] == current["timezone"]
               and datetime.fromisoformat(issue["closedAt"]) < cutoff]
    if selection:
        selected = [issue for issue in earlier if issue["id"] == selection]
        if len(selected) != 1:
            raise ValueError("Укажите предыдущий выпуск с более ранним периодом в том же часовом поясе.")
        return selected[0], Planning(root).view(selected[0]["planId"])
    tasks = {f["taskId"] for f in current["facts"]}
    directions = {f["directionId"] for f in current["facts"] if f["directionId"]}
    matches = []
    for issue in earlier:
        if matches and matches[0][0]["end"] != issue["end"]:
            break
        try:
            old = Planning(root).view(issue["planId"])
        except (ValueError, FileNotFoundError):
            continue
        if old["status"] == "closed" and any(
                row["id"] in tasks or row.get("direction") in directions for row in old["items"]):
            matches.append((issue, old))
    if len(matches) > 1:
        raise ValueError("За предыдущий период несколько выпусков. Выберите --previous ID: " +
                         ", ".join(issue["id"] for issue, old in matches))
    return matches[0] if matches else (None, None)


def _changes(old, facts):
    if old["status"] != "closed":
        raise ValueError("Для сравнения нужен снимок плана предыдущего выпуска.")
    rows = {row["id"]: row for row in old["items"] if not row.get("missing")}
    changes = []
    for fact in facts:
        if fact["kind"] != "snapshot" or fact.get("missing") or fact["taskId"] not in rows:
            continue
        row = rows[fact["taskId"]]
        changed = []
        for key, name in _DATE_LABELS.items():
            before, after = row[key], fact["dates"][key]
            if before != after:
                changed.append(f"{name} {before or '—'} → {after or '—'}")
        if changed:
            href = "/#plan/" + quote(old["id"], safe="") + "?task=" + quote(row["id"], safe="")
            evidence = {"kind": "snapshot", "path": old["path"], "line": row["snapshotLine"], "href": href}
            changes.append({**fact, "kind": "change",
                            "text": fact["title"] + " — " + ", ".join(changed) + ".",
                            "sources": [evidence, fact["sources"][0]]})
    return changes


def _display_date(value, year=None):
    moment = date.fromisoformat(value)
    suffix = "" if moment.year == year else f" {moment.year}"
    return f"{moment.day} {_MONTHS[moment.month - 1]}" + suffix


def _finding(fact, year):
    title, text = fact["title"], fact["text"]
    if fact["kind"] == "event":
        prefix = _display_date(fact["date"], year) + " · "
        if title not in text:
            prefix += "**" + storage.markdown_label(title) + ".** "
        return prefix + storage.markdown_label(text)
    prefix = f"«{title}» — " if fact["kind"] == "snapshot" else title + " — "
    description = text[len(prefix):] if text.startswith(prefix) else text
    rendered = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", lambda m: _display_date(m[0], year), description)
    return storage.markdown_label((prefix if text.startswith(prefix) else "") + rendered)


def _sources(root, folder, sources, origin):
    links = []
    for item in sources:
        path, line = item["path"], item["line"]
        kind = item["kind"]
        name = f"{Path(path).stem} · строка {line}"
        if origin:
            role = "Гант" if kind == "snapshot" else "Источник"
            links.append(f"[{role} · строка {line}]({origin}{item['href']})")
        local = storage.markdown_link(name, root / path, folder)
        if kind == "snapshot":
            local = local[:-1] + "#" + quote("Итог цикла") + ")"
        links.append(local)
    return " · ".join(links)


def _render(root, folder, title, data, format_, previous, origin):
    zone = timezone.utc if data["timezone"] == "UTC" else ZoneInfo(data["timezone"])
    local_time = datetime.fromisoformat(data["closedAt"]).astimezone(zone)
    lines = ["# " + storage.markdown_label(title), "",
             f"События с {_display_date(data['start'], local_time.year)} по {_display_date(data['end'])} ({data['timezone']}).",
             "План " + storage.markdown_link(data["planTitle"], root / data["planPath"], folder)
             + " закрыт " + local_time.strftime("%d.%m.%Y в %H:%M") + ".", ""]
    if previous:
        lines += ["Предыдущий выпуск — " + storage.markdown_link(previous["title"], root / previous["path"], folder), ""]
    by_direction = format_["group_by"] == "direction"
    groups = defaultdict(lambda: defaultdict(list))
    for fact in data["facts"]:
        direction = (fact.get("directionId"), fact.get("directionTitle") or "Без направления")
        groups[direction][fact["kind"]].append(fact)
    directions = list(groups)
    if by_direction:
        sections = [(d[1], [(_KIND_LABELS[k], groups[d][k]) for k in _KIND_LABELS]) for d in directions]
    else:
        sections = [(_KIND_LABELS[k], [(d[1], groups[d][k]) for d in directions]) for k in _KIND_LABELS]
    for name, subgroups in sections:
        if not any(facts for _, facts in subgroups):
            continue
        lines += ["## " + storage.markdown_label(name), ""]
        for label, facts in subgroups:
            if facts:
                lines += ["### " + storage.markdown_label(label), ""]
                for fact in facts:
                    links = _sources(root, folder, fact["sources"], origin)
                    lines.append("- " + _finding(fact, local_time.year) + " " + links)
                lines.append("")
    if not data["facts"]:
        lines.extend(["В снимке нет работ.", ""])
    if data.get("warnings"):
        lines += ["## Источники для сверки", ""]
        for warning in data["warnings"]:
            links = _sources(root, folder, warning["sources"], origin)
            lines.append("- " + storage.markdown_label(warning["text"]) + " " + links)
        lines.append("")
    return "\n".join(lines)


def _preview_item(item, origin):
    view = {**item, "sources": [dict(source) for source in item["sources"]]}
    brief.clip_fields(view, (field for field in ("text", "title", "directionTitle") if field in view))
    if origin:
        for source in view["sources"]:
            source["href"] = origin + source["href"]
    return view


def weekly_review(root, plan_id, *, start=None, end=None, timezone_name="UTC", gantt_url=None,
                  previous=None, save=False, warnings_only=False, limit=brief.DEFAULT_LIMIT, offset=0):
    root = Path(root).resolve()
    origin = _origin(gantt_url)
    lock = storage._write_lock(root) if save else storage._vault_lock(root, write=False)
    with lock:
        issues, claimed = _issue_catalog(root)
        if save:
            # A published issue stays readable even if its task or source was removed.
            for issue in issues:
                if issue["planId"] != plan_id or issue["timezone"] != timezone_name:
                    continue
                period = _period(datetime.fromisoformat(issue["closedAt"]), start, end, timezone_name)
                if period == (issue["start"], issue["end"]):
                    if len(claimed[issue["id"]]) > 1:
                        raise storage.ConflictError("Один ID недельного обзора найден в нескольких файлах.")
                    return {**issue, "saved": True, "replayed": True}
        data = build_weekly_evidence(root, plan_id, start=start, end=end, timezone_name=timezone_name)
        key = "/".join([plan_id, data["start"], data["end"], data["timezone"]])
        issue_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "second-brain/weekly-review/" + key))
        existing = [issue for issue in issues if issue["id"] == issue_id]
        if len(existing) > 1:
            raise storage.ConflictError("Один ID недельного обзора найден в нескольких файлах.")
        if save and claimed.get(issue_id):
            names = ", ".join(sorted(claimed[issue_id]))
            raise storage.ConflictError(f"ID этого выпуска уже записан. Проверьте свойства обзора в {names}.")
        format_ = _format(root)
        title = f"{format_['title']} · {data['start']} — {data['end']}"
        previous_issue, old = _prior_issue(root, issues, data, previous)
        if previous_issue:
            data["facts"] += _changes(old, data["facts"])
        folder = storage._inside(root, "Reports")
        if not folder.is_dir():
            raise ValueError("Для обзора нужна папка Reports в выбранном хранилище.")
        warnings = brief.page(data["warnings"], limit, offset if warnings_only else 0)
        detail = {"id": issue_id, "planId": plan_id, "title": title, "start": data["start"],
                  "end": data["end"], "timezone": data["timezone"], "closedAt": data["closedAt"],
                  "previous": previous_issue, "warningTotal": warnings["total"], "groupBy": format_["group_by"]}
        shown_warnings = [_preview_item(item, origin) for item in warnings["items"]]
        if warnings_only:
            return {**detail, **warnings, "items": shown_warnings}
        detail["warnings"] = shown_warnings
        if save:
            fields = {"type": _REPORT_TYPE, "schema_version": 1, "id": issue_id, "title": title,
                      "plan_id": plan_id, "period_start": data["start"], "period_end": data["end"],
                      "timezone": data["timezone"], "closed_at": data["closedAt"]}
            body = _render(root, folder, title, data, format_, previous_issue, origin)
            path = storage._publish_record(root, fields, body, kinds={_REPORT_TYPE: "Reports"})
            return {**detail, "path": path.relative_to(root).as_posix(), "total": len(data["facts"]),
                    "saved": True, "replayed": False}
        path = folder / (storage._filename(title) + ".md")
        if path.exists():
            path = path.with_name(f"{path.stem} ({issue_id}).md")
        page = brief.page(data["facts"], limit, offset)
        page["items"] = [_preview_item(item, origin) for item in page["items"]]
        return {**detail, **page, "path": existing[0]["path"] if existing else path.relative_to(root).as_posix(),
                "published": bool(existing)}


def add_command(commands):
    parser = commands.add_parser("weekly-review", help="подготовить обзор закрытой недели со ссылками")
    parser.add_argument("plan_id")
    parser.add_argument("--start", help="первый день событий, YYYY-MM-DD")
    parser.add_argument("--end", help="последний день событий включительно, YYYY-MM-DD")
    parser.add_argument("--timezone", default="UTC", help="часовой пояс для дат периода и момента закрытия")
    parser.add_argument("--gantt-url", help="адрес рабочей оболочки этого хранилища")
    parser.add_argument("--previous", help="ID предыдущего выпуска при нескольких параллельных обзорах")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--save", action="store_true", help="сохранить все выводы в Reports")
    action.add_argument("--warnings", action="store_true", help="показать страницу источников для сверки")
    parser.add_argument("--limit", type=brief.result_limit, default=brief.DEFAULT_LIMIT)
    parser.add_argument("--offset", type=brief.offset, default=0)


def run(root, args):
    return weekly_review(root, args.plan_id, start=args.start, end=args.end, timezone_name=args.timezone,
                         gantt_url=args.gantt_url, previous=args.previous, save=args.save,
                         warnings_only=args.warnings, limit=args.limit, offset=args.offset)
