"""Optional edge weights and sourced readiness of canonical task cards."""

from decimal import Decimal, Inexact, localcontext
import hashlib
import json

from . import brief, evidence, planning, storage
from .okr import KeyResults, _check_quote, _measured_date, _number, _text, _valid_revision


_WEIGHT = " <!-- okr-edge-weight: "
_READING = " <!-- task-readiness: "
_KIND = {"fact": "факт", "estimate": "оценка"}
_TASK_FORMULA = "Σ(вес задачи × готовность задачи)"
_GOAL_FORMULA = "Σ(вес КР × готовность КР)"
_PLAN_FORMULA = "Среднее арифметическое готовности всех целей"


def _bounded_number(value, maximum):
    number = _number(value)
    if not 0 <= Decimal(number) <= maximum:
        raise ValueError(f"Укажите значение от 0 до {maximum}.")
    return number


def _marked(record, marker):
    if marker not in record["body"]:
        return
    for index, line in enumerate(record["body"].splitlines()):
        if marker in line:
            visible, encoded = line.rsplit(marker, 1)
            try:
                if not encoded.endswith(" -->"):
                    raise ValueError
                data = json.loads(encoded[:-4])
                if not isinstance(data, dict):
                    raise ValueError
            except ValueError as error:
                raise storage.ConflictError(f'Проверьте запись в строке {index + record["body_start"]}.') from error
            yield index, visible, data


def _weights(record):
    rows = {}
    for index, visible, edge in _marked(record, _WEIGHT):
        label, separator, weight = visible.rpartition(" · вес ")
        key = (edge.get("kind"), edge.get("id"))
        try:
            if (not label.startswith("- ") or not separator or key[0] not in ("objective", "task")
                    or not isinstance(key[1], str) or not key[1] or key in rows):
                raise ValueError
            rows[key] = (index, _bounded_number(weight, 1))
        except ValueError as error:
            raise storage.ConflictError(f'Проверьте вес в строке {index + record["body_start"]}.') from error
    return rows


def weight_fields(record):
    rows = _weights(record)
    weight = rows.get(("objective", record.get("objective_id")))
    return {"weight": weight[1] if weight else None,
            "taskWeights": {key: item[1] for (kind, key), item in rows.items() if kind == "task"}}


def _readings(record):
    rows = []
    for index, visible, data in _marked(record, _READING):
        try:
            date = _measured_date(data["date"])
            value = _bounded_number(data["value"], 100)
            kind = _KIND[data["kind"]]
            if (not visible.startswith(f"- {date} · {value}% · {kind} · ")
                    or data["actor"] not in ("ai", "manual") or type(data["ownerConfirmed"]) is not bool):
                raise ValueError
            reason = data["reason"]
            if reason is not None:
                _text(reason, "основание")
            if data["source"] is not None:
                data["source"] = evidence.linked_source(data["source"])
                if data["kind"] == "fact" and not (reason and (data["actor"] == "manual" or data["ownerConfirmed"])):
                    _check_quote(value, "%", date, data["source"]["quote"])
            elif not (reason and (data["actor"] == "manual" or data["ownerConfirmed"])):
                raise ValueError
            rows.append({**data, "line": record["body_start"] + index})
        except (KeyError, TypeError, ValueError) as error:
            raise storage.ConflictError(f'Проверьте готовность в строке {index + record["body_start"]}.') from error
    return sorted(rows, key=lambda row: row["date"])


def _resolve(root, row):
    if not row:
        return None
    if row["source"] is None:
        return dict(row)
    try:
        source = evidence.resolve_source(root, row["source"])
    except (ValueError, OSError):
        source = {**row["source"], "unavailable": True}
    return {**row, "source": source}


def _updated_body(record, header, *, heading, index=None, line=None, after=None):
    newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
    body = record["body"].splitlines(keepends=True)
    if index is not None:
        body[index] = (line + newline) if line is not None else ""
    else:
        position = len(body) if after is None else after + 1
        prefix = newline if position and not body[position - 1].endswith(("\r", "\n")) else ""
        if after is None and heading not in record["body"].splitlines():
            prefix += newline + heading + newline + newline
        body.insert(position, prefix + line + newline)
    return header + "".join(body).encode()


def _result(formula, count, value, weight_sum, missing_weights, missing_values, estimates):
    return {"formula": formula, "count": count, "value": value,
            "weightSum": weight_sum, "missingWeights": missing_weights, "missingValues": missing_values,
            "hasEstimates": estimates, "status": "complete" if value is not None else "incomplete"}


def _plain(value):
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _weighted(children, weights, formula):
    total = Decimal(0)
    weighted = Decimal(0)
    contributions = {}
    missing_weights, missing_values = [], []
    estimates = False
    with localcontext() as ctx:
        ctx.prec = 600
        for key, value, estimated in children:
            contributions[key] = None
            weight = weights.get(key)
            if weight is None:
                missing_weights.append(key)
            else:
                fraction = Decimal(weight)
                total += fraction
                if fraction == 0:
                    contributions[key] = "0"
                    continue
                if value is not None:
                    points = fraction * Decimal(value)
                    contributions[key] = _plain(points)
                    weighted += points
            if value is None:
                missing_values.append(key)
            estimates |= estimated
        complete = children and not (missing_weights or missing_values) and total == 1
        result = _result(formula, len(children), _plain(weighted) if complete else None,
                         _plain(total), missing_weights, missing_values, estimates)
        return {**result, "contributions": contributions}


