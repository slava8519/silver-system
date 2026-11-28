"""Move reviewed Inbox files while keeping their immutable originals and receipts."""

from itertools import count
import os
from pathlib import Path
import re

from . import brief, inbox, storage, vault_check
from .batch import _journal


def completed_original(service, digest, check=None):
    """A damaged, unfinished or deferred review must keep its incoming copy."""
    receipt = service._receipt(digest)
    raw = service._receipt_bytes(receipt, digest)
    if raw is None or inbox.manual_remainder(raw):
        return None
    jobs, questions = _journal(raw, digest)
    if questions or any(not job["done"] for job in jobs.values()):
        return None
    checked = check if check is not None else vault_check.check_after_intake(service.root, digest)
    if checked["status"] != "ok":
        message = checked["issues"]["items"][0]["message"]
        raise storage.ConflictError(message)
    return service._original(digest)


def _target(root, incoming, digest):
    relative = incoming.relative_to(root / "Inbox")
    folder = Path("Archive/Inbox")
    generated = relative.parent == Path("Transcripts") and re.fullmatch(r"[0-9a-f]{64}\.md", relative.name)
    for number in count(1):
        if generated:
            # The transcript header links to ../../Sources/Originals from a depth of two.
            version = "" if number == 1 else f" ({number})"
            label = folder / f"Transcript {relative.stem}{version}.md"
        else:
            # Keep even maximum-length filenames intact in the versions directory.
            parent = folder if number == 1 else folder / "Versions" / str(number)
            label = parent / relative
        target = storage._inside(root, label)
        if not target.exists() or (target.is_file() and inbox._file_digest(target)[0] == digest):
            return target


def _link_transcript(service, incoming, original):
    """A generated receipt should still open its transcript after the Inbox copy moves."""
    if (incoming.parent != service.root / "Inbox/Transcripts" or incoming.suffix != ".md"
            or not re.fullmatch("[0-9a-f]{64}", incoming.stem)):
        return
    receipt = service._receipt(incoming.stem)
    previous = service._receipt_bytes(receipt, incoming.stem)
    if previous is None:
        return
    label = "Расшифровка для разбора"
    old = storage.markdown_link(label, incoming, receipt.parent).encode()
    new = storage.markdown_link(label, original, receipt.parent).encode()
    rows = previous.splitlines(keepends=True)
    updated = b"".join(row.replace(old, new, 1) if row.rstrip(b"\r\n") == old else row for row in rows)
    storage.replace_bytes(receipt, previous, updated)


def _move(service, incoming, digest, target, original):
    from .inbox_activity import remember_inbox_date
    target.parent.mkdir(parents=True, exist_ok=True)
    # Refuse a changed input and never replace a same-named archived version.
    if inbox._file_digest(incoming)[0] != digest:
        raise storage.ConflictError("Входящий изменился перед переносом. Повторите просмотр.")
    remember_inbox_date(service.root, incoming, digest)
    try:
        os.link(incoming, target)
    except FileExistsError:
        if inbox._file_digest(target)[0] != digest:
            raise storage.ConflictError("Архивный файл изменился. Повторите просмотр.")
    _link_transcript(service, incoming, original)
    if inbox._file_digest(incoming)[0] != digest:
        raise storage.ConflictError("Входящий изменился во время переноса. Проверьте обе версии.")
    incoming.unlink()


def finish_review(service, path, digest):
    with storage._write_lock(service.root):
        checked = vault_check.check_after_intake(service.root, digest)
        if checked["status"] != "ok":
            return {"check": checked}
        try:
            original = completed_original(service, digest, checked)
            if original:
                incoming = service._incoming(path, attachments=True, missing_ok=True)
                if incoming.exists() and inbox._file_digest(incoming)[0] == digest:
                    _move(service, incoming, digest, _target(service.root, incoming, digest), original)
        except (ValueError, OSError) as error:
            # The review is already committed. Keep its receipt available for a retry.
            return {"check": checked, "archiveIssue": {"path": path, "reason": str(error)}}
        return {"check": checked}


def archive_inbox(root, *, apply=False, limit=10):
    service = inbox.Inbox(root)
    files, errors = [], []
    moved = 0
    with storage._vault_lock(service.root, write=apply):
        for path in service._files():
            relative = path.relative_to(service.root).as_posix()
            try:
                digest, _ = inbox._file_digest(path)
                original = completed_original(service, digest)
                if original is None:
                    continue
                target = _target(service.root, path, digest)
                if apply:
                    _move(service, path, digest, target, original)
                    moved += 1
                files.append({"path": relative, "archivePath": target.relative_to(service.root).as_posix()})
            except (OSError, ValueError) as error:
                errors.append({"path": relative, "reason": str(error)})
    return {"archived": moved, "files": brief.page(files, limit, 0), "issues": brief.page(errors, limit, 0)}
