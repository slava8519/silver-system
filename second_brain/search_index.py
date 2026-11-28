"""Disposable SQLite catalogue and line index derived from the searchable Markdown files."""

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from contextlib import ExitStack, closing, contextmanager
from itertools import chain
from pathlib import Path

from . import storage
from .people import People, _parse_facts


_VERSION = 6
_NON_ASCII_BREAK = re.compile(r"[^\w\s\x00-\x7f](?=[^\W_])")
_SCHEMA = """
CREATE TABLE metadata(signature TEXT NOT NULL);
CREATE TABLE documents(
    key INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, stamp TEXT NOT NULL,
    id TEXT, type TEXT, title TEXT, aliases TEXT, alias_terms TEXT, direction TEXT,
    revision TEXT, brief_names TEXT);
CREATE INDEX documents_id ON documents(id);
CREATE TABLE lines(
    key INTEGER PRIMARY KEY, document INTEGER NOT NULL, line INTEGER NOT NULL, text TEXT NOT NULL,
    terms_suffix TEXT NOT NULL, brief INTEGER NOT NULL, fact_id TEXT, kind TEXT, direction TEXT);
CREATE INDEX lines_document ON lines(document);
CREATE INDEX lines_unicode ON lines(key) WHERE kind='unicode-break';
CREATE VIEW fragment_terms AS
    SELECT l.key AS rowid, fold(d.title) AS title_terms, d.alias_terms,
           fold(l.text) || l.terms_suffix AS body_terms
    FROM lines l JOIN documents d ON d.key=l.document;
CREATE VIRTUAL TABLE fragments USING fts5(
    title_terms, alias_terms, body_terms, content='fragment_terms', content_rowid='rowid', tokenize='unicode61');
"""


def _stamp(path):
    info = path.stat()
    return f"{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}:{info.st_ino}"


def _manifest(root):
    digest = hashlib.sha256()
    files = {}
    for path in chain(storage.iter_markdown_paths(root), People(root).paths()):
        relative = path.relative_to(root).as_posix()
        stamp = _stamp(path)
        files[relative] = stamp
        digest.update(os.fsencode(relative) + b"\0" + stamp.encode() + b"\0")
    return files, digest.hexdigest()


def _database(root):
    if not root.is_dir():
        raise FileNotFoundError(f"Хранилище не найдено: {root}")
    return storage._inside(root, Path(".second-brain") / "search.sqlite3")


def _connect(path, *, read_only=False):
    from .search import _normalize
    db = sqlite3.connect(path.as_uri() + ("?mode=ro" if read_only else ""), uri=True, timeout=15)
    db.create_function("fold", 1, _normalize)
    db.row_factory = sqlite3.Row
    return closing(db)


def _signature(db):
    if db.execute("PRAGMA user_version").fetchone()[0] != _VERSION:
        return None
    # A partially lost database can retain metadata and its matching signature.
    db.execute("""SELECT documents.key,lines.key,fragments.rowid FROM documents,lines,fragments LIMIT 0""")
    row = db.execute("SELECT signature FROM metadata").fetchone()
    return row[0] if row else None


def _lines(path, record):
    from .search import _normalize, _source_lines
    from .project_updates import projection
    if record["type"] == "person":
        for fact in _parse_facts(record).values():
            if fact["visibility"] == "shared":
                yield (fact["cardLine"], fact["text"], "", False, fact["id"], fact["kind"], fact["direction"])
        return
    projected, hidden = projection(record) if record["type"] == "project" else ([], set())
    fields = {entry["line"]: entry for entry in projected}
    for number, line in _source_lines(path, record, hidden):
        entry = fields.get(number)
        brief = bool(entry and entry["field"] == "brief")
        suffix = ""
        if brief:
            suffix = " коротко сводка"
        elif entry:
            suffix = " событие история " + (entry.get("date") or "")
        kind = None
        if number < record["body_start"]:
            kind = "title"
        elif not line.isascii() and any(unicodedata.category(m[0]) in ("Mn", "Mc", "Me", "Co", "Cn")
                                        for m in _NON_ASCII_BREAK.finditer(_normalize(line))):
            # Python can split at marks/private characters that SQLite unicode61
            # keeps inside a token, e.g. casefolded capital İ. Verify these rare
            # lines in ask even when FTS reports no hit. Lexical ranking stays intact.
            kind = "unicode-break"
        yield number, line, suffix, brief, None, kind, None


