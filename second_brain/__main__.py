"""Run the file-based second brain from the project directory."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from . import brief, intake_cli, okr_cli, planning_cli, search, storage, teams_cli, weekly_report
from .examples import create_examples


def command_line():
    parser = argparse.ArgumentParser(description="Локальное хранилище Markdown")
    parser.add_argument("--vault", required=True, type=Path, help="путь к хранилищу")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="подготовить пустое хранилище или дополнить существующее")
    commands.add_parser("examples", help="создать отдельное хранилище с тремя примерами")
    demo = commands.add_parser("demo", help="открыть локальный деморежим в браузере")
    demo.add_argument("--port", type=int, default=8765, help="локальный порт (по умолчанию 8765)")
    work = commands.add_parser("work", help="открыть рабочее хранилище в локальной оболочке")
    work.add_argument("--port", type=int, default=8766)
    ask = commands.add_parser("ask", help="найти ответ в нескольких записях и показать строки источников")
    ask.add_argument("query")
    ask.add_argument("--direction", action="append", metavar="ID", help="ограничить поиск направлением (повторяемый параметр)")
    ask.add_argument("--brief", action="store_true", help="вернуть только выдержки и координаты источников")
    ask.add_argument("--with-owner-context", action="store_true",
                     help="добавить выбранные владельцем карточку, команду и страницу её участников")
    ask.add_argument("--limit", type=brief.result_limit, help="число записей в кратком ответе (1–20, обычно 5)")
    overview = commands.add_parser("overview", help="показать состав и записанное состояние хранилища")
    overview.add_argument("--brief", action="store_true", help="показать только количество карточек по типам")
    overview.add_argument("--direction", metavar="ID", help="посчитать карточки выбранного направления с --brief")
    overview.add_argument("--folders", action="store_true", help="показать только пути папок и количество карточек")
    overview.add_argument("--limit", type=brief.result_limit, help="число папок с --folders (1–20, обычно 5)")
    overview.add_argument("--offset", type=brief.offset, help="сколько папок пропустить с --folders")
    create = commands.add_parser("create", help="создать направление, проект или заметку")
    create.add_argument("kind", choices=("direction", "project", "note"))
    create.add_argument("title")
    create.add_argument("--alias", action="append", default=[])
    create.add_argument("--direction", metavar="ID")
    create.add_argument("--body", default="")
    create.add_argument("--request-id", help="повторить то же создание с тем же ключом без дубликата")
    items = commands.add_parser("list", help="перечислить записи хранилища или направления")
    items.add_argument("--direction", metavar="ID")
    items.add_argument("--type", choices=storage.KINDS, help="отобрать карточки одного типа")
    items.add_argument("--match", metavar="ТЕКСТ", help="найти подстроку в названии или алиасе")
    items.add_argument("--limit", type=brief.result_limit, help="число кандидатов (1–20, при фильтрации обычно 5)")
    items.add_argument("--offset", type=brief.offset, help="сколько найденных карточек пропустить")
    show = commands.add_parser("show", help="прочитать запись и её версию")
    show.add_argument("id")
    show.add_argument("--brief", action="store_true", help="прочитать ревизию и поля карточки без основного текста")
    update = commands.add_parser("update", help="обновить поля прочитанной версии записи")
    update.add_argument("id")
    update.add_argument("--revision", required=True)
    update.add_argument("--body")
    update.add_argument("--title")
    aliases = update.add_mutually_exclusive_group()
    aliases.add_argument("--alias", action="append", dest="alias", help="новый список алиасов")
    aliases.add_argument("--clear-aliases", action="store_const", const=[], dest="alias")
    membership = update.add_mutually_exclusive_group()
    membership.add_argument("--direction", metavar="ID")
    membership.add_argument("--detach", action="store_true", help="снять связь с направлением")
    for option, field in (("status", "status"), ("due", "due"), ("next-step", "nextStep")):
        create.add_argument("--" + option, dest=field, default=argparse.SUPPRESS)
        group = update.add_mutually_exclusive_group()
        group.add_argument("--" + option, dest=field, default=argparse.SUPPRESS)
        group.add_argument("--clear-" + option, action="store_const", const=None, dest=field,
                           default=argparse.SUPPRESS, help="очистить поле " + option)
    lookup = commands.add_parser("search", help="найти фрагменты с путями и номерами строк")
    lookup.add_argument("query")
    lookup.add_argument("--limit", type=int, default=5)
    read = commands.add_parser("read", help="открыть строки источника из выдачи поиска")
    read.add_argument("path")
    read.add_argument("start", type=int)
    read.add_argument("end", type=int)
    commands.add_parser("rebuild", help="пересобрать поисковый индекс из файлов")
    intake_cli.add_commands(commands)
    planning_cli.add_commands(commands)
    okr_cli.add_commands(commands)
    teams_cli.add_commands(commands)
    weekly_report.add_command(commands)
    return parser


def summary(root, path, record):
    return {"id": record["id"], "type": record["type"], "title": record["title"],
            "direction": record["direction"], "path": path.relative_to(root).as_posix()}


def list_records(root, args):
    if args.direction:
        direction = storage.read_record(storage.find_record(root, args.direction))
        if direction["type"] != "direction":
            raise ValueError("Для --direction требуется ID направления.")
    match = None
    if args.match is not None:
        match = search._normalize(args.match.strip())
        if not match:
            raise ValueError("Укажите фрагмент названия или алиаса.")

    def candidates():
        for path, record in storage.iter_records(root):
            if args.direction and record["direction"] != args.direction:
                continue
            if args.type and record["type"] != args.type:
                continue
            if match and not any(match in search._normalize(name)
                                 for name in (record["title"], *record["aliases"])):
                continue
            yield summary(root, path, record)

    if args.type or match or args.limit or args.offset is not None:
        result = brief.page(candidates(), args.limit or brief.DEFAULT_LIMIT, args.offset or 0)
        for item in result["items"]:
            brief.clip_fields(item, ("title",))
        return result
    return list(candidates())


def run(args):
    # Keep the final path component intact so examples detects even a dangling symlink.
    root = args.vault.expanduser().absolute()
    if args.command in ("demo", "work"):
        from .webserver import serve
        return serve(root, args.port, mode=args.command)
    if args.command == "examples":
        create_examples(root)
        return {"vault": str(root)}
    root = root.resolve()
    if args.command == "init":
        storage.initialize(root)
        return {"vault": str(root)}
    if not root.is_dir():
        raise FileNotFoundError(f"Хранилище не найдено: {root}")
    if getattr(args, "intake", False):
        return intake_cli.run(root, args)
    if getattr(args, "planning", False):
        return planning_cli.run(root, args)
    if getattr(args, "okr", False):
        return okr_cli.run(root, args)
    if getattr(args, "teams", False):
        return teams_cli.run(root, args)
    if args.command == "weekly-review":
        return weekly_report.run(root, args)
    if args.command == "ask":
        from .query import WorkspaceQuery
        if args.limit and not args.brief:
            raise ValueError("Для ограничения числа выдержек укажите --brief.")
        data = {"query": args.query}
        if args.direction:
            data["directionIds"] = args.direction
        result = WorkspaceQuery(root).ask(data)
        limit = args.limit or brief.DEFAULT_LIMIT
        if args.brief:
            result = brief.answer(result, limit)
        if args.with_owner_context:
            from .teams import Teams
            context = Teams(root).context(limit=limit)
            result["ownerContext"] = brief.owner_context(context) if args.brief else context
        return result
    if args.command in ("create", "update", "overview"):
        from .workspace import Workspace
        workspace = Workspace(root)
        if args.command == "overview":
            if args.folders:
                if args.direction:
                    raise ValueError("Папки относятся ко всему хранилищу. Уберите --direction.")
                return brief.page(workspace.overview()["folders"], args.limit or brief.DEFAULT_LIMIT, args.offset or 0)
            if args.limit or args.offset is not None:
                raise ValueError("Для постраничного перечня папок укажите --folders.")
            if args.direction and not args.brief:
                raise ValueError("Для сводки по одному направлению укажите --brief.")
            result = workspace.overview()
            return brief.overview(result, args.direction) if args.brief else result
        fields = {key: getattr(args, key) for key in ("title", "body", "direction")
                  if getattr(args, key) is not None}
        fields.update({key: vars(args)[key] for key in ("status", "due", "nextStep") if key in vars(args)})
        if args.alias is not None:
            fields["aliases"] = args.alias
        if args.command == "create":
            record = workspace.create_record({**fields, "type": args.kind, "requestId": args.request_id})
        else:
            if args.detach:
                fields["direction"] = None
            if not fields:
                raise ValueError("Укажите, какое поле карточки обновить.")
            record = workspace.edit_record({**fields, "id": args.id, "revision": args.revision})
        return {key: record[key] for key in ("id", "type", "title", "direction", "path")}
    if args.command == "list":
        return list_records(root, args)
    if args.command == "show":
        if args.brief:
            from .workspace import Workspace
            return brief.record(Workspace(root).get_record(args.id))
        path = storage.find_record(root, args.id)
        return {**storage.read_record(path), "path": path.relative_to(root).as_posix()}
    if args.command == "search":
        return search.search(root, args.query, args.limit)
    if args.command == "read":
        return search.fragment(root, args.path, args.start, args.end)
    if args.command == "rebuild":
        search.rebuild(root)
        return {"vault": str(root), "index": "rebuilt"}


def main():
    args = command_line().parse_args()
    try:
        result = run(args)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(error, file=sys.stderr)
        return 1
    if result is None:
        return 0
    if isinstance(result, str):
        print(result)
    elif (getattr(args, "brief", False) or getattr(args, "folders", False) or getattr(args, "intake", False)
          or getattr(args, "planning", False) or getattr(args, "okr", False) or getattr(args, "teams", False)
          or args.command == "weekly-review"
          or (args.command == "list" and isinstance(result, dict))):
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
