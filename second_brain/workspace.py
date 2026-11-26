"""Administration of an existing vault through the shared Markdown storage operations."""

import os
from pathlib import Path

from . import storage
from .okr import KeyResults
from .people import People
from .planning import Planning
from .teams import Teams


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

    def overview(self):
        records = []
        ids = set()
        counts = {}
        for current, directories, _ in os.walk(self.root):
            parent = Path(current)
            directories[:] = [name for name in directories
                              if not name.startswith(".") and not (parent / name).is_symlink()]
            for name in directories:
                counts[(parent / name).relative_to(self.root).as_posix()] = 0
        for path, record in storage.iter_records(self.root):
            if record["id"] in ids:
                raise ValueError(f"Один ID найден в нескольких файлах: {record['id']}")
            ids.add(record["id"])
            records.append(self._summary(path, record))
        person_paths = [p.relative_to(self.root) for p in People(self.root).card_paths()]
        team_paths = (p.relative_to(self.root) for p in Teams(self.root).card_paths())
        work_paths = (p.relative_to(self.root) for p in Planning(self.root).card_paths())
        okr_paths = (p.relative_to(self.root) for p in KeyResults(self.root).card_paths())
        for relative in [*(Path(r["path"]) for r in records), *person_paths, *team_paths, *work_paths, *okr_paths]:
            for directory in relative.parents:
                if directory != Path("."):
                    key = directory.as_posix()
                    counts[key] = counts.get(key, 0) + 1
        folders = [{"path": key, "count": count} for key, count in sorted(counts.items())]
        return {"name": self.root.name, "records": records, "folders": folders, "peopleCount": len(person_paths)}

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
        return self._detail(storage.edit(self.root, record_id, revision, changes), record_id)
