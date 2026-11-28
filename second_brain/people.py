"""Canonical person cards with scoped, sourced facts in ordinary Markdown."""

import json
import os
import stat
import uuid
from pathlib import Path
from urllib.parse import quote

from . import storage


_KINDS = {"role": "Роль", "fact": "Факт", "observation": "Наблюдение"}
_AREAS = {"shared": "общее", "personal": "личное"}
_ACTIONS = {"observed": "Ручная правка при сверке", "corrected": "Уточнение"}
_FACT = "<!-- brain-fact "
_VERSION = "<!-- brain-version "
_FACT_FIELDS = ("id", "kind", "direction", "visibility", "sourceId", "sourcePath", "line", "quote")


def _single_line(value):
    return (isinstance(value, str) and bool(value.strip())
            and value.splitlines() == [value] and "\0" not in value)


def _stable_id(parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(parts, ensure_ascii=False)))


def selected_id(person, source_id):
    """Validate an identity choice and obtain its ID without changing the vault."""
    if not isinstance(person, dict):
        raise ValueError("Укажите выбранного человека или явно создайте новую карточку.")
    if set(person) == {"id"}:
        if not _single_line(person["id"]):
            raise ValueError("Укажите ID человека.")
        return person["id"]
    if (person.keys() - {"name", "new", "aliases", "key"} or person.get("new") is not True
            or not _single_line(person.get("name")) or not _single_line(source_id)):
        raise ValueError("Выберите ID существующей карточки или явно укажите имя и создание новой.")
    key = person.get("key", person["name"].strip().casefold())
    if not _single_line(key):
        raise ValueError("Укажите непустой ключ для различения тёзок.")
    aliases = person.get("aliases", [])
    if not isinstance(aliases, list) or not all(_single_line(alias) for alias in aliases):
        raise ValueError("Укажите список дополнительных имён человека.")
    return _stable_id(["second-brain/person", source_id, key])


def fact_id(person_id, source_id, fact):
    return _stable_id(["second-brain/fact", person_id, source_id, fact["kind"], fact["direction"],
                       fact["line"], fact["quote"] if "quote" in fact else fact["text"]])


def _marker(prefix, fields):
    content = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    # The source text is allowed to contain literal HTML comment delimiters.
    return prefix + content.replace("<", "\\u003c").replace(">", "\\u003e") + " -->"


def _markup(line, prefix):
    if not line.endswith(" -->"):
        raise ValueError("Повреждена служебная строка в карточке человека.")
    try:
        fields = json.loads(line[len(prefix):-4])
    except ValueError:
        fields = None
    if not isinstance(fields, dict):
        raise ValueError("Не удалось прочитать служебную строку в карточке человека.")
    return fields


def _parse_facts(record):
    facts = {}
    versions = {}
    lines = record["body"].splitlines()
    for i, line in enumerate(lines):
        if line.startswith(_FACT):
            metadata = _markup(line, _FACT)
            fact = {key: metadata.get(key) for key in _FACT_FIELDS}
            if (type(fact["line"]) is not int or fact["line"] < 1
                    or not all(_single_line(fact[k]) for k in _FACT_FIELDS if k != "line")
                    or fact["kind"] not in _KINDS or fact["visibility"] not in _AREAS
                    or fact["id"] in facts or fact["id"] != fact_id(record["id"], fact["sourceId"], fact)):
                raise ValueError("Проверьте поля и область видимости факта в карточке человека.")
            snapshot = fact
            facts[fact["id"]] = fact
            fact["cardLine"] = record["body_start"] + i + 1
        elif line.startswith(_VERSION):
            meta = _markup(line, _VERSION)
            if not _single_line(meta.get("id")) or not _single_line(meta.get("action")) or meta["action"] not in _ACTIONS:
                raise ValueError("Проверьте запись истории в карточке человека.")
            snapshot = {"action": meta["action"]}
            versions.setdefault(meta["id"], []).append(snapshot)
        else:
            continue
        if i + 1 >= len(lines) or not lines[i + 1].startswith("> ") or not lines[i + 1][2:].strip():
            raise ValueError("После метки факта или версии ожидается строка Markdown-цитаты.")
        snapshot["text"] = lines[i + 1][2:]
    for fact in facts.values():
        known = [{"action": "imported", "text": fact["quote"]}, *versions.get(fact["id"], ())]
        fact["editedInFile"] = known[-1]["text"] != fact["text"]
        fact["history"] = known if fact["editedInFile"] else known[:-1]
    return facts


