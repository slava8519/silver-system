"""Bounded command-line views of the project catalogue and the actual project chart."""

from . import brief
from .project_activity import ProjectActivity


def add_commands(commands):
    membership = commands.add_parser("plan-project", help="выбрать проект или его работу в план, прочитать историю выбора")
    membership.add_argument("plan_id")
    action = membership.add_mutually_exclusive_group(required=True)
    action.add_argument("--include", metavar="ID", help="включить проект в план")
    action.add_argument("--exclude", metavar="ID", help="снять явное включение проекта")
    action.add_argument("--task", metavar="ID", help="выбрать существующую работу включённого проекта")
    action.add_argument("--history", action="store_true", help="показать изменения от новых к старым")
    membership.add_argument("--project", metavar="ID", help="проект для выбранной работы")
    membership.add_argument("--revision", metavar="REV", help="прочитанная версия активного плана для записи")
    membership.add_argument("--pending", action="store_true", help="показать только несверенные ручные правки")
    membership.add_argument("--limit", type=brief.result_limit, default=brief.DEFAULT_LIMIT)
    membership.add_argument("--offset", type=brief.offset, default=0)
    membership.set_defaults(project_activity=True)
    catalog = commands.add_parser("activity-list", help="показать проекты, планы любого периода или КР без плана")
    catalog.add_argument("--view", choices=("projects", "plans", "unassigned"), default="projects")
    catalog.add_argument("--direction")
    catalog.add_argument("--project")
    catalog.add_argument("--plan")
    chart = commands.add_parser("project-gantt", help="показать график задач одного проекта")
    chart.add_argument("id")
    for command in (catalog, chart):
        command.add_argument("--match", default="")
        command.add_argument("--limit", type=brief.result_limit, default=brief.DEFAULT_LIMIT)
        command.add_argument("--offset", type=brief.offset, default=0)
        command.set_defaults(project_activity=True)


def _compact(row, exclude):
    result = {k: v for k, v in row.items() if k not in exclude}
    brief.clip_fields(result, (k for k in ("title", "directionTitle", "status", "projectStart", "due", "nextStep", "objective") if k in result))
    return result


def _matches(row, query):
    return not query or query in row["title"].casefold()


def _activity_page(catalog, args):
    collection = "unassignedResults" if args.view == "unassigned" else args.view
    result = []
    query = args.match.strip().casefold()
    for row in catalog[collection]:
        if not _matches(row, query):
            continue
        if args.view == "projects":
            link = next((p for p in row["plans"] if p["id"] == args.plan), None) if args.plan else None
            matches = ((not args.direction or args.direction == row["direction"])
                       and (not args.project or args.project == row["id"])
                       and (not args.plan or link))
            item = {**_compact(row, ("plans",)), "planCount": len(row["plans"])}
            if link:
                item["planLink"] = _compact(link, ())
        elif args.view == "plans":
            link = next((p for p in row["projects"] if p["id"] == args.project), None) if args.project else None
            matches = ((not args.direction or args.direction in row["directionIds"])
                       and (not args.plan or row["id"] == args.plan) and (not args.project or link))
            item = {**_compact(row, ("projects", "directionIds")), "projectCount": len(row["projects"])}
            if link:
                item["projectLink"] = _compact(link, ())
        else:
            matches = ((not args.direction or args.direction == row["direction"])
                       and (not args.project or args.project in row["projectIds"]) and not args.plan)
            item = {**_compact(row, ("projectIds",)), "projectCount": len(row["projectIds"])}
        if matches:
            result.append(item)
    return {"view": args.view, **brief.page(result, args.limit, args.offset)}


def run(root, args):
    if args.command == "plan-project":
        if args.history:
            if args.project or args.revision:
                raise ValueError("Для просмотра истории достаточно ID плана.")
            from .project_feedback import ProjectFeedback
            return ProjectFeedback(root).history(args.plan_id, pending=args.pending, limit=args.limit, offset=args.offset)
        if args.pending or not args.revision or bool(args.task) != bool(args.project):
            raise ValueError("Для записи укажите --revision, а для выбора работы вместе с --task укажите --project.")
        from .project_membership import ProjectMembership
        service = ProjectMembership(root)
        if args.task:
            return service.add_task(args.plan_id, args.project, args.task, revision=args.revision, actor="ai")
        return service.set(args.plan_id, args.include or args.exclude, present=args.include is not None,
                           revision=args.revision, actor="ai")
    if args.command == "activity-list":
        result = _activity_page(ProjectActivity(root).catalog(), args)
    else:
        from .project_gantt import ProjectGantt
        chart = ProjectGantt(root).view(args.id)
        query = args.match.strip().casefold()
        rows = brief.page((r for r in chart["items"] if _matches(r, query)), args.limit, args.offset)
        items = []
        for row in rows["items"]:
            item = {k: row.get(k) for k in ("id", "title", "path", "revision", "projectLine", "kind", "status", "statusLine",
                                            "start", "startLine", "due", "dueLine", "forecast", "forecastLine",
                                            "completed", "completedLine")}
            reading = row.get("readiness")
            item["readiness"] = {k: reading[k] for k in ("value", "date", "kind", "line")} if reading else None
            deps = row.get("predecessors", [])
            item.update(predecessorCount=len(deps), externalPredecessorCount=sum(bool(d.get("external")) for d in deps))
            items.append(_compact(item, ()))
        header = {k: chart["project"].get(k) for k in ("id", "title", "path", "revision", "direction", "status",
                                                       "projectStart", "due", "nextStep", "stateSources")}
        result = {"scope": "project", "project": _compact(header, ()), **rows, "items": items}
    return {**result, "limit": args.limit, "offset": args.offset}
