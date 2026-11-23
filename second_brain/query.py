"""Read answers and durable, line-level citations from an ordinary Markdown vault."""

import hashlib
from pathlib import Path
import re
from urllib.parse import quote

from . import questions, storage


def _line_ref(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_link(record, number, ref):
    return f"/source/{quote(record['id'], safe='')}?line={number}&ref={ref}#cited-line"


def _state_sources(root, path, record):
    """Expose just the YAML line of each public project state field."""
    if record["type"] != "project":
        return
    raw = record["raw"].splitlines()
    for field in storage.STATE_FIELDS:
        if not record.get(field):
            continue
        number = record["field_lines"][field]
        line = raw[number - 1]
        # A cleared field must never reattach to matching prose in the body.
        ref = _line_ref(f"project-field\0{field}\0{line}")
        yield field, {"path": path.relative_to(root).as_posix(), "start": number, "end": number,
                      "lines": [{"n": number, "text": line}], "ref": ref, "href": _source_link(record, number, ref)}


def source_fragment(root, path, record, number, *, linked=True):
    """Build a citation from a single file revision, including its nearby lines."""
    first = record["body_start"]
    lines = record["body"].splitlines()
    index = number - first
    if not 0 <= index < len(lines):
        raise storage.ConflictError("Строка источника изменилась. Повторите вопрос.")
    context = range(max(0, index - 2), min(len(lines), index + 3))
    source = {"path": path.relative_to(root).as_posix(), "start": number, "end": number,
              "lines": [{"n": first + i, "text": lines[i]} for i in context]}
    if linked:
        ref = _line_ref(lines[index])
        source.update(ref=ref, href=_source_link(record, number, ref))
        if "evidence" in record:
            evidence = record["evidence"]
            source["evidence"] = evidence
            source["evidenceHref"] = f"/evidence/{quote(evidence['personId'], safe='')}/{quote(evidence['factId'], safe='')}"
    return source


class WorkspaceQuery:
    """Read project and note bodies in a vault, optionally limited to named direction IDs."""

    def __init__(self, root, direction_ids=None, *, include_people=True):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"Хранилище не найдено: {self.root}")
        self.direction_ids = None if direction_ids is None else frozenset(direction_ids)
        self.include_people = include_people

    def _fact_record(self, fact):
        kind = {"role": "роль role", "fact": "факт fact", "observation": "наблюдение observation"}
        return {"id": f"person:{fact['personId']}:{fact['id']}", "title": fact["personName"],
                "aliases": fact.get("aliases", []), "direction": fact["direction"], "type": "person-fact",
                "body": fact["text"], "body_start": fact["cardLine"], "search_kind": kind[fact["kind"]],
                "evidence": {"personId": fact["personId"], "factId": fact["id"]}}

    def allows(self, record):
        return record["type"] in ("project", "note") and (
            self.direction_ids is None or record["direction"] in self.direction_ids
        )

    def ask(self, data):
        query = data.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("Введите вопрос длиной от 1 до 1000 символов.")
        selected = None
        if "directionIds" in data:
            ids = data["directionIds"]
            if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
                raise ValueError("Укажите список ID направлений.")
            selected = set(ids)

        scoped = {}
        directions = set()
        for path, record in storage.iter_records(self.root):
            if record["type"] == "direction":
                directions.add(record["id"])
            elif self.allows(record) and (selected is None or record["direction"] in selected):
                if record["id"] in scoped:
                    raise ValueError(f"Один ID найден в нескольких файлах: {record['id']}")
                scoped[record["id"]] = path, record
        if selected and (not selected <= directions or
                         self.direction_ids is not None and not selected <= self.direction_ids):
            raise ValueError("Выбранное направление не входит в доступную область поиска.")
        if self.include_people:
            from .people import People
            scope = self.direction_ids if selected is None else selected
            for fact in People(self.root).shared_facts(direction_ids=scope):
                record = self._fact_record(fact)
                scoped[record["id"]] = self.root / fact["path"], record

        matches, missing = questions.passages(query, [r for _, r in scoped.values()])
        items = []
        for record_id, (label, number) in matches.items():
            path, record = scoped[record_id]
            if number < record["body_start"]:
                current = storage.read_record(path, include_raw=True)
                if current["revision"] != record["revision"]:
                    raise storage.ConflictError("Карточка изменилась во время ответа. Повторите вопрос.")
                field, source = next((k, s) for k, s in _state_sources(self.root, path, current) if s["start"] == number)
                answer = current[field]
            else:
                source = source_fragment(self.root, path, record, number)
                answer = record["body"].splitlines()[number - record["body_start"]]
            items.append({"id": record_id, "title": record["title"], "label": label,
                          "supported": True, "answer": answer, "source": source})
        message = ""
        if not items:
            message = "В выбранных записях не найден фрагмент, подтверждающий ответ."
        elif missing:
            message = "Не найден подтверждающий фрагмент для: " + ", ".join(missing) + "."
        return {"items": items, "message": message}

    def open_citation(self, data):
        record_id, number, ref = data.get("id"), data.get("line"), data.get("ref")
        if (not isinstance(record_id, str) or not record_id or type(number) is not int or number < 1
                or not isinstance(ref, str) or not re.fullmatch("[0-9a-f]{64}", ref)):
            raise ValueError("Ссылка на строку источника неполна.")
        if self.include_people and record_id.startswith("person:"):
            from .people import People
            for fact in People(self.root).shared_facts(direction_ids=self.direction_ids):
                record = self._fact_record(fact)
                if record["id"] == record_id:
                    if _line_ref(fact["text"]) != ref:
                        raise storage.ConflictError("Формулировка изменилась. Задайте вопрос ещё раз.")
                    source = source_fragment(self.root, self.root / fact["path"], record, fact["cardLine"])
                    return record, source
            raise FileNotFoundError("Подтверждённый общий факт не найден.")
        path = storage.find_record(self.root, record_id)
        record = storage.read_record(path, include_raw=True)
        if record["id"] != record_id or not self.allows(record):
            raise ValueError("Запись находится за пределами выбранной области поиска.")
        for _, source in _state_sources(self.root, path, record):
            if source["ref"] == ref:
                return record, source
        first = record["body_start"]
        body = record["body"].splitlines()
        if not first <= number < first + len(body) or _line_ref(body[number - first]) != ref:
            # A file rename retains its ID. Inside a file, only follow identical, unique text.
            matches = [n for n, line in enumerate(body, first) if _line_ref(line) == ref]
            if len(matches) != 1:
                raise storage.ConflictError("Строка источника изменилась. Задайте вопрос ещё раз.")
            number = matches[0]
        return record, source_fragment(self.root, path, record, number)
