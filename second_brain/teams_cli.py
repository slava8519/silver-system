"""People groups and the explicitly selected owner context of a vault."""

import argparse

from . import brief


def add_commands(commands):
    person = commands.add_parser("person-create", help="создать карточку человека")
    team = commands.add_parser("team-create", help="создать группу людей")
    for parser in (person, team):
        parser.add_argument("name", help="имя человека или название группы")
        parser.add_argument("--request-id", required=True, help="устойчивый ключ одного запроса на создание")

    listing = commands.add_parser("team-list", help="найти группы по названию или участнику")
    listing.add_argument("--match", default="", help="часть названия группы")
    listing.add_argument("--person", metavar="ID", help="группы выбранного человека")
    show = commands.add_parser("team-show", help="прочитать карточку группы и страницу её участников")
    rename = commands.add_parser("team-rename", help="изменить название группы")
    rename.add_argument("id")
    rename.add_argument("name", help="новое название")
    member = commands.add_parser("team-member", help="добавить участника группы или исключить из группы")
    member.add_argument("id", help="ID группы")
    member.add_argument("person", help="ID человека")
    action = member.add_mutually_exclusive_group(required=True)
    action.add_argument("--add", dest="present", action="store_true")
    action.add_argument("--remove", dest="present", action="store_false")
    for parser in (rename, member):
        parser.add_argument("--revision", required=True, help="прочитанная ревизия группы")
    show.add_argument("id", help="ID группы")

    context = commands.add_parser("owner-context", help="прочитать или изменить выбор владельца и своей команды")
    for field, label in (("owner", "владельца"), ("team", "своей команды")):
        selection = context.add_mutually_exclusive_group()
        selection.add_argument("--" + field, metavar="ID", default=argparse.SUPPRESS, help="выбрать карточку " + label)
        selection.add_argument("--clear-" + field, dest=field, action="store_const", const=None,
                               default=argparse.SUPPRESS, help="снять выбор " + label)
    context.add_argument("--revision", help="прочитанная ревизия настроек для записи")
    for parser in (listing, show, context):
        parser.add_argument("--limit", type=brief.result_limit, default=10, help="число записей на странице при чтении")
        parser.add_argument("--offset", type=brief.offset, default=0, help="сколько записей пропустить при чтении")
    for parser in (person, team, listing, show, rename, member, context):
        parser.set_defaults(teams=True)


def run(root, args):
    from .teams import Teams

    service = Teams(root)
    if args.command == "team-list":
        if args.person is not None:
            page = service.for_person(args.person, match=args.match, limit=args.limit, offset=args.offset)
        else:
            page = service.list(match=args.match, limit=args.limit, offset=args.offset)
        return {**page, "items": [brief.named_card(team) for team in page["items"]]}
    if args.command == "team-show":
        team = service.show(args.id, limit=args.limit, offset=args.offset)
        return {**brief.named_card(team), "members": [brief.named_card(member) for member in team["members"]]}
    if args.command == "owner-context":
        changes = {key: vars(args)[key] for key in ("owner", "team") if key in vars(args)}
        if changes:
            if args.revision is None:
                raise ValueError("Для изменения укажите --revision из owner-context.")
            result = service.set_context(revision=args.revision, **changes)
            receipt = {key: result[key] for key in ("owner", "team", "ownerLine", "teamLine", "path", "revision", "total")}
            return brief.owner_context(receipt)
        if args.revision is not None:
            raise ValueError("С --revision укажите выбор владельца, команды или снятие выбора.")
        return brief.owner_context(service.context(limit=args.limit, offset=args.offset))
    if args.command == "person-create":
        result = service.create_person(args.name, request_id=args.request_id)
    elif args.command == "team-create":
        result = service.create(args.name, request_id=args.request_id)
    elif args.command == "team-rename":
        result = service.rename(args.id, args.name, args.revision)
    else:
        result = service.member(args.id, args.person, present=args.present, revision=args.revision)
    return brief.named_card({key: result[key] for key in ("id", "name", "path", "revision", "total") if key in result})
