"""Withdraw proven, unchanged examples from an editable Markdown vault."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import posixpath
import re
import tempfile
import unicodedata
import zipfile
import zlib
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote

from . import storage


_CONTROL = ".second-brain-demo"
_ARCHIVE = "Archive/DemoCleanup"
_FOLDERS = {folder.split("/")[0] for folder in storage.FOLDERS} - {"Archive"}
_NEUTRAL_HOME = storage.START_PAGES["Home.md"].encode()
_TEXT = {".md", ".txt", ".org", ".yaml", ".yml", ".json", ".canvas", ".csv", ".html", ".base"}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_WIKI = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
_LINK = re.compile(r"\]\(<?([^\n)>]+)>?\)")


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _root(path):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Нужна существующая папка хранилища.")
    return path.resolve()


def _file(root, name):
    return storage._inside(root, name)


def _read(path):
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Ожидался обычный файл: {path.name}")
    return path.read_bytes()


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _control(root, name):
    path = _file(root, f"{_CONTROL}/{name}.json")
    raw = _read(path)
    return None if raw is None else json.loads(raw)


def _publish(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    storage.replace_bytes(path, None, data)


def _eligible(name):
    if not isinstance(name, str) or any(char in name for char in "\\:\0\n\r") or PureWindowsPath(name).drive:
        return False
    parts = name.split("/")
    return (all(part and not part.startswith(".") for part in parts)
            and (name == "Home.md" or len(parts) >= 2 and parts[0] in _FOLDERS
                 or len(parts) > 2 and parts[:2] == ["Archive", "Inbox"])
            and parts[:2] != ["Settings", "Demo"])


def _entries(entries):
    if not isinstance(entries, dict) or any(
        not _eligible(name) or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
        for name, digest in entries.items()
    ):
        raise ValueError("Повреждён список исходных учебных файлов.")
    return entries


def _walk(root):
    def failed(error):
        raise error

    runtime = root / ".runtime"
    entry = runtime / "second_brain" / "__main__.py"
    installed = not any(path.is_symlink() for path in (runtime, entry.parent, entry)) and entry.is_file()
    for directory, directories, files in os.walk(root, onerror=failed):
        base = Path(directory)
        # Only the shipped program and its own documentation can contain
        # unconditional sample links. Custom files still protect owner references.
        if installed and base == runtime:
            directories[:] = [d for d in directories if d not in {"second_brain", "examples", "docs", "plugins", ".agents"}
                              or (base / d).is_symlink()]
            files = [name for name in files if name not in {"README.md", "ROADMAP.md"} or (base / name).is_symlink()]
        directories[:] = sorted(d for d in directories if d not in {".git", ".obsidian", ".trash"}
                                and (base / d).relative_to(root).as_posix() not in
                                {_ARCHIVE, _CONTROL, ".second-brain"})
        # A user's symlink can itself point to an example; report its lexical target
        # without opening it. os.walk never descends into directory symlinks.
        for name in sorted(files + [d for d in directories if (base / d).is_symlink()]):
            path = base / name
            relative = path.relative_to(root).as_posix()
            if not name.startswith(".brain-") and relative != "Settings/Demo/catalog.json":
                yield relative, path
    # Bookmarks are explicit owner references. Obsidian's transient workspace
    # state also lists recently opened files and should not retain every example.
    settings = root / ".obsidian"
    if not settings.is_symlink():
        bookmarks = settings / "bookmarks.json"
        if bookmarks.is_file() or bookmarks.is_symlink():
            yield ".obsidian/bookmarks.json", bookmarks


def _identifier(raw):
    if raw and raw.startswith(b"---"):
        for line in raw.splitlines()[1:]:
            if line in (b"---", b"..."):
                break
            if line.startswith(b"id:"):
                try:
                    value = storage._scalar(line[3:].decode())
                except (ValueError, UnicodeDecodeError):
                    break
                return value if isinstance(value, str) and value else None
    return None


def record_seed(root):
    """Snapshot a private, still-unpublished generator output before its final rename."""
    root = _root(root)
    if _control(root, "cleaned") is not None or _control(root, "pending") is not None:
        raise ValueError("Очистка этого хранилища уже начата.")
    manifest = {}
    ids = {}
    for name, path in _walk(root):
        if not _eligible(name):
            continue
        raw = _read(_file(root, name))
        if raw is None or name == "Home.md" and raw == _NEUTRAL_HOME:
            continue
        manifest[name] = _hash(raw)
        identifier = _identifier(raw) if path.suffix.lower() == ".md" else None
        if identifier:
            ids[name] = identifier
    path = _file(root, f"{_CONTROL}/seed.json")
    path.parent.mkdir(exist_ok=True)
    storage.replace_bytes(path, _read(path), _json({"version": 1, "files": manifest, "ids": ids}))


def _seed(root):
    manifest = _control(root, "seed")
    if manifest is not None:
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise ValueError("Неизвестная версия манифеста учебных файлов.")
        entries = _entries(manifest.get("files"))
        ids = manifest.get("ids", {})
        if not isinstance(ids, dict) or any(k not in entries or not isinstance(v, str) for k, v in ids.items()):
            raise ValueError("Повреждены идентификаторы учебных файлов.")
        # Old generators recorded completed examples at their former Inbox
        # paths. Recognize a later standard archive move only when the seed
        # also proves that this exact material already had a receipt.
        for name, digest in list(entries.items()):
            if (not name.startswith("Inbox/") or "Sources/Processed/" + digest + ".md" not in entries
                    or os.path.lexists(root / name)):
                continue
            archived = "Archive/" + name
            try:
                target = _file(root, archived)
            except ValueError:
                continue  # An owner's symlink is never a generated archive move.
            if archived not in entries and target.is_file() and _file_hash(target) == digest:
                entries[archived] = entries.pop(name)
                if name in ids:
                    ids[archived] = ids.pop(name)
        return entries, ids
    from .legacy_examples import legacy_seed
    return _entries(legacy_seed(root) or {}), {}


def _fold(text):
    return unicodedata.normalize("NFC", text).casefold()


def _references(name, text, targets):
    """Resolve stable IDs, vault paths, relative Markdown and Obsidian links."""
    text = _fold(unquote(text))
    parent = posixpath.dirname(_fold(name))
    linked = set()
    for expression in (_WIKI, _LINK):
        for raw in expression.findall(text):
            target = raw.split("#", 1)[0].strip().strip("<>")
            if target:
                linked.add(target)
                linked.add(posixpath.normpath(posixpath.join(parent, target)).lstrip("/"))
    for candidate, (path, ids) in targets.items():
        basename = posixpath.basename(path)
        spellings = {path, basename}
        if path.endswith(".md"):
            spellings.update((path[:-3], basename[:-3]))
        # Obsidian can shorten a nested path to its unambiguous suffix.
        for spelling in tuple(spellings):
            while "/" in spelling:
                spelling = spelling.split("/", 1)[1]
                spellings.add(spelling)
        if (linked.intersection(spellings) or path in text or basename in text
                or any(token in text for token in ids)):
            yield candidate


def _unavailable():
    return {"available": False, "cleaned": False, "removeCount": 0, "keepCount": 0, "revision": ""}


def _plan(root, seed=None, withdrawn=None):
    entries, ids = seed if seed is not None else _seed(root)
    if not entries:
        return _unavailable(), {}
    same, changed, texts, state, links = set(), set(), {}, {}, {}
    owners, digests = set(), set()
    inventory = dict(_walk(root))
    # Validate the preview again before committing, including newly added references
    # from external editors that do not participate in the vault lock.
    if withdrawn:
        revision, files = withdrawn
        for name in files:
            active = inventory.pop(name, None)
            if active is not None and (name != "Home.md" or active.is_symlink() or _read(active) != _NEUTRAL_HOME):
                raise storage.ConflictError("На месте учебного файла появилась новая запись.")
            inventory[name] = _held(root, revision, name)
    for name, path in inventory.items():
        if path.is_symlink():
            destination = os.readlink(path)
            target = os.path.normpath(os.path.join(path.parent, destination))
            links[name] = os.path.relpath(target, root).replace(os.sep, "/")
            state[name] = ["link", destination]
            if name in entries:
                changed.add(name)
            continue
        if not path.is_file():
            if name in entries:
                changed.add(name)
            continue
        incoming = name.startswith(("Inbox/", "Archive/Inbox/"))
        if path.suffix.lower() not in _TEXT and name not in entries and not incoming:
            continue
        safe_path = _file(root, path.relative_to(root))
        raw = None
        if path.suffix.lower() in _TEXT:
            raw = _read(safe_path)
            if raw is None:
                raise storage.ConflictError("Состав хранилища изменился. Повторите просмотр.")
            texts[name] = raw.decode("utf-8", errors="replace")
            digest = _hash(raw)
        else:
            digest = _file_hash(safe_path)
        state[name] = digest
        if incoming:
            # Inbox matches a processing receipt and archived original by content
            # hash even when another capture used a different name.
            texts[name] = texts.get(name, "") + "\n" + digest
        if name in entries:
            (same if digest == entries[name] else changed).add(name)
        else:
            digests.add(digest)
            identifier = _identifier(raw)
            if identifier:
                owners.add(identifier)
    # A relocated example stays where the owner put it. It still protects its own links.
    moved = {name for name in entries.keys() - state.keys()
             if ids.get(name) in owners or entries[name] in digests}
    targets = {}
    for name in same:
        tokens = {_fold(token) for token in (ids.get(name), _identifier(texts.get(name, "").encode())) if token}
        stem = Path(name).stem
        if _DIGEST.fullmatch(stem) or name.startswith("Settings/Intake/"):
            tokens.add(_fold(stem))
        targets[name] = (_fold(name), tokens)

    keep = {name for name in same if any(name == target or name.startswith(target + "/")
                                         for target in links.values())}
    unresolved = {name: target for name, target in targets.items() if name not in keep}
    queue = list((texts.keys() - same) | keep)
    while queue and unresolved:
        source = queue.pop()
        for candidate in list(_references(source, texts.get(source, ""), unresolved)):
            keep.add(candidate)
            queue.append(candidate)
            del unresolved[candidate]
    removable = {name: entries[name] for name in sorted(same - keep)}
    revision = _hash(_json([str(root), state, sorted(removable), sorted(changed | keep | moved)]))
    return {"available": True, "cleaned": False, "removeCount": len(removable),
            "keepCount": len(changed | keep | moved), "revision": revision}, removable


def _receipt(root):
    result = _control(root, "cleaned")
    if result is None:
        return None
    if (not isinstance(result, dict) or result.get("cleaned") is not True
            or not _DIGEST.fullmatch(str(result.get("revision", "")))):
        raise ValueError("Повреждена квитанция очистки демоданных.")
    expected_archive = (None if result.get("version") == 2 and result.get("removeCount") == 0
                        else f"{_ARCHIVE}/{result['revision']}.zip")
    if result.get("archive") != expected_archive:
        raise ValueError("Повреждена квитанция очистки демоданных.")
    return result


def is_cleaned(root):
    root = Path(root)
    return root.is_dir() and _receipt(_root(root)) is not None


def _legacy_empty_archive(root, receipt):
    # The original one-folder installer made examples referenced by its own manual
    # appear user-owned. Keep the working interface while allowing a fresh preview.
    return (receipt.get("version", 1) == 1 and receipt.get("removeCount") == 0
            and receipt.get("keepCount", 0) > 0 and (root / ".runtime").is_dir())


def _pending(root):
    journal = _control(root, "pending")
    if journal is None:
        return None
    if not isinstance(journal, dict) or not isinstance(journal.get("preview"), dict):
        raise ValueError("Повреждён журнал очистки демоданных.")
    _entries(journal.get("files"))
    if not _DIGEST.fullmatch(str(journal["preview"].get("revision", ""))):
        raise ValueError("Повреждена версия журнала очистки.")
    return journal


def preview(root):
    root = _root(root)
    with storage._vault_lock(root, write=False):
        result = _receipt(root)
        if result is not None and not _legacy_empty_archive(root, result):
            return result
        journal = _pending(root)
        if journal is not None and (result is None or journal["preview"]["revision"] != result["revision"]):
            return journal["preview"]
        plan = _plan(root)[0]
        return result if result is not None and not plan["removeCount"] else plan


def _held(root, revision, name):
    return _file(root, f"{_CONTROL}/withdrawn/{revision}/{name}")


def _prune_holding(root, journal):
    revision = journal["preview"]["revision"]
    control = _file(root, _CONTROL)
    folders = set()
    for name in journal["files"]:
        parent = _held(root, revision, name).parent
        while parent != control:
            folders.add(parent)
            parent = parent.parent
    for folder in sorted(folders, key=lambda path: len(path.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST):
                raise


def _restore(root, journal):
    # Only paths actually withdrawn by this operation can be restored.
    # Hard-link creation cannot overwrite a new user file at the original path.
    for name in journal["files"]:
        held = _held(root, journal["preview"]["revision"], name)
        if not held.is_file():
            continue
        active = _file(root, name)
        if name == "Home.md" and _read(active) == _NEUTRAL_HOME:
            storage.replace_bytes(active, _NEUTRAL_HOME, held.read_bytes())
        elif not active.exists():
            active.parent.mkdir(parents=True, exist_ok=True)
            os.link(held, active)
        elif active.read_bytes() != held.read_bytes():
            # A newly created personal file wins. The withdrawn original stays in
            # its archive, and a racing editor's exact version stays in holding.
            continue
        held.unlink()
    _prune_holding(root, journal)
    _file(root, f"{_CONTROL}/pending.json").unlink()


def _withdraw(root, revision, name, digest):
    active = _file(root, name)
    held = _held(root, revision, name)
    held.parent.mkdir(parents=True, exist_ok=True)
    raw = _read(active)
    if held.exists() or raw is None or _hash(raw) != digest:
        raise storage.ConflictError("Учебный файл изменился. Повторите просмотр.")
    active.rename(held)
    if held.is_symlink() or _hash(held.read_bytes()) != digest:
        raise storage.ConflictError("Учебный файл изменился во время очистки.")
    if name == "Home.md":
        _publish(active, _NEUTRAL_HOME)


def _backup(root, revision, files):
    name = f"{_ARCHIVE}/{revision}.zip"
    path = _file(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # Publish an independent, complete ZIP before withdrawing the first file.
        fd, staged = tempfile.mkstemp(prefix=".brain-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w+b") as stream:
                with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                    for member, digest in files.items():
                        raw = _read(_file(root, member))
                        if raw is None or _hash(raw) != digest:
                            raise storage.ConflictError("Учебный файл изменился. Повторите просмотр.")
                        archive.writestr(member, raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(staged, path)
            except FileExistsError:
                pass  # Verify an existing archive and leave its bytes untouched.
        finally:
            os.unlink(staged)
    _verify_archive(root, name, files)
    return name


def _verify_archive(root, archive_name, files, *, withdrawn=None):
    path = _file(root, archive_name)
    if not path.is_file():
        raise storage.ConflictError("Архив демоданных недоступен.")
    try:
        with zipfile.ZipFile(path) as archive:
            if sorted(archive.namelist()) != sorted(files):
                raise storage.ConflictError("Состав архива демоданных изменился.")
            for member, digest in files.items():
                source = _held(root, withdrawn, member) if withdrawn else _file(root, member)
                raw = _read(source)
                if (raw is None or _hash(raw) != digest or archive.getinfo(member).file_size != len(raw)
                        or archive.read(member) != raw):
                    raise storage.ConflictError("Архив и исходные учебные файлы не совпадают.")
    except (zipfile.BadZipFile, EOFError, RuntimeError, zlib.error) as error:
        raise storage.ConflictError("Повреждён архив учебных файлов.") from error


def _finish(root, journal):
    revision = journal["preview"]["revision"]
    archive_path = _file(root, f"{_ARCHIVE}/{revision}.zip")
    if not archive_path.is_file():
        raise storage.ConflictError("Архив демоданных недоступен.")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for name, digest in journal["files"].items():
                held = _held(root, revision, name)
                raw = _read(held)
                # Only discard the temporary inode if the independent backup still
                # has the same bytes. A racing editor may have written to that inode.
                if raw is not None and _hash(raw) == digest:
                    if archive.getinfo(name).file_size == len(raw) and archive.read(name) == raw:
                        held.unlink()
    except (zipfile.BadZipFile, EOFError, KeyError, RuntimeError, zlib.error) as error:
        raise storage.ConflictError("Повреждён архив учебных файлов.") from error
    _prune_holding(root, journal)
    _file(root, f"{_CONTROL}/pending.json").unlink()


def apply(root, revision):
    root = _root(root)
    if not isinstance(revision, str) or not _DIGEST.fullmatch(revision):
        raise ValueError("Сначала просмотрите состав учебных данных.")
    with storage._write_lock(root):
        previous = _receipt(root)
        journal = _pending(root)
        if journal is not None:
            if previous is not None and journal["preview"]["revision"] == previous["revision"]:
                _finish(root, journal)
            else:
                _restore(root, journal)
        if previous is not None:
            if previous["revision"] == revision:
                return previous
            if not _legacy_empty_archive(root, previous):
                raise storage.ConflictError("Эта версия очистки уже не актуальна.")
        seed = _seed(root)
        report, files = _plan(root, seed)
        if not report["available"] or revision != report["revision"] or (previous is not None and not files):
            raise storage.ConflictError("Состав хранилища изменился. Повторите просмотр.")
        receipt_path = _file(root, f"{_CONTROL}/cleaned.json")
        if not files:
            result = dict(report, version=2, cleaned=True, archive=None)
            _publish(receipt_path, _json(result))
            return result
        archive = _backup(root, revision, files)
        journal = {"preview": report, "files": files}
        pending = _file(root, f"{_CONTROL}/pending.json")
        _publish(pending, _json(journal))
        result = dict(report, version=2, cleaned=True, archive=archive)
        try:
            for name, digest in files.items():
                _withdraw(root, revision, name, digest)
            confirmed, _ = _plan(root, seed, withdrawn=(revision, files))
            if confirmed["revision"] != revision:
                raise storage.ConflictError("Состав хранилища изменился во время очистки.")
            _verify_archive(root, archive, files, withdrawn=revision)
            storage.replace_bytes(receipt_path, _json(previous) if previous is not None else None, _json(result))
        except Exception:
            _restore(root, journal)
            raise
        _finish(root, journal)
        return result
