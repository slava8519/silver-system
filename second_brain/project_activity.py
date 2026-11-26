"""Catalogue of projects and period plans based on their explicit Markdown links."""

from collections import defaultdict
from datetime import date

from . import planning, storage
from .cycles import closed_metadata, frozen_rows
from .okr import KeyResults
from .okr_links import context_projects
from .project_membership import included_projects
from .workspace import Workspace


def _by_title(row):
    return (row.get("title") or "").casefold(), row["id"]


def _dated_work(rows):
    """The span of recorded work dates, independent of a project's own deadline."""
    values = []
    completed = 0
    for row in rows:
        for field in ("start", "due", "forecast", "completed"):
            value = row.get(field)
            if not value:
                continue
            try:
                valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
            except ValueError:
                valid = False
            if valid:
                values.append(value)
                completed += field == "completed"
    return {"taskCount": len(rows), "completedCount": completed,
            "start": min(values, default=None), "end": max(values, default=None)}


def _cards(root):
    directions, projects = {}, {}
    workspace = Workspace(root)
    folders = [storage.KINDS[k] for k in ("direction", "project")]
    for path in storage.iter_markdown_paths(root, folders=folders):
        try:
            record = storage.read_record(path)
        except storage.UnmanagedMarkdown:
            continue
        if record["type"] not in ("direction", "project"):
            continue
        key = record["id"]
        if key in projects or key in directions:
            raise ValueError(f"Один ID найден в нескольких карточках: {key}")
        summary = workspace._summary(path, record)
        row = {k: summary[k] for k in ("id", "title", "path")}
        if record["type"] == "direction":
            directions[key] = row
        else:
            state = ("status", "projectStart", "due", "nextStep")
            projects[key] = {**row, **{k: summary[k] for k in ("direction", *state)}, "plans": [],
                             "stateSources": {k: summary["fieldSources"][k] for k in state if k in summary["fieldSources"]}}
    for project in projects.values():
        project["directionTitle"] = directions.get(project["direction"], {}).get("title")
    return directions, projects


def _project_links(rows, results, projects, included, *, historical):
    """Keep explicit inclusion, ownership of work and KR context independent."""
    links = {}
    def link(identifier, title):
        if identifier not in links:
            project = projects.get(identifier)
            name = title if historical else (project or {}).get("title")
            links[identifier] = {"id": identifier, "title": name, "missing": project is None,
                                 "explicitlyIncluded": False, "taskCount": 0, "okrTaskCount": 0, "contextResultCount": 0}
        elif not links[identifier]["title"] and title:
            links[identifier]["title"] = title
        return links[identifier]

    for project in included:
        link(project["id"], project["title"])["explicitlyIncluded"] = True
    contributing = {key for result in results for key in result["taskIds"]}
    for row in rows:
        if row.get("project"):
            entry = link(row["project"], row.get("projectTitle"))
            entry["taskCount"] += 1
            entry["okrTaskCount"] += row["id"] in contributing
    for result in results:
        for context in result.get("contextProjects", []):
            link(context["id"], context["title"])["contextResultCount"] += 1
    return sorted(links.values(), key=_by_title)


class ProjectActivity:
    def __init__(self, root):
        self.results = KeyResults(root)
        self.planning = self.results.planning
        self.root = self.results.root

    def catalog(self):
        with storage._vault_lock(self.root, write=False):
            return self._catalog()

    def _catalog(self):
        directions, projects = _cards(self.root)
        plans = list(self.planning._entries("plan"))
        plan_ids = {record["id"] for _, _, record, _ in plans}
        tasks, project_tasks = {}, defaultdict(list)
        inherited = {key: p["direction"] for key, p in projects.items()}
        for path, _, record, _ in self.planning._entries("task"):
            row = self.planning._task_summary(path, record, inherited)
            tasks[row["id"]] = row
            if row["project"] in projects:
                project_tasks[row["project"]].append(row)

        goals = {g["id"]: g for g in self.results._objectives()}
        by_plan = defaultdict(list)
        for goal in goals.values():
            goal["results"] = []
            by_plan[goal["planId"]].append(goal)
        live_links = set()
        unassigned = []
        project_cache = {key: {k: p[k] for k in ("id", "title", "path")} for key, p in projects.items()}
        for path, _, record, _ in self.results._entries():
            ids, _ = planning._membership(record["body"])
            live_links.update(ids)
            contexts = context_projects(self.root, record, project_cache)
            goal = goals.get(record.get("objective_id"))
            if goal and goal["planId"] in plan_ids:
                goal["results"].append({"id": record["id"], "taskIds": ids, "contextProjects": contexts})
            else:
                scope = {tasks[key]["project"] for key in ids if key in tasks and tasks[key]["project"]}
                scope.update(p["id"] for p in contexts)
                unassigned.append({"id": record["id"], "title": record["title"], "path": path.relative_to(self.root).as_posix(),
                                   "direction": record.get("direction"),
                                   "directionTitle": directions.get(record.get("direction"), {}).get("title"),
                                   "objective": goal["title"] if goal else record.get("objective"),
                                   "missingObjective": bool(record.get("objective_id") and not goal),
                                   "missingPlan": bool(goal), "projectIds": sorted(scope)})

        summaries = []
        for path, _, record, _ in plans:
            closed = closed_metadata(record)
            plan = self.planning._plan_summary(path, record, closed)
            if closed is not None:
                rows = frozen_rows(record, closed)
                included = closed.get("includedProjects", [])
                structure = closed.get("okr")
                goal_rows = structure["objectives"] if structure is not None else []
                results = [r for g in goal_rows for r in g["results"]]
                known = structure is not None
            else:
                included = included_projects(self.root, record, projects)
                goal_rows = by_plan[plan["id"]]
                results = [r for g in goal_rows for r in g["results"]]
                direct, _ = planning._membership(record["body"])
                linked = dict.fromkeys([*direct, *(key for r in results for key in r["taskIds"])])
                rows = [tasks.get(key, {"id": key, "missing": True}) for key in linked]
                known = True
            links = _project_links(rows, results, projects, included, historical=closed is not None)
            if not known:
                for link in links:
                    link.update(okrTaskCount=None, contextResultCount=None)
            areas = {row["direction"] for row in rows if row.get("direction")}
            areas.update(g["direction"] for g in goal_rows if g.get("direction"))
            areas.update(p["direction"] for p in included if p.get("direction"))
            plan.update(_dated_work(rows), objectiveCount=len(goal_rows) if known else None,
                        resultCount=len(results) if known else None, hierarchyRecorded=known,
                        missingTaskCount=sum(bool(row.get("missing")) for row in rows),
                        directionIds=sorted(areas), projects=links)
            for link in links:
                if link["id"] in projects:
                    counts = {k: link[k] for k in ("explicitlyIncluded", "taskCount", "okrTaskCount", "contextResultCount")}
                    projects[link["id"]]["plans"].append(
                        {k: plan[k] for k in ("id", "title", "status", "closedAt")} | counts)
            summaries.append(plan)

        for key, project in projects.items():
            rows = project_tasks[key]
            project.update(_dated_work(rows), okrTaskCount=sum(t["id"] in live_links for t in rows))
            project["plans"].sort(key=_by_title)
        return {"name": self.root.name, "root": str(self.root), "directions": sorted(directions.values(), key=_by_title),
                "projects": sorted(projects.values(), key=_by_title), "plans": sorted(summaries, key=_by_title),
                "unassignedResults": sorted(unassigned, key=_by_title)}
