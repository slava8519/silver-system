import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from second_brain import demo_cleanup as cleanup, storage
from second_brain.examples import create_examples
from second_brain.search import search


class DemoCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "vault"

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_ordinary_vault_stays_untouched(self):
        storage.initialize(self.root)
        self.write("Notes/Своё.md", "[[Пример]]")
        before = self.snapshot()
        with patch.object(cleanup, "_walk", side_effect=AssertionError("полный обход")):
            self.assertFalse(cleanup.preview(self.root)["available"])
            self.assertFalse(cleanup.is_cleaned(self.root))
        self.assertEqual(before, self.snapshot())

    def test_card_archive_home_index_and_repeated_apply(self):
        create_examples(self.root)
        plan = cleanup.preview(self.root)
        before = self.snapshot()
        manifest = json.loads(before[".second-brain-demo/seed.json"])["files"]
        self.assertEqual(plan["removeCount"], len(manifest))
        self.assertEqual(plan["keepCount"], 0)
        self.assertNotIn("Start-Here.md", manifest)
        self.assertTrue(search(self.root, "кедр"))
        result = cleanup.apply(self.root, plan["revision"])
        self.assertTrue(result["cleaned"])
        self.assertTrue(cleanup.is_cleaned(self.root))
        self.assertEqual(cleanup.preview(self.root), result)
        with zipfile.ZipFile(self.root / result["archive"]) as archive:
            self.assertEqual(sorted(archive.namelist()), sorted(manifest))
            for name in manifest:
                self.assertEqual(archive.read(name), before[name])
                if name != "Home.md":
                    self.assertFalse((self.root / name).exists())
        self.assertEqual(list((self.root / "Archive").rglob("*.md")), [])
        self.assertEqual((self.root / "Home.md").read_text(), storage.START_PAGES["Home.md"])
        self.assertEqual((self.root / "Start-Here.md").read_bytes(), before["Start-Here.md"])
        self.assertEqual(search(self.root, "кедр"), [])
        (self.root / "Home.md").unlink()
        self.write("Notes/Своя.md", "после очистки")
        self.assertEqual(cleanup.apply(self.root, plan["revision"]), result)
        self.assertFalse((self.root / "Home.md").exists())
        self.assertEqual((self.root / "Notes/Своя.md").read_text(), "после очистки")

    def test_keep_edits_moved_records_and_transitive_owner_links(self):
        storage.initialize(self.root)
        direction = storage.create(self.root, "direction", "Маяк")
        area_id = storage.read_record(direction)["id"]
        project = storage.create(self.root, "project", "Переезд базы", direction=area_id)
        self.write("Tasks/Проверка.md", "[Проект](../Projects/Переезд%20базы.md)")
        self.write("Plans/Недельный.md", "[[Проверка#Пункты|задача]]")
        moved = storage.create(self.root, "note", "Перенос", direction=area_id)
        edited = self.write("Notes/Пример.md", "исходная")
        unrelated = self.write("Notes/Другая.md", "исходная")
        gone = self.write("Notes/Удалена.md", "давно исчезла")
        self.write("Settings/Intake/" + area_id + ".md", "правило")
        cleanup.record_seed(self.root)
        moved = moved.rename(self.root / "Notes/Моя новая.md")
        with moved.open("a") as output:
            output.write("Совсем новое содержание")
        edited.write_text("личная правка")
        gone.unlink()
        personal = self.write("Notes/Моя.md", "[[Недельный]]")
        control = self.write("Settings/Demo/catalog.json", json.dumps({"path": "Notes/Другая.md"}, ensure_ascii=False))
        plan = cleanup.preview(self.root)
        self.assertEqual((plan["removeCount"], plan["keepCount"]), (1, 7))
        result = cleanup.apply(self.root, plan["revision"])
        self.assertFalse(unrelated.exists())
        with zipfile.ZipFile(self.root / result["archive"]) as archive:
            self.assertEqual(archive.namelist(), ["Notes/Другая.md"])
        self.assertTrue(all(p.exists() for p in [direction, project, moved, edited, personal, control]))
        self.assertEqual(edited.read_text(), "личная правка")
        self.assertFalse(gone.exists())

    def test_edited_home_and_publication_roots(self):
        create_examples(self.root)
        home = self.root / "Home.md"
        home.write_text(home.read_text() + "Мои дополнительные разделы.")
        plan = cleanup.preview(self.root)
        self.assertEqual(plan["removeCount"], 0)
        self.assertGreater(plan["keepCount"], 1)
        other = Path(self.temp.name) / "copy"
        shutil.copytree(self.root, other)
        with self.assertRaises(storage.ConflictError):
            cleanup.apply(other, plan["revision"])
        self.write("Notes/Моя.md", "Новая запись с момента просмотра.")
        with self.assertRaises(storage.ConflictError):
            cleanup.apply(self.root, plan["revision"])
        plan = cleanup.preview(self.root)
        cleanup.apply(self.root, plan["revision"])
        self.assertTrue(home.read_text().endswith("Мои дополнительные разделы."))

    def test_crash_recovery_and_concurrent_reference(self):
        create_examples(self.root)
        old = cleanup.preview(self.root)
        manifest = json.loads((self.root / ".second-brain-demo/seed.json").read_bytes())["files"]
        withdraw = cleanup._withdraw
        calls = []
        def stop_after_first(root, revision, name, digest):
            withdraw(root, revision, name, digest)
            calls.append(name)
            raise KeyboardInterrupt()
        with patch.object(cleanup, "_withdraw", side_effect=stop_after_first):
            with self.assertRaises(KeyboardInterrupt):
                cleanup.apply(self.root, old["revision"])
        self.assertFalse((self.root / calls[0]).exists())
        self.assertEqual(cleanup.preview(self.root), old)
        self.write("Мой план.md", "[[" + calls[0] + "]]")
        with self.assertRaises(storage.ConflictError):
            cleanup.apply(self.root, old["revision"])
        for name, digest in manifest.items():
            self.assertEqual(hashlib.sha256((self.root / name).read_bytes()).hexdigest(), digest)

        current = cleanup.preview(self.root)
        def update_while_writing(root, revision, name, digest):
            withdraw(root, revision, name, digest)
            self.write("Ещё запись.md", "[[" + name + "]]")
        with patch.object(cleanup, "_withdraw", side_effect=update_while_writing):
            with self.assertRaises(storage.ConflictError):
                cleanup.apply(self.root, current["revision"])
        self.assertFalse(cleanup.is_cleaned(self.root))
        self.assertTrue(all((self.root / name).exists() for name in manifest))
        final = cleanup.preview(self.root)
        self.assertGreater(final["keepCount"], current["keepCount"])
        self.assertTrue(cleanup.apply(self.root, final["revision"])["cleaned"])
        self.assertTrue((self.root / calls[0]).exists())

    def test_personal_inbox_duplicate_protects_its_original_and_receipt(self):
        storage.initialize(self.root)
        raw = b"sample attachment"
        source_id = hashlib.sha256(raw).hexdigest()
        incoming = self.root / "Inbox" / "Example.bin"
        incoming.write_bytes(raw)
        original = self.root / "Sources" / "Originals" / (source_id + ".bin")
        original.write_bytes(raw)
        self.write("Sources/Processed/" + source_id + ".md", 'inbox_path: "Inbox/Example.bin"')
        self.write("Notes/Разовая.md", "учебная")
        cleanup.record_seed(self.root)
        mine = self.root / "Inbox" / "My attachment.bin"
        mine.write_bytes(raw)
        plan = cleanup.preview(self.root)
        self.assertEqual((plan["removeCount"], plan["keepCount"]), (1, 3))
        result = cleanup.apply(self.root, plan["revision"])
        self.assertEqual(mine.read_bytes(), original.read_bytes())
        with zipfile.ZipFile(self.root / result["archive"]) as archive:
            self.assertEqual(archive.namelist(), ["Notes/Разовая.md"])

    def test_interrupted_housekeeping_does_not_restore_committed_records(self):
        create_examples(self.root)
        plan = cleanup.preview(self.root)
        with patch.object(cleanup, "_finish", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                cleanup.apply(self.root, plan["revision"])
        self.assertTrue(cleanup.is_cleaned(self.root))
        self.assertTrue((self.root / ".second-brain-demo/pending.json").exists())
        self.assertTrue(cleanup.apply(self.root, plan["revision"])["cleaned"])
        self.assertFalse((self.root / ".second-brain-demo/pending.json").exists())
        self.assertFalse((self.root / ".second-brain-demo/withdrawn").exists())
        self.assertEqual(list(storage.iter_records(self.root)), [])

    def test_existing_zip_must_contain_only_verified_originals(self):
        create_examples(self.root)
        plan = cleanup.preview(self.root)
        archive = self.root / "Archive" / "DemoCleanup" / (plan["revision"] + ".zip")
        archive.parent.mkdir()
        with zipfile.ZipFile(archive, "w") as content:
            content.writestr("Notes/Своё.md", "личное")
        with self.assertRaises(storage.ConflictError):
            cleanup.apply(self.root, plan["revision"])
        self.assertTrue((self.root / "Notes/Проба № 3.md").exists())
        with zipfile.ZipFile(archive) as content:
            self.assertEqual(content.namelist(), ["Notes/Своё.md"])
        self.assertFalse(cleanup.is_cleaned(self.root))

    def test_malformed_manifest_and_symlinks_cannot_reach_outside_vault(self):
        create_examples(self.root)
        seed = self.root / ".second-brain-demo/seed.json"
        original = seed.read_bytes()
        obj = json.loads(original)
        obj["files"]["Notes/../../outside"] = "a" * 64
        seed.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):
            cleanup.preview(self.root)
        seed.write_bytes(original)
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        sample = self.root / "Notes/Проба № 3.md"
        sample.unlink()
        sample.symlink_to(outside)
        user_link = self.root / "Ссылка"
        target = self.root / "Projects"
        user_link.symlink_to(target)
        plan = cleanup.preview(self.root)
        self.assertGreater(plan["keepCount"], 1)
        (self.root / "Archive/DemoCleanup").symlink_to(outside)
        with self.assertRaises(ValueError):
            cleanup.apply(self.root, plan["revision"])
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue(sample.is_symlink())
        self.assertTrue(list(target.glob("*.md")))
