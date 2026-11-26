"""A plan's explicitly chosen projects and the individual work selected from them."""

from . import planning, storage
from .cycles import closed_metadata
from .okr import KeyResults, _text
from .okr_links import _connections, _remove
from .teams import _check_revision, _ensure_section, _require_revision


_PROJECTS = ("<!-- plan-projects -->", "<!-- /plan-projects -->")


def _members(lines):
    return _connections(lines, _PROJECTS, "project", location="плане")


def included_projects(root, plan, projects=None):
    identifiers, _ = _members(plan["body"].splitlines())
    if not identifiers:
        return []
    if projects is None:
        projects = {}
        for _, record in storage.iter_records(root):
            key = record["id"]
            if key in identifiers and record["type"] == "project":
                if key in projects:
                    raise ValueError(f"Один ID найден в нескольких карточках проектов: {key}")
                projects[key] = record
    return [{"id": key, "title": projects.get(key, {}).get("title"),
             "direction": projects.get(key, {}).get("direction")} for key in identifiers]


class ProjectMembership:
    def __init__(self, root):
        self.planning = planning.Planning(root)
        self.root = self.planning.root

    def _active_entry(self, plan_id, actor, revision):
        _require_revision(revision)
        if actor not in ("manual", "ai"):
            raise ValueError("Укажите автора решения — manual или ai.")
        entry = self.planning._find("plan", plan_id)
        if closed_metadata(entry[2]) is not None:
            raise storage.ConflictError("План закрыт. Выберите активный план для изменения состава.")
        return entry

    def _project(self, project_id):
        path = storage.find_record(self.root, project_id)
        project = storage.read_record(path)
        if project["type"] != "project":
            raise ValueError("Выберите карточку проекта.")
        return path, project

    def _result(self, path, plan, project_id, *, present, changed):
        return {"planId": plan["id"], "projectId": project_id, "explicitlyIncluded": present,
                "path": path.relative_to(self.root).as_posix(), "revision": plan["revision"], "changed": changed}

    def _save(self, entry, lines, **event):
        from .project_feedback import append_event
        path, original, _, header = entry
        append_event(lines, **event)
        updated = header + "".join(lines).encode()
        plan, _ = storage._parse_record(path, updated, kinds=("plan",))
        storage.replace_bytes(path, original, updated)
        return plan

    def set(self, plan_id, project_id, *, present, revision, actor="ai"):
        project_id = _text(project_id, "ID проекта")
        if type(present) is not bool:
            raise ValueError("Укажите включение или исключение проекта.")
        with storage._write_lock(self.root):
            entry = self._active_entry(plan_id, actor, revision)
            path, _, plan, header = entry
            lines = plan["body"].splitlines(keepends=True)
            members, bounds = _members(lines)
            if (project_id in members) == present:
                return self._result(path, plan, project_id, present=present, changed=False)
            _check_revision(revision, plan["revision"])
            newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
            if present:
                project_path, project = self._project(project_id)
                title = project["title"]
                index = _ensure_section(lines, bounds, "## Проекты плана", _PROJECTS, newline)
                link = storage.markdown_link(title, project_path, path.parent)
                lines.insert(index, f"- {link} {storage.id_marker('project', project_id)}{newline}")
            else:
                # A deleted project can still be removed from an active plan.
                try:
                    _, project = self._project(project_id)
                    title = project["title"]
                except FileNotFoundError:
                    title = project_id
                _remove(lines, members[project_id])
            saved = self._save(entry, lines, project_id=project_id, title=title, present=present,
                               actor=actor, newline=newline)
            return self._result(path, saved, project_id, present=present, changed=True)

    def add_task(self, plan_id, project_id, task_id, *, revision, actor="ai"):
        project_id = _text(project_id, "ID проекта")
        with storage._write_lock(self.root):
            entry = self._active_entry(plan_id, actor, revision)
            path, _, plan, header = entry
            lines = plan["body"].splitlines(keepends=True)
            members, _ = _members(lines)
            if project_id not in members:
                raise storage.ConflictError("Сначала включите выбранный проект в план.")
            _, project = self._project(project_id)
            target, _, task, _ = self.planning._find("task", task_id)
            if task.get("project") != project_id:
                raise ValueError("Выбранная работа относится к другому проекту или направлению.")
            direct, end = planning._membership(plan["body"])
            linked = KeyResults(self.root).plan_task_ids({plan_id})
            if task_id in direct or task_id in linked.get(plan_id, ()):
                return {**self._result(path, plan, project_id, present=True, changed=False), "taskId": task_id}
            _check_revision(revision, plan["revision"])
            newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
            planning._insert_task(lines, end, task, target, path, newline)
            saved = self._save(entry, lines, project_id=project_id, title=project["title"], present=True,
                               actor=actor, newline=newline, task_id=task_id, task_title=task["title"])
            return {**self._result(path, saved, project_id, present=True, changed=True), "taskId": task_id}
