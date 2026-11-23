"""Owner-authored Inbox rules and source-backed updates to shared project fields."""

import hashlib
import json
from pathlib import Path
import re

from . import storage
from .inbox import Inbox, KINDS, VISIBILITY
from .storage import markdown_label as _label, markdown_link as _link, replace_bytes as _replace
from .people import _single_line


_RULE_FIELDS = ("direction", "project_fields", "person_visibilities")
_LABELS = {"status": "Статус", "due": "Срок", "next_step": "Следующий шаг"}


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _permissions(values, allowed):
    if not isinstance(values, list) or not all(isinstance(v, str) and v in allowed for v in values):
        raise ValueError("В правиле указаны неизвестные поля проекта или области видимости.")
    return list(dict.fromkeys(values))


def _instructions(text):
    if not isinstance(text, str) or not text.strip() or "\0" in text:
        raise ValueError("Напишите, какие входящие относятся к направлению и как с ними поступать.")
    return text.strip()


def _rule_parts(data):
    lines = data.splitlines(keepends=True)
    end = next((n for n in range(1, len(lines)) if lines[n].rstrip(b"\r\n") in (b"---", b"...")), None)
    if not lines or lines[0].rstrip(b"\r\n") != b"---" or end is None:
        raise ValueError("Проверьте YAML-шапку правила разбора.")
    fields, numbers = {}, {}
    for n, raw in enumerate(lines[1:end], 2):
        key, sep, value = raw.decode("utf-8").partition(":")
        if not sep or key not in _RULE_FIELDS:
            continue
        if key in fields:
            raise ValueError("Повтор поля в правиле разбора.")
        numbers[key] = n
        fields[key] = storage._scalar(value) if key == "direction" else storage._aliases(value)
        # A flow list, including an explicit empty list, defines each permission set.
        if key != "direction" and not value.lstrip().startswith("["):
            raise ValueError("Запишите разрешения правила списком в квадратных скобках.")
    if fields.keys() != set(_RULE_FIELDS):
        raise ValueError("В правиле нужны direction, project_fields и person_visibilities.")
    fields["project_fields"] = _permissions(fields["project_fields"], storage.STATE_FIELDS)
    fields["person_visibilities"] = _permissions(fields["person_visibilities"], VISIBILITY)
    fields["instructions"] = _instructions(b"".join(lines[end + 1:]).decode("utf-8"))
    return fields, b"".join(lines[:end + 1]), numbers


