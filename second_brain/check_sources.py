"""Read-only integrity checks for content-addressed Inbox originals and receipts."""

from collections import defaultdict
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
from urllib.parse import unquote

from . import batch, inbox, people, storage


_DIGEST = re.compile(r"[0-9a-f]{64}")
_DESTINATION = re.compile(r"<!-- (person|fact)-id: (.+) -->")


@contextmanager
def _regular_file(path, mode, **kwargs):
    def open_regular(name, flags):
        return os.open(name, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))

    if path.is_symlink():
        raise OSError("Символическая ссылка")
    with open(path, mode, opener=open_regular, **kwargs) as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError("Ожидался обычный файл")
        yield stream


def _entries(root, subfolder, report, source_id):
    try:
        directory = storage._inside(root, Path("Sources") / subfolder)
    except ValueError:
        return None  # The caller checks the vault's directory structure.
    try:
        if not directory.is_dir():
            return None
        if source_id and subfolder == "Processed":
            receipt = directory / (source_id + ".md")
            try:
                receipt.lstat()
            except FileNotFoundError:
                return []
            return [receipt]
        return sorted(p for p in directory.iterdir() if (source_id is None or p.stem == source_id)
                      and _DIGEST.fullmatch(p.stem))
    except OSError:
        report.unreadable("source_directory_unreadable", directory, 0, "Не удалось перечислить файлы.")
        return None


def _header(lines, digest):
    """Return the header's first invalid line, or zero for a valid receipt."""
    if not lines or lines[0] != "---":
        return 1
    required = {"type": "processed_source", "schema_version": "1", "source_id": digest}
    found = {}
    for number, row in enumerate(lines[1:], 2):
        if row == "---":
            return 0 if set(required).issubset(found) else number
        key, sep, value = row.partition(":")
        if not sep or key not in (*required, "inbox_path"):
            continue
        try:
            value = storage._scalar(value)
        except ValueError:
            return number
        if key in found or (key in required and value != required[key]):
            return number
        if key == "inbox_path" and not batch._stored_path(value, "Inbox"):
            return number
        found[key] = value
    return 1


def _broken_event(markers, digest):
    # The journal reader rejects the first invalid event and all longer prefixes.
    lo, hi = 0, len(markers) - 1
    while lo < hi:
        middle = (lo + hi) // 2
        try:
            batch._journal("\n".join(row for _, row in markers[:middle + 1]).encode(), digest)
        except (ValueError, RecursionError):
            hi = middle
        else:
            lo = middle + 1
    return markers[lo][0]


def check_quotes(original, requests, report, code):
    """Match receipt and card citations using the same Unicode line boundaries as Inbox."""
    pending = dict(requests)
    try:
        with _regular_file(original, "r", encoding="utf-8-sig", newline="") as stream:
            number = 0
            for physical_line in stream:
                for line in physical_line.splitlines():
                    number += 1
                    selected = pending.pop(number, ())
                    for quote, path, location in selected:
                        if quote not in line:
                            report.add(code, path, location, f"Цитата отсутствует в строке {number} оригинала.")
                if not pending:
                    break
    except OSError:
        report.unreadable("source_unreadable", original, 1, "Не удалось прочесть строки оригинала.")
        return
    except UnicodeError:
        report.add("source_encoding", original, 1, "Цитата ссылается на строки оригинала с повреждённой кодировкой.")
        return
    for number, items in pending.items():
        for _, path, location in items:
            report.add(code, path, location, f"В оригинале отсутствует строка {number}.")