def _mean(goals):
    missing = [g["id"] for g in goals if g["progress"]["value"] is None]
    value = None
    if goals and not missing:
        with localcontext() as ctx:
            ctx.prec = 600
            total = sum(Decimal(g["progress"]["value"]) for g in goals)
            average = total / len(goals)
            # Quarter means may repeat indefinitely. Keep finite inputs exact.
            if ctx.flags[Inexact]:
                average = average.quantize(Decimal("0.000001"))
            value = _plain(average)
    return _result(_PLAN_FORMULA, len(goals), value, None, [], missing,
                   any(g["progress"]["hasEstimates"] for g in goals))


def _task_readings(root, items):
    from .plan_transaction import previous_versions

    pending = previous_versions(root) if items else {}
    tasks = {}
    for task in items:
        reading = None
        if not task.get("missing"):
            path = storage._inside(root, task["path"])
            payload = path.read_bytes()
            if path in pending and hashlib.sha256(payload).hexdigest() == pending[path][1]:
                payload = pending[path][0]
            current_revision = hashlib.sha256(payload).hexdigest()
            expected_revision = task.get("revision")
            if expected_revision is not None and expected_revision != current_revision:
                raise storage.ConflictError("Задача изменилась во время чтения. Откройте план ещё раз.")
            task["revision"] = current_revision
            if expected_revision is None or _READING.encode() in payload:
                record, _ = storage._parse_record(path, payload, kinds=("task",))
                if record["id"] != task["id"]:
                    raise storage.ConflictError("Карточка задачи была заменена. Откройте результат ещё раз.")
                rows = _readings(record)
                reading = _resolve(root, rows[-1]) if rows else None
        task["readiness"] = reading
        tasks[task["id"]] = reading
    return tasks


def _task_progress(identifiers, weights, readings):
    children = []
    for identifier in identifiers:
        reading = readings.get(identifier)
        children.append((identifier, reading["value"] if reading else None,
                         bool(reading and reading["kind"] == "estimate")))
    return _weighted(children, weights, _TASK_FORMULA)


def _goal_progress(results):
    children = [(kr["id"], kr["progress"]["value"], kr["progress"]["hasEstimates"]) for kr in results]
    return _weighted(children, {kr["id"]: kr["weight"] for kr in results}, _GOAL_FORMULA)


def enrich_card(root, card):
    readings = _task_readings(root, card["tasks"])
    card["progress"] = _task_progress(readings.keys(), card["taskWeights"], readings)


def enrich_frozen_contributions(items, structure):
    """Earlier snapshots stored weights and readings; derive details from those original values."""
    if not structure:
        return
    readings = {item["id"]: item.get("readiness") for item in items}
    for goal in structure["objectives"]:
        for result in goal["results"]:
            progress = result.get("progress")
            if progress is not None and "contributions" not in progress:
                calculated = _task_progress(result["taskIds"], result["taskWeights"], readings)
                progress["contributions"] = calculated["contributions"]
        progress = goal.get("progress")
        if progress is not None and "contributions" not in progress:
            progress["contributions"] = _goal_progress(goal["results"])["contributions"]


def enrich_board(root, view, structure):
    """Add one readiness per physical task and compute the two levels of edges once."""
    readings = _task_readings(root, view["items"])
    for goal in structure["objectives"]:
        results = goal["results"]
        for result in results:
            result["progress"] = _task_progress(result["taskIds"], result["taskWeights"], readings)
        goal["progress"] = _goal_progress(results)
    structure["progress"] = _mean(structure["objectives"])


