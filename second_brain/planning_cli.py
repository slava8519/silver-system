"""Bounded CLI views of the optional task and plan records."""

import argparse
import json
import sys

from . import brief, search


def _paging(parser):
    parser.add_argument("--limit", type=brief.result_limit, default=10)
    parser.add_argument("--offset", type=brief.offset, default=0)


def _plans_page(result, args):
    plans = brief.page(result["plans"], args.limit, args.offset)
    return {**result, "plans": plans["items"], "plansTotal": plans["total"], "truncated": plans["truncated"]}


def _schedule_command(commands):
    schedule = commands.add_parser("task-schedule", help="показать согласованный срок, изменить прогноз или записать факт завершения")
    schedule.add_argument("task_id")
    action = schedule.add_mutually_exclusive_group()
    action.add_argument("--show", action="store_true", help="показать три даты задачи (по умолчанию)")
    action.add_argument("--history", action="store_true", help="показать историю прогноза и завершения")
    for field, label in (("forecast", "прогноз"), ("completed", "факт завершения")):
        action.add_argument("--" + field, metavar="YYYY-MM-DD", default=argparse.SUPPRESS, help="записать " + label)
        action.add_argument("--clear-" + field, dest=field, action="store_const", const=None,
                            default=argparse.SUPPRESS, help="снять " + label)
    schedule.add_argument("--apply", metavar="REV", help="применить версию из предпросмотра")
    schedule.add_argument("--reason", help="сохранить пояснение в истории правки")
    schedule.add_argument("--owner-confirmed", action="store_true", help="владелец явно подтвердил факт или его исправление")
    schedule.add_argument("--source", metavar="ПУТЬ", help="путь к карточке заметки или проекта")
    schedule.add_argument("--line", type=int, metavar="N", help="номер строки основания")
    schedule.add_argument("--quote", help="точная цитата из строки основания")
    _paging(schedule)
    schedule.set_defaults(planning=True)


def _schedule(root, planning, args):
    from .schedule import Schedule

    service = Schedule(root)
    field = next((name for name in ("forecast", "completed") if hasattr(args, name)), None)
    if field is None:
        if any(v is not None for v in (args.apply, args.reason, args.source, args.line, args.quote)) or args.owner_confirmed:
            raise ValueError("Для записи выберите --forecast, --completed или соответствующее снятие даты.")
        if args.history:
            return brief.page(reversed(service.history(args.task_id)), args.limit, args.offset)
        row = planning.task(args.task_id)
        return {key: row[key] for key in ("id", "title", "path", "due", "dueLine", "forecast", "forecastLine",
                                          "completed", "completedLine")}
    if (args.source is None) != (args.line is None) or (args.quote is not None and args.source is None):
        raise ValueError("Для ссылки передайте --source ПУТЬ и --line N вместе.")
    sources = []
    if args.source is not None:
        source = {"path": args.source, "line": args.line}
        if args.quote is not None:
            source["quote"] = args.quote
        sources.append(source)
    evidence = {"actor": "ai", "sources": sources, "reason": args.reason, "owner_confirmed": args.owner_confirmed}
    value = getattr(args, field)
    if args.apply is None:
        result = service.preview(args.task_id, field, value, **evidence)
    else:
        result = service.apply(args.task_id, field, value, args.apply, **evidence)
    return _plans_page(result, args)


