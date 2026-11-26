from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import uuid

from examples import gantt_vault, inbox_vault, okr_vault, planning_vault, working_vault
from second_brain import legacy_examples, storage
from second_brain.demo import DemoVault
from second_brain.examples import create_examples


class DemoSeedTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "Vault"

    def test_each_generator_registers_its_final_state(self):
        variants = [
            (create_examples, "Notes/Памятка для поездок.md", {}),
            (working_vault.generate, "People/Дарья Миронова.md", {}),
            (planning_vault.generate, "Plans/Запуск Маяка.md", {}),
            (gantt_vault.generate, "Notes/Окно проверки на стенде.md", {}),
            (okr_vault.generate, "Plans/OKR/Время восстановления базы.md", {}),
            (inbox_vault.generate, "Inbox/04 — Без названия.txt", {}),
            (inbox_vault.generate, "Settings/Owner.md", {"with_teams": True}),
        ]
        for index, (generator, sample, options) in enumerate(variants):
            destination = self.root / str(index)
            with self.subTest(generator=generator, options=options):
                generator(destination, **options)
                captured = json.loads((destination / ".second-brain-demo/seed.json").read_text())["files"]
                for relative in ("Home.md", sample):
                    self.assertEqual(captured[relative], sha256((destination / relative).read_bytes()).hexdigest())
                self.assertNotIn("Start-Here.md", captured)

    def test_old_okr_fixture_recovers_complete_originals_and_preserves_edits(self):
        okr_vault.generate(self.root, legacy=True)
        (self.root / ".second-brain-demo/seed.json").unlink()
        entries = legacy_examples.legacy_seed(self.root)
        paths = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*.md") if p.name != "Start-Here.md"}
        self.assertEqual(set(entries), paths)
        for path, digest in entries.items():
            self.assertEqual(digest, sha256((self.root / path).read_bytes()).hexdigest(), path)
        changed = "Plans/OKR/Время восстановления базы.md"
        with (self.root / changed).open("ab") as stream:
            stream.write("\nПримечание участника учений.\n".encode())
        own = storage.create(self.root, "note", "Моя запись", "Своя тема")
        recovered = legacy_examples.legacy_seed(self.root)
        self.assertEqual(entries, recovered)
        self.assertNotEqual(recovered[changed], sha256((self.root / changed).read_bytes()).hexdigest())
        self.assertNotIn(own.relative_to(self.root).as_posix(), recovered)

    def test_old_catalog_evidence_survives_custom_home_and_excludes_a_replacement(self):
        demo = DemoVault(self.root)
        (self.root / ".second-brain-demo/seed.json").unlink()
        # First-generation vaults placed id before title in the YAML header.
        for card, _ in storage.iter_records(self.root):
            header, body = card.read_bytes()[4:].split(b"\n---\n", 1)
            rows = header.splitlines()
            identifier = next(row for row in rows if row.startswith(b"id: "))
            rows.remove(identifier)
            rows.insert(2, identifier)
            card.write_bytes(b"---\n" + b"\n".join(rows) + b"\n---\n" + body)
        demo.add_direction({"title": "Хор", "noteTitle": "Репетиция", "body": "В четверг", "requestId": "choir"})
        (self.root / "Home.md").write_text("# Личная домашняя страница\n", encoding="utf-8")
        replaced = "Notes/Памятка для поездок.md"
        path = self.root / replaced
        identifier = storage.read_record(path)["id"]
        path.write_bytes(path.read_bytes().replace(identifier.encode(), str(uuid.uuid4()).encode()))
        entries = legacy_examples.legacy_seed(self.root)
        self.assertNotEqual(entries[replaced], sha256(path.read_bytes()).hexdigest())
        self.assertNotEqual(entries["Home.md"], sha256((self.root / "Home.md").read_bytes()).hexdigest())
        self.assertNotIn("Notes/Репетиция.md", entries)
        self.assertNotIn("Directions/Хор.md", entries)
        self.assertNotIn("Settings/Demo/catalog.json", entries)
        unchanged = "Projects/Переезд базы заказов.md"
        self.assertEqual(entries[unchanged], sha256((self.root / unchanged).read_bytes()).hexdigest())
        # An unrelated catalog cannot make an ordinary vault eligible.
        catalog = self.root / "Settings/Demo/catalog.json"
        catalog.write_text(json.dumps({"vaultId": str(uuid.uuid4()), "records": {"x": str(uuid.uuid4())}}))
        self.assertIsNone(legacy_examples.legacy_seed(self.root))

    def test_ordinary_vault_or_matching_names_do_not_establish_demo_provenance(self):
        storage.initialize(self.root)
        storage.create(self.root, "direction", "Личное", "Мои личные правила")
        storage.create(self.root, "note", "Памятка для поездок", "Мои личные правила")
        with patch.object(legacy_examples, "_template", side_effect=AssertionError):
            self.assertIsNone(legacy_examples.legacy_seed(self.root))
        other = self.root.parent / "Reference"
        create_examples(other)
        (self.root / "Home.md").write_bytes((other / "Home.md").read_bytes())
        self.assertIsNone(legacy_examples.legacy_seed(self.root))
