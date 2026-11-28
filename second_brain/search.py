"""Search the current Markdown files and return citations relative to the vault."""

import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Optional

from . import search_index
from .search_index import rebuild
from .storage import KINDS


_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_STOP_WORDS = set("""
    а без бы был была было были в во где для до его ее за и из или к как какой
    какая какие какое каких какому на над о об от по под при с со у чем чему
    через что этот эта это the a an is at by for of on to what where which
""".split())

# Prefix queries cover common Russian case endings without a dictionary.
_ENDINGS = (
    "иями", "ями", "ами", "ого", "его", "ыми", "ими", "ому", "ему",
    "лась", "лось", "лся", "лись",
    "ом", "ем", "ой", "ей", "ым", "им", "ах", "ях", "ам", "ям", "ии", "ия",
    "ую", "юю", "ая", "яя", "ые", "ие", "ое", "ее", "ый", "ий",
    "а", "я", "у", "ю", "е", "ы", "и", "ь",
)


def _normalize(text):
    return unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")


def word_stems(text):
    """Yield normalized search terms in order, keeping phrase boundaries comparable."""
    for word in _WORD.findall(_normalize(text)):
        if word in _STOP_WORDS:
            continue
        for ending in _ENDINGS:
            if word.endswith(ending) and len(word) - len(ending) >= 3:
                word = word[:-len(ending)]
                break
        yield word


def _match_query(query):
    if len(query) > 1000:
        raise ValueError("Сократите запрос до 1000 символов.")
    terms = dict.fromkeys(word_stems(query))
    # Only alphanumeric tokens enter MATCH. Its operators are ordinary text here.
    return " OR ".join(f'"{term}"*' for term in terms)


def visible_body_lines(record, hidden=None):
    """Keep file coordinates while excluding the project log's internal bookkeeping."""
    if "candidate_lines" in record:
        return iter(record["candidate_lines"])
    if hidden is None and record["type"] == "project":
        from .project_updates import projection
        _, hidden = projection(record)
    lines = enumerate(record["body"].splitlines(), record["body_start"])
    return ((n, line) for n, line in lines if n not in hidden) if hidden else lines


def _source_lines(path, record, hidden):
    found = False
    for number, line in visible_body_lines(record, hidden):
        if line.strip() and line.strip() != "# " + record["title"]:
            found = True
            yield number, line
    if not found:
        # Empty cards still have a citable title in their Markdown source.
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("title:") or line.strip() == "# " + record["title"]:
                yield number, line
                return


def refresh_index(root: Path):
    """Bring the shared search index up to date; return whether it was rebuilt."""
    with search_index.current(Path(root).resolve()) as (_, rebuilt):
        return rebuilt


def _results(db, expression, limit, record_id, name):
    db.row_factory = sqlite3.Row
    rows = db.execute("""
        WITH candidates AS (
            SELECT d.path, d.id, d.title, l.line, l.text, rank AS score,
                   l.brief AND instr(d.brief_names, ?) > 0 AS exact_brief,
                   row_number() OVER (PARTITION BY d.key ORDER BY
                       (l.brief AND instr(d.brief_names, ?) > 0) DESC, rank, l.line) AS position
            FROM fragments JOIN lines l ON l.key=fragments.rowid JOIN documents d ON d.key=l.document
            WHERE fragments MATCH ? AND (? IS NULL OR d.id = ?) AND rank MATCH 'bm25(4,7,1)'
        )
        SELECT path, id, title, line AS start, line AS end, text FROM candidates WHERE position = 1
        ORDER BY exact_brief DESC, score, path LIMIT ?
    """, (name, name, expression, record_id, record_id, limit))
    return [dict(row) for row in rows]


def search(root: Path, query: str, limit: int = 5, *, record_id: Optional[str] = None) -> list[dict]:
    """Return the best matching source line per file. The consumer interprets it."""
    if not 1 <= limit <= 20:
        raise ValueError("Количество результатов должно быть от 1 до 20.")
    expression = _match_query(query)
    if not expression:
        return []
    root = Path(root).resolve()
    name = "\x1f" + " ".join(word_stems(query)) + "\x1f"
    try:
        with search_index.current(root) as (db, _):
            return _results(db, expression, limit, record_id, name)
    except sqlite3.DatabaseError:
        rebuild(root)  # Metadata can survive damage to the full-text table.
    with search_index.current(root) as (db, _):
        return _results(db, expression, limit, record_id, name)


def fragment(root: Path, path: str, start: int, end: int) -> str:
    """Read a note range or one explicitly scoped person, team or profile citation."""
    relative = Path(path)
    if relative.as_posix() == "Settings/Owner.md" or relative.parts[:1] == ("People",):
        from .teams import Teams
        if ".." in relative.parts:
            raise ValueError("Нужен относительный путь к Markdown-файлу в области поиска.")
        link = Teams(root).link_fragment(relative, start, end)
        if link is not None:
            return link
        from .people import People
        if start == end and start > 0:
            for fact in People(root).shared_facts():
                if relative.as_posix() == fact["path"] and start == fact["cardLine"]:
                    return fact["text"]
        raise ValueError("Укажите одну подтверждённую общую строку карточки человека.")
    if (relative.is_absolute() or ".." in relative.parts or not relative.parts
            or relative.parts[0] not in KINDS.values() or relative.suffix.lower() != ".md"):
        raise ValueError("Нужен относительный путь к Markdown-файлу в области поиска.")
    current = Path(root).resolve()
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Путь к фрагменту содержит символическую ссылку.")
    if start < 1 or end < start or end - start + 1 > 80:
        raise ValueError("Укажите диапазон от 1 до 80 строк, начиная с первой или позднее.")
    excerpt = []
    number = 0
    with current.open(encoding="utf-8") as stream:
        for raw in stream:
            # Match the same Unicode line boundaries used by the search index.
            for line in raw.splitlines():
                number += 1
                if number >= start:
                    excerpt.append(line)
                if number == end:
                    return "\n".join(excerpt)
    raise ValueError(f"В файле только {number} строк.")