class Routing:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def _rule_path(self, direction):
        # IDs in older vaults can contain punctuation unsafe in filenames.
        name = direction if re.fullmatch(r"[\w-]{1,90}", direction, re.ASCII) else "_" + _hash(direction.encode())
        return storage._inside(self.root, Path("Settings/Intake") / (name + ".md"))

    def _direction(self, direction):
        if not _single_line(direction):
            raise ValueError("Укажите ID существующего направления.")
        record = storage.read_record(storage.find_record(self.root, direction))
        if record["type"] != "direction" or record["id"] != direction:
            raise ValueError("Укажите ID существующего направления.")
        return record

    def _rule(self, record):
        direction = record["id"]
        path = self._rule_path(direction)
        row = {"direction": direction, "title": record["title"], "aliases": record["aliases"],
               "instructions": "", "project_fields": [], "person_visibilities": [],
               "revision": None, "path": path.relative_to(self.root).as_posix()}
        if path.exists():
            data = path.read_bytes()
            row["revision"] = _hash(data)
            try:
                fields, _, _ = _rule_parts(data)
                if fields["direction"] != direction:
                    raise ValueError("ID в правиле не совпадает с направлением.")
                row.update(fields)
            except ValueError as error:
                row["error"] = str(error)
        return row

    def rules(self):
        rows = []
        seen = set()
        for _, record in storage.iter_records(self.root):
            if record["type"] == "direction":
                if record["id"] in seen:
                    raise ValueError("Один ID направления найден в нескольких карточках.")
                seen.add(record["id"])
                rows.append(self._rule(record))
        return sorted(rows, key=lambda row: (row["title"].casefold(), row["direction"]))

    def save_rule(self, data):
        required = {"direction", "instructions", "project_fields", "person_visibilities"}
        if not isinstance(data, dict) or not required.issubset(data) or data.keys() - (required | {"expected_revision"}):
            raise ValueError("Укажите направление, инструкцию, поля проектов и доступ к фактам людей.")
        return self.set_rule(**data)

    def set_rule(self, direction, instructions, project_fields, person_visibilities, expected_revision=None):
        content = _instructions(instructions)
        permissions = {"project_fields": _permissions(project_fields, storage.STATE_FIELDS),
                       "person_visibilities": _permissions(person_visibilities, VISIBILITY)}
        with storage._write_lock(self.root):
            record = self._direction(direction)
            path = self._rule_path(direction)
            path.parent.mkdir(exist_ok=True)
            before = path.read_bytes() if path.exists() else None
            if expected_revision != (_hash(before) if before is not None else None):
                raise storage.ConflictError("Правила направления изменились. Перечитайте их.")
            if before is None:
                fields = {"direction": direction, **permissions}
                header = "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in fields.items()) + "---\n"
                header = header.encode("utf-8")
            else:
                previous, header, numbers = _rule_parts(before)
                if previous["direction"] != direction:
                    raise storage.ConflictError("Сверьте владельца файла правила в Markdown.")
                changes = {k: v for k, v in permissions.items() if previous[k] != v}
                if not changes and content == previous["instructions"]:
                    return self._rule(record)
                header = storage._patch_header(header, {"field_lines": numbers}, changes)
            newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
            after = header.rstrip(b"\r\n") + (newline * 2 + newline.join(content.splitlines()) + newline).encode()
            _replace(path, before, after)
            return self._rule(record)

    def _policy(self, action):
        rule = self._rule(self._direction(action.get("direction")))
        if not rule["revision"] or rule.get("error"):
            raise ValueError("У направления нет действующего правила разбора Inbox.")
        if action.get("rule_revision") != rule["revision"]:
            raise storage.ConflictError("Правила направления изменились. Сверьте фрагмент с текущими правилами.")
        return rule

    def validate_person(self, action):
        rule = self._policy(action)
        if action.get("visibility") not in rule["person_visibilities"]:
            raise ValueError("Правило направления не разрешает такой доступ к факту о человеке.")
        fact_kind = action.get("fact_kind")
        if not isinstance(fact_kind, str) or fact_kind not in KINDS:
            raise ValueError("Укажите вид факта о человеке: role, fact или observation.")

    def apply_project(self, action, source):
        with storage._write_lock(self.root):
            return self.apply_project_locked(action, source)

    def apply_project_locked(self, action, source):
        """Caller holds the vault write lock, including while checking the rule revision."""
        field = action.get("field")
        if field not in storage.STATE_FIELDS or action.get("visibility", "shared") != "shared":
            raise ValueError("Укажите общедоступное поле проекта.")
        project_id, value = action.get("project"), action.get("value")
        quote, line = action.get("quote"), action.get("line")
        if not _single_line(project_id) or not _single_line(value) or not _single_line(quote) or value not in quote:
            raise ValueError("Значение поля проекта должно дословно входить в цитату.")
        if type(line) is not int or line < 1:
            raise ValueError("Укажите номер строки оригинала.")
        source_id = source["id"] if isinstance(source, dict) else source
        evidence = Inbox(self.root).evidence(source_id, line)
        if quote not in evidence["text"]:
            raise ValueError("Цитата не совпадает со строкой оригинала.")
        path = storage.find_record(self.root, project_id)
        before = path.read_bytes()
        record, header = storage._parse_record(path, before)
        if record["id"] != project_id or record["type"] != "project":
            raise ValueError("Укажите ID проекта.")
        key = _hash(json.dumps([source_id, line, action.get("direction"), project_id, field]).encode())
        prefix = f"# inbox-change {key} ".encode()
        signature = _hash(value.encode())
        result = {"project": project_id, "field": field, "value": value, "path": path.relative_to(self.root).as_posix(),
                  "line": line, "source": {"id": source_id, "path": evidence["path"]}}
        # Keeping bookkeeping in the YAML header leaves only human-readable
        # changes in project search and source excerpts.
        markers = [m for m in header.splitlines(keepends=True) if m.startswith(prefix)]
        if markers:
            match = re.fullmatch(re.escape(prefix) + rb"([a-f0-9]{64}) ([a-f0-9]{64})", markers[0].rstrip(b"\r\n"))
            if len(markers) != 1 or not match or match[1].decode() != signature:
                raise storage.ConflictError("В истории уже сохранено другое решение для этого фрагмента и поля.")
            # Further planned fields may use this revision only if the card is
            # still byte-for-byte the output of the original write.
            untouched = _hash(before.replace(markers[0], b"", 1)) == match[2].decode()
            chain = record["revision"] if untouched else action.get("expected_revision")
            return {**result, "revision": record["revision"], "chain_revision": chain, "replayed": True}
        rule = self._policy(action)
        if field not in rule["project_fields"]:
            raise ValueError("Правило направления не разрешает запись в это общее поле проекта.")
        if record["direction"] != rule["direction"]:
            raise ValueError("Проект должен принадлежать выбранному направлению.")
        if record["revision"] != action.get("expected_revision"):
            raise storage.ConflictError("Проект изменился. Перечитайте карточку и сверьте решение.")
        if field in record["field_lines"] and field not in record:
            raise ValueError("Проверьте существующее нестандартное значение поля в Markdown.")
        old = record.get(field)
        header = storage._patch_header(header, record, {} if old == value else {field: value})
        newline = b"\r\n" if header.startswith(b"---\r\n") else b"\n"
        content = header + record["body"].encode()
        if not content.endswith(newline):
            content += newline
        original = self.root / evidence["path"]
        link = _link(f"Источник, строка {line}", original, path.parent)
        old_text = f" (прежде — {_label(old)})" if old and old != value else ""
        entry = f"- {_LABELS[field]} — {_label(value)}{old_text}. {link}"
        content += newline + entry.encode() + newline
        position = len(header) - len(header.splitlines(keepends=True)[-1])
        marker = prefix + f"{signature} {_hash(content)}".encode() + newline
        after = content[:position] + marker + content[position:]
        _replace(path, before, after)
        revision = _hash(after)
        return {**result, "revision": revision, "chain_revision": revision, "replayed": False}