def _index_file(db, root, relative, stamp):
    from .search import _normalize, word_stems
    path = storage._inside(root, relative)
    if Path(relative).parts[0] == "People":
        record = next((r for _, _, r in People(root)._cards((path,))), None)
    else:
        try:
            record = storage.read_record(path)
        except storage.UnmanagedMarkdown:
            record = None
    row = db.execute("INSERT INTO documents(path,stamp) VALUES (?,?)", (relative, stamp))
    if record is not None:
        key = row.lastrowid
        aliases = record["aliases"]
        names = "\x1f" + "\x1f".join(" ".join(word_stems(n)) for n in [record["title"], *aliases]) + "\x1f"
        db.execute("""UPDATE documents SET id=?,type=?,title=?,aliases=?,alias_terms=?,direction=?,
                      revision=?,brief_names=? WHERE key=?""",
                   (record["id"], record["type"], record["title"], json.dumps(aliases, ensure_ascii=False),
                    _normalize(" ".join(aliases)), record.get("direction"), record["revision"], names, key))
        db.executemany("""INSERT INTO lines(document,line,text,terms_suffix,brief,fact_id,kind,direction)
                          VALUES (?,?,?,?,?,?,?,?)""", ((key, *line) for line in _lines(path, record)))
        db.execute("""INSERT INTO fragments(rowid,title_terms,alias_terms,body_terms)
                      SELECT rowid,title_terms,alias_terms,body_terms FROM fragment_terms
                      WHERE rowid IN (SELECT key FROM lines WHERE document=?)""", (key,))
    if _stamp(path) != stamp:
        raise storage.ConflictError("Карточка изменилась во время индексирования. Повторите запрос.")


def _sync(db, root, files, signature):
    # Delete using the original indexed terms before replacing the source rows.
    previous = {row["path"]: row for row in db.execute("SELECT key,path,stamp,type FROM documents")}
    removed_person_facts = False
    for relative, row in previous.items():
        key, stamp = row["key"], row["stamp"]
        if files.get(relative) == stamp:
            continue
        removed_person_facts |= row["type"] == "person"
        db.execute("""INSERT INTO fragments(fragments,rowid,title_terms,alias_terms,body_terms)
                      SELECT 'delete',rowid,title_terms,alias_terms,body_terms FROM fragment_terms
                      WHERE rowid IN (SELECT key FROM lines WHERE document=?)""", (key,))
        db.execute("DELETE FROM lines WHERE document=?", (key,))
        db.execute("DELETE FROM documents WHERE key=?", (key,))
    for relative, stamp in files.items():
        if relative not in previous or previous[relative]["stamp"] != stamp:
            _index_file(db, root, relative, stamp)
    if db.execute("""SELECT 1 FROM documents WHERE type='person' GROUP BY id HAVING count(*)>1""").fetchone():
        raise ValueError("Один ID человека найден в нескольких карточках.")
    if removed_person_facts:
        # An owner can make a previously shared fact private. Merge FTS tombstones
        # while secure_delete is on so the old terms leave the on-disk cache too.
        db.execute("INSERT INTO fragments(fragments) VALUES ('optimize')")
    db.execute("DELETE FROM metadata")
    db.execute("INSERT INTO metadata VALUES (?)", (signature,))


def _replace(root, database, files, signature):
    fd, name = tempfile.mkstemp(dir=database.parent, suffix=".sqlite3")
    os.close(fd)
    temporary = Path(name)
    try:
        with _connect(temporary) as db, db:
            db.executescript(_SCHEMA)
            db.execute(f"PRAGMA user_version={_VERSION}")
            _sync(db, root, files, signature)
        os.replace(temporary, database)
    finally:
        temporary.unlink(missing_ok=True)


def _update(root, database, *, force=False):
    database.parent.mkdir(exist_ok=True)
    with storage._vault_lock(database.parent, write=True):
        files, signature = _manifest(root)
        if not force and database.is_file():
            try:
                with _connect(database) as db, db:
                    db.execute("BEGIN IMMEDIATE")
                    stored = _signature(db)
                    if stored == signature:
                        return False
                    if stored is not None:
                        db.execute("PRAGMA secure_delete=ON")
                        _sync(db, root, files, signature)
                        return True
            except sqlite3.DatabaseError as error:
                if isinstance(error, sqlite3.OperationalError) and "locked" in str(error).lower():
                    raise
        _replace(root, database, files, signature)
        return True


def rebuild(root):
    root = Path(root).resolve()
    _update(root, _database(root), force=True)


