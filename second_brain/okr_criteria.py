"""Versioned success criteria and dated observations in a key result's Markdown."""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import uuid

from . import evidence, storage
from .okr import _check_date, _check_quote, _check_revision, _measured_date, _number, _text, _valid_revision


_BEGIN, _END = "<!-- okr-criteria -->", "<!-- /okr-criteria -->"
_PREFIX = " <!-- okr-criterion: "
_OPERATORS = ("<=", ">=", "=")
_SYMBOLS = {"<=": "≤", ">=": "≥", "=": "="}
_YES_NO = re.compile(r'(?:^|[.;:!=—–]|\s-)\s*[«"]?(да|нет|yes|no|true|false)(?=\s*(?:$|[.,;!»"]))', re.I)


def criterion_id(identifier, request_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"second-brain/criterion/{identifier}/{request_id}"))


def _comparison(operator, target):
    if operator not in _OPERATORS:
        raise ValueError("Выберите условие сравнения <=, >= или =.")
    return operator, _number(target)


def _value(criterion, value):
    if criterion["kind"] == "numeric":
        return _number(value)
    if type(value) is not bool:
        raise ValueError("Для качественного критерия передайте подтверждённое да или нет.")
    return value


def _validate_observation(criterion, value, date, quote, line):
    if criterion["kind"] == "numeric":
        _check_quote(value, criterion["unit"], date, quote, full_line=line)
        return
    _check_date(date, line)
    # The owner classifies the cited statement. Where the statement explicitly
    # records a yes/no answer, reject a contradictory submitted choice.
    def answers(text):
        return {word.casefold() in ("да", "yes", "true") for word in _YES_NO.findall(text)}
    full, excerpt = answers(line), answers(quote)
    bare = quote.strip().casefold() in ("да", "нет", "yes", "no", "true", "false")
    if ((full and value not in full) or (excerpt and value not in excerpt)
            or (bare and value not in full)):
        raise ValueError("Ответ да/нет расходится с указанной строкой источника.")
    if len(full) > 1 and len(excerpt) != 1:
        raise ValueError("Выберите точную цитату с ответом для этого критерия.")


def _observation(root, criterion, value, date, source):
    value, date = _value(criterion, value), _measured_date(date)
    checked, line = evidence.verify_source_line(root, source)
    _validate_observation(criterion, value, date, checked["quote"], line)
    return {"value": value, "date": date, "source": checked}


def _same(a, b):
    return (a["value"] == b["value"] and a["date"] == b["date"]
            and all(a["source"][k] == b["source"][k] for k in ("id", "ref", "quote")))


def _visible(event, criterion):
    title = storage.markdown_label(criterion["title"])
    kind = event["event"]
    unit = storage.markdown_label(criterion["unit"]) if criterion["kind"] == "numeric" else ""
    if kind == "define":
        rule = "подтверждение да/нет"
        if criterion["kind"] == "numeric":
            rule = f'{_SYMBOLS[event["operator"]]} {event["target"]} {unit}'
            if criterion["primary"]:
                rule += " · основная метрика КР"
        return f"- Критерий · {title} · {rule}"
    if kind == "target":
        old_value = "—" if event["fromTarget"] is None else event["fromTarget"]
        old_rule = " ".join(filter(None, (_SYMBOLS.get(event["fromOperator"]), old_value)))
        new_rule = f'{_SYMBOLS[event["operator"]]} {event["target"]}'
        return f'- Ориентир · {title} · {old_rule} → {new_rule} {unit} · {event["changedAt"]}'
    label = "База" if kind == "baseline" else "Факт"
    value = (event["value"] + " " + unit if criterion["kind"] == "numeric" else ("да" if event["value"] else "нет"))
    source = event["source"]
    link = storage.markdown_link(f'{source["path"]}, строка {source["line"]}', source["path"], event["directory"])
    return f'- {label} · {title} · {event["date"]} · {value} · {link}'


def _row(event, criterion):
    return _visible(event, criterion) + _PREFIX + storage.comment_json(event) + " -->"


