"""CLI for optional objectives and measured key results."""

from . import brief, search


def add_commands(commands):
    create = commands.add_parser("okr-create", help="создать ключевой результат цели и связать с задачами")
    create.add_argument("title")
    parent = create.add_mutually_exclusive_group(required=True)
    parent.add_argument("--objective", help="текст цели для отдельного КР без плана")
    parent.add_argument("--objective-id", metavar="ID", help="цель выбранного плана")
    create.add_argument("--direction", metavar="ID", help="направление для отдельного КР")
    create.add_argument("--unit", help="единица измерения")
    create.add_argument("--target", help="целевое значение")
    create.add_argument("--task", action="append", default=[], metavar="ID", help="связать задачу (можно повторять)")
    create.add_argument("--request-id", required=True, help="устойчивый ключ одного запроса на создание")

    objective = commands.add_parser("okr-objective-create", help="создать цель в выбранном плане")
    objective.add_argument("title")
    objective.add_argument("--plan", required=True, metavar="ID")
    objective.add_argument("--direction", required=True, metavar="ID")
    objective.add_argument("--request-id", required=True)

    board = commands.add_parser("okr-plan", help="показать цели, результаты, дедуплицированные работы или пробелы плана")
    board.add_argument("id", metavar="PLAN_ID")
    board.add_argument("--view", choices=("objectives", "results", "tasks", "gaps"), default="objectives")
    board.add_argument("--objective", metavar="ID")
    board.add_argument("--result", metavar="ID")

    listing = commands.add_parser("okr-list", help="найти ключевые результаты по направлению, цели или названию")
    listing.add_argument("--direction", metavar="ID")
    listing.add_argument("--match", default="", help="фрагмент цели или названия результата")
    show = commands.add_parser("okr-show", help="прочитать результат, связанные задачи или историю измерений")
    show.add_argument("id")
    sections = show.add_mutually_exclusive_group()
    sections.add_argument("--projects", action="store_true", help="показать контекстные проекты КР")
    sections.add_argument("--criteria", action="store_true", help="показать условия и последние подтверждения")
    sections.add_argument("--history", action="store_true", help="показать историю измерений")
    criterion = commands.add_parser("okr-criterion-show", help="прочитать критерий и страницы его истории")
    criterion.add_argument("id", metavar="KR_ID")
    criterion.add_argument("criterion_id", metavar="CRITERION_ID")
    criterion.add_argument("--history", choices=("measurements", "baselines", "targets"))
    for parser in (listing, show, board, criterion):
        parser.add_argument("--limit", type=brief.result_limit, default=brief.DEFAULT_LIMIT)
        parser.add_argument("--offset", type=brief.offset, default=0)

    assign = commands.add_parser("okr-assign", help="привязать отдельный ключевой результат к цели плана")
    assign.add_argument("id", metavar="KR_ID")
    assign.add_argument("objective_id", metavar="OBJECTIVE_ID")

    link = commands.add_parser("okr-link", help="связать существующую задачу с ключевым результатом")
    link.add_argument("id")
    link.add_argument("task_id")
    unlink = commands.add_parser("okr-unlink", help="снять связь работы с КР вместе с весом этой связи")
    unlink.add_argument("id", metavar="KR_ID")
    unlink.add_argument("task_id", metavar="TASK_ID")
    context = commands.add_parser("okr-context", help="связать КР с контекстным проектом или снять связь")
    context.add_argument("id", metavar="KR_ID")
    context.add_argument("project_id", metavar="PROJECT_ID")
    operation = context.add_mutually_exclusive_group(required=True)
    operation.add_argument("--add", dest="present", action="store_true")
    operation.add_argument("--remove", dest="present", action="store_false")
    measure = commands.add_parser("okr-measure", help="записать измеренное значение с датой и источником")
    measure.add_argument("id")
    condition = commands.add_parser("okr-criterion-add", help="добавить подтверждаемое условие достижения")
    condition.add_argument("id", metavar="KR_ID")
    condition.add_argument("title")
    condition.add_argument("--kind", required=True, choices=("numeric", "boolean"))
    condition.add_argument("--primary", action="store_true", help="оценивать основную метрику КР")
    condition.add_argument("--unit")
    condition.add_argument("--operator", choices=("<=", ">=", "="))
    condition.add_argument("--target")
    condition.add_argument("--baseline", metavar="VALUE")
    condition.add_argument("--baseline-date")
    condition.add_argument("--baseline-source")
    condition.add_argument("--baseline-line", type=int)
    condition.add_argument("--baseline-quote")
    condition.add_argument("--request-id", required=True)
    threshold = commands.add_parser("okr-criterion-target", help="изменить порог критерия с записью истории")
    threshold.add_argument("id", metavar="KR_ID")
    threshold.add_argument("criterion_id", metavar="CRITERION_ID")
    threshold.add_argument("--operator", required=True, choices=("<=", ">=", "="))
    threshold.add_argument("--target", required=True)
    observe = commands.add_parser("okr-criterion-measure", help="подтвердить замер или качественный исход")
    observe.add_argument("id", metavar="KR_ID")
    observe.add_argument("criterion_id", metavar="CRITERION_ID")
    observe.add_argument("--baseline", action="store_true", help="сохранить исходное значение показателя")
    for parser in (measure, observe):
        parser.add_argument("--value", required=True, help="число или да/нет для качественного критерия")
        parser.add_argument("--date", required=True, metavar="YYYY-MM-DD", help="дата наблюдения")
        parser.add_argument("--source", required=True, metavar="ПУТЬ", help="заметка или проект с подтверждением")
        parser.add_argument("--line", required=True, type=int, metavar="N", help="строка подтверждения")
        parser.add_argument("--quote", help="точная выдержка из строки")
    for parser in (measure, link, unlink, context, assign, condition, threshold, observe):
        parser.add_argument("--revision", required=True, help="прочитанная ревизия ключевого результата")
    for parser in (objective, board, create, listing, show, link, unlink, context, measure, assign,
                   condition, threshold, observe, criterion):
        parser.set_defaults(okr=True)


