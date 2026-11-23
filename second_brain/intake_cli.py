"""Command-line actions for the Inbox, direction rules and people."""

import json
from pathlib import Path
import sys

from . import brief


def add_commands(commands):
    capture_text = commands.add_parser("inbox-text", help="сохранить текст из stdin во входящие")
    capture_text.add_argument("--title", help="название заметки")
    capture_file = commands.add_parser("inbox-file", help="скопировать локальный файл во входящие")
    capture_file.add_argument("file", type=Path, help="путь к файлу")
    inbox = commands.add_parser("inbox", help="перечислить тексты и вложения во входящих")
    _page_options(inbox)
    preview = commands.add_parser("inbox-read", help="прочитать строки одного входящего и его ревизию")
    preview.add_argument("path")
    preview.add_argument("--start", type=int, default=1)
    preview.add_argument("--limit", type=int, default=20)
    preview.add_argument("--column", type=int, default=1, help="начальный символ длинной строки")
    original = commands.add_parser("original", help="открыть сохранённый оригинал в контексте разбора")
    original.add_argument("source_id")
    original.add_argument("--start", type=int, default=1)
    original.add_argument("--limit", type=int, default=20)
    original.add_argument("--column", type=int, default=1)
    process = commands.add_parser("inbox-process", help="сохранить оригинал и подтверждённые строки встречи")
    process.add_argument("path")
    process.add_argument("--revision", required=True)
    participant = process.add_mutually_exclusive_group(required=True)
    participant.add_argument("--person", metavar="ID")
    participant.add_argument("--new-person", metavar="ИМЯ")
    process.add_argument("--person-key", help="различить тёзок, создаваемых из одного источника")
    process.add_argument("--person-alias", action="append", help="дополнительное имя нового человека")
    process.add_argument("--fact", action="append", required=True, nargs=5,
                         metavar=("ВИД", "НАПРАВЛЕНИЕ", "ДОСТУП", "СТРОКА", "ЦИТАТА"),
                         help="role/fact/observation, ID направления, shared/personal, номер и выдержка")
    people = commands.add_parser("people", help="найти людей по имени, алиасу или направлению")
    people.add_argument("--match", default="")
    people.add_argument("--direction")
    _page_options(people)
    person = commands.add_parser("person", help="получить роли и факты выбранного человека")
    person.add_argument("id")
    person.add_argument("--direction")
    person.add_argument("--personal", action="store_true", help="включить личные сведения этого человека")
    person.add_argument("--history", action="store_true", help="включить сохранённые формулировки")
    _page_options(person)
    correct = commands.add_parser("person-correct", help="уточнить один факт с сохранением источника")
    correct.add_argument("id")
    correct.add_argument("fact_id")
    correct.add_argument("--revision", required=True)
    correct.add_argument("--text", required=True)
    correct.add_argument("--personal", action="store_true")
    evidence = commands.add_parser("evidence", help="сверить цитату одного факта с оригиналом")
    evidence.add_argument("id", help="ID человека")
    evidence.add_argument("fact_id")
    evidence.add_argument("--personal", action="store_true")
    pending = commands.add_parser("inbox-new", help="найти новые источники и уточнения владельца")
    rules = commands.add_parser("inbox-rules", help="прочитать правила разбора по направлениям")
    rules.add_argument("--direction", help="ID одного направления")
    triage = commands.add_parser("inbox-triage", help="показать вопросы владельцу со строками источников")
    for parser in (pending, rules, triage):
        _page_options(parser)
    apply = commands.add_parser("inbox-apply", help="применить разбор одного источника из JSON на stdin")
    resolve = commands.add_parser("inbox-resolve", help="вернуть один вопрос в работу с ответом владельца")
    resolve.add_argument("id")
    resolve.add_argument("clarification")
    rule = commands.add_parser("inbox-rule", help="сохранить правило направления из JSON на stdin")
    for parser in (capture_text, capture_file, inbox, preview, original, process, people, person, correct, evidence,
                   pending, rules, triage, apply, resolve, rule):
        parser.set_defaults(intake=True)


def _page_options(parser):
    parser.add_argument("--limit", type=brief.result_limit, default=10)
    parser.add_argument("--offset", type=brief.offset, default=0)


