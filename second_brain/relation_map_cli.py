"""Command-line navigation through the same direct links as the workspace map."""

from . import brief


def add_commands(commands):
    catalog = commands.add_parser("map", help="открыть карту направлений и связей карточек")
    actions = catalog.add_subparsers(dest="map_action")
    search = actions.add_parser("search", help="найти карточку по названию или другому имени")
    search.add_argument("query", metavar="ТЕКСТ")
    focus = actions.add_parser("focus", help="показать группы прямых связей карточки")
    focus.add_argument("id", metavar="ID")
    group = actions.add_parser("group", help="показать страницу выбранной группы связей")
    group.add_argument("group", metavar="ГРУППА", help="ключ группы из ответа карты, unassigned или broken")
    group.add_argument("--id", metavar="ID", help="карточка, для которой открыта группа")
    group.add_argument("--match", dest="query", metavar="ТЕКСТ", default="", help="искать внутри группы")
    source = actions.add_parser("source", help="проверить строку основания из ответа карты")
    source.add_argument("id", metavar="ID", help="ID из поля source")
    source.add_argument("line", metavar="СТРОКА", type=int)
    source.add_argument("--revision", metavar="РЕВИЗИЯ", help="проверить версию источника из ответа карты")
    for command in (catalog, search, group):
        command.add_argument("--limit", type=brief.result_limit, default=brief.DEFAULT_LIMIT)
        command.add_argument("--offset", type=brief.offset, default=0)


def run(root, args):
    from .relation_map import RelationMap

    service = RelationMap(root)
    if args.map_action == "source":
        return service.read_source(args.id, args.line, args.revision)
    return service.query(action=args.map_action or "home", id=getattr(args, "id", None),
                         query=getattr(args, "query", ""), group=getattr(args, "group", None),
                         limit=args.limit, offset=args.offset)
