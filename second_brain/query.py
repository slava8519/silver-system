"""Read answers and durable, line-level citations from an ordinary Markdown vault."""

import hashlib
from itertools import islice
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

from . import archive_reader, questions, search_index, storage
from .search import visible_body_lines


def _line_ref(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_link(record, number, ref, archive_plan=None):
    plan = archive_plan or record.get("archivePlan")
    suffix = "&archivePlan=" + quote(plan, safe="") if plan else ""
    return f"/source/{quote(record['id'], safe='')}?line={number}&ref={ref}{suffix}#cited-line"


def _state_sources(root, path, record):
    """Expose just the YAML line of each public project state field."""
    if record["type"] != "project":
        return
    raw = record["raw"].splitlines()
    for field in storage.PROJECT_EVIDENCE_FIELDS:
        if not record.get(field):
            continue
        number = record["field_lines"][field]
        line = raw[number - 1]
        # A cleared field must never reattach to matching prose in the body.
        ref = _line_ref(f"project-field\0{field}\0{line}")
        yield field, {"path": path.relative_to(root).as_posix(), "start": number, "end": number,
                      "lines": [{"n": number, "text": line}], "ref": ref, "href": _source_link(record, number, ref)}


def source_fragment(root, path, record, number, *, linked=True, visible=None, cited_ref=None):
    """Build a citation from a single file revision, including its nearby lines."""
    first = record["body_start"]
    lines = record["body"].splitlines()
    index = number - first
    if not 0 <= index < len(lines):
        raise storage.ConflictError("Строка источника изменилась. Повторите вопрос.")
    if visible is None and record["type"] in ("project", "project_history"):
        visible = dict(visible_body_lines(record))
    if visible is not None and number not in visible:
        raise storage.ConflictError("Строка источника изменилась. Повторите вопрос.")
    context = range(max(0, index - 2), min(len(lines), index + 3))
    source = {"path": path.relative_to(root).as_posix(), "start": number, "end": number,
              "lines": [{"n": first + i, "text": visible[first+i] if isinstance(visible, dict) else lines[i]} for i in context
                        if visible is None or first + i in visible]}
    cited_text = archive_reader.original_citation(record, number, cited_ref)
    if cited_text is not None:
        next(row for row in source["lines"] if row["n"] == number)["text"] = cited_text
    if linked:
        ref = cited_ref if cited_text is not None else _line_ref(lines[index])
        plan = archive_reader.latest_plan(record) if record["type"] == "project" else None
        source.update(ref=ref, href=_source_link(record, number, ref, plan))
        if record["type"] == "project_history":
            source["id"] = record["id"]
        if plan:
            source["archivePlan"] = plan
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

    def _project_updates(self, request, scoped, limit, *, allow_state):
        from . import project_updates
        fields, names, message, period = request
        briefs, events, gaps = [], [], []
        total = 0
        for record_id, label in names.items():
            path, record = scoped[record_id]
            updates = project_updates.read_record_updates(self.root, path, record, limit=limit,
                                                          start=period[0], end=period[1], include_archived="event" in fields)
            warnings = updates.get("warnings", [])
            if warnings:
                gaps.append(f"«{label}» — часть журнала требует сверки. " + " ".join(warnings))
            if "brief" in fields:
                if updates["brief"]:
                    briefs.append((record, path, label, "brief", updates["brief"]))
                    total += 1
                elif allow_state and (record.get("status") or record.get("next_step")):
                    current = storage.read_record(path, include_raw=True)
                    if current["revision"] != record["revision"]:
                        raise storage.ConflictError("Карточка изменилась во время ответа. Повторите вопрос.")
                    for kind, source in _state_sources(self.root, path, current):
                        if kind in ("status", "next_step"):
                            briefs.append((record, path, label, kind, {"text": current[kind], "source": source}))
                            total += 1
                elif not warnings:
                    gaps.append(f"«{label}» — сводка «Коротко» пока не записана.")
            if "event" in fields:
                total += updates["total"]
                events.extend((record, path, label, "event", entry) for entry in updates["events"])
                if not updates["total"] and not warnings:
                    gap = ("в журнале проекта нет датированных событий за указанный период" if any(period)
                           else "в отдельном журнале проекта пока нет записей")
                    gaps.append(f"«{label}» — {gap}.")
        items = []
        for record, path, label, kind, entry in (briefs + events)[:limit]:
            source = entry.get("source")
            if source is None:
                number, end = entry["line"], entry.get("end", entry["line"])
                origin_path, origin_record = path, record
                if "archiveId" in entry:
                    origin_record = archive_reader.read_path(self.root, entry["path"], owner=(path, record))
                    origin_path = self.root / entry["path"]
                source = source_fragment(self.root, origin_path, origin_record, number, visible=range(number, end + 1))
                source["end"] = end
                if entry.get("sources"):
                    source["origin"] = next((s for s in entry["sources"] if s["available"]), entry["sources"][0])
            item = {"id": record["id"], "title": record["title"], "label": label, "kind": kind,
                    "supported": True, "answer": entry["text"], "source": source}
            if kind == "event" and entry.get("date"):
                item["date"] = entry["date"]
            items.append(item)
        return {"items": items, "total": total, "truncated": total > len(items),
                "message": " ".join(filter(None, [message, *gaps]))}

    def ask(self, data):
        query = data.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("Введите вопрос длиной от 1 до 1000 символов.")
        history = data.get("history", False)
        if type(history) is not bool:
            raise ValueError("Укажите логическое значение для history.")
        selected = None
        if "directionIds" in data:
            ids = data["directionIds"]
            if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
                raise ValueError("Укажите список ID направлений.")
            selected = set(ids)
            if self.direction_ids is not None and not selected <= self.direction_ids:
                raise ValueError("Выбранное направление не входит в доступную область поиска.")

        scope = self.direction_ids if selected is None else selected
        limit = data.get("limit")
        if "limit" in data and (type(limit) is not int or not 1 <= limit <= 20):
            raise ValueError("Количество результатов должно быть от 1 до 20.")
        # A restricted query must not index bodies outside its scope even on a cold cache.
        with search_index.optional(self.root, allow_sync=scope is None and self.include_people) as db:
            if db is not None:
                try:
                    return self._scoped_answer(query, scope, selected, db, limit, history)
                except sqlite3.DatabaseError:
                    # A damaged postings table can still have a readable catalogue.
                    pass
        return self._scoped_answer(query, scope, selected, None, limit, history)

    def _scoped_answer(self, query, scope, selected, db, limit, history):
        records = (search_index.records(self.root, db, scope=scope) if db is not None
                   else storage.iter_records(self.root, direction_ids=scope))
        scoped = {}
        directions = set()
        for path, record in records:
            if record["type"] == "direction":
                directions.add(record["id"])
            elif self.allows(record) and (scope is None or record["direction"] in scope):
                if record["id"] in scoped:
                    raise ValueError(f"Один ID найден в нескольких файлах: {record['id']}")
                scoped[record["id"]] = path, record
        if selected and not selected <= directions:
            raise ValueError("Выбранное направление не входит в доступную область поиска.")
        if history and (self.root / "Archive/ProjectHistory").is_dir():
            if db is not None:
                archives = search_index.archive_records(self.root, db, scope)
            else:
                projects = [(path, r) for path, r in scoped.values() if r["type"] == "project"]
                archives = (entry for path, project in projects for entry in archive_reader.parts(self.root, path, project))
            for path, record in archives:
                if record["project_id"] in scoped:
                    if record["id"] in scoped:
                        raise ValueError(f"Один ID найден в нескольких карточках: {record['id']}")
                    scoped[record["id"]] = path, record
        return self._answer(query, scoped, scope, db, limit, history)

    def _answer(self, query, scoped, scope, db, limit, history):
        def load(identifier, *, raw=False):
            path, record = scoped[identifier]
            if "body" not in record or (raw and "raw" not in record):
                if record["type"] == "project_history":
                    current = archive_reader.read_path(self.root, path)
                else:
                    current = storage.read_record(storage._inside(self.root, path.relative_to(self.root)), include_raw=raw)
                if current["revision"] != record["revision"]:
                    raise storage.ConflictError("Карточка изменилась во время ответа. Повторите вопрос.")
                scoped[identifier] = path, current
                return current
            return record

        projects = [r for _, r in scoped.values() if r["type"] == "project"]
        split = questions.split_update_question(query, projects) if not history else None
        if split:
            results = [self._answer(part, scoped.copy(), scope, db, limit, history) for part in split]
            total = sum(result.get("total", len(result["items"])) for result in results)
            items = [item for result in results for item in result["items"]][:limit]
            return {"items": items, "total": total, "truncated": total > len(items),
                    "message": " ".join(r["message"] for r in results if r["message"])}

        field_request = questions.project_field_request(query, projects) if not history else None
        if field_request is not None:
            targets, message = field_request
            items, gaps = [], []
            for identifier, label, field in targets:
                record = load(identifier, raw=True)
                path, _ = scoped[identifier]
                source = dict(_state_sources(self.root, path, record)).get(field)
                if source is None:
                    gaps.append(f"«{label}» — поле {field} не заполнено.")
                else:
                    items.append({"id": identifier, "title": record["title"], "label": label, "supported": True,
                                  "answer": record[field], "source": source})
            return {"items": items[:limit], "total": len(items), "truncated": len(items[:limit]) < len(items),
                    "message": " ".join(filter(None, [message, *gaps]))}

        update_request = questions.project_update_request(query, projects)
        if update_request and (not history or "event" in update_request[0]):
            if history:
                update_request = (["event"], *update_request[1:])
            for identifier in update_request[1]:
                load(identifier)
            return self._project_updates(update_request, scoped, limit or 5, allow_state=not questions.asks_for_brief(query))
        if self.include_people and not history:
            from .people import People
            facts = search_index.shared_facts(db, scope) if db is not None else People(self.root).shared_facts(direction_ids=scope)
            for fact in facts:
                if scope is None or fact["direction"] in scope:
                    record = self._fact_record(fact)
                    scoped[record["id"]] = self.root / fact["path"], record

        def candidates(mentions, details, decisions):
            lines = None
            if db is not None and not mentions and details:
                lines = search_index.matching_lines(db, details, scope, history=history)
            for identifier, (_, record) in scoped.items():
                if mentions and identifier not in mentions:
                    continue
                if lines is not None and record["type"] != "person-fact":
                    if identifier not in lines:
                        continue
                    # A recorded decision can inherit its meaning from a section heading.
                    # Other unnamed subjects are scored solely from matching source lines.
                    if not decisions:
                        yield {**record, "candidate_lines": lines[identifier], "history_only": history}
                        continue
                current = load(identifier)
                yield {**current, "candidate_lines": visible_body_lines(current, history=history), "history_only": history}

        matches, missing = questions.passages(query, [r for _, r in scoped.values()], candidates_for=candidates,
                                              history=history)
        if history:
            # A project and its quarterly parts share the same name in a question.
            missing = [label for label in missing if label not in matches.values()]
        items = []
        for (record_id, number), label in islice(matches.items(), limit):
            record = load(record_id)
            path, _ = scoped[record_id]
            if number < record["body_start"]:
                current = load(record_id, raw=True)
                field, source = next((k, s) for k, s in _state_sources(self.root, path, current) if s["start"] == number)
                answer = current[field]
            else:
                visible = dict(visible_body_lines(record, history=history))
                source = source_fragment(self.root, path, record, number, visible=visible)
                answer = visible[number]
            items.append({"id": record_id, "title": record["title"], "label": label,
                          "supported": True, "answer": answer, "source": source})
        message = ""
        if not items:
            message = "В выбранных записях не найден фрагмент, подтверждающий ответ."
        elif missing:
            message = "Не найден подтверждающий фрагмент для: " + ", ".join(missing) + "."
        result = {"items": items, "message": message}
        if limit is not None:
            result.update(total=len(matches), truncated=len(matches) > len(items))
        return result

    def open_citation(self, data):
        record_id, number, ref = data.get("id"), data.get("line"), data.get("ref")
        plan = data.get("archivePlan")
        if (not isinstance(record_id, str) or not record_id or type(number) is not int or number < 1
                or not isinstance(ref, str) or not re.fullmatch("[0-9a-f]{64}", ref)
                or plan is not None and (not isinstance(plan, str) or not re.fullmatch("[0-9a-f]{64}", plan))):
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
        if archive_reader.is_archive_id(record_id):
            record = archive_reader.by_id(self.root, record_id)
            path = self.root / record["path"]
            allowed = self.direction_ids is None or record["direction"] in self.direction_ids
        else:
            path = storage.find_record(self.root, record_id)
            record = storage.read_record(path, include_raw=True)
            allowed = self.allows(record)
        if record["id"] != record_id or not allowed:
            raise ValueError("Запись находится за пределами выбранной области поиска.")
        for _, source in _state_sources(self.root, path, record):
            if source["ref"] == ref:
                return record, source
        available = dict(visible_body_lines(record))
        lines = record["body"].splitlines()
        def matches(n):
            return _line_ref(lines[n - record["body_start"]]) == ref
        prior_rotation = record["type"] == "project" and archive_reader.latest_plan(record) != plan
        original = archive_reader.original_citation(record, number, ref)
        if prior_rotation or number not in available or (original is None and not matches(number)):
            candidates = [n for n in available if matches(n)]
            if prior_rotation:
                path, record, number = archive_reader.citation_line(self.root, path, record, number, ref, candidates, plan)
                if record["type"] == "project_history":
                    available = archive_reader.visible_lines(record)
            elif len(candidates) == 1:
                number = candidates[0]
            else:
                raise storage.ConflictError("Строка источника изменилась. Задайте вопрос ещё раз.")
        return record, source_fragment(self.root, path, record, number, visible=available, cited_ref=ref)
