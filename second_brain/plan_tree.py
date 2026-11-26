"""A period's work breakdown. Task ownership, dates and dependencies stay on the task."""

import json
import re

from . import brief, storage


_PARENT = " <!-- brain-plan-parent: "


def parent_links(body):
    parents = {}
    for line in body.splitlines():
        if not line.startswith("- ") or _PARENT not in line:
            continue
        try:
            payload = line.rsplit(_PARENT, 1)[1]
            if not payload.endswith(" -->"):
                raise ValueError
            event = json.loads(payload[:-4])
            child, parent = event["child"], event["parent"]
            if not isinstance(child, str) or (parent is not None and not isinstance(parent, str)):
                raise ValueError
            if parent is None:
                parents.pop(child, None)
            else:
                parents[child] = parent
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError("Проверьте запись вложенности в карточке плана.") from error
    return parents


def check_parents(parents):
    done = set()
    for start in parents:
        chain = set()
        cursor = start
        while cursor in parents and cursor not in done:
            if cursor in chain:
                raise ValueError("Связь замыкает цепочку родительских работ.")
            chain.add(cursor)
            cursor = parents[cursor]
        done.update(chain)


def set_parent(planning, plan_id, task_id, parent_id, revision):
    from .cycles import closed_metadata
    if not isinstance(revision, str) or not re.fullmatch("[0-9a-f]{64}", revision):
        raise ValueError("Укажите прочитанную версию плана.")
    if not isinstance(task_id, str) or (parent_id is not None and not isinstance(parent_id, str)):
        raise ValueError("Укажите ID работы и родителя.")
    with storage._write_lock(planning.root):
        path, raw, record, header = planning._find("plan", plan_id)
        if closed_metadata(record):
            raise storage.ConflictError("Вложенность сохранена в закрытом плане.")
        members = {r["id"]: r for r in planning._view(path, record)["items"]}
        child = members.get(task_id)
        parent = members.get(parent_id)
        if not child or child.get("missing") or (parent_id is not None and (not parent or parent.get("missing"))):
            raise ValueError("Выберите существующие работы этого плана.")
        parents = parent_links(record["body"])
        if parents.get(task_id) != parent_id:
            if revision != record["revision"]:
                raise storage.ConflictError("План изменился. Перечитайте его перед изменением вложенности.")
            if parent_id:
                parents[task_id] = parent_id
                check_parents(parents)
            name = storage.markdown_link(child["title"], planning.root / child["path"], path.parent)
            target = storage.markdown_link(parent["title"], planning.root / parent["path"], path.parent) if parent else "верхний уровень"
            event = storage.comment_json({"child": task_id, "parent": parent_id})
            newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
            body = record["body"] + newline * 2
            if _PARENT not in record["body"]:
                body += "## Вложенность работ" + newline * 2
            body += f"- {name} → {target}{_PARENT}{event} -->{newline}"
            saved = header + body.encode()
            storage.replace_bytes(path, raw, saved)
            revision = storage._parse_record(path, saved, kinds=("plan",))[0]["revision"]
        else:
            revision = record["revision"]
        return {"id": plan_id, "taskId": task_id, "parentId": parent_id, "revision": revision}


def outline(board):
    """One occurrence per branch. Repeated occurrences refer to the same task ID."""
    rows = {r["id"]: r for r in board["items"]}
    parents = {r["id"]: r["parentId"] for r in rows.values() if r.get("parentId") in rows}
    check_parents(parents)
    links = {}
    goals = board["objectives"]
    for goal in goals:
        for result in goal["results"]:
            for task_id in result["taskIds"]:
                links.setdefault(task_id, set()).add(result["id"])
    branches = {}
    for task_id in rows:
        ancestor = task_id
        while ancestor not in links and ancestor in parents:
            ancestor = parents[ancestor]
        for branch in links.get(ancestor, (None,)):
            branches.setdefault(branch, []).append(task_id)

    entries = []
    def append(key, kind, identifier, title, parent_key=None, level=0, result_id=None):
        entries.append({"key": key, "kind": kind, "id": identifier, "title": title,
                        "parentKey": parent_key, "level": level, "resultId": result_id})

    def tasks(branch, root_key, level):
        ids = branches.get(branch, [])
        included = set(ids)
        contextual = set()
        for identifier in ids:
            parent = parents.get(identifier)
            while parent is not None and parent not in included:
                included.add(parent)
                contextual.add(parent)
                parent = parents.get(parent)
        children = {}
        for identifier in rows:
            if identifier not in included:
                continue
            parent = parents.get(identifier)
            children.setdefault(parent if parent in included else None, []).append(identifier)
        stack = [(identifier, root_key, level) for identifier in reversed(children.get(None, []))]
        while stack:
            identifier, previous, depth = stack.pop()
            key = (branch or "outside") + "/task/" + identifier
            append(key, "task", identifier, rows[identifier].get("title") or identifier, previous, depth, branch)
            entries[-1].update(contextOnly=identifier in contextual, linkedToResult=branch in links.get(identifier, ()))
            stack.extend((child, key, depth + 1) for child in reversed(children.get(identifier, [])))

    for goal in goals:
        goal_key = "objective/" + goal["id"]
        append(goal_key, "objective", goal["id"], goal["title"])
        entries[-1]["readiness"] = (goal.get("progress") or {}).get("value")
        entries[-1]["hasEstimates"] = (goal.get("progress") or {}).get("hasEstimates")
        for kr in goal["results"]:
            key = "result/" + kr["id"]
            append(key, "result", kr["id"], kr["title"], goal_key, 1, kr["id"])
            entries[-1]["readiness"] = (kr.get("progress") or {}).get("value")
            entries[-1]["hasEstimates"] = (kr.get("progress") or {}).get("hasEstimates")
            tasks(kr["id"], key, 2)
    if branches.get(None):
        if goals:
            append("outside", "outside", None, "Другие работы плана")
        tasks(None, "outside" if goals else None, 1 if goals else 0)
    return entries


def add_commands(commands):
    tree = commands.add_parser("plan-tree", help="показать дерево целей, результатов и работ плана")
    tree.add_argument("id")
    tree.add_argument("--result", help="показать ветвь одного КР")
    tree.add_argument("--limit", type=brief.result_limit, default=brief.DEFAULT_LIMIT)
    tree.add_argument("--offset", type=brief.offset, default=0)
    parent = commands.add_parser("plan-parent", help="указать родителя одной работы в выбранном плане")
    parent.add_argument("id")
    parent.add_argument("task")
    group = parent.add_mutually_exclusive_group(required=True)
    group.add_argument("--parent")
    group.add_argument("--clear", action="store_true")
    parent.add_argument("--revision", required=True)
    for parser in (tree, parent):
        parser.set_defaults(plan_tree=True)


def run(root, args):
    from .okr import KeyResults
    from .planning import Planning
    if args.command == "plan-parent":
        return set_parent(Planning(root), args.id, args.task, args.parent, args.revision)
    board = KeyResults(root).board(args.id)
    entries = outline(board)
    if args.result:
        entries = [e for e in entries if e["resultId"] == args.result]
        if not entries:
            raise ValueError("Результат не найден в этом плане.")
    page = brief.page(entries, args.limit, args.offset)
    for row in page["items"]:
        brief.clip_fields(row, ("title",))
    return {"plan": args.id, "revision": board["plan"]["revision"], "historical": board["historical"], **page}