def add_commands(commands):
    _schedule_command(commands)
    task = commands.add_parser("task-create", help="записать задачу с необязательными проектом и сроком")
    plan = commands.add_parser("plan-create", help="создать именованный план работ")
    for parser in (task, plan):
        parser.add_argument("title")
        parser.add_argument("--request-id", help="устойчивый ключ одного запроса на создание")
    task.add_argument("--project", metavar="ID")
    task.add_argument("--direction", metavar="ID")
    task.add_argument("--due", metavar="YYYY-MM-DD")
    task.add_argument("--kind", choices=("task", "milestone"), default="task", help="обычная работа или веха")

    dependency = commands.add_parser("task-dependency", help="показать, добавить или снять зависимость от предшественника")
    dependency.add_argument("task_id", help="ID зависимой работы")
    edge = dependency.add_mutually_exclusive_group()
    edge.add_argument("--add", metavar="ID", help="добавить предшественника")
    edge.add_argument("--remove", metavar="ID", help="убрать предшественника")
    edge.add_argument("--history", action="store_true", help="история добавления и снятия предшественников этой работы")
    _paging(dependency)
    scenario = commands.add_parser("task-scenario", help="рассчитать влияние прогноза на связанные работы")
    scenario.add_argument("task_id")
    scenario.add_argument("--forecast", required=True, metavar="YYYY-MM-DD")
    scenario.add_argument("--apply", metavar="REV", help="сохранить прогнозы из подтверждённого расчёта")
    scenario.add_argument("--plans-offset", type=brief.offset, default=0, help="смещение в списках планов строк")
    _paging(scenario)

    tasks = commands.add_parser("task-list", help="найти задачи по названию, направлению или проекту")
    plans = commands.add_parser("plan-list", help="найти планы по названию")
    for parser in (tasks, plans):
        parser.add_argument("--match", default="", help="часть названия")
        _paging(parser)
    tasks.add_argument("--project", metavar="ID")
    tasks.add_argument("--direction", metavar="ID")

    show_task = commands.add_parser("task-show", help="прочитать задачу и строку её срока")
    show_plan = commands.add_parser("plan-show", help="показать работы и даты активного плана или снимок закрытого")
    close = commands.add_parser("plan-close", help="проверить итог цикла и закрыть план по ревизии предпросмотра")
    close.add_argument("--apply", metavar="REV", help="закрыть весь план по проверенной ревизии")
    add = commands.add_parser("plan-add", help="включить существующую задачу в план")
    add.add_argument("plan_id")
    add.add_argument("task_id")
    add.add_argument("--revision", help="прочитанная ревизия плана")
    move = commands.add_parser("task-due", help="предпросмотр или применение изменения срока задачи")
    date = move.add_mutually_exclusive_group(required=True)
    date.add_argument("--date", metavar="YYYY-MM-DD")
    date.add_argument("--clear", action="store_true", help="снять срок")
    move.add_argument("--apply", metavar="REV", help="применить изменение по ревизии из предпросмотра")
    history = commands.add_parser("task-history", help="показать последние изменения срока задачи")
    for parser in (show_task, move, history):
        parser.add_argument("task_id")
    for parser in (show_plan, close):
        parser.add_argument("plan_id")
        parser.add_argument("--task", metavar="ID", help="показать только строку выбранной работы")
    for parser in (show_plan, close, move, history):
        _paging(parser)
    row = commands.add_parser("gantt-row", help="прочитать сводку задачи и страницу событий с основаниями")
    row.add_argument("task_id")
    _paging(row)
    row.add_argument("--manual", action="store_true", help="прочитать только ручные события")
    edit = commands.add_parser("gantt-edit", help="обновить Коротко или Последнее по источникам из JSON на stdin")
    edit.add_argument("--accept-manual", metavar="REV",
                      help="применить согласованный владельцем текст к прочитанной ручной версии")
    pending = commands.add_parser("gantt-feedback", help="найти ещё не сверенные ручные правки и даты")
    pending.add_argument("--plan", metavar="ID")
    _paging(pending)
    reviewed = commands.add_parser("gantt-reviewed", help="отметить прочитанную версию ручных правок задачи")
    reviewed.add_argument("task_id")
    reviewed.add_argument("token", help="отпечаток ручных правок из gantt-feedback")
    for parser in (task, plan, tasks, plans, show_task, show_plan, add, move, history, row, edit, pending, reviewed,
                   dependency, scenario, close):
        parser.set_defaults(planning=True)


def _dependency_command(root, args):
    from .dependencies import Dependencies

    service = Dependencies(root)
    if args.command == "task-dependency":
        if args.history:
            return brief.page(reversed(service.history(args.task_id)), args.limit, args.offset)
        if args.add is None and args.remove is None:
            result = service.links(args.task_id)
        else:
            result = service.link(args.task_id, args.add or args.remove, remove=args.remove is not None)
        for group in ("predecessors", "successors"):
            page = brief.page(result[group], args.limit, args.offset)
            result[group] = page["items"]
            result[group + "Total"] = page["total"]
            result[group + "Truncated"] = page["truncated"]
        return result
    if args.apply is None:
        result = service.preview(args.task_id, args.forecast)
    else:
        result = service.apply(args.task_id, args.forecast, args.apply)
    page = brief.page(result["rows"], args.limit, args.offset)
    rows = []
    for row in page["items"]:
        plans = brief.page(row["plans"], brief.DEFAULT_LIMIT, args.plans_offset)
        rows.append({**row, "plans": plans["items"], "plansTotal": plans["total"], "plansTruncated": plans["truncated"]})
    return {**result, "rows": rows, "rowsTotal": page["total"], "truncated": page["truncated"]}