def _read(record):
    lines = record["body"].splitlines()
    if not any(marker in record["body"] for marker in (_BEGIN, _END, _PREFIX)):
        return {}, None
    try:
        if lines.count(_BEGIN) != 1 or lines.count(_END) != 1:
            raise ValueError
        begin, end = lines.index(_BEGIN), lines.index(_END)
        if begin >= end:
            raise ValueError
        criteria = {}
        for i, line in enumerate(lines):
            if _PREFIX not in line:
                if begin < i < end and line.startswith(("- Критерий · ", "- Ориентир · ", "- Факт · ", "- База · ")):
                    raise ValueError
                continue
            if not begin < i < end or not line.endswith(" -->"):
                raise ValueError
            visible, encoded = line.rsplit(_PREFIX, 1)
            event = json.loads(encoded[:-4])
            identifier = event["id"]
            if not isinstance(identifier, str) or str(uuid.UUID(identifier)) != identifier:
                raise ValueError
            action = event["event"]
            if action == "define":
                if identifier in criteria or type(event["primary"]) is not bool:
                    raise ValueError
                criterion = {k: event[k] for k in ("id", "kind", "title", "primary", "unit", "operator", "target")}
                _text(criterion["title"], "название критерия")
                if criterion["kind"] == "numeric":
                    _text(criterion["unit"], "единицу измерения")
                    _comparison(criterion["operator"], criterion["target"])
                    if criterion["primary"] and any(c["primary"] for c in criteria.values()):
                        raise ValueError
                elif (criterion["kind"] != "boolean" or criterion["primary"]
                      or any(criterion[k] is not None for k in ("unit", "operator", "target"))):
                    raise ValueError
                criterion.update(history=[], baselineHistory=[], targetHistory=[], requestHash=event["requestHash"])
                criteria[identifier] = criterion
            else:
                criterion = criteria[identifier]
                if action == "target":
                    if criterion["kind"] != "numeric" or not datetime.fromisoformat(event["changedAt"]).tzinfo:
                        raise ValueError
                    _comparison(event["operator"], event["target"])
                    change = {k: event[k] for k in ("fromOperator", "fromTarget", "operator", "target", "changedAt")}
                    criterion["targetHistory"].append(change)
                    criterion.update(operator=change["operator"], target=change["target"])
                elif action in ("measure", "baseline"):
                    if (action == "baseline" and criterion["kind"] != "numeric"
                            or action == "measure" and criterion["primary"]):
                        raise ValueError
                    value = _value(criterion, event["value"])
                    date = _measured_date(event["date"])
                    source = evidence.linked_source(event["source"])
                    _validate_observation(criterion, value, date, source["quote"], source["quote"])
                    ledger = criterion["baselineHistory" if action == "baseline" else "history"]
                    ledger.append({"value": value, "date": date, "source": source})
                else:
                    raise ValueError
            if visible != _visible(event, criterion):
                raise ValueError
        return criteria, end
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise storage.ConflictError(f'Проверьте раздел критериев в «{record["title"]}».') from error


def _resolve(root, observation):
    try:
        source = evidence.resolve_source(root, observation["source"])
    except (ValueError, OSError):
        source = {**observation["source"], "unavailable": True}
    return {**observation, "source": source}


def summarize(root, record, metric_history, detailed):
    criteria, _ = _read(record)
    result = []
    for c in criteria.values():
        c.pop("requestHash")
        for key in ("history", "baselineHistory"):
            if key == "history" and c["primary"]:
                c[key] = metric_history
            else:
                ordered = sorted(c[key], key=lambda row: row["date"])
                c[key] = [_resolve(root, obs) for obs in (ordered if detailed else ordered[-1:])]
        if c["primary"]:
            c["target"] = record.get("target")
            if c["unit"] != record["unit"]:
                raise storage.ConflictError("Единица основной метрики и её критерия расходятся.")
        measure = c["measurement"] = c["history"][-1] if c["history"] else None
        c["baseline"] = c["baselineHistory"][-1] if c["baselineHistory"] else None
        c["met"] = None
        after_baseline = not c["baseline"] or measure and measure["date"] >= c["baseline"]["date"]
        if measure and after_baseline and not measure["source"].get("unavailable"):
            if c["kind"] == "boolean":
                c["met"] = measure["value"]
            elif c["target"] is not None:
                actual, target = Decimal(measure["value"]), Decimal(c["target"])
                c["met"] = {"<=": actual <= target, ">=": actual >= target, "=": actual == target}[c["operator"]]
        if not detailed:
            for key in ("history", "baselineHistory", "targetHistory"):
                c.pop(key)
        result.append(c)
    values = [c["met"] for c in result]
    if record.get("target") is not None and not any(c["primary"] for c in result):
        # A pre-existing numeric target has no direction of comparison yet.
        values.append(None)
    return {"criteria": result, "criteriaMet": all(values) if values and None not in values else None}


def _save(service, entry, end, rows, *, target=None):
    from .okr import _parse_key_result
    path, original, record, header = entry
    newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
    if target is not None:
        header = storage._patch_header(header, record, {"target": target})
    lines = record["body"].splitlines(keepends=True)
    if end is None:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines.extend(text + newline for text in ("", "## Критерии результата", "", _BEGIN, *rows, _END))
    else:
        lines[end:end] = [text + newline for text in rows]
    updated = header + "".join(lines).encode()
    saved, _ = _parse_key_result(path, updated)
    result = service._summarize(path, saved, detailed=True)
    storage.replace_bytes(path, original, updated)
    return result


def _active(service, record):
    if record.get("objective_id"):
        service._active_plan(service._objective(record["objective_id"])["planId"])


