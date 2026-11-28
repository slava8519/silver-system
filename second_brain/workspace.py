"""Administration of an existing vault through the shared Markdown storage operations."""

import os
from pathlib import Path
import unicodedata

from . import storage


# Public API spelling and the corresponding user-editable YAML fields.
FIELDS = {"title": "title", "aliases": "aliases", "direction": "direction",
          "status": "status", "due": "due", "projectStart": "project_start", "nextStep": "next_step"}


class Workspace:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir() or not any((self.root / folder).is_dir() for folder in storage.KINDS.values()):
            raise ValueError("Укажите существующее хранилище. Для новой папки сначала выполните init.")

    def _summary(self, path, record):
        relative = path.relative_to(self.root).as_posix()
        result = {"id": record["id"], "type": record["type"], "path": relative}
        fields = {name: field for name, field in FIELDS.items() if name != "projectStart" or record["type"] == "project"}
        result.update({name: record.get(field) for name, field in fields.items()})
        result["fieldSources"] = {
            name: {"path": relative, "line": record["field_lines"][field]}
            for name, field in fields.items() if result[name] is not None and field in record["field_lines"]
        }
        return result

    def context(self, *, directions=False):
        context = {"name": self.root.name}
        if directions:
            context["directions"] = [{"id": r["id"], "title": r["title"]} for r in storage.direction_headers(self.root)]
        return context

    def overview(self):
        from .check_records import card_headers
        records = []
        ids = set()
        counts = {}
        people_count = 0
        for current, directories, _ in os.walk(self.root):
            parent = Path(current)
            directories[:] = [name for name in directories
                              if not name.startswith(".") and not (parent / name).is_symlink()]
            for name in directories:
                counts[(parent / name).relative_to(self.root).as_posix()] = 0
        for path, record in card_headers(self.root):
            kind = record["type"]
            if kind in storage.KINDS or kind in ("person", "team", "task", "plan"):
                # The three general record types share one lookup namespace.
                identity = ("record" if kind in storage.KINDS else kind, record["id"])
                if identity in ids:
                    raise ValueError(f"Один ID найден в нескольких файлах: {record['id']}")
                ids.add(identity)
            if kind in storage.KINDS:
                records.append(self._summary(path, record))
            if kind == "person":
                people_count += 1
            for directory in path.relative_to(self.root).parents:
                if directory != Path("."):
                    key = directory.as_posix()
                    counts[key] = counts.get(key, 0) + 1
        folders = [{"path": key, "count": count} for key, count in sorted(counts.items())]
        return {"name": self.root.name, "records": records, "folders": folders, "peopleCount": people_count}

    def _detail(self, path, expected_id=None):
        record = storage.read_record(path, include_raw=True)
        if expected_id is not None and record["id"] != expected_id:
            raise storage.ConflictError("По этому пути теперь другая запись. Обновите карточку.")
        result = self._summary(path, record)
        result.update({key: record[key] for key in ("body", "revision", "body_start", "raw")})
        if record["type"] == "project":
            from . import project_updates
            updates = project_updates.read_record_updates(self.root, path, record, limit=3)
            result["projectUpdates"] = {key: updates[key] for key in ("brief", "events", "total", "warnings")
                                        if key in updates}
            entries, hidden = project_updates.projection(record)
            if entries or hidden:
                covered = set(hidden)
                for entry in entries:
                    covered.update(range(entry["line"], entry.get("end", entry["line"]) + 1))
                lines = enumerate(record["body"].splitlines(keepends=True), record["body_start"])
                result["displayBody"] = "".join(line for number, line in lines if number not in covered)
        return result

    def get_record(self, record_id):
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("Укажите ID записи.")
        return self._detail(storage.find_record(self.root, record_id), record_id)

    def linked_record(self, relative, *, short=False):
        """Open a card or ordinary Markdown note reached from a link in a reading view."""
        parts = relative.split("/") if isinstance(relative, str) else []
        name_only = len(parts) == 1 and short
        if (not (name_only or len(parts) >= 2 and parts[0] in storage.KINDS.values())
                or not parts[-1].lower().endswith(".md")
                or any(not part or part.startswith(".") or "\\" in part or ":" in part
                       or any(ord(char) < 32 for char in part) for part in parts)):
            raise ValueError("Укажите путь к Markdown в папке карточек.")
        path = None if name_only else storage._inside(self.root, Path(*parts))
        if short and (path is None or not path.exists()):
            # Obsidian's short filenames may refer to another folder. Never guess
            # between two notes of the same name.
            def name_key(name):
                return unicodedata.normalize("NFC", name).casefold()
            name = name_key(parts[-1])
            matches = [p for p in storage.iter_markdown_paths(self.root)
                       if name_key(p.name) == name and not any(part.startswith(".") for part in p.relative_to(self.root).parts)]
            if len(matches) > 1:
                raise ValueError("Найдено несколько файлов с таким именем. Уточните путь в ссылке.")
            if matches:
                path = matches[0]
        if path is None or not path.is_file():
            raise FileNotFoundError(relative)
        try:
            return self._detail(path)
        except storage.UnmanagedMarkdown:
            raw = path.read_text(encoding="utf-8")
            lines = raw.splitlines(keepends=True)
            start = 0
            if lines and lines[0].rstrip("\r\n") == "---":
                start = next((i + 1 for i, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") in ("---", "...")), 0)
            return {"type": "markdown", "title": path.stem, "path": path.relative_to(self.root).as_posix(),
                    "raw": raw, "body": "".join(lines[start:])}

    def create_record(self, data):
        if data.keys() - (FIELDS.keys() | {"type", "body", "requestId"}):
            raise ValueError("В запросе создания есть неподдерживаемые поля.")
        path = storage.create(self.root, data.get("type"), data.get("title"), data.get("body", ""),
                              aliases=data.get("aliases"), direction=data.get("direction"),
                              status=data.get("status"), due=data.get("due"), project_start=data.get("projectStart"),
                              next_step=data.get("nextStep"), request_id=data.get("requestId"))
        return self._detail(path)

    def edit_record(self, data):
        if data.keys() - (FIELDS.keys() | {"id", "revision", "body"}):
            raise ValueError("В запросе изменения есть неподдерживаемые поля.")
        record_id = data.get("id")
        revision = data.get("revision")
        if not isinstance(record_id, str) or not record_id or not isinstance(revision, str) or not revision:
            raise ValueError("Для сохранения нужны ID и прочитанная версия записи.")
        changes = {field: data[name] for name, field in FIELDS.items() if name in data}
        if "body" in data:
            changes["body"] = data["body"]
        return self._detail(storage.edit(self.root, record_id, revision, changes, preserve_newlines=True), record_id)