def run(root, args):
    from .planning import Planning

    planning = Planning(root)
    command = args.command
    if command == "task-schedule":
        return _schedule(root, planning, args)
    if command in {"task-dependency", "task-scenario"}:
        return _dependency_command(root, args)
    if command in {"gantt-feedback", "gantt-reviewed"}:
        from .gantt_feedback import GanttFeedback
        feedback = GanttFeedback(root)
        if command == "gantt-reviewed":
            return feedback.acknowledge(args.task_id, args.token)
        return brief.page(feedback.pending(args.plan), args.limit, args.offset)
    if command in {"gantt-row", "gantt-edit"}:
        from .gantt import Gantt
        gantt = Gantt(root)
        if command == "gantt-edit":
            data = json.load(sys.stdin)
            if not isinstance(data, dict) or "actor" in data or data.get("field") not in ("brief", "latest"):
                raise ValueError("Передайте JSON с полем brief или latest. Правки через эту команду отмечаются как ИИ.")
            saved = gantt.edit({**data, "actor": "ai"}, accepted_manual_revision=args.accept_manual)
            return {key: saved[key] for key in ("id", "path", "revision", "latestTotal")}
        row = gantt.detail(args.task_id, all_entries=True)
        entries = (e for e in row["latest"] if not args.manual or e["manual"])
        page = brief.page(entries, args.limit, args.offset)
        return {**{key: row[key] for key in ("id", "title", "kind", "revision", "path", "start", "due",
                                             "forecast", "completed", "brief")},
                "latest": page["items"], "latestTotal": page["total"], "truncated": page["truncated"]}
    if command == "task-create":
        return planning.create_task(args.title, project=args.project, direction=args.direction,
                                    due=args.due, request_id=args.request_id, kind=args.kind)
    if command == "plan-create":
        plan = planning.create_plan(args.title, request_id=args.request_id)
        return {key: plan[key] for key in ("id", "title", "path", "revision")}
    if command in ("task-list", "plan-list"):
        if command == "task-list":
            rows = (item for item in planning.tasks()
                    if (not args.project or item["project"] == args.project)
                    and (not args.direction or item["direction"] == args.direction))
        else:
            rows = planning.plans()
        query = search._normalize(args.match.strip())
        result = brief.page((item for item in rows if query in search._normalize(item["title"])), args.limit, args.offset)
        for item in result["items"]:
            brief.clip_fields(item, ("title",))
            if command == "task-list":
                item.pop("revision", None)
        return result
    if command == "task-show":
        return planning.task(args.task_id)
    if command == "plan-add":
        plan = planning.add_task(args.plan_id, args.task_id, expected_revision=args.revision)
        return {key: plan[key] for key in ("id", "title", "path", "revision")} | {"taskId": args.task_id}
    if command in {"plan-show", "plan-close"}:
        if command == "plan-close":
            from .cycles import Cycles
            cycles = Cycles(root)
            plan = cycles.preview(args.plan_id) if args.apply is None else cycles.close(args.plan_id, args.apply)
        else:
            plan = planning.view(args.plan_id)
        members = (row for row in plan["items"] if args.task is None or row["id"] == args.task)
        page = brief.page(members, args.limit, args.offset)
        for item in page["items"]:
            item.pop("revision", None)
            brief.clip_fields(item, (field for field in ("title", "projectTitle", "directionTitle") if field in item))
        return {**plan, **page, "planItemsTotal": len(plan["items"])}

    from .plan_dates import Deadlines
    deadlines = Deadlines(root)
    if command == "task-history":
        return brief.page(reversed(deadlines.history(args.task_id)), args.limit, args.offset)
    due = None if args.clear else args.date
    if args.apply is None:
        result = deadlines.preview(args.task_id, due)
    else:
        result = deadlines.apply(args.task_id, due, args.apply)
    return _plans_page(result, args)