def add_criterion(service, identifier, *, title, kind, revision, request_id, unit=None, operator=None,
                  target=None, primary=False, baseline=None):
    _valid_revision(revision)
    title = _text(title, "название критерия")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise ValueError("Укажите устойчивый идентификатор запроса.")
    if kind not in ("numeric", "boolean") or type(primary) is not bool:
        raise ValueError("Выберите числовое условие или подтверждение да/нет.")
    cid = criterion_id(identifier, request_id)
    if target is not None:
        target = _number(target)
    if unit is not None:
        unit = _text(unit, "единицу измерения")
    with storage._write_lock(service.root):
        entry = service._find(identifier)
        path, _, record, _ = entry
        criteria, end = _read(record)
        params = [title, kind, primary, unit, operator, target]
        if kind == "boolean":
            if primary or any(v is not None for v in (unit, operator, target, baseline)):
                raise ValueError("Качественный критерий хранит подтверждение да/нет и его основание.")
        else:
            if primary:
                if not record["unit"] or unit is not None and unit != record["unit"]:
                    raise ValueError("Основной критерий должен использовать единицу числовой метрики КР.")
                unit = record["unit"]
                if target is None:
                    target = criteria[cid]["target"] if cid in criteria else record.get("target")
            unit = _text(unit, "единицу измерения")
            operator, target = _comparison(operator, target)
        definition = {"id": cid, "event": "define", "kind": kind, "title": title, "unit": unit,
                      "operator": operator, "target": target, "primary": primary}
        rows = []
        if baseline is not None:
            if not isinstance(baseline, dict):
                raise ValueError("Укажите значение базы, дату и ссылку на строку.")
            obs = _observation(service.root, definition, baseline.get("value"), baseline.get("date"), baseline.get("source"))
            source = obs["source"]
            params.append([obs["value"], obs["date"], source["id"], source["ref"], source["quote"]])
            event = {"id": cid, "event": "baseline", "directory": path.parent.relative_to(service.root).as_posix(), **obs}
            rows.append(_row(event, definition))
        definition["requestHash"] = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        if cid in criteria:
            if criteria[cid]["requestHash"] != definition["requestHash"]:
                raise storage.ConflictError("Ключ запроса использован с другими параметрами критерия.")
            return service._summarize(path, record, detailed=True)
        if primary and any(c["primary"] for c in criteria.values()):
            raise ValueError("Для основной метрики КР уже определён критерий.")
        _check_revision(record, revision)
        _active(service, record)
        if primary and record.get("target") is not None and record["target"] != target:
            change = {"id": cid, "event": "target", "changedAt": datetime.now(timezone.utc).isoformat(),
                      "fromOperator": None, "fromTarget": record["target"], "operator": operator, "target": target}
            rows.insert(0, _row(change, definition))
        return _save(service, entry, end, [_row(definition, definition), *rows], target=target if primary else None)


def observe_criterion(service, identifier, criterion_id, *, value, date, source, revision, baseline=False):
    _valid_revision(revision)
    if type(baseline) is not bool:
        raise ValueError("Нужно явно выбрать исходное значение или фактический замер.")
    with storage._write_lock(service.root):
        entry = service._find(identifier)
        path, _, record, _ = entry
        criteria, end = _read(record)
        c = criteria.get(_text(criterion_id, "ID критерия"))
        if c is None:
            raise FileNotFoundError("Критерий не найден в указанном КР.")
        if c["primary"] and not baseline:
            return service.measure(identifier, value=value, date=date, source=source, revision=revision)
        if baseline and c["kind"] != "numeric":
            raise ValueError("Базовое значение применимо к числовому критерию.")
        obs = _observation(service.root, c, value, date, source)
        history = c["baselineHistory" if baseline else "history"]
        if any(_same(previous, obs) for previous in history):
            return service._summarize(path, record, detailed=True)
        _check_revision(record, revision)
        event = {"event": "baseline" if baseline else "measure", "id": criterion_id,
                 "directory": path.parent.relative_to(service.root).as_posix(), **obs}
        return _save(service, entry, end, [_row(event, c)])


def set_criterion_target(service, identifier, criterion_id, *, operator, target, revision):
    _valid_revision(revision)
    operator, target = _comparison(operator, target)
    with storage._write_lock(service.root):
        entry = service._find(identifier)
        path, _, record, _ = entry
        criteria, end = _read(record)
        c = criteria.get(_text(criterion_id, "ID критерия"))
        if c is None:
            raise FileNotFoundError("Критерий не найден в указанном КР.")
        if c["kind"] != "numeric":
            raise ValueError("Выберите числовой критерий для изменения ориентира.")
        old_target = record.get("target") if c["primary"] else c["target"]
        if c["operator"] == operator and old_target == target:
            return service._summarize(path, record, detailed=True)
        _check_revision(record, revision)
        _active(service, record)
        change = {"id": criterion_id, "event": "target", "changedAt": datetime.now(timezone.utc).isoformat(),
                  "fromTarget": old_target, "fromOperator": c["operator"], "operator": operator, "target": target}
        return _save(service, entry, end, [_row(change, c)], target=target if c["primary"] else None)
