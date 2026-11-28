"""CLI for authored reports, recurring series and their versioned templates."""

import json
from pathlib import Path
import sys

from . import brief


def add_commands(commands):
    def command(name, help_text):
        parser = commands.add_parser(name, help=help_text)
        parser.set_defaults(reports=True)
        return parser

    issues = command("report-list", "показать выпуски отчётов")
    templates = command("report-template-list", "показать доступные шаблоны отчётов")
    schedules = command("report-series-list", "показать серии и очередные периоды")
    issues.add_argument("--series", metavar="ID", help="выпуски одной серии")
    issues.add_argument("--match", metavar="ТЕКСТ", help="найти выпуск по фрагменту названия")
    issues.add_argument("--status", choices=("draft", "final", "posted"), help="выбрать выпуски по статусу")
    templates.add_argument("--origin", choices=("builtin", "custom"), help="коробочные или пользовательские шаблоны")
    templates.add_argument("--match", metavar="ТЕКСТ", help="найти шаблон по фрагменту названия или адресата")
    schedules.add_argument("--due-on", metavar="YYYY-MM-DD",
                           help="серии, очередной период которых завершится к этой дате включительно")
    for parser in (issues, templates, schedules):
        parser.add_argument("--limit", type=brief.result_limit, default=brief.DEFAULT_LIMIT)
        parser.add_argument("--offset", type=brief.offset, default=0)

    show = command("report-show", "прочитать сохранённый выпуск и его источники")
    finalize = command("report-finalize", "утвердить прочитанную ревизию черновика")
    finalize.add_argument("--revision", required=True, help="ревизия черновика из report-show")
    source = command("report-source", "сверить одно основание выпуска со строкой Markdown")
    template = command("report-template-show", "прочитать текущую или указанную версию шаблона")
    series = command("report-series-show", "прочитать правила серии и даты очередного выпуска")
    for parser in (show, finalize, source, template, series):
        parser.add_argument("id", help="ID из списка")
    source.add_argument("--index", type=brief.offset, required=True, help="индекс основания из sources, начиная с 0")
    template.add_argument("--version", type=int, help="номер опубликованной версии")
    show.add_argument("--brief", action="store_true", help="показать только свойства и число источников")

    new_issue = command("report-save", "сохранить разовый выпуск или выпуск серии")
    new_template = command("report-template-save", "создать шаблон или сохранить новую версию")
    new_series = command("report-series-create", "задать ритм серии и начало первого периода")
    for parser in (new_issue, new_template, new_series):
        parser.add_argument("title", help="название")
        parser.add_argument("--request-id", help="устойчивый ключ для повтора операции")
    for parser in (new_issue, new_template):
        body = parser.add_mutually_exclusive_group(required=True)
        body.add_argument("--body-file", metavar="ПУТЬ", help="файл Markdown в UTF-8 или - для stdin")
        body.add_argument("--body", help="текст Markdown")
    new_template.add_argument("--audience", required=True, help="кому адресован выпуск")
    new_template.add_argument("--guidance", required=True, help="правила отбора и подачи материала")
    new_template.add_argument("--id", help="ID шаблона при создании следующей версии")
    new_template.add_argument("--revision", help="текущая ревизия изменяемого шаблона")
    for parser in (new_issue, new_series):
        parser.add_argument("--template", required=True, metavar="ID", help="ID шаблона")
    new_series.add_argument("--cadence", choices=("daily", "weekly", "biweekly", "monthly", "quarterly", "yearly"), required=True)
    new_series.add_argument("--start", required=True, help="первый день начального периода, YYYY-MM-DD")
    new_issue.add_argument("--template-version", type=int, required=True, help="использованная версия шаблона")
    new_issue.add_argument("--draft", action="store_true", help="сохранить черновик для последующей финализации")
    new_issue.add_argument("--template-revision", help="отпечаток прочитанной версии шаблона")
    new_issue.add_argument("--series", metavar="ID", help="ID серии периодических выпусков")
    new_issue.add_argument("--start", help="первый день периода включительно, YYYY-MM-DD")
    new_issue.add_argument("--end", help="последний день периода включительно, YYYY-MM-DD")
    new_issue.add_argument("--as-of", help="дата актуальности сведений, YYYY-MM-DD")
    sources = new_issue.add_mutually_exclusive_group()
    sources.add_argument("--source", action="append", nargs=3, metavar=("ПУТЬ", "СТРОКА", "ЦИТАТА"),
                         help="относительный путь в хранилище, номер строки с 1, цитата (можно повторять)")
    sources.add_argument("--sources-file", metavar="ПУТЬ", help="массив JSON с path, line, quote и необязательным claim")


def _text_file(path):
    return sys.stdin.read() if path == "-" else Path(path).expanduser().read_text(encoding="utf-8")


def _receipt(result):
    summary = {k: v for k, v in result.items() if k not in ("body", "guidance", "history", "sources")}
    if "sources" in result:
        summary["sourceCount"] = len(result["sources"])
    return summary


def run(root, args):
    from . import reports

    if args.command == "report-list":
        return reports.list_issues(root, series_id=args.series, match=args.match, status=args.status,
                                   limit=args.limit, offset=args.offset)
    if args.command == "report-template-list":
        return reports.list_templates(root, origin=args.origin, match=args.match, limit=args.limit, offset=args.offset)
    if args.command == "report-series-list":
        return reports.list_series(root, due_on=args.due_on, limit=args.limit, offset=args.offset)
    if args.command == "report-show":
        result = reports.get_issue(root, args.id)
        return _receipt(result) if args.brief else result
    if args.command == "report-finalize":
        return reports.finalize_issue(root, args.id, revision=args.revision)
    if args.command == "report-source":
        return reports.read_issue_source(root, args.id, args.index)
    if args.command == "report-template-show":
        return reports.get_template(root, args.id, version=args.version)
    if args.command == "report-series-show":
        return reports.get_series(root, args.id)
    if args.command == "report-series-create":
        return reports.create_series(root, title=args.title, template_id=args.template, cadence=args.cadence,
                                     start=args.start, request_id=args.request_id)
    if args.body_file == "-" and getattr(args, "sources_file", None) == "-":
        raise ValueError("Передайте через stdin либо текст отчёта, либо JSON источников. Для другого укажите файл.")
    body = _text_file(args.body_file) if args.body_file is not None else args.body
    if args.command == "report-template-save":
        result = reports.save_template(root, title=args.title, id=args.id, revision=args.revision, audience=args.audience,
                                       guidance=args.guidance, body=body, request_id=args.request_id)
    else:
        if args.sources_file is not None:
            sources = json.loads(_text_file(args.sources_file))
            if not isinstance(sources, list):
                raise ValueError("В файле источников ожидается массив JSON.")
        else:
            sources = [{"path": path, "line": int(line), "quote": quote} for path, line, quote in args.source or []]
        result = reports.save_issue(root, title=args.title, template_id=args.template, template_version=args.template_version,
                                    template_revision=args.template_revision, body=body, sources=sources,
                                    series_id=args.series, start=args.start, end=args.end,
                                    as_of=args.as_of, request_id=args.request_id, draft=args.draft)
    return _receipt(result)
