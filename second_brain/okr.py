"""Optional key results with measured evidence and references to existing tasks."""

from decimal import Decimal
import hashlib
import json
import re
import uuid

from . import evidence, planning, storage


_KINDS = {"key_result": "Plans/OKR"}
_OBJECTIVE_KINDS = {"objective": "Plans/OKR/Objectives"}
_FIELDS = ("objective", "objective_id", "unit", "target")
_HISTORY_START = "<!-- okr-measurements -->"
_HISTORY_END = "<!-- /okr-measurements -->"
_SOURCE = " <!-- okr-source: "
_LABELS = ("id", "title", "objective", "direction", "unit")


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or value.splitlines() != [value] or "\0" in value:
        raise ValueError(f"Укажите {label} одной строкой.")
    return value.strip()


def _number(value):
    """Preserve the decimal precision of CLI inputs, including a decimal comma."""
    if type(value) is float:
        value = format(Decimal(str(value)), "f")
    raw = str(value).strip().replace(",", ".").replace("−", "-")
    if len(raw) > 128 or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", raw):
        raise ValueError("Укажите конечное десятичное число.")
    number = Decimal(raw)
    if not number:
        return "0"
    plain = format(number, "f")
    return plain.rstrip("0").rstrip(".") if "." in plain else plain


def _parse_key_result(path, data, *, objective_ids=None):
    record, header = storage._parse_record(path, data, kinds=_KINDS, optional_scalars=_FIELDS)
    if objective_ids is not None and record.get("objective_id") not in objective_ids:
        return record, header
    for field in ("objective", "direction"):
        _text(record.get(field), field)
    record.setdefault("unit", None)
    if record["unit"] is not None:
        _text(record["unit"], "единицу измерения")
    line = record["field_lines"].get("target")
    if line is not None:
        value = record.get("target")
        # The shared YAML reader leaves complex owner fields opaque.
        # Distinguish a cleared scalar from an unsupported list or mapping.
        lines = header.decode().splitlines()
        raw = lines[line - 1].partition(":")[2].strip()
        following = next((s for s in lines[line:] if s.strip() and not s.lstrip().startswith("#")), "")
        empty_scalar = re.fullmatch(r"(?:null|~)?(?:\s*#.*)?", raw, re.I)
        if (value is None and not empty_scalar) or following.startswith((" ", "\t", "- ")):
            raise ValueError("Целевое значение должно содержать число или быть пустым.")
        record["target"] = _number(value) if value is not None and value.strip() else None
    if record.get("target") is not None and record["unit"] is None:
        raise ValueError("Для числового ориентира КР укажите единицу измерения.")
    return record, header


def _measured_date(value):
    if value is None:
        raise ValueError("Укажите дату измерения.")
    return planning.validate_due(value)


_GROUPED_INTEGER = r"[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+"
_QUANTITY = rf"(?<![\w.,+−/-])([+−-]?(?:(?:{_GROUPED_INTEGER}|[0-9]+)(?:[.,][0-9]+)?|[.,][0-9]+))"
_UNGROUP = str.maketrans("", "", " \u00a0\u202f")


def _check_date(date, line):
    dates = evidence.date_spans(line)
    if dates and date not in dates.values():
        raise ValueError("Дата измерения расходится с датой в источнике.")
    return dates


def _check_quote(value, unit, date, quote, *, full_line=None):
    # A short quotation must contain a whole measured quantity from the
    # original line. Substrings of a signed value, a date or a group of
    # thousands can otherwise masquerade as independent measurements.
    line = full_line if full_line is not None else quote
    dates = _check_date(date, line)
    units = re.compile(_QUANTITY + r"\s*" + re.escape(unit) + r"(?![\w/])")
    for m in units.finditer(line):
        if any(a < m.end(1) and m.start(1) < b for a, b in dates):
            continue
        if _number(m[1].translate(_UNGROUP)) != value:
            continue
        if any(q.start() <= m.start() and m.end() <= q.end() for q in re.finditer(re.escape(quote), line)):
            return
    raise ValueError("В цитате должны быть записаны измеренное значение и его единица рядом.")


