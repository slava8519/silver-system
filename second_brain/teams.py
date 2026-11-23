"""Explicit group memberships and the owner's chosen people context."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from urllib.parse import unquote
import uuid

from . import brief, people, storage


_GROUPS = {"team": "People/Groups"}
_PROFILE = "Settings/Owner.md"
_HISTORY = ("<!-- people-history -->", "<!-- /people-history -->")
_ROSTER = ("<!-- team-members -->", "<!-- /team-members -->")
_CONTEXT = ("<!-- owner-context -->", "<!-- /owner-context -->")


def _string(value, label):
    if not people._single_line(value):
        raise ValueError(f"Укажите {label} одной непустой строкой.")
    return value


def _paging(limit, offset):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError("Укажите размер страницы от 1 до 100 и неотрицательное смещение.")


def _bounds(lines, markers):
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == markers[0]]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == markers[1]]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise storage.ConflictError("Проверьте границы раздела связей в Markdown.")
    return starts[0], ends[0]


def _link_marker(line, kind):
    prefix = f"<!-- {kind}-id:"
    if prefix not in line:
        return None
    match = re.search(r"(?<= )" + re.escape(prefix) + r" ([A-Za-z0-9._~%\-]+) -->", line)
    if match is None or line.count(prefix) != 1:
        raise storage.ConflictError("Проверьте ссылку и ID в разделе связей.")
    return match


def _remove_link(lines, index, kind):
    line = lines[index]
    marker = _link_marker(line, kind)
    before, after = line[:marker.start()].rstrip(), line[marker.end():]
    prefix = {"owner": "Моя карточка — ", "team": "Моя команда — ", "person": "- "}[kind]
    # A plain generated link can be removed. Preserve any hand-edited prose
    # and links around the marker; they are no longer a current association.
    plain = re.fullmatch(r"\[(?:\\.|[^\]\\])*\]\([^)]+\)", before.removeprefix(prefix))
    if plain and not after.strip():
        del lines[index]
    elif plain:
        lines[index] = after.lstrip(" \t")
    else:
        lines[index] = before + after


def _links(lines, markers, kinds):
    """Read only the marked links; preserve arbitrary owner prose around them."""
    bounds = _bounds(lines, markers)
    links = {}
    if bounds:
        for i in range(bounds[0] + 1, bounds[1]):
            found = [(kind, marker) for kind in kinds if (marker := _link_marker(lines[i], kind))]
            if not found:
                continue
            if len(found) > 1:
                raise storage.ConflictError("Для каждой связи в Markdown нужна отдельная строка.")
            kind, marker = found[0]
            identifier = _string(unquote(marker[1], errors="strict"), "ID")
            key = identifier if markers == _ROSTER else kind
            if key in links:
                raise storage.ConflictError("Одна связь дважды указана в Markdown.")
            links[key] = (identifier, i)
    return links, bounds


def _ensure_section(lines, bounds, heading, markers, newline):
    if bounds:
        return bounds[1]
    if lines and not lines[-1].endswith(("\r", "\n")):
        lines[-1] += newline
    lines.extend(line + newline for line in ("", heading, "", *markers))
    return len(lines) - 1


def _log(lines, message, newline):
    end = _ensure_section(lines, _bounds(lines, _HISTORY), "## История изменений", _HISTORY, newline)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines.insert(end, f"- {stamp} · {message}{newline}")


def _identity_link(link, identifier):
    return link + " (" + storage.markdown_label(identifier) + ")"


def _missing(identifier):
    return {"id": identifier, "name": None, "path": None, "missing": True}


def _require_revision(revision):
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise ValueError("Укажите прочитанную ревизию записи.")


def _check_revision(revision, expected):
    if revision != expected:
        raise storage.ConflictError("Запись изменилась. Перечитайте её перед сохранением.")


class Teams:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.people = people.People(self.root)
        if not any((self.root / folder).is_dir() for folder in ("People", *storage.KINDS.values())):
            raise ValueError("Сначала подготовьте хранилище командой init.")

    def _groups(self):
        seen = set()
        for path in self.people.paths():
            data = path.read_bytes()
            try:
                record, header = storage._parse_record(path, data, kinds=_GROUPS)
            except storage.UnmanagedMarkdown:
                continue
            identifier = _string(record["id"], "ID группы")
            _string(record["title"], "название группы")
            if identifier in seen:
                raise ValueError(f"Один ID группы найден в нескольких карточках: {identifier}")
            seen.add(identifier)
            yield path, data, record, header

    def card_paths(self):
        """Yield group cards for folder inventory without counting them as people."""
        for path, _, _, _ in self._groups():
            yield path

    def _group(self, identifier):
        identifier = _string(identifier, "ID группы")
        matches = [entry for entry in self._groups() if entry[2]["id"] == identifier]
        if not matches:
            raise FileNotFoundError(f"Группа не найдена: {identifier}")
        return matches[0]

    def _person(self, identifier):
        path, _, record = self.people._locate(identifier)
        return self.people._summary(path, record)

    def create_person(self, name, *, request_id):
        return self._create("person", name, request_id)

    def create(self, name, *, request_id):
        return self._create("team", name, request_id)

    def _create(self, kind, name, request_id):
        name = _string(name, "имя человека" if kind == "person" else "название группы").strip()
        key = _string(request_id, "ключ запроса")
        if len(key) > 128:
            raise ValueError("Ключ запроса должен занимать до 128 символов.")
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"second-brain/direct/{kind}/{key}"))
        fingerprint = hashlib.sha256(name.encode()).hexdigest()
        fields = {"type": kind, "schema_version": 1, "id": identifier, "title": name, "creation_hash": fingerprint}
        kinds = {"person": "People"} if kind == "person" else _GROUPS
        with storage._write_lock(self.root):
            try:
                entry = self.people._locate(identifier) if kind == "person" else self._group(identifier)
            except FileNotFoundError:
                folder = storage._inside(self.root, kinds[kind])
                folder.mkdir(parents=True, exist_ok=True)
                body = "# " + storage.markdown_label(name) + "\n"
                if kind == "team":
                    body += "\n## Участники\n\n" + "\n".join(_ROSTER) + "\n"
                storage._publish_record(self.root, fields, body, kinds=kinds)
            else:
                if entry[2].get("creation_hash") != fingerprint:
                    raise storage.ConflictError("Ключ запроса уже использован с другим именем.")
            return self._person(identifier) if kind == "person" else self.show(identifier)

    def _group_page(self, match, person_id, limit, offset):
        _paging(limit, offset)
        if not isinstance(match, str):
            raise ValueError("Укажите фрагмент названия группы.")
        term = match.strip().casefold()
        rows = []
        with storage._vault_lock(self.root, write=False):
            for path, _, record, _ in self._groups():
                if term not in record["title"].casefold():
                    continue
                links, _ = _links(record["body"].splitlines(), _ROSTER, ("person",))
                row = {**self.people._summary(path, record), "revision": record["revision"], "total": len(links)}
                if person_id is not None:
                    if person_id not in links:
                        continue
                    row["line"] = record["body_start"] + links[person_id][1]
                rows.append(row)
        return brief.page(sorted(rows, key=lambda r: (r["name"].casefold(), r["id"])), limit, offset)

    def list(self, match="", limit=20, offset=0):
        return self._group_page(match, None, limit, offset)

    def for_person(self, person_id, *, match="", limit=20, offset=0):
        return self._group_page(match, _string(person_id, "ID человека"), limit, offset)

    def show(self, identifier, *, limit=20, offset=0):
        _paging(limit, offset)
        with storage._vault_lock(self.root, write=False):
            path, _, record, _ = self._group(identifier)
            return self._detail(path, record, limit, offset)

    def _detail(self, path, record, limit=20, offset=0):
        links, _ = _links(record["body"].splitlines(), _ROSTER, ("person",))
        page = brief.page(links.values(), limit, offset)
        wanted = {key for key, _ in page["items"]}
        people_by_id = {r["id"]: self.people._summary(p, r) for p, _, r in self.people._cards()
                        if r["id"] in wanted} if wanted else {}
        members = [{**people_by_id.get(key, _missing(key)), "line": record["body_start"] + i}
                   for key, i in page["items"]]
        return {**self.people._summary(path, record), "revision": record["revision"], "members": members,
                "total": page["total"], "truncated": page["truncated"]}

    def _save_group(self, path, before, header, lines):
        after = header + "".join(lines).encode()
        saved, _ = storage._parse_record(path, after, kinds=_GROUPS)
        result = self._detail(path, saved)
        storage.replace_bytes(path, before, after)
        return result

    def rename(self, identifier, name, revision):
        _require_revision(revision)
        name = _string(name, "название группы").strip()
        with storage._write_lock(self.root):
            path, before, record, header = self._group(identifier)
            if record["title"] != name:
                _check_revision(revision, record["revision"])
                header = storage._patch_header(header, record, {"title": name})
                old = "# " + storage.markdown_label(record["title"])
                lines = record["body"].splitlines(keepends=True)
                if lines and lines[0].rstrip("\r\n") == old:
                    lines[0] = "# " + storage.markdown_label(name) + lines[0][len(old):]
                newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
                previous = storage.markdown_label(record["title"])
                current = storage.markdown_label(name)
                _log(lines, f"Название группы «{previous}» → «{current}» ({storage.markdown_label(identifier)})", newline)
                return self._save_group(path, before, header, lines)
            return self._detail(path, record)

    def _markdown_link(self, row, directory):
        return storage.markdown_link(row["name"], self.root / row["path"], directory)

    def _identity(self, kind, identifier):
        if kind in ("person", "owner"):
            return self._person(identifier)
        path, _, record, _ = self._group(identifier)
        return self.people._summary(path, record)

    def _previous_identity(self, kind, identifier, directory):
        # Audit only the canonical identity even if the owner added a private
        # annotation alongside its link. After removal the ID remains usable.
        try:
            row = self._identity(kind, identifier)
        except FileNotFoundError:
            return storage.markdown_label(identifier)
        return _identity_link(self._markdown_link(row, directory), identifier)

    def member(self, identifier, person_id, *, present, revision):
        _require_revision(revision)
        person_id = _string(person_id, "ID человека")
        if type(present) is not bool:
            raise ValueError("Укажите, нужно добавить человека в группу или исключить.")
        with storage._write_lock(self.root):
            path, before, record, header = self._group(identifier)
            lines = record["body"].splitlines(keepends=True)
            links, bounds = _links(lines, _ROSTER, ("person",))
            if (person_id in links) != present:
                _check_revision(revision, record["revision"])
                newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
                if present:
                    person = self._person(person_id)
                    link = self._markdown_link(person, path.parent)
                    end = _ensure_section(lines, bounds, "## Участники", _ROSTER, newline)
                    lines.insert(end, "- " + link + " " + storage.id_marker("person", person_id) + newline)
                    change = "Добавлен участник · " + _identity_link(link, person_id)
                else:
                    _remove_link(lines, links[person_id][1], "person")
                    change = "Исключён участник · " + self._previous_identity("person", person_id, path.parent)
                _log(lines, change, newline)
                return self._save_group(path, before, header, lines)
            return self._detail(path, record)

    def _profile(self):
        path = storage._inside(self.root, _PROFILE)
        data = path.read_bytes() if path.exists() else None
        lines = data.decode().splitlines(keepends=True) if data is not None else []
        refs, bounds = _links(lines, _CONTEXT, ("owner", "team"))
        return path, data, lines, refs, bounds

    def link_fragment(self, relative, start, end):
        """Expose one current link from the profile or a team. Person cards use their own fact reader."""
        with storage._vault_lock(self.root, write=False):
            if relative.as_posix() == _PROFILE:
                path, _, lines, refs, _ = self._profile()
                links = [(kind, key, i) for kind, (key, i) in refs.items()]
                first, command = 1, "owner-context"
            elif (relative.parts[:1] == ("People",) and relative.suffix.lower() == ".md"
                  and all(not part.startswith(".") for part in relative.parts)):
                path = storage._inside(self.root, relative)
                if not path.is_file():
                    return None
                try:
                    record, _ = storage._parse_record(path, path.read_bytes(), kinds=_GROUPS)
                except storage.UnmanagedMarkdown:
                    return None
                lines = record["body"].splitlines()
                refs, _ = _links(lines, _ROSTER, ("person",))
                links = [("person", key, i) for key, i in refs.values()]
                first, command = record["body_start"], "team-show"
            else:
                return None
            for kind, identifier, i in links:
                if start == end == first + i:
                    # The exact marker on this line is sufficient proof even when
                    # a card moved or an owner added private text around a link.
                    raw = lines[i].rstrip()
                    marker = _link_marker(raw, kind)[0]
                    try:
                        target = self._identity(kind, identifier)
                    except FileNotFoundError:
                        return marker
                    link = self._markdown_link(target, path.parent) + " " + marker
                    return link if link in raw else marker
            raise ValueError(f"Укажите одну строку текущей связи. Актуальные номера возвращает {command}.")

    def context(self, *, limit=10, offset=0):
        _paging(limit, offset)
        with storage._vault_lock(self.root, write=False):
            _, data, _, refs, _ = self._profile()
            return self._context_view(data, refs, limit, offset)

    def _context_view(self, data, refs, limit=10, offset=0):
        result = {"path": _PROFILE, "revision": hashlib.sha256(data or b"").hexdigest(), "owner": None,
                  "team": None, "ownerLine": None, "teamLine": None, "members": [], "total": 0, "truncated": False}
        for key, (identifier, index) in refs.items():
            result[key + "Line"] = index + 1
            try:
                if key == "owner":
                    result[key] = self._person(identifier)
                else:
                    group = self.show(identifier, limit=limit, offset=offset)
                    result[key] = {k: group[k] for k in ("id", "name", "path")}
                    result.update({k: group[k] for k in ("members", "total", "truncated")})
            except FileNotFoundError:
                result[key] = _missing(identifier)
        return result

    def set_context(self, *, revision, **changes):
        _require_revision(revision)
        if changes.keys() - {"owner", "team"} or not changes:
            raise ValueError("Укажите владельца или выбранную команду.")
        with storage._write_lock(self.root):
            path, data, lines, refs, bounds = self._profile()
            updated = {key: value for key, value in changes.items()
                       if value != (refs[key][0] if key in refs else None)}
            if updated:
                _check_revision(revision, hashlib.sha256(data or b"").hexdigest())
                newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
                additions = []
                log = []
                for key, value in updated.items():
                    label = "Моя карточка" if key == "owner" else "Моя команда"
                    previous = "не выбрано"
                    if key in refs:
                        previous = self._previous_identity(key, refs[key][0], path.parent)
                    current = "не выбрано"
                    if value is not None:
                        row = self._identity(key, value)
                        link = self._markdown_link(row, path.parent)
                        additions.append(label + " — " + link + " " + storage.id_marker(key, value) + newline)
                        current = _identity_link(link, value)
                    log.append(label + " · " + previous + " → " + current)
                if not lines:
                    lines.append("# Профиль владельца" + newline)
                end = _ensure_section(lines, bounds, "## Контекст для ИИ", _CONTEXT, newline)
                # Insert before the original closing marker, then remove exactly
                # the superseded lines. Other notes and choices keep their bytes.
                lines[end:end] = additions
                for i, key in sorted(((refs[k][1], k) for k in updated if k in refs), reverse=True):
                    _remove_link(lines, i, key)
                _log(lines, " · ".join(log), newline)
                after = "".join(lines).encode()
                refs, _ = _links(lines, _CONTEXT, ("owner", "team"))
                result = self._context_view(after, refs)
                path.parent.mkdir(parents=True, exist_ok=True)
                storage.replace_bytes(path, data, after)
                return result
            return self._context_view(data, refs)
