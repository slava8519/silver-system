import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

from tools import build_package


ROOT = Path(__file__).resolve().parents[1]


class PackageBuildTests(unittest.TestCase):
    def test_build_from_unversioned_sources_is_reproducible_and_excludes_local_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            for source in [*build_package.inventory(), "tools/install.py", "tools/build_package.py"]:
                target = root / source
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / source, target)
            for scratch in ("private/mapping.md", "local/work.md", ".git/config", "docs/notes.md",
                            "second_brain/debug.py", "second_brain/web/scratch.js",
                            ".agents/skills/brain-private/SKILL.md", ".agents/skills/brain-ask/tmp.md",
                            "plugins/obsidian-gantt/data.json", ".obsidian/workspace.json"):
                path = root / scratch
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("UNPUBLISHED")
            command = [sys.executable, "tools/build_package.py"]
            subprocess.run(command, cwd=root, check=True, capture_output=True)
            first = root / "dist/second-brain.zip"
            destination = Path(temporary) / "elsewhere/ready.zip"
            os.utime(root / "docs/guide.md", (1_000_000_000, 1_000_000_000))
            subprocess.run([*command, "--output", str(destination)], cwd=root, check=True, capture_output=True)
            self.assertEqual(destination.read_bytes(), first.read_bytes())

            with ZipFile(first) as package:
                names = package.namelist()
                self.assertEqual(names, sorted(set(names)))
                self.assertEqual({name.split("/")[0] for name in names},
                                 {"install.py", "manifest.json", "START-HERE.md", "payload"})
                self.assertEqual(package.read("install.py"), (ROOT / "tools/install.py").read_bytes())
                manifest = json.loads(package.read("manifest.json"))
                self.assertEqual(manifest["schema_version"], 1)
                files = manifest["files"]
                self.assertEqual(set(files), {name[8:] for name in names if name.startswith("payload/")})
                for path, digest in files.items():
                    with self.subTest(path=path):
                        data = package.read("payload/" + path)
                        self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
                        self.assertNotEqual(data, b"UNPUBLISHED")
                        self.assertNotIn(".runtime/.agents", path)
                self.assertIn(".runtime/second_brain/web/note-links.js", files)
                self.assertIn(".runtime/second_brain/web/record-document.js", files)
                self.assertIn(".agents/skills/brain-update/scripts/compare.py", files)
                plugin = "plugins/obsidian-gantt/"
                active = {path for path in files if path.startswith(".obsidian/")}
                self.assertEqual(active, {".obsidian/" + plugin + name for name in
                                         ("manifest.json", "main.js", "server.js", "styles.css")})
                for path in active:
                    self.assertEqual(files[path], files[path.replace(".obsidian/", ".runtime/", 1)])

    def test_installed_links_and_source_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release.zip"
            build_package.build_package(ROOT, output)
            with ZipFile(output) as package:
                manifest = json.loads(package.read("manifest.json"))
                paths = set(manifest["files"])
                for source, dest in build_package.inventory().items():
                    packaged = package.read("payload/" + dest)
                    raw = (ROOT / source).read_bytes()
                    if packaged == raw:
                        self.assertNotIn(dest, manifest["source_files"])
                    else:
                        self.assertEqual(manifest["source_files"][dest], hashlib.sha256(raw).hexdigest())
                    if not dest.endswith(".md"):
                        continue
                    for url in re.findall(r"\]\((<[^>\n]+>|[^\s()]+)\)", packaged.decode()):
                        link = urlsplit(url.strip("<>"))
                        if link.scheme or link.netloc or not link.path or link.path.startswith("/"):
                            continue
                        target = posixpath.normpath(posixpath.join(posixpath.dirname(dest), unquote(link.path)))
                        with self.subTest(document=dest, link=url):
                            self.assertTrue(target in paths or any(name.startswith(target + "/") for name in paths))
                guide = package.read("payload/.runtime/docs/guide.md").decode()
                self.assertIn("(../../.agents/skills/brain-ask/SKILL.md)", guide)
                self.assertIn("(../README.md)", guide)
                self.assertNotIn("](../tests/)", guide)
                plugin = package.read("payload/.runtime/plugins/obsidian-gantt/README.md").decode()
                self.assertIn("(../../../.agents/skills/brain-vault/SKILL.md)", plugin)

    def test_active_skill_can_link_back_into_runtime(self):
        paths = build_package.inventory()
        source = ".agents/skills/brain-start/SKILL.md"
        sample = b"[Guide](../../../docs/guide.md#one)\n```markdown\n[example](foo)\n```\n"
        self.assertEqual(build_package.installed_markdown(sample, source, paths),
                         sample.replace(b"../../../docs/", b"../../../.runtime/docs/"))

    def test_required_source_symlink_is_rejected_before_a_previous_zip_is_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repository"
            root.mkdir()
            external = Path(temporary) / "elsewhere"
            external.mkdir()
            (external / "install.py").write_text("outside the source tree")
            try:
                (root / "tools").symlink_to(external, target_is_directory=True)
            except OSError:
                self.skipTest("Creating symlinks is unavailable")
            previous = root / "release.zip"
            previous.write_bytes(b"previous")
            with self.assertRaisesRegex(ValueError, "tools/install.py"):
                build_package.build_package(root, previous)
            self.assertEqual(previous.read_bytes(), b"previous")
