"""Bounded, per-vault diagnostic snapshots in the current user's OS cache."""

from contextlib import closing, suppress
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import uuid


_KEEP = 8
_MISSING = "Сохранённая проверка недоступна. Запустите новую проверку."


def _database(root):
    configured = os.environ.get("LOCALAPPDATA" if os.name == "nt" else "XDG_CACHE_HOME")
    if configured and Path(configured).is_absolute():
        base = Path(configured)
    elif os.name == "nt":
        base = Path.home() / "AppData/Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Caches"
    else:
        base = Path.home() / ".cache"
    vault_key = hashlib.sha256(os.fsencode(os.path.normcase(str(root)))).hexdigest()
    folder = (base / "second-brain" / "checks" / vault_key).resolve()
    if folder == root or root in folder.parents:
        raise ValueError("Для сохранённой проверки укажите каталог кеша вне хранилища.")
    return folder / "results.sqlite3"


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def save(root, summary, issues):
    path = _database(root)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with suppress(FileExistsError):
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    identifier = uuid.uuid4().hex
    metadata = {**summary, "snapshot": identifier}
    try:
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS checks (id TEXT PRIMARY KEY, metadata TEXT, total INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS issues (check_id TEXT REFERENCES checks(id) ON DELETE CASCADE,"
                       " position INTEGER, finding TEXT, PRIMARY KEY(check_id, position))")
            db.execute("INSERT INTO checks VALUES (?, ?, ?)", (identifier, _encode(metadata), len(issues)))
            db.executemany("INSERT INTO issues VALUES (?, ?, ?)",
                           ((identifier, n, _encode(issue)) for n, issue in enumerate(issues)))
            db.execute("DELETE FROM checks WHERE rowid NOT IN (SELECT rowid FROM checks ORDER BY rowid DESC LIMIT ?)",
                       (_KEEP,))
    except sqlite3.Error as error:
        raise ValueError("Не удалось сохранить результат проверки в локальном кеше.") from error
    return metadata


def read(root, identifier, limit, offset):
    if not isinstance(identifier, str) or not re.fullmatch("[0-9a-f]{32}", identifier):
        raise ValueError("Укажите ID сохранённой проверки или new для нового прохода.")
    path = _database(root)
    if not path.is_file():
        raise ValueError(_MISSING)
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db, db:
            db.execute("BEGIN")
            row = db.execute("SELECT metadata,total FROM checks WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise ValueError(_MISSING)
            metadata, total = row
            rows = db.execute("SELECT finding FROM issues WHERE check_id=? AND position>=? ORDER BY position LIMIT ?",
                              (identifier, min(offset, total), limit))
            items = [json.loads(finding) for finding, in rows]
    except sqlite3.Error as error:
        raise ValueError(_MISSING) from error
    return {**json.loads(metadata), "issues": {"items": items, "total": total, "truncated": offset + len(items) < total}}
