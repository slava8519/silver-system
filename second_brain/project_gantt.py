"""A live timeline of the work explicitly assigned to one project."""

from . import dependencies, storage
from .okr_progress import _task_readings
from .planning import Planning
from .workspace import Workspace


class ProjectGantt:
    def __init__(self, root):
        self.planning = Planning(root)
        self.root = self.planning.root

    def view(self, project_id):
        with storage._vault_lock(self.root, write=False):
            project = Workspace(self.root).get_record(project_id)
            if project["type"] != "project":
                raise ValueError("Выберите ID проекта для Ганта.")
            fields = ("id", "title", "path", "revision")
            summary = {key: project[key] for key in fields}
            state = ("status", "projectStart", "due", "nextStep")
            project_fields = {key: project[key] for key in ("direction", *state)}
            project_fields["stateSources"] = {key: project["fieldSources"][key] for key in state
                                               if key in project["fieldSources"]}
            project_fields["projectUpdates"] = project["projectUpdates"]
            view = {**summary, "scope": "project", "project": {**summary, **project_fields},
                    "items": [], "tree": [], "objectives": []}

            # Index task cards once for resolving predecessor IDs across project boundaries.
            # Plan memberships, frozen rows and OKR context do not assign work to a project.
            cards = {}
            selected = []
            for path, _, record, _ in self.planning._entries("task"):
                cards[record["id"]] = (path, record)
                if record.get("project") == project_id:
                    selected.append((path, record))
            directions = {project_id: project["direction"]}
            for path, record in selected:
                row = self.planning._task_summary(path, record, directions)
                row.update(projectLine=record["field_lines"].get("project"),
                           status=record.get("status"), statusLine=record["field_lines"].get("status"),
                           predecessors=self._predecessors(row, record, cards))
                view["items"].append(row)
            self.planning._group_titles(view)
            _task_readings(self.root, view["items"])
            return view

    def _predecessors(self, row, record, cards):
        _, links, _ = dependencies._section(record)
        predecessors = []
        for key, index in links.items():
            edge = {"id": key, "linkPath": row["path"], "linkLine": record["body_start"] + index}
            if key in cards:
                path, predecessor = cards[key]
                edge.update(title=predecessor["title"], path=path.relative_to(self.root).as_posix(),
                            kind="milestone" if predecessor.get("task_kind") == "milestone" else "task",
                            external=predecessor.get("project") != row["project"])
            else:
                edge.update(title=key, path=None, kind=None, external=True, missing=True)
            predecessors.append(edge)
        return predecessors
