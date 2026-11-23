from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from second_brain import capture, storage


class CaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.root = storage.initialize(self.parent / "Vault")
        self.service = capture.Capture(self.root)

    def test_text_and_attachments_preserve_source_bytes_and_revision(self):
        source = "\n  Орбита: решение по входу.  \r\n\r\n"
        image = b"\x89PNG\r\n\x1a\n\xff\x00\xaa"
        pdf = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
        for receipt, name, data in (
            (self.service.save_text(source, "Планёрка ИТ.md"), "Планёрка ИТ.md", source.encode()),
            (self.service.save_file("Семейный чат.png", image), "Семейный чат.png", image),
            (self.service.save_file("Проба № 3.pdf", pdf), "Проба № 3.pdf", pdf),
            (self.service.save_file("README", b"one\r\n"), "README", b"one\r\n"),
        ):
            self.assertEqual(receipt, {"path": "Inbox/" + name, "size": len(data),
                                       "revision": sha256(data).hexdigest()})
            self.assertEqual((self.root / receipt["path"]).read_bytes(), data)

    def test_concurrent_new_names_preserve_existing_file_and_each_upload(self):
        original = self.root / "Inbox/Чат.png"
        original.write_bytes(b"existing")
        payloads = [bytes([i]) for i in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(lambda data: self.service.save_file("Чат.png", data), payloads))
        self.assertEqual(original.read_bytes(), b"existing")
        self.assertEqual({r["path"] for r in receipts}, {f"Inbox/Чат ({i}).png" for i in range(2, 10)})
        for receipt, data in zip(receipts, payloads):
            self.assertEqual((self.root / receipt["path"]).read_bytes(), data)
        duplicate = self.service.save_file("Чат.png", payloads[0])
        self.assertEqual(duplicate["path"], "Inbox/Чат (10).png")
        self.assertEqual(self.service.save_text("раз")["path"], "Inbox/Заметка.md")
        self.assertEqual(self.service.save_text("два", "")["path"], "Inbox/Заметка (2).md")
        self.assertFalse(any(p.name.startswith(".brain-") for p in original.parent.iterdir()))

    def test_invalid_names_symlinks_and_filename_portability(self):
        for name in ("", ".", "..", "/etc/passwd", "../source", "sub/x", r"..\x", r"C:\x", "C:some", "x\0y"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.service.save_file(name, b"source")
        self.assertEqual(self.service.save_file("CON.txt", b"safe")["path"], "Inbox/_CON.txt")
        self.assertEqual(self.service.save_file(".чат.png", b"safe")["path"], "Inbox/чат.png")
        long_name = "переписка" * 60 + ".webp"
        saved = self.service.save_file(long_name, b"contents")["path"]
        self.assertTrue(saved.endswith(".webp"))
        self.assertLessEqual(len(Path(saved).name.encode()), 255)
        target = self.parent / "original.txt"
        target.write_bytes(b"original")
        inbox = self.root / "Inbox"
        (inbox / "original.txt").symlink_to(target)
        receipt = self.service.save_file("original.txt", b"upload")
        self.assertEqual(receipt["path"], "Inbox/original (2).txt")
        self.assertEqual(target.read_bytes(), b"original")
        inbox.rename(self.root / "Moved")
        inbox.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.service.save_file("outside.txt", b"upload")
        self.assertFalse((self.parent / "outside.txt").exists())

    def test_byte_limits_and_failed_publication(self):
        with patch.object(capture, "MAX_TEXT_BYTES", 4), patch.object(capture, "MAX_FILE_BYTES", 4):
            self.assertEqual(self.service.save_text("аб")["size"], 4)
            self.assertEqual(self.service.save_file("ok", b"1234")["size"], 4)
            with self.assertRaises(capture.CaptureTooLarge):
                self.service.save_text("абв")
            with self.assertRaises(capture.CaptureTooLarge):
                self.service.save_file("large", b"12345")
        for text in ("", "\r\n ", "\0", None):
            with self.assertRaises(ValueError):
                self.service.save_text(text)
        empty = self.service.save_file("empty", b"")
        self.assertEqual(empty["size"], 0)
        self.assertEqual((self.root / empty["path"]).read_bytes(), b"")
        with patch.object(capture.os, "link", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.service.save_file("failed", b"payload")
        self.assertEqual({p.name for p in (self.root / "Inbox").iterdir()}, {"Заметка.md", "ok", "empty"})
