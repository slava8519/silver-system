"""Read canonical card identities and resolve their current, typed relationships."""

from collections import defaultdict
from io import BytesIO
import os
from pathlib import Path
import re

from . import cycles, dependencies, gantt, okr, okr_criteria, okr_links, okr_progress, people
from . import plan_tree, planning, project_membership, project_updates, reports, storage, teams
from .schedule import schedule_history


_FOLDERS = (*storage.KINDS.values(), "Tasks", "Plans", "People", "Reports",
            reports.TEMPLATES, reports.SERIES)
_FIELDS = {
    **dict.fromkeys(storage.KINDS, ()), "person": (), "team": (), "task": planning.TASK_SCALARS,
    "plan": (cycles.CYCLE_SEAL,), "objective": ("plan_id",), "key_result": okr._FIELDS,
    "report_issue": reports._ISSUE_FIELDS, "report_series": reports._SERIES_FIELDS,
    "weekly_review": ("plan_id",), "report_template": reports._TEMPLATE_FIELDS,
}
_SCOPES = {
    **dict.fromkeys(storage.KINDS, tuple(storage.KINDS.values())),
    "task": ("Tasks",), "plan": ("Plans",), "person": ("People",), "team": ("People",),
    "key_result": ("Plans/OKR",), "objective": ("Plans/OKR",),
    "weekly_review": ("Reports",), "report_issue": ("Reports",),
    "report_series": (reports.SERIES,), "report_template": (reports.TEMPLATES,),
}


def _paths(root, report):
    def unreadable(path):
        report.unreadable("record.unreadable", path, 0, "Не удалось прочитать раздел карточек.")

    for folder in _FOLDERS:
        start = root / folder
        try:
            storage._inside(root, folder)
            if not start.exists():
                continue
            if not start.is_dir():
                unreadable(start)
                continue
            for current, dirs, files in os.walk(start, onerror=lambda exc: unreadable(Path(exc.filename))):
                current = Path(current)
                # People.paths excludes hidden names; the general Markdown reader accepts them.
                dirs[:] = sorted(d for d in dirs if not (current / d).is_symlink()
                                 and (folder != "People" or not d.startswith(".")))
                for name in sorted(files):
                    path = current / name
                    if folder == "People" and name.startswith("."):
                        continue
                    if path.suffix.lower() == ".md" and not path.is_symlink() and path.is_file():
                        yield path
        except (ValueError, OSError):
            unreadable(start)


def _kind(data):
    """Admit free-form Markdown and foreign schemas alongside managed cards."""
    lines = BytesIO(data)
    if lines.readline().rstrip(b"\r\n") != b"---":
        return None
    fields = {}
    types = []
    for line in lines:
        if line.rstrip(b"\r\n") in (b"---", b"..."):
            break
        key, sep, raw = line.partition(b":")
        if sep and key in (b"type", b"id", b"schema_version"):
            fields[key] = raw
        if sep and key == b"type":
            types.append(raw)
    if b"id" not in fields and b"schema_version" not in fields:
        return None  # type: note occurs in ordinary Obsidian frontmatter too.
    for raw in types:
        value = storage._scalar(raw.decode())
        if value in _FIELDS:
            return value
    if b"id" in fields and b"schema_version" in fields and not fields.get(b"type", b"").strip():
        raise ValueError("Отсутствует тип карточки.")
    return None


def _header_problem(data):
    fields = {}
    lines = BytesIO(data)
    next(lines, None)
    for number, raw in enumerate(lines, 2):
        if raw.rstrip(b"\r\n") in (b"---", b"..."):
            break
        key, sep, value = raw.partition(b":")
        if not sep or key not in (b"type", b"id", b"title", b"schema_version"):
            continue
        name = key.decode("ascii")
        if name in fields:
            return number, f"Поле {name} повторяется в YAML-шапке."
        try:
            fields[name] = storage._scalar(value.decode())
        except ValueError:
            return number, f"Не удалось прочитать поле {name} в YAML-шапке."
        if not fields[name].strip():
            return number, f"В YAML-шапке оставлено пустое поле {name}."
        if name == "schema_version" and fields[name] != "1":
            return number, "Читается только версия схемы 1. Сверьте schema_version карточки."
    else:
        return 1, "В начале карточки отсутствует закрывающая граница YAML-шапки."
    for name in ("type", "id", "title", "schema_version"):
        if name not in fields:
            return 1, f"В YAML-шапке карточки отсутствует поле {name}."
    return 1, "Проверьте формат полей карточки и кодировку UTF-8."


