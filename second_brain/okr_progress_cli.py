"""Compact CLI for the readiness of work and the arithmetic of a period plan."""

from . import brief
from .okr_progress import Progress


def _page_arguments(command):
    command.add_argument("--limit", type=brief.result_limit, default=brief.DEFAULT_LIMIT)
    command.add_argument("--offset", type=brief.offset, default=0)


def add_commands(commands):
    weight = commands.add_parser("okr-weight", help="указать вес КР в цели или работы в КР")
    weight.add_argument("result")
    weight.add_argument("--task")
    amount = weight.add_mutually_exclusive_group(required=True)
    amount.add_argument("--value")
    amount.add_argument("--clear", action="store_true")
    weight.add_argument("--revision", required=True)
    ready = commands.add_parser("task-readiness", help="прочитать историю или записать готовность работы")
    ready.add_argument("task")
    for field in ("value", "date", "revision", "reason", "source", "quote"):
        ready.add_argument("--" + field)
    ready.add_argument("--line", type=int)
    ready.add_argument("--kind", choices=("fact", "estimate"))
    ready.add_argument("--actor", choices=("ai", "manual"))
    ready.add_argument("--owner-confirmed", action="store_true")
    _page_arguments(ready)
    progress = commands.add_parser("okr-progress", help="проверить готовность и вклады по плану, цели или КР")
    progress.add_argument("plan")
    scope = progress.add_mutually_exclusive_group()
    scope.add_argument("--objective")
    scope.add_argument("--result")
    _page_arguments(progress)
    for command in (weight, ready, progress):
        command.set_defaults(okr_progress=True)


def _readiness(row):
    if row is None:
        return None
    result = dict(row)
    brief.clip_fields(result, ("reason",))
    if row["source"]:
        result["source"] = {k: row["source"][k] for k in ("path", "line", "unavailable") if k in row["source"]}
    return result


def _short_card(row):
    result = dict(row)
    brief.clip_fields(result, ("title",))
    return result


def _progress_page(service, args):
    board = service.results.board(args.plan)
    selected = board["plan"]
    members = board["objectives"]
    weights = {}
    if args.objective:
        selected = next((g for g in members if g["id"] == args.objective), None)
        if selected is None:
            raise FileNotFoundError("Цель отсутствует в этом плане.")
        members = selected["results"]
        weights = {result["id"]: result.get("weight") for result in members}
    elif args.result:
        selected = next((result for g in members for result in g["results"] if result["id"] == args.result), None)
        if selected is None:
            raise FileNotFoundError("КР отсутствует в этом плане.")
        tasks = {task["id"]: task for task in board["items"]}
        members = [tasks[identifier] for identifier in selected["taskIds"]]
        weights = selected.get("taskWeights", {})
    summary = selected.get("progress") if args.result or args.objective else board.get("progress")
    output = {k: selected[k] for k in ("id", "title", "path") if k in selected}
    output.update(historical=board["historical"], progress=None)
    if summary:
        output["progress"] = {k: v for k, v in summary.items()
                              if k not in ("missingWeights", "missingValues", "contributions")}
        output["progress"].update(missingWeightCount=len(summary["missingWeights"]),
                                  missingValueCount=len(summary["missingValues"]))
    rows = brief.page(members, args.limit, args.offset)
    items = []
    for member in rows["items"]:
        item = {k: member.get(k) for k in ("id", "title", "path")}
        if args.objective or args.result:
            item["weight"] = weights.get(member["id"])
            item["contribution"] = summary["contributions"].get(member["id"]) if summary else None
        if args.result:
            item["readiness"] = _readiness(member.get("readiness"))
        else:
            p = member.get("progress")
            item.update(value=p["value"] if p else None, hasEstimates=p["hasEstimates"] if p else None)
        items.append(_short_card(item))
    return {**_short_card(output), **rows, "items": items, "limit": args.limit, "offset": args.offset}


def run(root, args):
    service = Progress(root)
    if args.command == "okr-progress":
        return _progress_page(service, args)
    if args.command == "okr-weight":
        return service.set_weight(args.result, task_id=args.task, weight=args.value, revision=args.revision)
    if args.value is None:
        if any(getattr(args, key) is not None for key in ("date", "revision", "kind", "actor", "reason", "source",
                                                         "line", "quote")) or args.owner_confirmed:
            raise ValueError("Для изменения готовности укажите --value.")
        page = service.readiness(args.task, limit=args.limit, offset=args.offset)
        return {**_short_card(page), "readiness": _readiness(page["readiness"]),
                "items": [_readiness(row) for row in page["items"]]}
    if (args.source is None) != (args.line is None) or (args.quote is not None and args.source is None):
        raise ValueError("Для основания укажите --source и --line вместе.")
    source = None
    if args.source is not None:
        source = {"path": args.source, "line": args.line}
        if args.quote is not None:
            source["quote"] = args.quote
    result = service.set_readiness(args.task, value=args.value, date=args.date, kind=args.kind, revision=args.revision,
                                   actor=args.actor or "ai", reason=args.reason, source=source,
                                   owner_confirmed=args.owner_confirmed)
    return {**_short_card(result), "readiness": _readiness(result["readiness"])}
