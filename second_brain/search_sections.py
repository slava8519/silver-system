"""Classify citable Markdown lines without changing their positions in the file."""

import json
import re

from .markdown_sections import fenced_lines, headings


_ARCHIVE_LINK = re.compile(r"^\[(?:Открыть запись в архиве|[0-9]{4}-Q[1-4])\]\([^)]*/Archive/ProjectHistory/")
_HISTORY = re.compile(
    r"(?:история(?:\s+(?:статус\w*|изменени\w*|верси\w*|свод\w*|событи\w*|решени\w*|закрытых)\b.*)?"
    r"|журнал(?:\s+(?:изменений|событий|статусов|решений))?"
    r"|архив(?:\s+(?:истории|статус\w*|свод\w*|запис\w*|событи\w*|\d{4})\b.*)?"
    r"|(?:предыдущ\w*|прежн\w*|прошл\w*|архивн\w*)\s+(?:статус\w*|свод\w*|состоян\w*|сним\w*|верси\w*)\b.*"
    r"|сним(?:ок|ки)\s+(?:рабочего\s+)?(?:состояни\w*|статус\w*)\b.*"
    r"|history|(?:status|change|revision|event)\s+(?:history|log)|changelog|journal"
    r"|(?:previous|past|archived)\s+(?:status(?:es)?|states?|summaries|snapshots?)\b.*"
    r"|(?:archive|archives|snapshots?)(?:\s+\d{4}\b.*)?)", re.I)


def lines(record, *, hidden=None):
    """Yield source number, searchable text and whether the line belongs to history."""
    if record["type"] == "project_history":
        from .archive_reader import visible_lines
        for number, text in visible_lines(record).items():
            yield number, text, True
        return
    from . import project_updates
    if hidden is None:
        hidden = project_updates.projection(record)[1] if record["type"] == "project" else ()
    body = record["body"].splitlines()
    code, _ = fenced_lines(body)
    section_heads = headings(body)
    history_level = 0
    summary_log = False
    for i, text in enumerate(body):
        number = record["body_start"] + i
        if i not in code and (text.startswith("<!-- brain-history-archive: ") or _ARCHIVE_LINK.match(text)):
            continue
        heading = section_heads.get(i)
        if heading:
            level, title, _ = heading
            if not history_level or level <= history_level:
                summary_log = record["type"] == "project" and text == project_updates._HISTORY_TITLE
                journal = record["type"] == "project" and title.casefold() == "последнее"
                history_level = level if journal or _HISTORY.fullmatch(title) else 0
            if record["type"] == "project" and title.casefold() == "архив истории":
                continue
        if number not in hidden:
            yield number, text, bool(history_level)
        elif summary_log and text.startswith(project_updates._HISTORY) and text.endswith(" -->"):
            try:
                change = json.loads(text[len(project_updates._HISTORY):-4])
                previous = change["before"]
                if isinstance(previous, dict) and isinstance(previous.get("text"), str) and previous["text"]:
                    yield number, previous["text"], True
            except (ValueError, KeyError, TypeError):
                continue