def _body_links(record, path, report):
    """Yield (stable ID, expected kind, one-based source line)."""
    kind, base = record["type"], record["body_start"]
    lines = record["body"].splitlines()
    if kind == "plan":
        closed = cycles.closed_metadata(record)
        if closed:
            cycles.frozen_rows(record, closed)
            return
        plan_tree.check_parents(plan_tree.parent_links(record["body"]))
    if kind in ("plan", "key_result"):
        planning._membership(record["body"])
        tasks, _ = okr_links._connections(lines, (planning._BEGIN, planning._END), "task")
        for identifier, (index, _) in tasks.items():
            yield identifier, "task", base + index
        markers = project_membership._PROJECTS if kind == "plan" else okr_links._PROJECTS
        projects, _ = okr_links._connections(lines, markers, "project")
        for identifier, (index, _) in projects.items():
            yield identifier, "project", base + index
    if kind == "task":
        for identifier, index in dependencies._section(record)[1].items():
            yield identifier, "task", base + index
        for key in ("start", "baseline", "due", "forecast", "completed"):
            if record.get(key) and storage._iso_date(record[key]) is None:
                report.add("record.date", path, record["field_lines"][key],
                           f"Укажите существующую календарную дату в поле {key}.")
        start, due = (storage._iso_date(record.get(k)) for k in ("start", "due"))
        if start and due and start > due:
            report.add("record.date", path, record["field_lines"]["due"], "Согласованный срок раньше начала работы.")
        gantt._row_fields(record, all_entries=True)
        schedule_history(record, path)
    elif kind == "report_issue":
        reports.issue_content(record)
    elif kind == "person":
        for fact in people._parse_facts(record).values():
            report.fact_source(path, fact)
            yield fact["direction"], "direction", fact["cardLine"] - 1
    elif kind == "team":
        members, _ = teams._links(lines, teams._ROSTER, ("person",))
        for identifier, index in members.values():
            yield identifier, "person", base + index
    elif kind == "project":
        project_updates._layout(record, strict=True)
        start, due = (storage._iso_date(record.get(k)) for k in ("project_start", "due"))
        if start and due and start > due:
            report.add("record.date", path, record["field_lines"]["due"], "Согласованный срок раньше начала проекта.")
    elif kind == "key_result":
        okr._ledger(record)
        okr_criteria._read(record)
        okr_progress._weights(record)


_REFS = {"note": {"direction": "direction"}, "project": {"direction": "direction"},
         "task": {"direction": "direction", "project": "project"},
         "key_result": {"direction": "direction", "objective_id": "objective"},
         "objective": {"plan_id": "plan", "direction": "direction"},
         "report_issue": {"series_id": "report_series"}}


def _owner(root, report, links):
    path = root / teams._PROFILE
    try:
        storage._inside(root, teams._PROFILE)
        if path.exists():
            if not path.is_file():
                raise OSError("Недоступен файл профиля.")
            refs, _ = teams._links(path.read_text(encoding="utf-8").splitlines(), teams._CONTEXT, ("owner", "team"))
            for field, (identifier, index) in refs.items():
                links.append((path, index + 1, identifier, "person" if field == "owner" else "team"))
    except OSError:
        report.unreadable("record.unreadable", path, 1, "Не удалось прочитать профиль владельца.")
    except ValueError:
        if path.is_symlink() or path.parent.is_symlink():
            report.unreadable("record.unreadable", path, 1, "Профиль недоступен по обычному пути хранилища.")
        else:
            report.add("record.structure", path, 1, "Проверьте разметку связей профиля владельца.")


def check_records(root, report):
    """Inspect every declared card; retain only identities and link coordinates between files."""
    identities = defaultdict(list)
    links = []
    checked = 0
    for path in _paths(root, report):
        try:
            data = path.read_bytes()
        except OSError:
            report.unreadable("record.unreadable", path, 1, "Не удалось прочитать файл карточки.")
            continue
        try:
            kind = _kind(data)
            if kind is None:
                continue
            if kind == "key_result":
                record, _ = okr._parse_key_result(path, data)
            else:
                record, _ = storage._parse_record(path, data, kinds=(kind,), optional_scalars=_FIELDS[kind])
            if not people._single_line(record["id"]) or not record["title"].strip():
                raise ValueError("Проверьте ID и название.")
        except (ValueError, TypeError, AttributeError):
            checked += 1
            line, message = _header_problem(data)
            report.add("record.header", path, line, message)
            continue
        checked += 1
        if not any(root / folder in path.parents for folder in _SCOPES[kind]):
            report.add("record.location", path, record["field_lines"]["type"],
                       "Такую карточку приложение ищет в " + " / ".join(_SCOPES[kind]) + ".")
            continue
        identifier = record["id"]
        if kind == "report_template":
            # Versions of the same template intentionally share an ID.
            try:
                expected = reports._template_path(root, identifier, record.get("version"))
                if path != expected:
                    raise ValueError("Путь версии шаблона.")
            except ValueError:
                report.add("record.template_version", path, record["field_lines"].get("version", 1),
                           "Проверьте ID, номер версии шаблона и имя его файла.")
            continue
        identities[identifier].append((path, record["field_lines"]["id"], kind))
        report.record_id(kind, identifier)
        for field, target in _REFS.get(kind, {}).items():
            if record.get(field):
                links.append((path, record["field_lines"][field], record[field], target))
            elif kind == "objective":
                report.add("record.header", path, record["field_lines"].get(field, 1),
                           f"В карточке цели требуется поле {field}.")
        try:
            for target, expected, line in _body_links(record, path, report):
                links.append((path, line, target, expected))
        except (ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError) as error:
            # The parser can embed private text in its error; publish only a known coordinate.
            coordinate = re.search(r"строк(?:а|е) (\d+)\.", str(error))
            line = int(coordinate[1]) if coordinate else min(record["body_start"], len(data.splitlines()))
            report.add("record.structure", path, line, "Проверьте служебные разделы и разметку в теле карточки.")
    _owner(root, report, links)
    for entries in identities.values():
        if len(entries) < 2:
            continue
        for i, (path, line, _) in enumerate(entries):
            other = entries[1 if i == 0 else 0][0].relative_to(root).as_posix()
            report.add("record.duplicate_id", path, line, f"Этот ID также записан в {other}.")
    for path, line, target, kind in links:
        matches = identities.get(target, ())
        if not matches or not any(found == kind for _, _, found in matches):
            report.add("record.reference", path, line, f"По указанному ID не найдена карточка типа {kind}.")
    return checked
