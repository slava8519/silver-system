import json
from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / ".agents/skills/brain-update/scripts/compare.py"
SKILL = ".agents/skills/brain-ask/SKILL.md"
PLUGIN = "plugins/obsidian-gantt"
PRIVATE = "Синтетический личный факт о кедровой пробе"


def write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def snapshot(root):
    result = {}
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[name] = ("link", os.readlink(path))
        elif path.is_dir():
            result[name] = ("dir",)
        else:
            result[name] = ("file", path.read_bytes(), path.stat().st_mtime_ns)
    return result


class SkillUpdateCompareTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = self.root / "Synthetic vault"
        self.base = self.root / "Previous release"
        self.target = self.root / "Candidate release"
        self.runtime = self.vault / ".runtime"
        self.plugin = self.vault / ".obsidian" / PLUGIN
        for name, content in {
            "README.md": "Program manual",
            "ROADMAP.md": "Roadmap",
            "second_brain/__init__.py": "from pathlib import Path\nPath(__file__).with_suffix('.ran').touch()\n",
            "second_brain/__main__.py": "from pathlib import Path\nPath(__file__).with_suffix('.ran').touch()\n",
            "second_brain/records.py": "# previous implementation\n",
            "examples/scenarios.json": "[]",
            SKILL: "Previous skill instructions",
            f"{PLUGIN}/manifest.json": '{"id":"obsidian-gantt","version":"0.2.0"}',
            f"{PLUGIN}/main.js": "// previous plugin",
            f"{PLUGIN}/server.js": "// previous launcher",
            f"{PLUGIN}/styles.css": "/* previous style */",
        }.items():
            write(self.base, name, content)
        shutil.copytree(self.base, self.target)
        shutil.copytree(self.base, self.runtime, ignore=shutil.ignore_patterns(".agents"))
        shutil.copytree(self.base / ".agents", self.vault / ".agents")
        write(self.vault, "Home.md", "Custom home")
        write(self.vault, "AGENTS.md", "Vault owner instructions")

    def compare(self, *options, with_base=True, target=None, expected_code=0):
        args = [sys.executable, str(SCRIPT), "--vault", str(self.vault), "--target", str(target or self.target)]
        if with_base:
            args += ["--base", str(self.base)]
        before = snapshot(self.root)
        result = subprocess.run(args + list(options), cwd=self.vault, capture_output=True,
                                encoding="utf-8", timeout=10)
        self.assertEqual(snapshot(self.root), before)
        self.assertNotIn(PRIVATE, result.stdout + result.stderr)
        self.assertEqual(result.returncode, expected_code, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertNotIn(PRIVATE, json.dumps(report, ensure_ascii=False))
        return report

    @staticmethod
    def changes(report):
        return {(item["component"], item["path"]): (item["status"], item["action"])
                for item in report["items"]}

    def test_three_way_comparison_uses_single_active_skill_copy(self):
        write(self.target, SKILL, "New skill instructions")
        write(self.vault, SKILL, PRIVATE)
        write(self.target, "second_brain/new.py", "# added\n")
        write(self.runtime, "second_brain/records.py", "# owner edit\n")
        (self.target / "ROADMAP.md").unlink()
        report = self.compare("--limit", "100")
        self.assertTrue(report["baseKnown"])
        changes = self.changes(report)
        self.assertEqual(report["installation"], "recognized")
        self.assertEqual(report["missingRuntimeParts"], [])
        self.assertNotIn(("runtime", f".runtime/{SKILL}"), changes)
        self.assertEqual(changes[("skills", SKILL)], ("conflict", "review"))
        self.assertEqual(changes[("runtime", ".runtime/second_brain/records.py")], ("local", "preserve"))
        self.assertEqual(changes[("runtime", ".runtime/second_brain/new.py")], ("upstream", "add"))
        self.assertEqual(changes[("runtime", ".runtime/ROADMAP.md")], ("upstream", "remove"))
        self.assertEqual(report["differenceCount"], len(changes))

    def test_old_runtime_with_a_bundled_copy_is_compared_separately(self):
        shutil.copytree(self.base / ".agents", self.runtime / ".agents")
        write(self.target, SKILL, "New release")
        write(self.vault, SKILL, "Owner version")
        report = self.compare()
        self.assertEqual(report["installation"], "recognized")
        self.assertEqual(self.changes(report), {
            ("runtime", f".runtime/{SKILL}"): ("upstream", "replace"),
            ("skills", SKILL): ("conflict", "review"),
        })

    def test_missing_active_skills_are_reported_in_single_folder_installation(self):
        shutil.rmtree(self.vault / ".agents")
        report = self.compare()
        self.assertEqual((report["installation"], report["components"]["skills"]), ("partial", "missing"))
        self.assertIn(("skills", SKILL), self.changes(report))

    def test_rewritten_package_links_match_upstream_but_owner_edits_remain_visible(self):
        files = {}
        sources = {}
        for installed_path, source_path in ((".runtime/README.md", "README.md"), (SKILL, SKILL)):
            source = (self.base / source_path).read_bytes()
            installed = write(self.vault, installed_path, source.decode() + " (packaged relative link)")
            files[installed_path] = sha256(installed.read_bytes()).hexdigest()
            sources[installed_path] = sha256(source).hexdigest()
        write(self.runtime, "package-manifest.json", json.dumps({"schema_version": 1, "files": files, "source_files": sources}))
        for known in (True, False):
            report = self.compare(with_base=known)
            self.assertEqual((report["installation"], report["differenceCount"]), ("recognized", 0))
        # Copying raw source over the packaged skill can break its rewritten links.
        shutil.copy2(self.target / SKILL, self.vault / SKILL)
        self.assertEqual(self.changes(self.compare()), {("skills", SKILL): ("local", "preserve")})
        write(self.target, "README.md", "Updated manual")
        write(self.vault, SKILL, PRIVATE)
        self.assertEqual(self.changes(self.compare()), {
            ("runtime", ".runtime/README.md"): ("upstream", "replace"),
            ("skills", SKILL): ("local", "preserve"),
        })
        write(self.target, SKILL, "New skill instructions")
        self.assertEqual(self.changes(self.compare())[("skills", SKILL)], ("conflict", "review"))

    def test_only_installed_plugin_files_are_compared(self):
        write(self.target, f"{PLUGIN}/main.js", "// candidate")
        before_install = self.compare()
        self.assertFalse(any(item["component"] == "plugin" for item in before_install["items"]))
        shutil.copytree(self.base / PLUGIN, self.plugin)
        write(self.vault, ".obsidian/community-plugins.json", "[]")
        write(self.plugin, "data.json", json.dumps({"setting": PRIVATE}, ensure_ascii=False))
        write(self.target, f"{PLUGIN}/data.json", "Candidate must not replace settings")
        changes = self.changes(self.compare("--limit", "100"))
        self.assertEqual(changes[("plugin", f".obsidian/{PLUGIN}/main.js")], ("upstream", "replace"))
        self.assertNotIn(("plugin", f".obsidian/{PLUGIN}/data.json"), changes)

    def test_no_base_does_not_attribute_differences_and_ignores_owner_files(self):
        write(self.vault, SKILL, PRIVATE)
        for title in ("IT rollout", "Household keys", "Cedar trial"):
            write(self.vault, f"Directions/{title}.md", PRIVATE)
        write(self.vault, "Tasks/Release date.md", "---\ndue: 2026-12-01\n---\n" + PRIVATE)
        write(self.vault, "Settings/Templates/Owner format.md", PRIVATE)
        write(self.vault, ".agents/skills/personal-helper/SKILL.md", PRIVATE)
        write(self.vault, ".agents/skills/brain-personal-helper/SKILL.md", PRIVATE)
        write(self.target, "AGENTS.md", "Repository development instructions")
        write(self.target, "Tasks/Release date.md", "Unrelated repository file")
        report = self.compare(with_base=False)
        self.assertFalse(report["baseKnown"])
        self.assertEqual(self.changes(report), {("skills", SKILL): ("unknown", "review")})
        self.assertEqual(report["differenceCount"], 1)

    def test_symlink_in_managed_path_is_unsafe_and_not_traversed(self):
        leaf = self.runtime / "second_brain/records.py"
        leaf.unlink()
        candidate = (self.target / "second_brain/records.py").read_text(encoding="utf-8")
        external = write(self.root, "external/copied.py", candidate)
        try:
            leaf.symlink_to(external)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symlinks unavailable: {error}")
        changes = self.changes(self.compare())
        self.assertEqual(changes[("runtime", ".runtime/second_brain/records.py")], ("unsafe", "review"))

    def test_separately_installed_program_requires_explicit_path(self):
        external = self.runtime.rename(self.root / "External application")
        unrecognized = self.compare()
        self.assertEqual(unrecognized["installation"], "unrecognized")
        self.assertEqual(unrecognized["items"], [])
        write(self.target, "second_brain/records.py", "# new version\n")
        self.assertEqual(self.changes(self.compare("--program-dir", str(external))), {
            ("runtime", "second_brain/records.py"): ("upstream", "replace")
        })

    def test_separate_source_checkout_with_its_own_skills_and_no_active_vault_copy(self):
        external = self.runtime.rename(self.root / "Source checkout")
        (self.vault / ".agents").rename(external / ".agents")
        write(self.target, SKILL, "New skill instructions")
        report = self.compare("--program-dir", str(external))
        self.assertEqual((report["installation"], report["components"]["skills"]), ("recognized", "missing"))
        self.assertEqual(self.changes(report), {("runtime", SKILL): ("upstream", "replace")})

    def test_original_program_tree_at_vault_root_counts_each_skill_once(self):
        for path in self.runtime.iterdir():
            path.rename(self.vault / path.name)
        self.runtime.rmdir()
        write(self.target, SKILL, "New skill instructions")
        report = self.compare("--program-dir", str(self.vault))
        self.assertEqual(report["installation"], "recognized")
        self.assertEqual(self.changes(report), {("skills", SKILL): ("upstream", "replace")})

    def test_installed_runtime_cannot_be_the_target_snapshot(self):
        report = self.compare(target=self.runtime, expected_code=2)
        self.assertTrue(report["error"])
        self.assertNotIn("differenceCount", report)

    def test_page_size_bounds_output_and_keeps_totals_across_pages(self):
        for number in range(40):
            write(self.target, f"second_brain/component{number:02d}.py", PRIVATE * 100)
        first = self.compare("--limit", "2")
        second = self.compare("--offset", "2", "--limit", "2")
        self.assertEqual(first["differenceCount"], 40)
        self.assertEqual(second["totals"], first["totals"])
        self.assertEqual(len(first["items"]), 2)
        self.assertEqual(len(second["items"]), 2)
        self.assertTrue(self.changes(first).keys().isdisjoint(self.changes(second)))
        self.assertLess(len(json.dumps(first, ensure_ascii=False).encode()), 3500)