class Progress:
    def __init__(self, root):
        self.results = KeyResults(root)
        self.planning = self.results.planning
        self.root = self.results.root

    def set_weight(self, result_id, *, weight, revision, task_id=None):
        _valid_revision(revision)
        if weight is not None:
            weight = _bounded_number(weight, 1)
        with storage._write_lock(self.root):
            path, data, record, header = self.results._find(result_id)
            parent_id = record.get("objective_id")
            if parent_id:
                parent = self.results._objective(parent_id)
                self.results._active_plan(parent["planId"])
            weights = _weights(record)
            if task_id is not None:
                ids, _ = planning._membership(record["body"])
                if task_id not in ids:
                    raise ValueError("Сначала свяжите эту задачу с ключевым результатом.")
                key = ("task", task_id)
                label = "Задача " + (self.planning.task(task_id)["title"] if weight is not None else task_id)
                other_weights = [weights[("task", i)][1] for i in ids if i != task_id and ("task", i) in weights]
            else:
                if not parent_id:
                    raise ValueError("Сначала привяжите ключевой результат к цели периода.")
                key = ("objective", parent_id)
                label = "Вклад в цель " + parent["title"]
                other_weights = [weight_fields(kr)["weight"] for _, _, kr, _ in self.results._entries({parent_id})
                                 if kr["id"] != result_id]
            current = weights.get(key)
            answer = {"id": result_id, "path": path.relative_to(self.root).as_posix(), "revision": record["revision"],
                      "weight": weight, "taskId": task_id, "objectiveId": parent_id}
            if (current[1] if current else None) == weight:
                return answer
            if record["revision"] != revision:
                raise storage.ConflictError("Вес или карточка изменились. Перечитайте ключевой результат.")
            with localcontext() as ctx:
                ctx.prec = 600
                if weight is not None and Decimal(weight) + sum(Decimal(w) for w in other_weights if w is not None) > 1:
                    raise ValueError("Сумма весов превышает 1. Сначала уменьшите другой вес.")
            line = None
            if weight is not None:
                marker = storage.comment_json({"kind": key[0], "id": key[1]})
                line = f"- {storage.markdown_label(label)} · вес {weight}{_WEIGHT}{marker} -->"
            updated = _updated_body(record, header, heading="## Веса связей", index=current[0] if current else None,
                                    line=line, after=max((i for i, _ in weights.values()), default=None))
            storage.replace_bytes(path, data, updated)
            return {**answer, "revision": hashlib.sha256(updated).hexdigest()}

    def readiness(self, task_id, *, limit=20, offset=0):
        if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
            raise ValueError("Укажите размер страницы 1–20 и неотрицательное смещение.")
        with storage._vault_lock(self.root, write=False):
            path, _, record, _ = self.planning._find("task", task_id)
            rows = list(reversed(_readings(record)))
            page = brief.page(rows, limit, offset)
            return {**self.planning._summary(path, record), **page, "limit": limit, "offset": offset,
                    "readiness": _resolve(self.root, rows[0]) if rows else None,
                    "items": [_resolve(self.root, row) for row in page["items"]]}

    def set_readiness(self, task_id, *, value, date, kind, revision, actor, reason=None, source=None,
                      owner_confirmed=False):
        _valid_revision(revision)
        value = _bounded_number(value, 100)
        date = _measured_date(date)
        if kind not in ("fact", "estimate") or actor not in ("ai", "manual") or type(owner_confirmed) is not bool:
            raise ValueError("Проверьте вид готовности, автора и подтверждение владельца.")
        if reason is not None:
            reason = _text(reason, "основание")
        manual = actor == "manual" or owner_confirmed
        if source is None and not (manual and reason):
            raise ValueError("Укажите источник или явное основание владельца.")
        with storage._write_lock(self.root):
            path, original, record, header = self.planning._find("task", task_id)
            if source is not None:
                source, full_line = evidence.verify_source_line(self.root, source)
                if kind == "fact" and not (manual and reason):
                    _check_quote(value, "%", date, source["quote"], full_line=full_line)
            decision = {"value": value, "date": date, "kind": kind, "actor": actor, "ownerConfirmed": owner_confirmed,
                        "reason": reason, "source": source}
            rows = _readings(record)
            latest = rows[-1] if rows else None
            same_day = next((r for r in reversed(rows) if r["date"] == date), None)
            if same_day:
                previous = {k: same_day[k] for k in decision}
                if previous["source"]:
                    previous["source"] = {k: v for k, v in previous["source"].items() if k != "href"}
                if previous == decision:
                    return {**self.planning._summary(path, record), "readiness": _resolve(self.root, latest)}
            if revision != record["revision"]:
                raise storage.ConflictError("Карточка задачи изменилась. Перечитайте её готовность.")
            protected = same_day or (latest if latest and date >= latest["date"] else None)
            if protected and not manual and (protected["actor"] == "manual" or protected["ownerConfirmed"]
                                             or (protected["kind"] == "fact" and kind == "estimate")):
                raise storage.ConflictError("Для изменения подтверждённой готовности нужно явное решение владельца.")
            explanation = ["владелец" if actor == "manual" else
                           "ИИ с подтверждением владельца" if owner_confirmed else "ИИ"]
            if reason:
                explanation.append(storage.markdown_label(reason))
            if source:
                explanation.append(storage.markdown_link(f'{source["path"]} · {source["line"]}',
                                                         self.root / source["path"], path.parent))
            line = (f"- {date} · {value}% · {_KIND[kind]} · " + " · ".join(explanation)
                    + _READING + storage.comment_json(decision) + " -->")
            after = max((row["line"] - record["body_start"] for row in rows), default=None)
            updated = _updated_body(record, header, heading="## История готовности", line=line, after=after)
            parsed, _ = storage._parse_record(path, updated, kinds=("task",))
            latest = _readings(parsed)[-1]
            storage.replace_bytes(path, original, updated)
            return {**self.planning._summary(path, parsed), "readiness": _resolve(self.root, latest)}
