"""Read and write the source-backed summary and event log of a project."""

import json
from pathlib import Path

from . import brief, project_updates


def add_commands(commands):
    show = commands.add_parser("project-updates", help="показать сводку проекта и страницу событий")
    show.add_argument("id")
    show.add_argument("--limit", type=brief.result_limit, default=brief.DEFAULT_LIMIT)
    show.add_argument("--offset", type=brief.offset, default=0)
    archive = commands.add_parser("project-history-archive", help="проверить и архивировать историю проекта по кварталам")
    archive.add_argument("id")
    archive.add_argument("--as-of", metavar="YYYY-MM-DD", help="дата отсчёта возраста записей")
    archive.add_argument("--reviewed-links", type=Path, metavar="JSON",
                         help="проверенные адреса и граница источника для указанной ревизии карточки")
    archive.add_argument("--apply", metavar="РЕВИЗИЯ", help="применить проверенный предпросмотр")
    edit = commands.add_parser("project-update", help="сохранить сводку проекта или отдельное событие с основанием")
    edit.add_argument("id")
    edit.add_argument("--revision", required=True)
    edit.add_argument("--field", choices=("brief", "event"), required=True)
    edit.add_argument("--text", required=True)
    edit.add_argument("--date")
    edit.add_argument("--source", required=True)
    edit.add_argument("--line", type=int, required=True)
    edit.add_argument("--quote", required=True)
    edit.add_argument("--accept-manual", metavar="РЕВИЗИЯ", help="сохранить формулировку, принятую владельцем для этой версии")


def run(root, args):
    if args.command == "project-history-archive":
        from .history_archive import rotate
        review = json.loads(args.reviewed_links.read_text(encoding="utf-8")) if args.reviewed_links else None
        return rotate(root, args.id, as_of=args.as_of, apply=args.apply, reviewed_links=review)
    if args.command == "project-updates":
        result = project_updates.read_updates(root, args.id, args.limit, args.offset)
    else:
        source = {"path": args.source, "line": args.line, "quote": args.quote}
        result = project_updates.write_update(root, args.id, field=args.field, revision=args.revision,
                                              text=args.text, date=args.date, sources=[source],
                                              accepted_manual_revision=args.accept_manual)
    for row in [result["brief"], *result["events"]]:
        if row:
            brief.clip_fields(row, ("text",))
    return result
