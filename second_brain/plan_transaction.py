"""Publish a forecast scenario across Markdown files under the vault lock.

While a transaction is pending, planning readers see the previous generation.
After a process interruption the next writer restores that generation. The
journal exists only until every replacement has succeeded.
"""

import hashlib
import json
from pathlib import Path
import re

from . import storage


_PENDING = ".second-brain-forecast-pending.json"


def _journal(root):
    return storage._inside(root, _PENDING)


def previous_versions(root):
    journal = _journal(root)
    if not journal.exists():
        return {}
    try:
        entries = json.loads(journal.read_bytes())
        versions = {}
        if not isinstance(entries, list) or not entries:
            raise ValueError
        for entry in entries:
            rel = Path(entry["path"])
            if (rel.is_absolute() or rel.parts[:1] != ("Tasks",) or ".." in rel.parts
                    or rel.suffix.lower() != ".md" or not re.fullmatch("[0-9a-f]{64}", entry["afterHash"])):
                raise ValueError
            path = storage._inside(root, rel)
            if path in versions:
                raise ValueError
            versions[path] = (entry["before"].encode(), entry["afterHash"])
        return versions
    except (TypeError, KeyError, AttributeError, ValueError) as error:
        raise storage.ConflictError(f"Повреждён журнал незавершённого сценария {journal.name}.") from error


def recover(root):
    """Restore only our published bytes. Leave an external editor's version intact."""
    versions = previous_versions(root)
    if not versions:
        return
    external = []
    for path, (before, after_hash) in versions.items():
        if path.is_file():
            current = path.read_bytes()
            if current == before:
                continue
            if hashlib.sha256(current).hexdigest() == after_hash:
                storage.replace_bytes(path, current, before)
                continue
        external.append(path.name)
    _journal(root).unlink()
    if external:
        raise storage.ConflictError("При отмене сценария сохранены внешние правки: " + ", ".join(external))


def publish(root, changes):
    """Caller holds the exclusive lock and has verified the full scenario."""
    if not changes:
        return
    journal = _journal(root)
    entries = [{"path": path.relative_to(root).as_posix(), "before": before.decode(),
                "afterHash": hashlib.sha256(after).hexdigest()} for path, before, after in changes]
    storage.replace_bytes(journal, None, json.dumps(entries, ensure_ascii=False).encode())
    try:
        for path, before, after in changes:
            storage.replace_bytes(path, before, after)
        journal.unlink()
    except BaseException:
        recover(root)
        raise
