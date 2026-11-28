import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import install


REPO = Path(__file__).resolve().parents[1]
SKILL = ".agents/skills/brain-example/SKILL.md"
DOC = ".runtime/docs/sample.md"
EXTRA = ".runtime/docs/retired.md"


class PackageInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.vault = self.base / "My Vault"
        self.package = self.base / "Package"
        self.package.mkdir()
        self.payload = self.package / "payload"
        self.payload.mkdir()
        shutil.copyfile(REPO / "tools/install.py", self.package / "install.py")
        for source in (REPO / "second_brain").glob("*.py"):
            self.put(".runtime/second_brain/" + source.name, source.read_bytes())
        self.put(".runtime/examples/scenarios.json", (REPO / "examples/scenarios.json").read_bytes())
        for name in install.PLUGIN_FILES:
            self.put(install.PLUGIN + name, (REPO / "plugins/obsidian-gantt" / name).read_bytes())
        self.put(SKILL, b"official skill\n")
        self.put(DOC, b"version 1")
        self.put(EXTRA, b"retired")
        self.seal()

    def put(self, rel, value):
        path = self.payload / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    def seal(self):
        doc = {"schema_version": 1, "files": {
            p.relative_to(self.payload).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in self.payload.rglob("*") if p.is_file()
        }, "source_files": {DOC: hashlib.sha256(b"source version").hexdigest()}}
        (self.package / "manifest.json").write_text(json.dumps(doc), encoding="utf-8")
        return doc

    def command(self, *args, success=True):
        run = subprocess.run([sys.executable, str(self.package / "install.py"), str(self.vault), *map(str, args)],
                             cwd=self.base, capture_output=True, text=True, timeout=45)
        if success:
            self.assertEqual(run.returncode, 0, run.stderr)
            return json.loads(run.stdout)
        self.assertNotEqual(run.returncode, 0, run.stdout)
        return run.stderr

    def all_bytes(self):
        return {p.relative_to(self.vault): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}

    def test_fresh_install_repeat_and_existing_user_instructions(self):
        self.vault.mkdir()
        (self.vault / "AGENTS.md").write_bytes(b"# Existing\r\nOwner text.\r\n")
        (self.vault / "CLAUDE.md").write_bytes(b"# My instructions\n")
        output = self.command()
        self.assertEqual(output["vault"], str(self.vault))
        agent = (self.vault / "AGENTS.md").read_bytes()
        self.assertTrue(agent.startswith(b"# Existing\r\nOwner text.\r\n"))
        self.assertIn(b".agents/skills/", agent)
        self.assertEqual((self.vault / "CLAUDE.md").read_text().count("@AGENTS.md"), 1)
        self.assertEqual((self.vault / install.BASE).read_bytes(), install.json_bytes(self.seal()))
        settings = json.loads((self.vault / (install.PLUGIN + "data.json")).read_text())
        self.assertEqual(settings, {"programDir": str(self.vault / ".runtime"), "python": sys.executable})
        self.assertTrue((self.vault / "Home.md").is_file())
        self.assertTrue((self.vault / SKILL).is_file())
        self.assertFalse((self.vault / ".runtime/.agents").exists())
        before = self.all_bytes()
        self.assertEqual(self.command()["filesChanged"], 0)
        self.assertEqual(self.all_bytes(), before)
        result = subprocess.check_output([sys.executable, "-B", "-m", "second_brain", "--vault", "..",
                                          "overview", "--brief"], cwd=self.vault / ".runtime", text=True)
        self.assertEqual(json.loads(result)["counts"]["direction"], 0)

    def test_demo_is_idempotent_and_never_inserted_into_working_vault(self):
        self.assertTrue(self.command("--demo")["demoCreated"])
        self.assertEqual(len(list((self.vault / "Directions").glob("*.md"))), 3)
        self.assertFalse(self.command("--demo")["demoCreated"])
        self.assertEqual(self.command("--demo")["filesChanged"], 0)
        self.vault = self.base / "Working"
        self.command()
        before = self.all_bytes()
        self.assertIn("демо", self.command("--demo", success=False))
        self.assertEqual(self.all_bytes(), before)

    def test_update_preserves_user_files_removed_pages_settings_and_unknown_files(self):
        self.command()
        for name in (SKILL, "AGENTS.md", "CLAUDE.md", install.PLUGIN + "data.json", ".runtime/docs/private.md"):
            (self.vault / name).write_bytes(b"my edits")
        (self.vault / "Home.md").rename(self.vault / "Personal Home.md")
        (self.vault / "Start-Here.md").unlink()
        self.put(DOC, b"version 2")
        (self.payload / EXTRA).unlink()
        self.seal()
        self.command()
        self.assertFalse((self.vault / EXTRA).exists())
        self.assertEqual((self.vault / DOC).read_bytes(), b"version 2")
        self.assertFalse((self.vault / "Home.md").exists())
        self.assertFalse((self.vault / "Start-Here.md").exists())
        for name in (SKILL, "AGENTS.md", "CLAUDE.md", install.PLUGIN + "data.json", ".runtime/docs/private.md"):
            self.assertEqual((self.vault / name).read_bytes(), b"my edits")

    def test_conflict_detected_before_any_change_and_backup_cannot_override_known_edit(self):
        self.command()
        (self.vault / DOC).write_bytes(b"manual")
        before = self.all_bytes()
        self.put(DOC, b"upstream changed too")
        self.put(SKILL, b"new skill")
        self.seal()
        backup = self.base / "backup"
        self.assertIn(DOC, self.command("--backup-dir", backup, success=False))
        self.assertFalse(backup.exists())
        self.assertEqual(self.all_bytes(), before)

    def test_legacy_backup_and_migration_preserve_custom_skill_versions(self):
        self.command()
        (self.vault / install.BASE).unlink()
        (self.vault / DOC).write_bytes(b"previous docs")
        (self.vault / "AGENTS.md").write_bytes(b"Use ./brain and .agents/skills.")
        nested = self.vault / (".runtime/" + SKILL)
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"handwritten only in old runtime")
        (nested.parent / "personal.txt").write_bytes(b"owner reference")
        before = self.all_bytes()
        self.assertIn("--backup-dir", self.command(success=False))
        self.assertEqual(self.all_bytes(), before)
        backup = self.base / "external backup"
        self.command("--backup-dir", backup)
        self.assertEqual((backup / DOC).read_bytes(), b"previous docs")
        self.assertEqual((self.vault / DOC).read_bytes(), b"version 1")
        self.assertEqual((self.vault / SKILL).read_bytes(), b"handwritten only in old runtime")
        self.assertEqual((self.vault / SKILL).with_name("personal.txt").read_bytes(), b"owner reference")
        self.assertFalse((self.vault / ".runtime/.agents").exists())
        self.assertEqual((self.vault / "AGENTS.md").read_bytes(), b"Use ./brain and .agents/skills.")
        self.assertEqual(self.command()["filesChanged"], 0)

    def test_both_divergent_skill_copies_abort_and_leave_owner_data_untouched(self):
        self.command()
        (self.vault / install.BASE).unlink()
        nested = self.vault / (".runtime/" + SKILL)
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"runtime edit")
        (self.vault / SKILL).write_bytes(b"root edit")
        before = self.all_bytes()
        self.assertIn("две разные", self.command("--backup-dir", self.base / "back", success=False))
        self.assertEqual(self.all_bytes(), before)

    def test_identical_legacy_overrides_stay_active_even_with_a_backup(self):
        self.command()
        (self.vault / install.BASE).unlink()
        nested = self.vault / (".runtime/" + SKILL)
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"one manual skill")
        (self.vault / SKILL).write_bytes(nested.read_bytes())
        (self.vault / DOC).write_bytes(b"old program")
        backup = self.base / "backup"
        self.command("--backup-dir", backup)
        self.assertEqual((self.vault / SKILL).read_bytes(), b"one manual skill")
        self.assertFalse(nested.exists())
        self.assertEqual(self.command("--backup-dir", backup)["filesChanged"], 0)

    def test_independent_user_skill_moves_without_becoming_package_owned(self):
        self.command()
        (self.vault / install.BASE).unlink()
        name = ".agents/skills/my-notes/SKILL.md"
        root_skill = self.vault / name
        old_skill = self.vault / (".runtime/" + name)
        old_skill.parent.mkdir(parents=True)
        (old_skill.parent / "empty").mkdir()
        old_skill.write_bytes(b"owner skill")
        script = old_skill.parent / "run.sh"
        script.write_bytes(b"#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
        side = self.vault / ".runtime/.agents/owner.txt"
        side.write_bytes(b"unrelated file")
        root_skill.parent.mkdir(parents=True)
        root_skill.write_bytes(b"different version")
        before = self.all_bytes()
        self.assertIn("две разные", self.command(success=False))
        self.assertEqual(self.all_bytes(), before)
        root_skill.unlink()
        self.command()
        self.assertEqual(root_skill.read_bytes(), b"owner skill")
        if os.name != "nt":
            self.assertEqual((root_skill.parent / "run.sh").stat().st_mode & 0o777, 0o755)
        self.assertNotIn(name, json.loads((self.vault / install.BASE).read_text())["files"])
        self.assertFalse((self.vault / ".runtime/.agents/skills").exists())
        self.assertEqual(side.read_bytes(), b"unrelated file")
        root_skill.write_bytes(b"new owner edit")
        self.assertEqual(self.command()["filesChanged"], 0)
        self.assertEqual(root_skill.read_bytes(), b"new owner edit")

    def test_interrupted_legacy_install_reuses_verified_backup_and_preserves_override(self):
        class PowerLoss(BaseException):
            pass
        for stop in (install.BASE, ".runtime/" + SKILL):
            with self.subTest(stop=stop):
                self.vault = self.base / ("Vault " + stop.split("/")[-1])
                self.command()
                (self.vault / install.BASE).unlink()
                nested = self.vault / (".runtime/" + SKILL)
                nested.parent.mkdir(parents=True)
                nested.write_bytes(b"owner override")
                (self.vault / DOC).write_bytes(b"legacy program")
                backup = self.base / ("Backup " + self.vault.name)
                original = install.replace
                def interrupt(path, expected, after):
                    if path == self.vault / stop:
                        raise PowerLoss()
                    original(path, expected, after)
                with mock.patch.object(install, "replace", side_effect=interrupt), self.assertRaises(PowerLoss):
                    install.install(self.package, self.vault, backup_dir=backup)
                self.assertEqual((self.vault / SKILL).read_bytes(), b"owner override")
                # Reuse must reject a damaged backup before touching the vault.
                before = self.all_bytes()
                (backup / DOC).write_bytes(b"corrupt")
                self.assertIn("резервной копии", self.command("--backup-dir", backup, success=False))
                self.assertEqual(self.all_bytes(), before)
                (backup / DOC).write_bytes(b"legacy program")
                self.command("--backup-dir", backup)
                self.assertFalse(nested.exists())
                self.assertEqual((self.vault / SKILL).read_bytes(), b"owner override")
                self.assertEqual((backup / SKILL).read_bytes(), b"official skill\n")
                self.assertEqual(self.command()["filesChanged"], 0)

    def test_corrupt_payload_unexpected_path_and_external_link_fail_before_write(self):
        self.put(DOC, b"corruption")
        self.assertIn(DOC, self.command(success=False))
        self.assertFalse(self.vault.exists())
        doc = self.seal()
        doc["files"]["Notes/owner.md"] = hashlib.sha256(b"").hexdigest()
        (self.package / "manifest.json").write_text(json.dumps(doc))
        self.assertIn("Notes/owner.md", self.command(success=False))
        self.assertFalse(self.vault.exists())
        self.seal()
        self.vault.mkdir()
        outside = self.base / "outside"
        outside.mkdir()
        try:
            (self.vault / ".runtime").symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(str(error))
        self.assertIn("Ссылка", self.command(success=False))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(list(self.vault.iterdir()), [self.vault / ".runtime"])

    def test_manifest_rejects_nonportable_and_user_owned_destinations(self):
        doc = self.seal()
        for name in ("../Notes/demo.md", ".runtime/docs/../demo.md", ".runtime/docs/COM1.txt",
                     ".runtime/docs/a:b", ".runtime/docs/ab.", install.PLUGIN + "data.json",
                     ".runtime/package-manifest.json", ".runtime/docs/SAMPLE.md"):
            with self.subTest(name=name):
                altered = {**doc, "files": {**doc["files"], name: hashlib.sha256(b"").hexdigest()}}
                (self.package / "manifest.json").write_text(json.dumps(altered))
                self.command(success=False)
                self.assertFalse(self.vault.exists())

    def test_write_failure_rolls_back_and_can_be_retried(self):
        self.command()
        self.put(DOC, b"updated")
        self.put(EXTRA, b"updated too")
        self.seal()
        before = self.all_bytes()
        original = install.replace
        def fail_once(path, expected, after):
            if path == self.vault / DOC and after == b"updated":
                raise OSError("simulated full disk")
            return original(path, expected, after)
        with mock.patch.object(install, "replace", side_effect=fail_once), self.assertRaisesRegex(OSError, "disk"):
            install.install(self.package, self.vault)
        self.assertEqual(self.all_bytes(), before)
        self.command()
        self.assertEqual((self.vault / DOC).read_bytes(), b"updated")


if __name__ == "__main__":
    unittest.main()