def _summary(record):
    result = {key: record[key] for key in ("id", "title", "objective", "objectiveId", "planId", "direction", "path", "unit", "target", "targetLine")}
    brief.clip_fields(result, ("title", "objective", "unit"))
    return result


def _short_measurement(value):
    if value is None:
        return None
    source = value["source"]
    return {"value": value["value"], "date": value["date"],
            "source": {k: source[k] for k in ("path", "line", "unavailable") if k in source}}


def _board_page(board, args):
    goals, tasks = board["objectives"], board["items"]
    children = [r for g in goals for r in g["results"]]
    total = {"objectives": len(goals), "results": len(children), "tasks": len(tasks),
             "gaps": sum(len(ids) for ids in board["gaps"].values())}
    if args.objective:
        goals = [g for g in goals if g["id"] == args.objective]
        if not goals:
            raise ValueError("Цель не входит в указанный план.")
        children = [r for goal in goals for r in goal["results"]]
    if args.result:
        children = [r for r in children if r["id"] == args.result]
        if not children:
            raise ValueError("Результат не входит в выбранный план или цель.")
        goals = [g for g in goals if g["id"] == children[0]["objectiveId"]]
    if args.objective or args.result:
        ids = {identifier for r in children for identifier in r["taskIds"]}
        tasks = [r for r in tasks if r["id"] in ids]
    if args.view == "objectives":
        rows = [{k: v for k, v in g.items() if k != "results"} | {"resultsTotal": len(g["results"])} for g in goals]
    elif args.view == "results":
        rows = [{**_summary(r), "revision": r["revision"], "tasksTotal": len(r["taskIds"]),
                 "measurement": _short_measurement(r["measurement"]), **_assessment(r)} for r in children]
    elif args.view == "tasks":
        fields = ("id", "title", "path", "kind", "project", "projectTitle", "direction", "directionTitle", "missing",
                  "start", "due", "forecast", "completed", "snapshotLine")
        rows = [{k: r[k] for k in fields if k in r} for r in tasks]
    else:
        sources = {"unlinkedTaskIds": tasks, "missingTaskIds": tasks,
                   "emptyObjectiveIds": goals, "emptyResultIds": children}
        rows = []
        for kind, entities in sources.items():
            affected = set(board["gaps"][kind])
            for entity in entities:
                if entity["id"] in affected:
                    rows.append({"kind": kind, **{k: entity[k] for k in ("id", "title", "path")}})
    page = brief.page(rows, args.limit, args.offset)
    for row in page["items"]:
        brief.clip_fields(row, [k for k in ("title", "directionTitle", "projectTitle") if k in row])
    plan = dict(board["plan"])
    brief.clip_fields(plan, ("title",))
    return {"plan": plan, "view": args.view, "historical": board["historical"],
            "hierarchyRecorded": board["hierarchyRecorded"], "counts": total, **page}


def _criterion_summary(item):
    summary = {k: v for k, v in item.items() if k not in ("history", "baselineHistory", "targetHistory")}
    brief.clip_fields(summary, ("title", "unit"))
    return summary


def _assessment(record):
    return {"criteriaMet": record.get("criteriaMet"), "criteriaTotal": len(record.get("criteria", []))}