def _snapshot(fact, previous, text, action):
    label = f"{_ACTIONS[action]} · {_KINDS[fact['kind']]} · строка источника {fact['line']}"
    return (f"\n**{label}**\n\nБыло\n> {previous}\n\nСтало\n"
            + _marker(_VERSION, {"id": fact["id"], "action": action}) + "\n> " + text + "\n")


class People:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = storage._inside(self.root, "People")

    def paths(self):
        """Enumerate possible cards without reading any body or following a symlink."""
        if self.directory.is_symlink():
            raise ValueError(f"Символическая ссылка в пути хранилища: {self.directory}")
        for current, directories, files in os.walk(self.directory):
            directories[:] = sorted(d for d in directories if not d.startswith(".") and not (Path(current) / d).is_symlink())
            for filename in sorted(files):
                path = Path(current) / filename
                if filename.startswith(".") or path.suffix.lower() != ".md" or path.is_symlink() or not path.is_file():
                    continue
                yield path

    def _cards(self, paths=None):
        seen = set()
        for path in self.paths() if paths is None else paths:
            data = path.read_bytes()
            try:
                record, _ = storage._parse_record(path, data, kinds={"person"})
            except storage.UnmanagedMarkdown:
                continue
            if not all(_single_line(value) for value in [record["id"], record["title"], *record["aliases"]]):
                raise ValueError("Проверьте ID, название и имена карточки человека.")
            if record["id"] in seen:
                raise ValueError("Один ID человека найден в нескольких карточках.")
            seen.add(record["id"])
            yield path, data, record

    def _locate(self, person_id):
        if not _single_line(person_id):
            raise ValueError("Укажите ID человека.")
        found = [entry for entry in self._cards() if entry[2]["id"] == person_id]
        if not found:
            raise FileNotFoundError("Карточка человека не найдена.")
        return found[0]

    def card_paths(self):
        """Yield canonical person files for vault folder counts."""
        for path, _, _ in self._cards():
            yield path

    def _summary(self, path, record):
        return {"id": record["id"], "name": record["title"], "path": path.relative_to(self.root).as_posix()}

    def candidates(self, match="", limit=10, *, direction_id=None, offset=0):
        return self.candidate_page(match, limit, direction_id=direction_id, offset=offset)["items"]

    def candidate_page(self, match="", limit=10, *, direction_id=None, offset=0):
        if not isinstance(match, str) or type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("Проверьте строку поиска, размер и начало страницы людей.")
        if direction_id is not None:
            storage._check_direction(self.root, "note", direction_id)
        term = match.strip().casefold()
        entries = []
        for path, _, record in self._cards():
            if not any(term in name.casefold() for name in [record["title"], *record["aliases"]]):
                continue
            if direction_id is not None and not any(f["visibility"] == "shared" and f["direction"] == direction_id
                                                    for f in _parse_facts(record).values()):
                continue
            entries.append(self._summary(path, record))
        entries.sort(key=lambda entry: (entry["name"].casefold(), entry["id"]))
        return {"items": entries[offset:offset + limit], "total": len(entries),
                "truncated": offset + limit < len(entries)}

    def _detail(self, path, record, personal):
        return {**self._summary(path, record), "aliases": record["aliases"], "revision": record["revision"],
                "facts": [f for f in _parse_facts(record).values() if f["visibility"] == "shared" or personal is True]}

    def detail(self, person_id, personal=False):
        path, _, record = self._locate(person_id)
        return self._detail(path, record, personal)

    def shared_facts(self, direction_ids=None):
        allowed = None if direction_ids is None else set(direction_ids)
        for path, _, record in self._cards():
            for fact in _parse_facts(record).values():
                if fact["visibility"] == "shared" and (allowed is None or fact["direction"] in allowed):
                    yield {**fact, "personId": record["id"], "personName": record["title"],
                           "aliases": record["aliases"], "path": path.relative_to(self.root).as_posix()}

    def resolve(self, person, source_id):
        """Resolve an explicitly selected identity under the caller's vault write lock."""
        person_id = selected_id(person, source_id)
        try:
            path, _, record = self._locate(person_id)
        except FileNotFoundError:
            if "id" in person:
                raise
            name, aliases = person["name"].strip(), person.get("aliases", [])
            fields = {"type": "person", "schema_version": 1, "id": person_id, "title": name, "aliases": aliases}
            header = "".join(key + ": " + json.dumps(val, ensure_ascii=False) + "\n" for key, val in fields.items())
            data = ("---\n" + header + "---\n# " + name + "\n").encode()
            stem = storage._filename(name)
            path = storage._inside(self.root, Path("People") / (stem + ".md"))
            with storage._staged(path, data) as staged:
                try:
                    os.link(staged, path)
                except FileExistsError:
                    path = path.with_name(f"{stem} ({person_id}).md")
                    os.link(staged, path)
            record = fields
        return self._summary(path, record)

    def _save(self, path, previous, content):
        encoded = content.encode("utf-8")
        if previous == encoded:
            return
        with storage._staged(path, encoded) as staged:
            staged.chmod(stat.S_IMODE(path.stat().st_mode))
            if path.is_symlink() or path.read_bytes() != previous:
                raise storage.ConflictError("Карточка человека изменилась. Перечитайте её.")
            os.replace(staged, path)

    def apply(self, person_record, source, facts, *, confirmed_ids=()):
        """Attach sourced facts once; the import service holds the vault write lock."""
        path, data, record = self._locate(person_record["id"])
        existing = _parse_facts(record)
        addition = []
        for fact in facts:
            key = fact_id(record["id"], source["id"], fact)
            previous = existing.get(key)
            if previous:
                if previous["visibility"] != fact["visibility"]:
                    raise storage.ConflictError("Этот факт уже сохранён с другой областью видимости.")
                if previous["editedInFile"]:
                    addition.append(_snapshot(previous, previous["history"][-1]["text"], previous["text"], "observed"))
                    previous["editedInFile"] = False
                continue
            if key in confirmed_ids:
                raise storage.ConflictError("Ранее подтверждённый фрагмент удалён из карточки. Сверьте её перед разбором.")
            direction = storage.find_record(self.root, fact["direction"])
            direction_record = storage.read_record(direction)
            if direction_record["type"] != "direction":
                raise ValueError("Для факта нужен ID направления.")
            scope = _AREAS[fact["visibility"]]
            role = _KINDS[fact["kind"]]
            meta = {"id": key, "kind": fact["kind"], "direction": fact["direction"], "visibility": fact["visibility"],
                    "sourceId": source["id"], "sourcePath": source["path"], "line": fact["line"], "quote": fact["text"]}
            direction_link = quote(os.path.relpath(direction, path.parent).replace(os.sep, "/"))
            original_link = quote(os.path.relpath(self.root / source["path"], path.parent).replace(os.sep, "/"))
            addition.append(f"\n### {role} · {direction_record['title']} · {scope}\n" + _marker(_FACT, meta)
                            + "\n> " + fact["text"] + "\n\n" + f"[Направление]({direction_link}) · "
                            + f"[Источник, строка {fact['line']}]({original_link})\n")
            existing[key] = {**meta, "text": fact["text"], "history": [], "editedInFile": False}
        if addition:
            content = data.decode("utf-8")
            newline = "\r\n" if content.startswith("---\r\n") else "\n"
            if not content.endswith(("\r", "\n")):
                content += newline
            self._save(path, data, content + "".join(addition).replace("\n", newline))
        return self.detail(record["id"], personal=True)

    def correct(self, data):
        """Patch only the addressed current line, preserving the original and known revisions."""
        if not isinstance(data, dict) or not _single_line(data.get("text")) or not _single_line(data.get("factId")):
            raise ValueError("Уточнение должно занимать одну непустую строку.")
        personal = data.get("personal", False) is True
        with storage._write_lock(self.root):
            path, original, record = self._locate(data.get("id"))
            if record["revision"] != data.get("revision"):
                raise storage.ConflictError("Карточка человека изменилась. Перечитайте её.")
            fact = _parse_facts(record).get(data.get("factId"))
            if not fact or (fact["visibility"] != "shared" and not personal):
                raise ValueError("Факт не найден в выбранной области.")
            if data["text"] != fact["text"]:
                lines = original.decode().splitlines(keepends=True)
                line = lines[fact["cardLine"] - 1]
                newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
                lines[fact["cardLine"] - 1] = "> " + data["text"] + (line[len(line.rstrip("\r\n")):] or newline)
                additions = []
                if fact["editedInFile"]:
                    additions.append(_snapshot(fact, fact["history"][-1]["text"], fact["text"], "observed"))
                additions.append(_snapshot(fact, fact["text"], data["text"], "corrected"))
                content = "".join(lines)
                if not content.endswith(("\r", "\n")):
                    content += newline
                self._save(path, original, content + "".join(additions).replace("\n", newline))
            return self.detail(record["id"], personal=personal)