def _ledger(record):
    """Visible dates and values are the record. The comment identifies their source."""
    lines = record["body"].splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == _HISTORY_START]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == _HISTORY_END]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError("Проверьте границы истории измерений в карточке ключевого результата.")
    rows = []
    for i in range(starts[0] + 1, ends[0]):
        line = lines[i].rstrip("\r\n")
        if _SOURCE not in line and not re.match(r"^- [0-9]{4}-[0-9]{2}-[0-9]{2} · ", line):
            continue
        try:
            if not record.get("unit"):
                raise ValueError
            visible, raw_source = line.rsplit(_SOURCE, 1)
            stamp, _, details = visible.removeprefix("- ").partition(" · ")
            raw_value, _, suffix = details.partition(" ")
            if (not line.startswith("- ") or not raw_source.endswith(" -->")
                    or not suffix.startswith(storage.markdown_label(record["unit"]) + " · ")):
                raise ValueError
            date = _measured_date(stamp)
            value = _number(raw_value)
            source = evidence.linked_source(json.loads(raw_source[:-4]))
            _check_quote(value, record["unit"], date, source["quote"])
        except (KeyError, ValueError, TypeError) as error:
            raise storage.ConflictError(
                f'Проверьте измерение в «{record["title"]}», строка {i + record["body_start"]}.'
            ) from error
        rows.append((i, {"date": date, "value": value, "source": source}))
    return rows, ends[0]


def _check_revision(record, revision):
    if revision != record["revision"]:
        raise storage.ConflictError("Карточка ключевого результата изменилась. Перечитайте её.")


def _valid_revision(revision):
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
        raise ValueError("Укажите прочитанную ревизию карточки ключевого результата.")


def _identity(namespace, request_id, fields, payload):
    if request_id is None:
        return str(uuid.uuid4())
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise ValueError("Некорректный идентификатор запроса.")
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    fields["creation_hash"] = fingerprint
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"second-brain/{namespace}/" + request_id))


