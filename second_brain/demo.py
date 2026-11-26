"""Loopback-only guided reader for an editable example vault."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from . import demo_cleanup, search, storage
from .query import WorkspaceQuery, source_fragment
from .examples import create_examples
from .webserver import server, serve


WEB = Path(__file__).resolve().parent / "web"
SCENARIOS = WEB.parent.parent / "examples" / "scenarios.json"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _field(data, name, limit):
    value = data.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        labels = {"title": "название направления", "noteTitle": "название заметки",
                  "body": "текст заметки", "query": "поисковый запрос"}
        raise ValueError(f"Проверьте поле «{labels.get(name, name)}» (до {limit} символов).")
    return value.strip()


def _unanswered(source=None, message="В записях не найден ответ на этот вопрос."):
    return {"answer": message, "supported": False, "source": source}


class DemoVault:
    def __init__(self, root):
        root = Path(root).expanduser()
        if root.is_symlink():
            raise ValueError("Для деморежима выберите обычную папку.")
        if not root.exists():
            create_examples(root)
        self.root = root.resolve()
        self.scenarios = _json(SCENARIOS)
        self.directory = storage._inside(self.root, "Settings/Demo")
        with storage._write_lock(self.root):
            catalog = self.directory / "catalog.json"
            live = {record["id"]: record for _, record in storage.iter_records(self.root)}
            if catalog.is_file() and not catalog.is_symlink():
                self.catalog = _json(catalog)
            else:
                # Bind pre-existing examples once, then follow their IDs across renames.
                sources = {}
                for scenario in self.scenarios:
                    areas = {r["id"] for r in live.values() if r["type"] == "direction"
                             and r["title"] == scenario["direction"]}
                    for example in scenario["records"]:
                        matches = [r["id"] for r in live.values() if r["direction"] in areas
                                   and r["type"] == example["type"] and r["title"] == example["title"]]
                        if len(matches) == 1:
                            sources[example["title"]] = matches[0]
                if not sources:
                    raise ValueError("Укажите новую папку для деморежима или хранилище с тремя примерами.")
                self.directory.mkdir(parents=True, exist_ok=True)
                self.catalog = {"vaultId": str(uuid.uuid4()), "records": sources}
            if "directions" not in self.catalog:
                # Older demo catalogs only stored source IDs. Recover their directions
                # from those links and keep them stable when users rename their files.
                directions = {}
                for scenario in self.scenarios:
                    label = scenario["direction"]
                    named = {r["id"] for r in live.values() if r["type"] == "direction"
                             and r["title"] == label}
                    linked = {live[record_id]["direction"] for example in scenario["records"]
                              for record_id in [self.catalog["records"].get(example["title"])]
                              if record_id in live and live[record_id]["direction"] in live}
                    candidates = (named & linked) or named or linked
                    if len(candidates) == 1:
                        directions[label] = candidates.pop()
                self.catalog["directions"] = directions
                with storage._staged(catalog, json.dumps(self.catalog, ensure_ascii=False).encode()) as temp:
                    os.replace(temp, catalog)
            for request in self._requests():
                if not (request / "done").exists():
                    self._finish(request)
        self.query = WorkspaceQuery(self.root, self.catalog["directions"].values(), include_people=False)

    def _requests(self):
        return (p for p in sorted(self.directory.iterdir())
                if re.fullmatch(r"[0-9a-f]{64}", p.name) and not p.is_symlink()
                and (p / "request.json").is_file())

    def _questions(self, scenario_id, scenario):
        number = 0
        for example in scenario["records"]:
            for key in ("question", "unanswered"):
                if key in example:
                    number += 1
                    yield f"{scenario_id}-{number}", example, key

    def guide(self):
        live = {record["id"]: record for _, record in storage.iter_records(self.root)}
        scenarios = []
        for n, scenario in enumerate(self.scenarios, 1):
            questions = []
            for question_id, example, key in self._questions(n, scenario):
                record = live.get(self.catalog["records"].get(example["title"]), {})
                question = {"id": question_id, "text": example[key],
                            "recordTitle": record.get("title", example["title"])}
                if record.get("type") == "project":
                    question["goal"] = next((line for line in record["body"].splitlines()
                                             if line.startswith("Цель")), "")
                questions.append(question)
            scenarios.append({"id": n, "title": scenario["direction"],
                              "description": scenario["description"], "questions": questions})
        return {"vaultId": self.catalog["vaultId"], "scenarios": scenarios,
                "custom": list(self._custom(live).values())}

    def _custom(self, live):
        reserved = set(self.catalog["directions"].values())
        directions = {r["id"]: {"id": r["id"], "title": r["title"], "notes": []}
                      for r in live.values() if r["type"] == "direction" and r["id"] not in reserved}
        for r in live.values():
            if r["type"] == "note" and r["direction"] in directions:
                directions[r["direction"]]["notes"].append({"id": r["id"], "title": r["title"]})
        return directions

    def open_citation(self, data):
        return self.query.open_citation(data)

    def ask(self, data):
        if "query" in data and "noteId" not in data and "questionId" not in data:
            return self.query.ask(data)
        daily = False
        example = None
        if "questionId" in data:
            choice = _field(data, "questionId", 32)
            for n, scenario in enumerate(self.scenarios, 1):
                for question_id, candidate, key in self._questions(n, scenario):
                    if question_id == choice:
                        example, query, daily = candidate, candidate[key], key == "unanswered"
            if example is None:
                raise ValueError("Выберите один из вопросов деморежима.")
            record_id = self.catalog["records"].get(example["title"])
            if not record_id:
                return _unanswered()
        else:
            record_id = _field(data, "noteId", 80)
            query = _field(data, "query", 1000)
            live = {r["id"]: r for _, r in storage.iter_records(self.root)}
            record = live.get(record_id)
            if not record or record["type"] != "note" or record["direction"] not in self._custom(live):
                raise ValueError("Выберите свою сохранённую заметку.")

        hits = search.search(self.root, query, limit=1, record_id=record_id)
        if not hits:
            return _unanswered()
        hit = hits[0]
        path = storage.find_record(self.root, record_id)
        record = storage.read_record(path)
        body = record["body"].splitlines()
        first = record["body_start"]
        number = hit["start"]
        if not first <= number < first + len(body) or hit["text"] != body[number - first]:
            return _unanswered()
        if daily:
            # The guided follow-up asks specifically about a day, not the first observation.
            day = re.compile(r"\b(?:(?:через|спустя)\s+(?:сутки|день|24\s+часа)|"
                             r"сутки\s+спустя|на\s+следующий\s+день)\b", re.I)
            day_line = next((i for i, line in enumerate(body, first) if day.search(line)), None)
            if day_line is None:
                source = source_fragment(self.root, path, record, number, linked=self.query.allows(record))
                return _unanswered(source, "Поиск не нашёл наблюдения через сутки.")
            number = day_line
        source = source_fragment(self.root, path, record, number, linked=self.query.allows(record))
        line = body[number - first]
        # An alias or title may retrieve a file even when its contents are unrelated.
        body_words = search._normalize(line)
        terms = re.findall(r'"([^"]+)"', search._match_query(query))
        if example and not daily:
            reference = search._normalize(example["evidence"])
            terms = [term for term in terms if term in reference]
        if not daily and not any(term in body_words for term in terms):
            if example or query.casefold() != record["title"].casefold():
                return _unanswered(source, "Запись найдена, но фрагмент не подтверждает ответ.")
        return {"answer": line, "supported": True, "source": source}

    def _finish(self, request):
        # Both ordinary storage records were staged before the durable request appeared.
        # Completing a partial write never replaces a user's edits or an existing file.
        saved = _json(request / "request.json")
        if not (request / "done").is_file():
            live = {record["id"] for _, record in storage.iter_records(self.root)}
            for kind in ("direction", "note"):
                if saved[kind] in live:
                    continue
                relative = Path(saved["files"][kind])
                if (len(relative.parts) != 2 or relative.parts[0] != storage.KINDS[kind]
                        or relative.suffix != ".md"):
                    raise ValueError("Проверьте пути в незавершённой записи деморежима.")
                prepared = storage._inside(request, Path("staged") / relative)
                staged_record = storage.read_record(prepared)
                if staged_record["id"] != saved[kind] or staged_record["type"] != kind:
                    raise ValueError("Подготовленная запись изменилась.")
                dest = storage._inside(self.root, relative)
                if dest.exists():
                    dest = dest.with_name(f"{dest.stem} ({saved[kind]}).md")
                os.link(prepared, dest)
            (request / "done").touch()
        staged = request / "staged"
        if staged.is_dir():
            shutil.rmtree(staged)
        custom = self._custom({r["id"]: r for _, r in storage.iter_records(self.root)})
        if saved["direction"] not in custom:
            raise ValueError("Сохранённое направление было удалено.")
        return custom[saved["direction"]]

    def add_direction(self, data):
        key = _field(data, "requestId", 128)
        title = _field(data, "title", 140)
        note_title = _field(data, "noteTitle", 140)
        body = _field(data, "body", 6000)
        digest = hashlib.sha256(json.dumps([title, note_title, body]).encode()).hexdigest()
        request = self.directory / hashlib.sha256(key.encode()).hexdigest()
        with storage._write_lock(self.root):
            if demo_cleanup.is_cleaned(self.root):
                raise storage.ConflictError("Деморежим завершён. Откройте рабочий обзор этой папки.")
            if request.exists():
                if request.is_symlink() or _json(request / "request.json")["digest"] != digest:
                    raise ValueError("Эта попытка сохранения относится к другой записи.")
                return self._finish(request)
            if any(r["type"] == "direction" and r["title"].casefold() == title.casefold()
                   for _, r in storage.iter_records(self.root)):
                raise ValueError("Такое направление уже есть. Выберите другое название.")
            with tempfile.TemporaryDirectory(dir=self.directory) as temp:
                pending = Path(temp) / "request"
                staged = pending / "staged"
                storage.initialize(staged)
                direction = storage.create(staged, "direction", title)
                area_id = storage.read_record(direction)["id"]
                note = storage.create(staged, "note", note_title, body=body, direction=area_id)
                saved = {"digest": digest, "direction": area_id, "note": storage.read_record(note)["id"],
                         "files": {"direction": direction.relative_to(staged).as_posix(),
                                   "note": note.relative_to(staged).as_posix()}}
                _write_json(pending / "request.json", saved)
                pending.rename(request)
            return self._finish(request)
