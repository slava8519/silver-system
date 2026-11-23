"""Optional key results with measured evidence and references to existing tasks."""

from decimal import Decimal
import hashlib
import json
import re
import uuid

from . import evidence, planning, storage


_KINDS = {"key_result": "Plans/OKR"}
_FIELDS = ("objective", "unit", "target")
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


def _parse_key_result(path, data):
    record, header = storage._parse_record(path, data, kinds=_KINDS, optional_scalars=_FIELDS)
    for field in ("objective", "direction", "unit"):
        _text(record.get(field), field)
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
    return record, header


def _measured_date(value):
    if value is None:
        raise ValueError("Укажите дату измерения.")
    return planning.validate_due(value)


_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря")
_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})(?!\d)"),
    re.compile(r"(?<!\d)(?P<day>\d{1,2})\s+(?P<month>" + "|".join(_MONTHS) + r")\s+(?P<year>\d{4})(?!\d)", re.I),
)
_GROUPED_INTEGER = r"[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+"
_QUANTITY = rf"(?<![\w.,+−/-])([+−-]?(?:(?:{_GROUPED_INTEGER}|[0-9]+)(?:[.,][0-9]+)?|[.,][0-9]+))"
_UNGROUP = str.maketrans("", "", " \u00a0\u202f")


def _check_quote(value, unit, date, quote, *, full_line=None):
    # A short quotation must contain a whole measured quantity from the
    # original line. Substrings of a signed value, a date or a group of
    # thousands can otherwise masquerade as independent measurements.
    line = full_line if full_line is not None else quote
    dates = {}
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(line):
            month = m["month"].lower()
            number = _MONTHS.index(month) + 1 if month in _MONTHS else int(month)
            dates[m.span()] = f'{m["year"]}-{number:02}-{int(m["day"]):02}'
    if dates and date not in dates.values():
        raise ValueError("Дата измерения расходится с датой в источнике.")
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


class KeyResults:
    def __init__(self, root):
        self.planning = planning.Planning(root)
        self.root = self.planning.root

    def _entries(self):
        seen = set()
        for path in storage.iter_markdown_paths(self.root, folders=_KINDS.values()):
            data = path.read_bytes()
            try:
                record, header = _parse_key_result(path, data)
            except storage.UnmanagedMarkdown:
                continue
            if record["id"] in seen:
                raise ValueError(f'Один ID ключевого результата найден в нескольких файлах: {record["id"]}')
            seen.add(record["id"])
            yield path, data, record, header

    def _find(self, identifier):
        matches = [row for row in self._entries() if row[2]["id"] == identifier]
        if not matches:
            raise FileNotFoundError(f"Ключевой результат не найден: {identifier}")
        return matches[0]

    def _summarize(self, path, record, detailed=False):
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
        return result

    def list(self, direction=None):
        with storage._vault_lock(self.root, write=False):
            return [self._summarize(path, record) for path, _, record, _ in self._entries()
                    if direction is None or record["direction"] == direction]

    def show(self, identifier):
        with storage._vault_lock(self.root, write=False):
            path, _, record, _ = self._find(identifier)
            return self._summarize(path, record, detailed=True)

    def _task_link(self, directory, task_id):
        task = self.planning.task(task_id)
        link = storage.markdown_link(task["title"], self.root / task["path"], directory)
        return f"- {link} {storage.id_marker('task', task_id)}"

    def create(self, title, *, objective, direction, unit, target=None, task_ids=(), request_id=None):
        fields = {"type": "key_result", "schema_version": 1, "title": _text(title, "название результата"),
                  "objective": _text(objective, "цель"), "unit": _text(unit, "единицу измерения"),
                  "direction": _text(direction, "ID направления")}
        if target is not None:
            fields["target"] = _number(target)
        if not isinstance(task_ids, (tuple, list)):
            raise ValueError("Передайте список ID существующих задач.")
        ids = list(dict.fromkeys(_text(k, "ID задачи") for k in task_ids))
        if request_id is None:
            identifier = str(uuid.uuid4())
        else:
            if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
                raise ValueError("Некорректный идентификатор запроса.")
            identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, "second-brain/key-result/" + request_id))
            fields["creation_hash"] = hashlib.sha256(json.dumps([fields, ids], sort_keys=True).encode()).hexdigest()
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
            folder = storage._inside(self.root, _KINDS["key_result"])
            direction_path = storage.find_record(self.root, fields["direction"])
            area = storage.read_record(direction_path)
            if area["type"] != "direction":
                raise ValueError("Укажите ID существующего направления.")
            body = [f'# {storage.markdown_label(fields["title"])}', '',
                    storage.markdown_link(area["title"], direction_path, folder), '',
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

    def link(self, identifier, task_id, revision):
        _valid_revision(revision)
        with storage._write_lock(self.root):
            entry = self._find(identifier)
            path, _, record, _ = entry
            ids, end = planning._membership(record["body"])
            if end is None:
                raise ValueError("Проверьте границы списка связанных работ в карточке.")
            line = self._task_link(path.parent, task_id)
            if task_id in ids:
                return self._summarize(path, record, detailed=True)
            _check_revision(record, revision)
            return self._insert(entry, end, line)

    def measure(self, identifier, *, value, date, source, revision):
        _valid_revision(revision)
        value, date = _number(value), _measured_date(date)
        with storage._write_lock(self.root):
            entry = self._find(identifier)
            path, _, record, _ = entry
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