class KeyResults:
    def __init__(self, root):
        self.planning = planning.Planning(root)
        self.root = self.planning.root

    def _objectives(self, plan_ids=None):
        seen = set()
        for path in storage.iter_markdown_paths(self.root, folders=_KINDS.values()):
            try:
                record, _ = storage._parse_record(path, path.read_bytes(), kinds=_OBJECTIVE_KINDS,
                                                  optional_scalars=("plan_id",))
            except storage.UnmanagedMarkdown:
                continue
            identifier = record["id"]
            if identifier in seen:
                raise ValueError(f"Один ID цели найден в нескольких файлах: {identifier}")
            seen.add(identifier)
            if plan_ids is not None and record.get("plan_id") not in plan_ids:
                continue
            yield {"id": identifier, "title": record["title"], "planId": _text(record.get("plan_id"), "ID плана"),
                   "direction": _text(record.get("direction"), "ID направления"), "revision": record["revision"],
                   "path": path.relative_to(self.root).as_posix(), "creation_hash": record.get("creation_hash")}

    def card_paths(self):
        for path in storage.iter_markdown_paths(self.root, folders=_KINDS.values()):
            try:
                storage._parse_record(path, path.read_bytes(), kinds=("objective", "key_result"))
            except storage.UnmanagedMarkdown:
                continue
            yield path

    def objectives(self, plan_id=None):
        with storage._vault_lock(self.root, write=False):
            selected = {plan_id} if plan_id is not None else None
            return [{k: v for k, v in o.items() if k != "creation_hash"} for o in self._objectives(selected)]

    def _objective(self, identifier):
        found = [row for row in self._objectives() if row["id"] == identifier]
        if not found:
            raise FileNotFoundError(f"Цель не найдена: {identifier}")
        return found[0]

    def _active_plan(self, identifier):
        from .cycles import closed_metadata
        path, _, record, _ = self.planning._find("plan", identifier)
        if closed_metadata(record) is not None:
            raise storage.ConflictError("План закрыт. Выберите активный план.")
        return path, record

    def create_objective(self, title, *, plan_id, direction, request_id=None):
        fields = {"type": "objective", "schema_version": 1, "title": _text(title, "название цели"),
                  "plan_id": _text(plan_id, "ID плана"), "direction": _text(direction, "ID направления")}
        identifier = _identity("objective", request_id, fields, fields)
        fields["id"] = identifier
        with storage._write_lock(self.root):
            if request_id is not None:
                previous = next((o for o in self._objectives() if o["id"] == identifier), None)
                if previous:
                    if previous.pop("creation_hash") != fields["creation_hash"]:
                        raise storage.ConflictError("Идентификатор запроса уже использован с другими данными.")
                    return previous
            plan_path, plan = self._active_plan(fields["plan_id"])
            area_path = storage.find_record(self.root, fields["direction"])
            area = storage.read_record(area_path)
            if area["type"] != "direction":
                raise ValueError("Укажите ID существующего направления.")
            folder = storage._inside(self.root, _OBJECTIVE_KINDS["objective"])
            body = [f'# {storage.markdown_label(fields["title"])}', '',
                    "План — " + storage.markdown_link(plan["title"], plan_path, folder), '',
                    "Направление — " + storage.markdown_link(area["title"], area_path, folder), '']
            folder.mkdir(parents=True, exist_ok=True)
            storage._publish_record(self.root, fields, "\n".join(body), kinds=_OBJECTIVE_KINDS)
            result = self._objective(identifier)
            result.pop("creation_hash")
            return result

    def _entries(self, objective_ids=None):
        seen = set()
        for path in storage.iter_markdown_paths(self.root, folders=_KINDS.values()):
            data = path.read_bytes()
            try:
                record, header = _parse_key_result(path, data, objective_ids=objective_ids)
            except storage.UnmanagedMarkdown:
                continue
            if record["id"] in seen:
                raise ValueError(f'Один ID ключевого результата найден в нескольких файлах: {record["id"]}')
            seen.add(record["id"])
            if objective_ids is None or record.get("objective_id") in objective_ids:
                yield path, data, record, header

    def plan_task_ids(self, plan_ids):
        """Resolve only explicit parent IDs; a legacy goal title has no period."""
        if not plan_ids:
            return {}
        parents = {goal["id"]: goal["planId"] for goal in self._objectives(plan_ids)}
        plan_tasks = {}
        if parents:
            for _, _, record, _ in self._entries(parents):
                dest = plan_tasks.setdefault(parents[record["objective_id"]], {})
                dest.update(dict.fromkeys(planning._membership(record["body"])[0]))
        return {plan_id: list(tasks) for plan_id, tasks in plan_tasks.items()}

    def _active_board(self, path, plan, *, validate_dates=False):
        plan_id = plan["id"]
        goals = {o["id"]: o for o in self._objectives({plan_id})}
        directions = {goal["direction"] for goal in goals.values()}
        names = {r["id"]: r["title"] for _, r in storage.iter_records(self.root)
                 if r["id"] in directions and r["type"] == "direction"} if directions else {}
        for goal in goals.values():
            goal["directionTitle"] = names.get(goal["direction"])
            goal.pop("creation_hash")
            goal["results"] = []
        linked = {}
        empty_results = []
        if goals:
            project_cache = {}
            for result_path, _, record, _ in self._entries(goals):
                goal = goals[record["objective_id"]]
                result = self._summarize(result_path, record, parent=goal, project_cache=project_cache)
                ids, _ = planning._membership(record["body"])
                goal["results"].append({**result, "taskIds": ids})
                linked.update(dict.fromkeys(ids))
                if not ids:
                    empty_results.append(result["id"])
        view = self.planning._view(path, plan, validate_dates=validate_dates, linked_task_ids=linked)
        self.planning._group_titles(view)
        items = view["items"]
        gaps = {"unlinkedTaskIds": [t["id"] for t in items if t["id"] not in linked],
                "missingTaskIds": [t["id"] for t in items if t.get("missing")],
                "emptyObjectiveIds": [o["id"] for o in goals.values() if not o["results"]],
                "emptyResultIds": empty_results}
        structure = {"objectives": list(goals.values()), "gaps": gaps}
        from .okr_progress import enrich_board
        enrich_board(self.root, view, structure)
        return view, structure

    def board(self, plan_id):
        from .cycles import closed_metadata
        with storage._vault_lock(self.root, write=False):
            path, _, record, _ = self.planning._find("plan", plan_id)
            closed = closed_metadata(record)
            historical = closed is not None
            if historical:
                from .okr_progress import enrich_frozen_contributions
                plan = self.planning._view(path, record)
                structure = closed.get("okr")
                enrich_frozen_contributions(plan["items"], structure)
            else:
                plan, structure = self._active_board(path, record)
            items = plan.pop("items")
            empty = {"objectives": [], "gaps": {"unlinkedTaskIds": [], "emptyObjectiveIds": [],
                                                "missingTaskIds": [t["id"] for t in items if t.get("missing")],
                                                "emptyResultIds": []}}
            return {"plan": plan, "historical": historical, "hierarchyRecorded": structure is not None,
                    "items": items, **(structure if structure is not None else empty)}

    def _find(self, identifier):
        matches = [row for row in self._entries() if row[2]["id"] == identifier]
        if not matches:
            raise FileNotFoundError(f"Ключевой результат не найден: {identifier}")
        return matches[0]

    def _summarize(self, path, record, detailed=False, parent=None, project_cache=None):
        rows, _ = _ledger(record)
        # Markdown shows the newest first. When two observations share a date,
        # the one added last sits above its predecessor.
        history = sorted((row for _, row in reversed(rows)), key=lambda row: row["date"])
        for row in history if detailed else history[-1:]:
            source = row["source"]
            try:
                row["source"] = evidence.resolve_source(self.root, source)
            except (ValueError, OSError):
                row["source"] = {**source, "unavailable": True}
        result = {k: record[k] for k in _LABELS}
        result.update(objectiveId=record.get("objective_id"), planId=None)
        if result["objectiveId"]:
            if parent is None:
                try:
                    parent = self._objective(result["objectiveId"])
                except FileNotFoundError:
                    result["missingObjective"] = True
            if parent is not None:
                result.update(objective=parent["title"], direction=parent["direction"], planId=parent["planId"])
        target = record.get("target")
        result.update(path=path.relative_to(self.root).as_posix(), revision=record["revision"],
                      target=target, targetLine=record["field_lines"]["target"] if target is not None else None,
                      measurement=history[-1] if history else None)
        if detailed:
            ids, _ = planning._membership(record["body"])
            needed = set(ids)
            tasks = {r["id"]: {"id": r["id"], "title": r["title"], "path": p.relative_to(self.root).as_posix()}
                     for p, _, r, _ in self.planning._entries("task") if r["id"] in needed} if ids else {}
            result["tasks"] = [tasks.get(k, {"id": k, "title": None, "path": None, "missing": True}) for k in ids]
            result["history"] = history
        from .okr_links import context_projects
        result["contextProjects"] = context_projects(self.root, record, project_cache)
        from .okr_criteria import summarize
        from .okr_progress import weight_fields
        result.update(summarize(self.root, record, history, detailed), **weight_fields(record))
        return result

    def add_criterion(self, identifier, **options):
        from .okr_criteria import add_criterion
        return add_criterion(self, identifier, **options)

    def observe_criterion(self, identifier, criterion_id, **options):
        from .okr_criteria import observe_criterion
        return observe_criterion(self, identifier, criterion_id, **options)

    def set_criterion_target(self, identifier, criterion_id, **options):
        from .okr_criteria import set_criterion_target
        return set_criterion_target(self, identifier, criterion_id, **options)

    def list(self, direction=None):
        with storage._vault_lock(self.root, write=False):
            parents = {o["id"]: o for o in self._objectives()}
            results = []
            project_cache = {}
            for path, _, record, _ in self._entries():
                parent = parents.get(record.get("objective_id"))
                area = parent["direction"] if parent else record["direction"]
                if direction is None or direction == area:
                    results.append(self._summarize(path, record, parent=parent, project_cache=project_cache))
            return results

    def show(self, identifier):
        with storage._vault_lock(self.root, write=False):
            path, _, record, _ = self._find(identifier)
            return self._summarize(path, record, detailed=True)

    def _task_link(self, directory, task_id):
        task = self.planning.task(task_id)
        link = storage.markdown_link(task["title"], self.root / task["path"], directory)
        return f"- {link} {storage.id_marker('task', task_id)}"

    def create(self, title, *, unit=None, objective=None, objective_id=None, direction=None,
               target=None, task_ids=(), request_id=None):
        fields = {"type": "key_result", "schema_version": 1, "title": _text(title, "название результата")}
        if objective_id is not None:
            if objective is not None or direction is not None:
                raise ValueError("Для цели по ID направление и название читаются из её карточки.")
            fields["objective_id"] = _text(objective_id, "ID цели")
        else:
            fields["objective"] = _text(objective, "цель")
        if unit is not None:
            fields["unit"] = _text(unit, "единицу измерения")
        elif target is not None:
            raise ValueError("Для числового ориентира укажите единицу измерения.")
        if objective_id is None:
            fields["direction"] = _text(direction, "ID направления")
        if target is not None:
            fields["target"] = _number(target)
        if not isinstance(task_ids, (tuple, list)):
            raise ValueError("Передайте список ID существующих задач.")
        ids = list(dict.fromkeys(_text(k, "ID задачи") for k in task_ids))
        identifier = _identity("key-result", request_id, fields, [fields, ids])
        fields["id"] = identifier
        with storage._write_lock(self.root):
            if request_id is not None:
                try:
                    path, _, existing, _ = self._find(identifier)
                except FileNotFoundError:
                    pass
                else:
                    if existing.get("creation_hash") != fields["creation_hash"]:
                        raise storage.ConflictError("Идентификатор запроса уже использован с другими данными.")
                    return self._summarize(path, existing, detailed=True)
            parent = None
            if objective_id is not None:
                parent = self._objective(fields["objective_id"])
                self._active_plan(parent["planId"])
                fields.update(objective=parent["title"], direction=parent["direction"])
            folder = storage._inside(self.root, _KINDS["key_result"])
            direction_path = storage.find_record(self.root, fields["direction"])
            area = storage.read_record(direction_path)
            if area["type"] != "direction":
                raise ValueError("Укажите ID существующего направления.")
            body = [f'# {storage.markdown_label(fields["title"])}', '',
                    storage.markdown_link(area["title"], direction_path, folder), '',
                    *([storage.markdown_link(parent["title"], self.root / parent["path"], folder), ''] if parent else []),
                    "## Связанные работы", '', planning._BEGIN,
                    *(self._task_link(folder, task_id) for task_id in ids), planning._END,
                    '', "## Измерения", '', _HISTORY_START, _HISTORY_END, '']
            folder.mkdir(parents=True, exist_ok=True)
            storage._publish_record(self.root, fields, "\n".join(body), kinds=_KINDS)
            return self.show(identifier)

    def _insert(self, entry, position, line):
        path, data, record, header = entry
        newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
        lines = record["body"].splitlines(keepends=True)
        lines.insert(position, line + newline)
        updated = header + "".join(lines).encode()
        saved, _ = _parse_key_result(path, updated)
        result = self._summarize(path, saved, detailed=True)
        storage.replace_bytes(path, data, updated)
        return result

    def assign_objective(self, identifier, objective_id, revision):
        _valid_revision(revision)
        objective_id = _text(objective_id, "ID цели")
        with storage._write_lock(self.root):
            path, original, record, header = self._find(identifier)
            if record.get("objective_id") == objective_id:
                return self._summarize(path, record, detailed=True)
            if record.get("objective_id"):
                raise storage.ConflictError("Ключевой результат уже привязан к другой цели.")
            _check_revision(record, revision)
            parent = self._objective(objective_id)
            self._active_plan(parent["planId"])
            if record["direction"] != parent["direction"]:
                raise ValueError("Выберите цель в том же направлении, что и ключевой результат.")
            header = storage._patch_header(header, record, {"objective_id": objective_id})
            newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
            link = storage.markdown_link(parent["title"], self.root / parent["path"], path.parent)
            note = f"Цель плана — {link} {storage.id_marker('objective', objective_id)}"
            updated = header + (record["body"] + newline + newline + note + newline).encode()
            parsed, _ = _parse_key_result(path, updated)
            result = self._summarize(path, parsed, detailed=True, parent=parent)
            storage.replace_bytes(path, original, updated)
            return result

    def link(self, identifier, task_id, revision):
        from .okr_links import set_connection
        return set_connection(self, identifier, task_id, kind="task", present=True, revision=revision)

    def unlink(self, identifier, task_id, revision):
        from .okr_links import set_connection
        return set_connection(self, identifier, task_id, kind="task", present=False, revision=revision)

    def context_project(self, identifier, project_id, *, present, revision):
        from .okr_links import set_connection
        return set_connection(self, identifier, project_id, kind="project", present=present, revision=revision)

    def measure(self, identifier, *, value, date, source, revision):
        _valid_revision(revision)
        value, date = _number(value), _measured_date(date)
        with storage._write_lock(self.root):
            entry = self._find(identifier)
            path, _, record, _ = entry
            if not record["unit"]:
                raise ValueError("У этого результата нет основной числовой метрики. Выберите критерий.")
            checked, full_line = evidence.verify_source_line(self.root, source)
            _check_quote(value, record["unit"], date, checked["quote"], full_line=full_line)
            rows, end = _ledger(record)
            for _, past in rows:
                prior = past["source"]
                if (past["value"] == value and past["date"] == date
                        and all(prior[k] == checked[k] for k in ("id", "ref", "quote"))):
                    return self._summarize(path, record, detailed=True)
            _check_revision(record, revision)
            link = storage.markdown_link(f'Строка {checked["line"]} · {checked["path"]}',
                                         self.root / checked["path"], path.parent)
            measured = f'{value} {storage.markdown_label(record["unit"])}'
            line = f'- {date} · {measured} · {link}{_SOURCE}{storage.comment_json(checked)} -->'
            position = next((i for i, past in rows if past["date"] <= date), end)
            return self._insert(entry, position, line)