def _criterion_write(service, args):
    if args.command == "okr-criterion-add":
        baseline = None
        if any(v is not None for v in (args.baseline, args.baseline_source, args.baseline_date,
                                       args.baseline_line, args.baseline_quote)):
            source = {"path": args.baseline_source, "line": args.baseline_line}
            if args.baseline_quote is not None:
                source["quote"] = args.baseline_quote
            baseline = {"value": args.baseline, "date": args.baseline_date, "source": source}
        card = service.add_criterion(args.id, title=args.title, kind=args.kind, primary=args.primary, unit=args.unit,
                                     operator=args.operator, target=args.target, baseline=baseline,
                                     revision=args.revision, request_id=args.request_id)
        from .okr_criteria import criterion_id
        selected = criterion_id(args.id, args.request_id)
    else:
        selected = args.criterion_id
        if args.command == "okr-criterion-target":
            card = service.set_criterion_target(args.id, selected, operator=args.operator, target=args.target,
                                                revision=args.revision)
        else:
            value = {"true": True, "да": True, "yes": True, "false": False, "нет": False, "no": False}.get(
                args.value.casefold(), args.value)
            source = {"path": args.source, "line": args.line}
            if args.quote is not None:
                source["quote"] = args.quote
            card = service.observe_criterion(args.id, selected, value=value, date=args.date, source=source,
                                             baseline=args.baseline, revision=args.revision)
    item = next(c for c in card["criteria"] if c["id"] == selected)
    return {"id": card["id"], "revision": card["revision"], "path": card["path"],
            "criterion": _criterion_summary(item), **_assessment(card)}


def run(root, args):
    from .okr import KeyResults

    service = KeyResults(root)
    if args.command == "okr-criterion-show":
        card = service.show(args.id)
        item = next((c for c in card["criteria"] if c["id"] == args.criterion_id), None)
        if item is None:
            raise FileNotFoundError("Критерий не найден в указанном КР.")
        detail = {"id": card["id"], "path": card["path"], "revision": card["revision"],
                  "criterion": _criterion_summary(item), **_assessment(card)}
        if args.history:
            field = {"measurements": "history", "baselines": "baselineHistory", "targets": "targetHistory"}[args.history]
            detail.update(brief.page(reversed(item[field]), args.limit, args.offset))
        return detail
    if args.command.startswith("okr-criterion-"):
        return _criterion_write(service, args)
    if args.command == "okr-plan":
        return _board_page(service.board(args.id), args)
    if args.command == "okr-objective-create":
        goal = service.create_objective(args.title, direction=args.direction, plan_id=args.plan, request_id=args.request_id)
        brief.clip_fields(goal, ("title",))
        return goal
    if args.command == "okr-list":
        query = search._normalize(args.match.strip())
        rows = (row for row in service.list(direction=args.direction)
                if any(query in search._normalize(row[key]) for key in ("title", "objective")))
        page = brief.page(rows, args.limit, args.offset)
        items = []
        for row in page["items"]:
            items.append({**_summary(row), "measurement": _short_measurement(row["measurement"])})
        return {**page, "items": items}
    if args.command == "okr-show":
        record = service.show(args.id)
        if args.projects:
            page = brief.page(record["contextProjects"], args.limit, args.offset)
            for project in page["items"]:
                brief.clip_fields(project, ("title",))
            return {"id": record["id"], "path": record["path"], "revision": record["revision"], **page}
        if args.criteria:
            page = brief.page((_criterion_summary(c) for c in record["criteria"]), args.limit, args.offset)
            return {"id": record["id"], "path": record["path"], "revision": record["revision"],
                    **_assessment(record), **page}
        field = "history" if args.history else "tasks"
        page = brief.page(reversed(record["history"]) if args.history else record["tasks"], args.limit, args.offset)
        if not args.history:
            rows = [{key: task[key] for key in ("id", "title", "path", "missing") if key in task}
                    for task in page["items"]]
            for row in rows:
                brief.clip_fields(row, ("title",))
            page["items"] = rows
        return {**_summary(record), **_assessment(record), "revision": record["revision"], "measurement": record["measurement"],
                field: page["items"], field + "Total": page["total"], "projectsTotal": len(record["contextProjects"]),
                "truncated": page["truncated"]}
    if args.command == "okr-create":
        record = service.create(args.title, objective=args.objective, objective_id=args.objective_id,
                                direction=args.direction, unit=args.unit,
                                target=args.target, task_ids=args.task, request_id=args.request_id)
    elif args.command == "okr-assign":
        record = service.assign_objective(args.id, args.objective_id, args.revision)
    elif args.command == "okr-context":
        record = service.context_project(args.id, args.project_id, present=args.present, revision=args.revision)
    elif args.command == "okr-unlink":
        record = service.unlink(args.id, args.task_id, args.revision)
    elif args.command == "okr-link":
        record = service.link(args.id, args.task_id, args.revision)
    else:
        source = {"path": args.source, "line": args.line}
        if args.quote is not None:
            source["quote"] = args.quote
        record = service.measure(args.id, value=args.value, date=args.date, source=source, revision=args.revision)
    result = {"id": record["id"], "path": record["path"], "revision": record["revision"],
              "planId": record["planId"], "objectiveId": record["objectiveId"],
              "tasksTotal": len(record["tasks"]), "projectsTotal": len(record["contextProjects"]),
              "historyTotal": len(record["history"]),
              "unit": record["unit"], "measurement": record["measurement"]}
    brief.clip_fields(result, ("unit",))
    return result