@contextmanager
def current(root, *, allow_sync=True):
    """Pin one SQLite read transaction so catalogue and passages agree across writers."""
    database = _database(root)
    if database.is_file():
        _, signature = _manifest(root)
        with _connect(database, read_only=True) as db:
            db.execute("BEGIN")
            try:
                fresh = _signature(db) == signature
            except sqlite3.DatabaseError:
                fresh = False
            if fresh:
                yield db, False
                return
    if not allow_sync:
        yield None, False
        return
    changed = _update(root, database)
    with _connect(database, read_only=True) as db:
        db.execute("BEGIN")
        yield db, changed


@contextmanager
def optional(root, *, allow_sync=True):
    """Read-only vaults and a scoped question with an outdated global cache can use Markdown."""
    with ExitStack() as stack:
        try:
            db, _ = stack.enter_context(current(root, allow_sync=allow_sync))
        except (OSError, ValueError, sqlite3.Error):
            db = None
        yield db


def record_path(root, relative):
    parts = Path(relative).parts
    if not parts or parts[0] not in storage.KINDS.values() or ".." in parts or Path(relative).suffix.lower() != ".md":
        raise ValueError("Недопустимый путь карточки в каталоге. Пересоберите индекс.")
    return root / relative


def find(db, identifier):
    rows = list(db.execute("SELECT path,type FROM documents WHERE id=? AND type IN ('direction','note','project')",
                           (identifier,)))
    if len(rows) > 1:
        raise ValueError(f"Один ID найден в нескольких файлах: {identifier}")
    if not rows:
        raise FileNotFoundError(f"Запись не найдена: {identifier}")
    return rows[0]


def _scope_filter(db, scope, column):
    if scope is None:
        return "1"
    # Keep arbitrary-sized selections in the connection instead of interpolating IDs.
    db.create_function("in_scope", 1, scope.__contains__)
    return f"in_scope({column})"


def records(root, db, *, kind=None, direction=None, scope=None):
    # Match the established folder and os.walk order, including cards moved to subfolders.
    folders = {folder: pos for pos, folder in enumerate(storage.KINDS.values())}
    def order(row):
        parts = row["path"].split("/")
        return folders[parts[0]], parts[1:-1], parts[-1]
    permitted = _scope_filter(db, scope, "CASE WHEN type='direction' THEN id ELSE direction END")
    rows = db.execute(f"""SELECT path,id,type,title,aliases,direction,revision FROM documents
                         WHERE type IN ('direction','note','project') AND (? IS NULL OR type=?)
                         AND (? IS NULL OR direction=?) AND {permitted}""", (kind, kind, direction, direction))
    for row in sorted(rows, key=order):
        record = dict(row)
        path = record_path(root, record.pop("path"))
        record["aliases"] = json.loads(record["aliases"])
        yield path, record


def shared_facts(db, scope):
    permitted = _scope_filter(db, scope, "l.direction")
    for row in db.execute(f"""SELECT d.path,d.id AS personId,d.title AS personName,d.aliases,l.line AS cardLine,
                             l.text,l.fact_id AS id,l.kind,l.direction FROM lines l
                             JOIN documents d ON d.key=l.document WHERE l.fact_id IS NOT NULL AND {permitted}
                             ORDER BY d.path,l.line"""):
        yield {**row, "aliases": json.loads(row["aliases"])}


def matching_lines(db, terms, scope):
    # _related permits a stem to be a prefix in either direction from four letters.
    # Shorter prefixes deliberately overselect. Markdown scoring verifies all evidence.
    prefixes = sorted({t[:4] for t in terms if t.isascii() or all('а' <= c <= 'я' for c in t)})
    if not prefixes:
        return None
    expr = "body_terms : (" + " AND ".join(f'"{term}"*' for term in prefixes[:16]) + ")"
    matched = {}
    permitted = _scope_filter(db, scope, "d.direction")
    for row in db.execute(f"""WITH candidates(key) AS (
            SELECT rowid FROM fragments WHERE fragments MATCH ?
            UNION SELECT key FROM lines WHERE kind='unicode-break')
            SELECT d.id,l.line,l.text FROM candidates c JOIN lines l ON l.key=c.key
            JOIN documents d ON d.key=l.document
            WHERE d.type IN ('project','note') AND l.kind IS NOT 'title' AND {permitted}
            ORDER BY d.key,l.line""", (expr,)):
        matched.setdefault(row["id"], []).append((row["line"], row["text"]))
    return matched