def _check_receipt(root, receipt, original, report):
    try:
        with _regular_file(receipt, "rb") as stream:
            raw = stream.read()
    except OSError:
        report.unreadable("receipt_unreadable", receipt, 1, "Не удалось прочесть обычный файл разбора.")
        return
    try:
        # Batch markers are delimited by byte newlines. Other Unicode line
        # separators can occur inside their JSON string values.
        lines = [line.decode("utf-8") for line in raw.splitlines()]
    except UnicodeError:
        report.add("receipt_encoding", receipt, 1, "Текст разбора повреждён или сохранён вне UTF-8.")
        return
    bad_line = _header(lines, receipt.stem)
    if bad_line:
        report.add("receipt_header", receipt, bad_line, "Сверьте шапку разбора и ID источника с именем файла.")
        return
    try:
        remainder = inbox.manual_remainder(raw)
        if remainder and original:
            location = next(i for i, line in enumerate(lines, 1) if line.startswith(inbox._REMAINDER))
            # The marker is sorted. Existence of its last line covers all previous coordinates.
            check_quotes(original, {remainder["lines"][-1]: [("", receipt, location)]}, report, "receipt_remaining")
    except ValueError:
        location = next((i for i, line in enumerate(lines, 1) if line.startswith(inbox._REMAINDER)), 1)
        report.add("receipt_remaining", receipt, location, "Проверьте отметку оставшихся строк ручного разбора.")
    markers = []
    has_fact = False
    for number, row in enumerate(lines, 1):
        destination = _DESTINATION.fullmatch(row)
        if destination:
            report.destination(receipt, number, destination[1], unquote(destination[2]))
            if destination[1] == "fact":
                has_fact = True
        elif row.startswith(batch._MARKER):
            markers.append((number, row))
    if not markers:
        if not has_fact:
            report.add("receipt_empty", receipt, 1,
                       "В разборе отсутствуют метки фактов и журнал обработки. Сверьте квитанцию с оригиналом.")
        return
    try:
        jobs, _ = batch._journal(raw, receipt.stem)
    except (ValueError, RecursionError):
        report.add("receipt_journal", receipt, _broken_event(markers, receipt.stem),
                   "Нарушена последовательность или структура служебных строк разбора.")
        return
    requested = defaultdict(list)
    for number, row in markers:
        event = people._markup(row, batch._MARKER)
        if event["op"] == "step":
            step = event["result"]
            kind = step["kind"]
            target = "project" if kind in batch._PROJECT_ACTIONS else "person" if kind == "person" else None
            if target:
                identifier = step.get(target)
                if people._single_line(identifier):
                    report.destination(receipt, number, target, identifier)
                else:
                    report.add("receipt_destination", receipt, number, "В применённом действии отсутствует ID адресата.")
        elif event["op"] == "begin":
            if not jobs[event["job"]]["done"]:
                report.add("receipt_unfinished", receipt, number,
                           "Пакетный разбор прерван. Продолжите обработку входящих, чтобы завершить его.")
            if original and event["source_path"] != original.relative_to(root).as_posix():
                report.add("receipt_source_path", receipt, number, "Путь оригинала в разборе расходится с архивом.")
            for action in event["actions"]:
                line, quote = action["line"], action["quote"]
                if line == 0 and quote == "" and action["kind"] in ("triage", "context", "transcript"):
                    continue  # Unreadable materials and binary attachments carry no source line.
                if line < 1 or not quote.strip():
                    report.add("receipt_quote", receipt, number, "Для действия отсутствуют строка или цитата оригинала.")
                else:
                    requested[line].append((quote, receipt, number))
    if original and requested:
        check_quotes(original, requested, report, "receipt_quote")


def check_sources(root, report, *, source_id=None):
    root = Path(root).resolve()
    if source_id is not None and (not isinstance(source_id, str) or not _DIGEST.fullmatch(source_id)):
        raise ValueError("Некорректный ID оригинала.")
    entries = _entries(root, "Originals", report, source_id)
    archives = defaultdict(list)
    verified = {}
    for path in entries or ():
        if not inbox._ARCHIVE_SUFFIX.fullmatch(path.suffix):
            continue
        digest = path.stem
        archives[digest].append(path)
        try:
            if path.is_symlink():
                raise OSError()
            actual, _ = inbox._file_digest(path)
        except (ValueError, OSError):
            report.unreadable("source_unreadable", path, 0, "Не удалось прочесть обычный файл оригинала.")
            continue
        if actual != digest:
            report.add("source_digest", path, 0, "SHA-256 содержимого расходится с ID оригинала.")
        else:
            verified[digest] = path
    for digest, paths in archives.items():
        if len(paths) > 1:
            report.add("source_ambiguous", paths[0], 0, f"Одному ID соответствуют оригиналы в {len(paths)} файлах.")
            verified.pop(digest, None)
    processed = _entries(root, "Processed", report, source_id)
    receipts = [p for p in processed or () if p.suffix == ".md"]
    if source_id and processed is not None and not receipts:
        expected = root / "Sources/Processed" / (source_id + ".md")
        report.add("receipt_missing", expected, 0, "Для завершённого разбора отсутствует ожидаемая квитанция.")
    for receipt in receipts:
        if entries is not None and receipt.stem not in archives:
            report.add("source_missing", receipt, 1, "Для этого разбора отсутствует архивный оригинал.")
        _check_receipt(root, receipt, verified.get(receipt.stem), report)
    return {"receipts": len(receipts), "originals": sum(map(len, archives.values()))}