def _excerpt(page, column):
    if column < 1:
        raise ValueError("Нумерация символов начинается с 1.")
    offset = column - 1
    for line in page["lines"]:
        text = line["text"]
        line["text"] = text[offset:offset + brief.EXCERPT_LENGTH]
        if offset:
            line["column"] = column
        if len(text) > offset + brief.EXCERPT_LENGTH:
            line["nextColumn"] = column + brief.EXCERPT_LENGTH
    return page


def run(root, args):
    from .inbox import Inbox
    from .people import People

    if args.command == "inbox-text":
        from .capture import Capture, MAX_TEXT_BYTES
        raw = sys.stdin.buffer.read(MAX_TEXT_BYTES + 1)
        if len(raw) > MAX_TEXT_BYTES:
            raise ValueError("Максимальный размер текста — 1 МиБ.")
        return Capture(root).save_text(raw.decode("utf-8"), args.title)
    if args.command == "inbox-file":
        from .capture import Capture, MAX_FILE_BYTES
        path = args.file.expanduser()
        if not path.is_file():
            raise ValueError("Укажите путь к обычному файлу.")
        with path.open("rb") as stream:
            return Capture(root).save_file(path.name, stream.read(MAX_FILE_BYTES + 1))
    if args.command in {"inbox-new", "inbox-rules", "inbox-rule", "inbox-triage", "inbox-apply", "inbox-resolve"}:
        from .batch import Batch
        from .routing import Routing

        if args.command == "inbox-rules":
            rules = Routing(root).rules()
            return brief.page((r for r in rules if not args.direction or r["direction"] == args.direction),
                              args.limit, args.offset)
        if args.command in {"inbox-rule", "inbox-apply"}:
            data = json.load(sys.stdin)
            if not isinstance(data, dict):
                raise ValueError("Ожидался объект JSON на stdin.")
            if args.command == "inbox-rule":
                return Routing(root).save_rule(data)
            return Batch(root).apply(data)
        batch = Batch(root)
        if args.command == "inbox-resolve":
            return batch.resolve(args.id, args.clarification)
        rows = batch.pending() if args.command == "inbox-new" else batch.triage()
        return brief.page(rows, args.limit, args.offset)
    if args.command == "inbox":
        return brief.page(Inbox(root).list_items(), args.limit, args.offset)
    if args.command == "inbox-read":
        return _excerpt(Inbox(root).preview(args.path, args.start, args.limit), args.column)
    if args.command == "original":
        return _excerpt(Inbox(root).original(args.source_id, args.start, args.limit), args.column)
    if args.command == "inbox-process":
        person = {"id": args.person} if args.person else {"name": args.new_person, "new": True}
        if args.person_key or args.person_alias:
            if not args.new_person:
                raise ValueError("Ключ и дополнительные имена применяются при создании человека.")
            if args.person_key:
                person["key"] = args.person_key
            if args.person_alias:
                person["aliases"] = args.person_alias
        facts = [{"kind": kind, "direction": direction, "visibility": visibility, "line": int(line), "text": quote}
                 for kind, direction, visibility, line, quote in args.fact]
        return Inbox(root).process({"path": args.path, "revision": args.revision, "person": person, "facts": facts})
    if args.command == "people":
        return People(root).candidate_page(args.match, limit=args.limit,
                                           direction_id=args.direction, offset=args.offset)
    if args.command == "evidence":
        return Inbox(root).fact_evidence(args.id, args.fact_id, args.personal)
    if args.command == "person-correct":
        updated = People(root).correct({"id": args.id, "factId": args.fact_id, "revision": args.revision,
                                        "text": args.text, "personal": args.personal})
        return {key: updated[key] for key in ("id", "name", "path", "revision")}
    card = People(root).detail(args.id, personal=args.personal)
    facts = (f for f in card["facts"] if not args.direction or f["direction"] == args.direction)
    page = brief.page(facts, args.limit, args.offset)
    displayed = []
    for fact in page["items"]:
        item = dict(fact)
        if not args.history:
            item.pop("history", None)
        brief.clip_fields(item, ("text", "quote"))
        displayed.append(item)
    return {**card, "facts": displayed, "total": page["total"], "truncated": page["truncated"]}
