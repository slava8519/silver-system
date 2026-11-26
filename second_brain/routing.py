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
_PROJECT_PERMISSIONS = (*storage.STATE_FIELDS, "event", "summary")
_DEFAULT_PROJECT_FIELDS = ("event", "summary")
_DEFAULT_INSTRUCTIONS = ("Для однозначно названного проекта сохраняй подтверждённые общедоступные события. "
                         "Обновляй «Коротко», когда новый факт меняет положение проекта.")
_UNSET = object()
_LABELS = {"status": "Статус", "due": "Срок", "next_step": "Следующий шаг"}


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _permissions(values, allowed):
    if not isinstance(values, list) or not all(isinstance(v, str) and v in allowed for v in values):
        raise ValueError("В правиле указаны неизвестные поля проекта или области видимости.")
    return list(dict.fromkeys(values))


def _project_permissions(values):
    fields = _permissions(values, _PROJECT_PERMISSIONS)
    if "summary" in fields and "event" not in fields:
        raise ValueError("Для «Коротко по событиям проекта» (summary) включите «События проекта» (event).")
    return fields


def _instructions(text):
    if not isinstance(text, str) or not text.strip() or "\0" in text:
        raise ValueError("Напишите, какие входящие относятся к направлению и как с ними поступать.")
    return text.strip()


def _replayed(before, header, prefix, signature, expected_revision, *, event=False):
    markers = [m for m in header.splitlines(keepends=True) if m.startswith(prefix)]
    if not markers:
        return None
    pattern = rb"([a-f0-9]{64}) ([a-f0-9]{64})"
    if event:
        pattern += rb" (updated|unchanged|manual_preserved|older_event|not_permitted)"
    match = re.fullmatch(re.escape(prefix) + pattern, markers[0].rstrip(b"\r\n"))
    if len(markers) != 1 or not match or match[1].decode() != signature:
        raise storage.ConflictError("В истории уже сохранено другое решение для этого фрагмента.")
    # Advance the next field's revision only if no other edits followed this write.
    untouched = _hash(before.replace(markers[0], b"", 1)) == match[2].decode()
    revision = _hash(before)
    result = {"revision": revision, "chain_revision": revision if untouched else expected_revision, "replayed": True}
    if event:
        result["summaryStatus"] = match[3].decode("ascii")
    return result


