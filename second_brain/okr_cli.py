"""CLI for optional objectives and measured key results."""

from . import brief, search


def add_commands(commands):
    create = commands.add_parser("okr-create", help="создать ключевой результат цели и связать с задачами")
    create.add_argument("title")
    create.add_argument("--objective", required=True, help="цель, достижению которой помогает результат")
    create.add_argument("--direction", required=True, metavar="ID")
    create.add_argument("--unit", required=True, help="единица измерения")
    create.add_argument("--target", help="целевое значение")
    create.add_argument("--task", action="append", default=[], metavar="ID", help="связать задачу (можно повторять)")
    create.add_argument("--request-id", required=True, help="устойчивый ключ одного запроса на создание")

    listing = commands.add_parser("okr-list", help="найти ключевые результаты по направлению, цели или названию")
    listing.add_argument("--direction", metavar="ID")
    listing.add_argument("--match", default="", help="фрагмент цели или названия результата")
    show = commands.add_parser("okr-show", help="прочитать результат, связанные задачи или историю измерений")
    show.add_argument("id")
    show.add_argument("--history", action="store_true", help="показать историю измерений")
    for parser in (listing, show):
        parser.add_argument("--limit", type=brief.result_limit, default=brief.DEFAULT_LIMIT)
        parser.add_argument("--offset", type=brief.offset, default=0)

    link = commands.add_parser("okr-link", help="связать существующую задачу с ключевым результатом")
    link.add_argument("id")
    link.add_argument("task_id")
    measure = commands.add_parser("okr-measure", help="записать измеренное значение с датой и источником")
    measure.add_argument("id")
    measure.add_argument("--value", required=True, help="измеренное число")
    measure.add_argument("--date", required=True, metavar="YYYY-MM-DD", help="дата измерения")
    measure.add_argument("--source", required=True, metavar="ПУТЬ", help="заметка или проект с измерением")
    measure.add_argument("--line", required=True, type=int, metavar="N", help="строка с измерением")
    measure.add_argument("--quote", help="точная выдержка из строки")
    for parser in (measure, link):
        parser.add_argument("--revision", required=True, help="прочитанная ревизия ключевого результата")
    for parser in (create, listing, show, link, measure):
        parser.set_defaults(okr=True)


def _summary(record):
    result = {key: record[key] for key in ("id", "title", "objective", "direction", "path", "unit", "target", "targetLine")}
    brief.clip_fields(result, ("title", "objective", "unit"))
    return result


def run(root, args):
    from .okr import KeyResults

    service = KeyResults(root)
    if args.command == "okr-list":
        query = search._normalize(args.match.strip())
        rows = (row for row in service.list(direction=args.direction)
                if any(query in search._normalize(row[key]) for key in ("title", "objective")))
        page = brief.page(rows, args.limit, args.offset)
        items = []
        for row in page["items"]:
            measurement = row["measurement"]
            if measurement is not None:
                source = measurement["source"]
                measurement = {"value": measurement["value"], "date": measurement["date"],
                               "source": {key: source[key] for key in ("path", "line", "unavailable") if key in source}}
            items.append({**_summary(row), "measurement": measurement})
        return {**page, "items": items}
    if args.command == "okr-show":
        record = service.show(args.id)
        field = "history" if args.history else "tasks"
        page = brief.page(reversed(record["history"]) if args.history else record["tasks"], args.limit, args.offset)
        if not args.history:
            rows = [{key: task[key] for key in ("id", "title", "path", "missing") if key in task}
                    for task in page["items"]]
            for row in rows:
                brief.clip_fields(row, ("title",))
            page["items"] = rows
        return {**_summary(record), "revision": record["revision"], "measurement": record["measurement"],
                field: page["items"], field + "Total": page["total"], "truncated": page["truncated"]}
    if args.command == "okr-create":
        record = service.create(args.title, objective=args.objective, direction=args.direction, unit=args.unit,
                                target=args.target, task_ids=args.task, request_id=args.request_id)
    elif args.command == "okr-link":
        record = service.link(args.id, args.task_id, args.revision)
    else:
        source = {"path": args.source, "line": args.line}
        if args.quote is not None:
            source["quote"] = args.quote
        record = service.measure(args.id, value=args.value, date=args.date, source=source, revision=args.revision)
    result = {"id": record["id"], "path": record["path"], "revision": record["revision"],
              "tasksTotal": len(record["tasks"]), "historyTotal": len(record["history"]),
              "unit": record["unit"], "measurement": record["measurement"]}
    brief.clip_fields(result, ("unit",))
    return result
