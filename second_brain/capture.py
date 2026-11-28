"""Save a new text capture or an unchanged attachment in the selected Inbox."""

from hashlib import sha256
from itertools import count
import os
from pathlib import Path, PureWindowsPath

from . import storage


MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024


class CaptureTooLarge(ValueError):
    """The submitted body exceeds the capture limit."""


class Capture:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def save_text(self, text, title=None):
        if not isinstance(text, str) or not text.strip() or "\0" in text:
            raise ValueError("Введите непустой текст без нулевых символов.")
        if title is not None and not isinstance(title, str):
            raise ValueError("Название заметки должно быть текстом.")
        data = text.encode("utf-8")
        if len(data) > MAX_TEXT_BYTES:
            raise CaptureTooLarge("Текст превышает 1 МиБ. Сохраните его как файл и добавьте во входящие.")
        name = (title or "").strip() or "Заметка"
        if name.lower().endswith(".md"):
            name = name[:-3]
        return self._save(storage._filename(name or "Заметка"), ".md", data)

    def save_file(self, name, data):
        if not isinstance(data, bytes):
            raise ValueError("Передайте содержимое файла.")
        if len(data) > MAX_FILE_BYTES:
            raise CaptureTooLarge("Размер файла превышает 32 МиБ.")
        if (not isinstance(name, str) or not name.strip(" .") or "/" in name or "\\" in name
                or "\0" in name or PureWindowsPath(name).drive):
            raise ValueError("Укажите имя файла без пути.")
        visible_name = name.strip(" .")
        suffix = Path(visible_name).suffix
        stem = visible_name[:-len(suffix)] if suffix else visible_name
        # Use the vault's filename policy on both parts, so shortening a long
        # basename still preserves the format. The prefix keeps a literal dot.
        extension = storage._filename("x" + suffix)[1:].encode()[:64].decode(errors="ignore")
        return self._save(storage._filename(stem), extension, data)

    def _save(self, stem, suffix, data):
        folder = storage._inside(self.root, "Inbox")
        if not folder.is_dir():
            raise ValueError("Сначала подготовьте Inbox хранилища командой init.")
        from .inbox import Inbox
        from .inbox_activity import record_arrival
        from .inbox_archive import completed_original
        digest = sha256(data).hexdigest()
        with storage._write_lock(self.root):
            try:
                reviewed = completed_original(Inbox(self.root), digest)
            except (OSError, ValueError):
                # Keep the uploaded bytes when the old receipt or original needs repair.
                reviewed = None
            if reviewed:
                return {"path": reviewed.relative_to(self.root).as_posix(), "size": len(data),
                        "revision": digest, "alreadyProcessed": True}
            with storage._staged(folder / stem, data) as staged:
                for number in count(1):
                    name = (stem if number == 1 else f"{stem} ({number})") + suffix
                    target = storage._inside(self.root, "Inbox") / name
                    try:
                        # Publish the complete file at a fresh name in one step.
                        os.link(staged, target)
                    except FileExistsError:
                        continue
                    relative = target.relative_to(self.root).as_posix()
                    try:
                        record_arrival(self.root, relative, digest)
                    except Exception:
                        if target.is_file() and not target.is_symlink() and target.samefile(staged):
                            target.unlink()
                        raise
                    return {"path": relative, "size": len(data), "revision": digest}