def _save_marked(path, before, content, header, prefix, signature, summary_status=None):
    newline = b"\r\n" if header.startswith(b"---\r\n") else b"\n"
    position = len(header) - len(header.splitlines(keepends=True)[-1])
    marker = prefix + f"{signature} {_hash(content)}".encode()
    if summary_status:
        marker += b" " + summary_status.encode("ascii")
    marker += newline
    after = content[:position] + marker + content[position:]
    _replace(path, before, after)
    revision = _hash(after)
    return {"revision": revision, "chain_revision": revision, "replayed": False}


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
    if not {"direction", "person_visibilities"} <= fields.keys():
        raise ValueError("В правиле нужны direction и person_visibilities.")
    fields.setdefault("project_fields", list(_DEFAULT_PROJECT_FIELDS))
    fields["project_fields"] = _project_permissions(fields["project_fields"])
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
               "revision": None, "path": None}
        if path.exists():
            data = path.read_bytes()
            row["revision"] = _hash(data)
            row["path"] = path.relative_to(self.root).as_posix()
            try:
                fields, _, _ = _rule_parts(data)
                if fields["direction"] != direction:
                    raise ValueError("ID в правиле не совпадает с направлением.")
                row.update(fields)
            except ValueError as error:
                row["error"] = str(error)
        else:
            row.update(instructions=_DEFAULT_INSTRUCTIONS, project_fields=list(_DEFAULT_PROJECT_FIELDS))
        return row

    def rules(self):
        rows = (self._rule(record) for record in storage.direction_headers(self.root))
        return sorted(rows, key=lambda row: (row["title"].casefold(), row["direction"]))

    def save_rule(self, data):
        required = {"direction", "instructions"}
        allowed = required | {"project_fields", "person_visibilities", "expected_revision"}
        if not isinstance(data, dict) or not required.issubset(data) or data.keys() - allowed:
            raise ValueError("Укажите направление и инструкцию разбора. Разрешения передаются списками.")
        return self.set_rule(**data)

    def set_rule(self, direction, instructions, project_fields=_UNSET, person_visibilities=_UNSET, expected_revision=None):
        content = _instructions(instructions)
        with storage._write_lock(self.root):
            record = self._direction(direction)
            path = self._rule_path(direction)
            path.parent.mkdir(exist_ok=True)
            before = path.read_bytes() if path.exists() else None
            if expected_revision != (_hash(before) if before is not None else None):
                raise storage.ConflictError("Правила направления изменились. Перечитайте их.")
            if before is None:
                previous = {"project_fields": list(_DEFAULT_PROJECT_FIELDS), "person_visibilities": []}
            else:
                previous, header, numbers = _rule_parts(before)
                if previous["direction"] != direction:
                    raise storage.ConflictError("Сверьте владельца файла правила в Markdown.")
            permissions = {
                "project_fields": previous["project_fields"] if project_fields is _UNSET else _project_permissions(project_fields),
                "person_visibilities": previous["person_visibilities"] if person_visibilities is _UNSET
                                       else _permissions(person_visibilities, VISIBILITY),
            }
            if before is None:
                fields = {"direction": direction, **permissions}
                header = "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in fields.items()) + "---\n"
                header = header.encode("utf-8")
            else:
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
        if rule.get("error"):
            raise ValueError("У направления нет действующего правила разбора Inbox.")
        if "rule_revision" not in action or action["rule_revision"] != rule["revision"]:
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

    def apply_project_event_locked(self, action, source):
        """Store only the reviewed public excerpt from the immutable Inbox original."""
        from . import project_updates
        from .evidence import require_event_date

        project_id, line, quote = action["project"], action["line"], action["quote"]
        if action.get("visibility", "shared") != "shared":
            raise ValueError("События проекта доступны в общем поиске. Выберите общую цитату.")
        original = Inbox(self.root).evidence(source["id"], line)
        if not _single_line(quote) or quote not in original["text"]:
            raise ValueError("Цитата события не совпадает со строкой оригинала.")
        date = action.get("date")
        if date is not None:
            require_event_date(date, [{"quote": quote}])
        path = storage.find_record(self.root, project_id)
        before = path.read_bytes()
        record, header = storage._parse_record(path, before)
        if record["id"] != project_id or record["type"] != "project":
            raise ValueError("Укажите ID проекта.")
        text = action.get("text") or quote
        summary = action.get("summary")
        event_key = _hash(json.dumps([source["id"], line, quote, project_id]).encode())
        prefix = f"# inbox-event {event_key} ".encode()
        signature = _hash(json.dumps([action["direction"], date, text, summary]).encode())
        result = {"eventId": event_key, "path": path.relative_to(self.root).as_posix()}
        expected = action["expected_revision"]
        replay = _replayed(before, header, prefix, signature, expected, event=True)
        if replay:
            return {**result, **replay}
        rule = self._policy(action)
        if "event" not in rule["project_fields"]:
            raise ValueError("Правило направления не разрешает сохранять события проекта.")
        if record.get("direction") != rule["direction"]:
            raise ValueError("Проект должен принадлежать выбранному направлению.")
        if record["revision"] != expected:
            raise storage.ConflictError("Проект изменился. Перечитайте карточку и сверьте решение.")
        excerpt = {"id": source["id"], "path": original["path"], "line": line, "quote": quote}
        allow_summary = "summary" in rule["project_fields"]
        body, state = project_updates.inbox_update(self.root, path, record, source=excerpt, date=date, text=text,
                                                   summary=summary if allow_summary else None)
        if summary and not allow_summary:
            state = "not_permitted"
        stored = _save_marked(path, before, header + body.encode("utf-8"), header, prefix, signature, state)
        return {**result, "summaryStatus": state, **stored}

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
        replay = _replayed(before, header, prefix, signature, action.get("expected_revision"))
        if replay:
            return {**result, **replay}
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
        return {**result, **_save_marked(path, before, content, header, prefix, signature)}
