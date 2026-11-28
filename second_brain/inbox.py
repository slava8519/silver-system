"""Import confirmed passages from one inbox document into a person's record."""

import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat

from . import people, storage
from .storage import id_marker as _receipt_marker, markdown_label as _label, markdown_link as _link


EXTENSIONS = {".md", ".txt"}
_ARCHIVE_SUFFIX = re.compile(r"\.[a-z0-9]{1,16}\Z")
_REMAINDER = "<!-- inbox-remaining: "
_HEADING = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
_TRANSCRIPT_HEADING = re.compile(
    r"# Расшифровка \[исходного вложения\]\(\.\./\.\./Sources/Originals/([0-9a-f]{64})\.[a-z0-9]{1,16}\)")
KINDS = {"role": "Роль", "fact": "Факт", "observation": "Наблюдение"}
VISIBILITY = {"shared": "Общее", "personal": "Личное"}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _file_digest(path, destination=None):
    digest = hashlib.sha256()
    size = 0
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as reader:
        if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
            raise ValueError("В Inbox ожидается обычный файл.")
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
            if destination is not None:
                destination.write(chunk)
    return digest.hexdigest(), size


def _lines(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Для разбора нужен текстовый файл в UTF-8.") from error
    if "\0" in text:
        raise ValueError("Во входящем файле обнаружены двоичные данные.")
    return text.splitlines()


def review_lines(lines):
    has_body = any(line.strip() and not _HEADING.match(line) for line in lines)
    return {n for n, line in enumerate(lines, 1) if line.strip() and not (has_body and _HEADING.match(line))}


def manual_remainder(raw):
    """Read the optional line selection; older manual receipts are complete."""
    if not raw or _REMAINDER.encode() not in raw:
        return None
    from .batch import _MARKER, _stored_path
    markers = [row.decode() for row in raw.splitlines() if row.startswith(_REMAINDER.encode())]
    if not markers:
        return None
    try:
        marker, = markers
        state = people._markup(marker, _REMAINDER)
        if (any(row.startswith(_MARKER.encode()) for row in raw.splitlines())
                or not _stored_path(state["path"], "Inbox") or Path(state["path"]).suffix.lower() not in EXTENSIONS
                or not isinstance(state["lines"], list) or not state["lines"]
                or any(type(n) is not int or n < 1 for n in state["lines"])
                or sorted(set(state["lines"])) != state["lines"]):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise storage.ConflictError("Проверьте отметку оставшихся строк в квитанции разбора.") from error
    return state


def without_remainder(text):
    return "".join(line for line in text.splitlines(keepends=True) if not line.startswith(_REMAINDER))


def _page(lines, start, limit):
    if type(start) is not int or start < 1 or type(limit) is not int or not 1 <= limit <= 80:
        raise ValueError("Укажите первую строку от 1 и количество строк от 1 до 80.")
    if start > max(1, len(lines)):
        raise ValueError(f"В документе только {len(lines)} строк.")
    offset = start - 1
    return {"total": len(lines), "lines": [
        {"n": number, "text": text} for number, text in enumerate(lines[offset:offset + limit], start)
    ]}


class Inbox:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        for folder in ("Inbox", "Sources/Originals", "Sources/Processed"):
            if not storage._inside(self.root, folder).is_dir():
                raise ValueError("Сначала подготовьте хранилище командой init.")

    def _incoming(self, name, *, attachments=False, missing_ok=False):
        if not isinstance(name, str) or not name or "\0" in name:
            raise ValueError("Укажите путь к файлу в Inbox.")
        path = Path(name)
        if (path.is_absolute() or len(path.parts) < 2 or path.parts[0] != "Inbox"
                or any(part.startswith(".") for part in path.parts)):
            raise ValueError("Нужен относительный путь к файлу внутри Inbox.")
        if not attachments and path.suffix.lower() not in EXTENSIONS:
            raise ValueError("Для вложения откройте сам файл. Построчный просмотр доступен для Markdown и TXT.")
        file = storage._inside(self.root, path)
        if not file.is_file() and not (missing_ok and not file.exists()):
            raise FileNotFoundError("Входящий файл не найден.")
        return file

    def _receipt(self, source_id):
        return storage._inside(self.root, Path("Sources/Processed") / (source_id + ".md"))

    def _original(self, source_id, extension=None):
        if not isinstance(source_id, str) or not re.fullmatch("[0-9a-f]{64}", source_id):
            raise ValueError("Некорректный ID оригинала.")
        folder = storage._inside(self.root, "Sources/Originals")
        existing = []
        for entry in folder.glob(source_id + ".*"):
            if not _ARCHIVE_SUFFIX.fullmatch(entry.name[len(source_id):]):
                continue
            file = storage._inside(folder, entry.name)
            if not file.is_file():
                raise storage.ConflictError("Путь оригинала занят каталогом.")
            existing.append(file)
        if len(existing) > 1:
            raise storage.ConflictError("У источника несколько файлов с одним ID.")
        if existing:
            return existing[0]
        if extension is None:
            raise FileNotFoundError("Оригинал источника не найден.")
        suffix = extension if _ARCHIVE_SUFFIX.fullmatch(extension) else ".bin"
        return storage._inside(folder, source_id + suffix)

    def _archive_attachment(self, incoming, revision):
        """Publish the exact version read by the reviewer under the caller's write lock."""
        target = self._original(revision, incoming.suffix.lower())
        if target.exists():
            if _file_digest(incoming)[0] != revision or _file_digest(target)[0] != revision:
                raise storage.ConflictError("Вложение или его оригинал изменились. Сверьте сохранённую версию.")
            return target
        with storage._staged(target, b"") as staged:
            with staged.open("wb") as output:
                digest, _ = _file_digest(incoming, output)
                output.flush()
                os.fsync(output.fileno())
            if digest != revision:
                raise storage.ConflictError("Вложение изменилось. Откройте новую версию и повторите разбор.")
            os.link(staged, target)
        return target

    def attachment(self, source_id):
        """Return the archived bytes for an explicit owner download."""
        path = self._original(source_id)
        if path.suffix in EXTENSIONS:
            raise ValueError("Укажите ID архивного вложения.")
        raw = path.read_bytes()
        if _digest(raw) != source_id:
            raise storage.ConflictError("Содержимое вложения не совпадает с сохранённой версией.")
        return path, raw

    def _files(self):
        for parent, dirs, files in os.walk(storage._inside(self.root, "Inbox")):
            parent = Path(parent)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (parent / d).is_symlink())
            for name in sorted(files):
                file = parent / name
                if name.startswith(".") or file.is_symlink() or not file.is_file():
                    continue
                yield file

    def _review_status(self, digest):
        receipt = self._receipt(digest)
        if not receipt.is_file():
            return {"processed": False}
        try:
            queued = manual_remainder(receipt.read_bytes())
        except (ValueError, OSError):
            queued = None  # The owner can still open the incoming text; integrity checks explain the receipt.
        return {"processed": True, **({"remainingQueued": True} if queued else {})}

    def list_items(self):
        result = []
        for file in self._files():
            try:
                digest, size = _file_digest(file)
            except FileNotFoundError:
                continue
            result.append({"path": file.relative_to(self.root).as_posix(), "size": size,
                           **self._review_status(digest),
                           **({"attachment": True} if file.suffix.lower() not in EXTENSIONS else {})})
        return result

    def preview(self, path, start=1, limit=30):
        file = self._incoming(path)
        data = file.read_bytes()
        revision = _digest(data)
        return {"path": file.relative_to(self.root).as_posix(), "revision": revision,
                **self._review_status(revision), **_page(_lines(data), start, limit)}

    def _facts(self, items, lines):
        if not isinstance(items, list) or not items:
            raise ValueError("Выберите хотя бы один подтверждённый фрагмент.")
        fields = {"kind", "visibility", "line", "text", "direction"}
        directions = {}
        facts = {}
        for item in items:
            if not isinstance(item, dict) or item.keys() != fields:
                raise ValueError("Каждому фрагменту нужны вид, видимость, направление, строка и текст.")
            kind, scope, line = item["kind"], item["visibility"], item["line"]
            if not isinstance(kind, str) or kind not in KINDS or not isinstance(scope, str) or scope not in VISIBILITY:
                raise ValueError("Выберите вид и область видимости фрагмента.")
            if type(line) is not int or not 1 <= line <= len(lines):
                raise ValueError("Укажите существующую строку входящего.")
            text = item["text"]
            if (not isinstance(text, str) or not text.strip() or re.search(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]", text)
                    or text not in lines[line - 1]):
                raise ValueError(f"Фрагмент должен дословно входить в строку {line}.")
            direction = item["direction"]
            if not isinstance(direction, str) or not direction:
                raise ValueError("Укажите ID направления для каждого фрагмента.")
            if direction not in directions:
                path = storage.find_record(self.root, direction)
                record = storage.read_record(path)
                if record["type"] != "direction":
                    raise ValueError("Связь фрагмента должна вести к направлению.")
                directions[direction] = (record["title"], path)
            key = (kind, direction, line, text)
            if key in facts and facts[key]["visibility"] != scope:
                raise ValueError("Один и тот же фрагмент указан с разной видимостью.")
            facts[key] = item
        return list(facts.values()), directions

    @staticmethod
    def _receipt_inbox_path(raw):
        for line in raw.splitlines()[1:]:
            if line == b"---":
                break
            if line.startswith(b"inbox_path:"):
                return storage._scalar(line.partition(b":")[2].decode())
        return None

    def _receipt_bytes(self, path, source_id):
        if not path.exists():
            return None
        data = path.read_bytes()
        with io.BytesIO(data) as stream:
            if stream.readline().rstrip(b"\r\n") != b"---":
                raise storage.ConflictError("У существующего разбора изменилась шапка.")
            ids = []
            for raw in stream:
                line = raw.rstrip(b"\r\n")
                if line == b"---":
                    break
                if line.startswith(b"source_id:"):
                    ids.append(storage._scalar(line.partition(b":")[2].decode("utf-8")))
            else:
                raise storage.ConflictError("У существующего разбора изменилась шапка.")
        if ids != [source_id]:
            raise storage.ConflictError("Существующий разбор указывает на другой источник.")
        return data

    def _save_receipt(self, path, previous, incoming, source, person, facts, directions, old_queue, new_queue):
        base = path.parent
        link_original = _link("Оригинал", self.root / source["path"], base)
        if previous is None:
            relative = incoming.relative_to(self.root).as_posix()
            initial = ("---\ntype: processed_source\nschema_version: 1\n"
                       f'source_id: "{source["id"]}"\ninbox_path: {json.dumps(relative, ensure_ascii=False)}\n'
                       f"---\n\n# Разбор входящего\n\n{link_original}\n\n## Подтверждённые фрагменты\n")
        else:
            initial = previous.decode("utf-8")
        # Each marker belongs to the original extraction; later corrections live on the person.
        existing = set(initial.splitlines())
        participant = _receipt_marker("person", person["id"])
        rows = [] if participant in existing else [participant]
        for fact in facts:
            marker = _receipt_marker("fact", people.fact_id(person["id"], source["id"], fact))
            if marker in existing:
                continue
            name_link = _link(person["name"], self.root / person["path"], base)
            direction = _link(*directions[fact["direction"]], base)
            row = (f'- {name_link} · {KINDS[fact["kind"]]} · {direction} · {VISIBILITY[fact["visibility"]]}'
                   f' · строка {fact["line"]} — {_label(fact["text"])}')
            rows.extend([marker, row])
        if old_queue != new_queue:
            if old_queue:
                initial = without_remainder(initial)
            if new_queue:
                rows.append(people._marker(_REMAINDER, new_queue))
        if not rows and previous is not None and old_queue == new_queue:
            return
        newline = "\r\n" if initial.startswith("---\r\n") else "\n"
        prefix = initial if initial.endswith(("\n", "\r")) else initial + newline
        content = (prefix + newline + newline.join(rows) + newline).encode("utf-8")
        with storage._staged(path, content) as staged:
            if previous is None:
                os.link(staged, path)
            else:
                if path.is_symlink() or path.read_bytes() != previous:
                    raise storage.ConflictError("Разбор изменился во время записи. Повторите обработку.")
                staged.chmod(stat.S_IMODE(path.stat().st_mode))
                os.replace(staged, path)

    def process(self, data):
        from .inbox_archive import finish_review
        required = {"path", "revision", "person", "facts"}
        if (not isinstance(data, dict) or not required <= data.keys() <= required | {"remaining"}
                or data.get("remaining", "finish") not in ("queue", "finish")):
            raise ValueError("Передайте входящий файл, его прочитанную версию, участника и фрагменты.")
        with storage._write_lock(self.root):
            incoming = self._incoming(data["path"], missing_ok=True)
            if incoming.exists():
                raw = incoming.read_bytes()
            else:
                # A retry can arrive after the completed source has left Inbox.
                original = self._original(data["revision"])
                receipt = self._receipt_bytes(self._receipt(data["revision"]), data["revision"])
                if not receipt or data["path"] != self._receipt_inbox_path(receipt):
                    raise FileNotFoundError("Входящий файл не найден.")
                raw = original.read_bytes()
            digest = _digest(raw)
            if data["revision"] != digest:
                raise storage.ConflictError("Входящий изменился. Перечитайте файл и снова выберите строки.")
            result = self._process_person(incoming, raw, digest, data["person"], data["facts"],
                                          remaining=data.get("remaining", "finish"))
        if "remaining" not in data:
            result.pop("remainingQueued")
        return {**result, **finish_review(self, data["path"], digest)}

    def _process_person(self, incoming, raw, digest, choice, items, *, remaining=None):
        """Import one identity and its evidence under the caller's vault lock."""
        lines = _lines(raw)
        facts, directions = self._facts(items, lines)
        original = self._original(digest, incoming.suffix.lower())
        if original.exists() and original.read_bytes() != raw:
            raise storage.ConflictError("Содержимое сохранённого оригинала изменилось.")
        receipt = self._receipt(digest)
        previous = self._receipt_bytes(receipt, digest)
        old_queue = manual_remainder(previous)
        new_queue = old_queue
        if remaining == "finish":
            new_queue = None
        elif remaining == "queue" and (previous is None or old_queue):
            candidates = set(old_queue["lines"]) if old_queue else review_lines(lines)
            candidates.difference_update(f["line"] for f in facts)
            label = old_queue["path"] if old_queue else incoming.relative_to(self.root).as_posix()
            new_queue = {"path": label, "lines": sorted(candidates)} if candidates else None
        service = people.People(self.root)
        person_id = people.selected_id(choice, digest)
        completed = set(previous.decode("utf-8").splitlines()) if previous else set()
        proposed = (people.fact_id(person_id, digest, fact) for fact in facts)
        confirmed = {key for key in proposed if _receipt_marker("fact", key) in completed}
        # Older receipts contain only fact IDs. A matching fact still proves the
        # person once existed, including when the original choice was "create".
        known_person = _receipt_marker("person", person_id) in completed or bool(confirmed)
        try:
            person = service.resolve({"id": person_id} if known_person else choice, digest)
        except FileNotFoundError as error:
            if known_person:
                raise storage.ConflictError("Карточка участника прежнего разбора удалена. Сверьте выбор человека.") from error
            raise
        source = {"id": digest, "path": original.relative_to(self.root).as_posix()}
        if not original.exists():
            with storage._staged(original, raw) as staged:
                os.link(staged, original)
        service.apply(person, source, facts, confirmed_ids=confirmed)
        self._save_receipt(receipt, previous, incoming, source, person, facts, directions, old_queue, new_queue)
        return {"source": source, "processedPath": receipt.relative_to(self.root).as_posix(),
                "personId": person["id"], "person": {key: person[key] for key in ("id", "name", "path")},
                "facts": len(facts), "remainingQueued": bool(new_queue)}

    def remaining(self, source_id, start=1, limit=20):
        with storage._vault_lock(self.root, write=False):
            path, lines = self.original_text(source_id)
            if type(start) is not int or start < 1 or type(limit) is not int or not 1 <= limit <= 80:
                raise ValueError("Укажите положительный номер строки и размер страницы от 1 до 80.")
            queue = manual_remainder(self._receipt_bytes(self._receipt(source_id), source_id))
            if queue is None:
                raise storage.ConflictError("У этого материала нет оставшихся строк для разбора.")
            numbers = [n for n in queue["lines"] if n >= start]
            if numbers and numbers[-1] > len(lines):
                raise storage.ConflictError("Квитанция указывает на отсутствующую строку оригинала.")
            return {"path": path.relative_to(self.root).as_posix(), "revision": source_id, "total": len(queue["lines"]),
                    "lines": [{"n": n, "text": lines[n - 1]} for n in numbers[:limit]],
                    "nextStart": numbers[limit] if len(numbers) > limit else None}

    def original_text(self, source_id):
        """Return the original path and decoded lines after validating the archived bytes."""
        path = self._original(source_id)
        if path.suffix not in EXTENSIONS:
            raise ValueError("Для просмотра архивного вложения откройте исходный файл.")
        raw = path.read_bytes()
        if _digest(raw) != source_id:
            raise storage.ConflictError("Содержимое оригинала не совпадает с сохранённой версией.")
        return path, _lines(raw)

    def original(self, source_id, start=1, limit=30):
        """Explicit owner view of the archived document, including any personal passages."""
        path, lines = self.original_text(source_id)
        page = _page(lines, start, limit)
        attachment = _TRANSCRIPT_HEADING.fullmatch(lines[0]) if lines else None
        if attachment:
            page["attachmentId"] = attachment[1]
        return {"path": path.relative_to(self.root).as_posix(), "revision": source_id, **page}

    def evidence(self, source_id, line):
        original = self.original(source_id, start=line, limit=1)
        if not original["lines"]:
            raise ValueError("Укажите существующую строку оригинала.")
        return {"path": original["path"], "start": line, "end": line, "text": original["lines"][0]["text"]}

    def fact_evidence(self, person_id, fact_id, personal=False):
        card = people.People(self.root).detail(person_id, personal=personal)
        fact = next((item for item in card["facts"] if item["id"] == fact_id), None)
        if not fact:
            raise FileNotFoundError("Фрагмент в выбранной области не найден.")
        evidence = self.evidence(fact["sourceId"], fact["line"])
        if not fact["quote"] or fact["quote"] not in evidence["text"]:
            raise storage.ConflictError("Цитата не совпадает со строкой оригинала.")
        return {**evidence, "text": fact["quote"]}
