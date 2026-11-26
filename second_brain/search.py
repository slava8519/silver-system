"""Search the current Markdown files and return citations relative to the vault."""

import hashlib
import os
import re
import sqlite3
import tempfile
import unicodedata
from contextlib import closing, contextmanager
from itertools import chain
from pathlib import Path
from typing import Optional

from .storage import KINDS, iter_markdown_paths, iter_records


# Bump this when the on-disk index layout or indexed text changes.
_INDEX_VERSION = b"second-brain-search-3\0"
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


def _signature(root):
    from .people import People
    digest = hashlib.sha256(_INDEX_VERSION)
    for path in chain(iter_markdown_paths(root), People(root).paths()):
        info = path.stat()
        digest.update(os.fsencode(path.relative_to(root)))
        digest.update(f"\0{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}\0".encode())
    return digest.hexdigest()


def _database(root):
    if not root.is_dir():
        raise FileNotFoundError(f"Хранилище не найдено: {root}")
    cache = root / ".second-brain"
    if cache.is_symlink():
        raise ValueError("Каталог поискового индекса содержит символическую ссылку.")
    cache.mkdir(exist_ok=True)
    database = cache / "search.sqlite3"
    if database.is_symlink():
        raise ValueError("Файл поискового индекса содержит символическую ссылку.")
    return database


def _read_index(database):
    # Published indexes are immutable. A writer replaces the complete file.
    return closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True))


def visible_body_lines(record, hidden=None):
    """Keep file coordinates while excluding the project log's internal bookkeeping."""
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


def _build(root, database, signature):
    from .people import People
    from .project_updates import projection
    descriptor, temporary = tempfile.mkstemp(dir=database.parent, suffix=".sqlite3")
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(temporary)) as db, db:
            db.execute("CREATE TABLE metadata (signature TEXT NOT NULL)")
            db.execute("""CREATE VIRTUAL TABLE fragments USING fts5(
                path UNINDEXED, id UNINDEXED, title UNINDEXED, line UNINDEXED, text UNINDEXED,
                title_terms, alias_terms, body_terms, brief_names UNINDEXED, tokenize='unicode61'
            )""")
            for path, record in iter_records(root):
                relative = path.relative_to(root).as_posix()
                title = record["title"]
                aliases = _normalize(" ".join(record["aliases"]))
                entries, hidden = projection(record) if record["type"] == "project" else ([], set())
                fields = {entry["line"]: entry for entry in entries}
                names = "\x1f" + "\x1f".join(" ".join(word_stems(name)) for name in [title, *record["aliases"]]) + "\x1f"
                for number, line in _source_lines(path, record, hidden):
                    entry = fields.get(number, {})
                    is_brief = entry.get("field") == "brief"
                    terms = _normalize(line)
                    if is_brief:
                        terms += " коротко сводка"
                    elif entry:
                        terms += " событие история " + (entry.get("date") or "")
                    db.execute("INSERT INTO fragments VALUES (?,?,?,?,?,?,?,?,?)", (
                        relative, record["id"], title, number, line, _normalize(title), aliases, terms,
                        names if is_brief else ""))
            for fact in People(root).shared_facts():
                db.execute("INSERT INTO fragments VALUES (?,?,?,?,?,?,?,?,?)", (
                    fact["path"], fact["personId"], fact["personName"], fact["cardLine"], fact["text"],
                    _normalize(fact["personName"]), _normalize(" ".join(fact.get("aliases", []))),
                    _normalize(fact["text"]), ""))
            db.execute("INSERT INTO metadata VALUES (?)", (signature,))
        os.replace(temporary, database)
    finally:
        Path(temporary).unlink(missing_ok=True)


def rebuild(root: Path):
    root = Path(root).resolve()
    database = _database(root)
    _build(root, database, _signature(root))


@contextmanager
def _current_index(root):
    database = _database(root)
    signature = _signature(root)
    if database.is_file():
        with _read_index(database) as db:
            try:
                current = db.execute("SELECT signature FROM metadata").fetchone() == (signature,)
            except sqlite3.DatabaseError:
                current = False
            if current:
                yield db, False
                return
    _build(root, database, signature)
    with _read_index(database) as db:
        yield db, True


def refresh_index(root: Path):
    """Bring the shared search index up to date; return whether it was rebuilt."""
    with _current_index(Path(root).resolve()) as (_, rebuilt):
        return rebuilt


def _results(db, expression, limit, record_id, name):
    db.row_factory = sqlite3.Row
    rows = db.execute("""
        WITH candidates AS (
            SELECT path, id, title, line, text, rank AS score, instr(brief_names, ?) > 0 AS exact_brief,
                   row_number() OVER (PARTITION BY path ORDER BY instr(brief_names, ?) > 0 DESC, rank, line) AS position
            FROM fragments
            WHERE fragments MATCH ?
              AND (? IS NULL OR id = ?)
              AND rank MATCH 'bm25(0,0,0,0,0,4,7,1)'
        )
        SELECT path, id, title, line AS start, line AS end, text
        FROM candidates WHERE position = 1
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
        with _current_index(root) as (db, _):
            return _results(db, expression, limit, record_id, name)
    except sqlite3.DatabaseError:
        rebuild(root)  # Metadata can survive damage to the full-text table.
    with _read_index(_database(root)) as db:
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
    lines = current.read_text(encoding="utf-8").splitlines()
    if end > len(lines):
        raise ValueError(f"В файле только {len(lines)} строк.")
    return "\n".join(lines[start - 1:end])
