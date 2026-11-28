"""Compare installed product files with local source snapshots. Reads files only."""

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys


RUNTIME_PARTS = ("second_brain", "examples", "docs", "plugins", "README.md", "ROADMAP.md")
SKILLS = Path(".agents/skills")
PLUGIN_DIR = ".obsidian/plugins/obsidian-gantt"
PLUGIN_FILES = ("main.js", "manifest.json", "server.js", "styles.css")
STATUSES = ("identical", "upstream", "local", "conflict", "unknown", "unsafe")


def kind(path):
    if path is None:
        return "missing"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unsafe"
    if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return "unsafe"
    if stat.S_ISREG(info.st_mode):
        return "file"
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    return "unsafe"


def member(root, relative):
    """A missing member returns None. A blocking link or file raises ValueError."""
    if root is None:
        return None
    for part in Path(relative).parts:
        before = kind(root)
        if before == "missing":
            return None
        if before != "directory":
            raise ValueError("Некорректный родительский каталог.")
        root = root / part
    return root


def file_digest(path):
    if kind(path) == "missing":
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        initial = os.fstat(stream.fileno())
        if not stat.S_ISREG(initial.st_mode):
            raise ValueError("Ожидался обычный файл.")
        digest = sha256()
        for block in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(block)
        final = path.lstat()
        if ((initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns) !=
                (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)):
            raise ValueError("Файл изменился во время чтения.")
        return digest.digest()


def classify(have, want, old, known):
    if have == want:
        return "identical", "none"
    if not known:
        return "unknown", "review"
    if have == old:
        if have is None:
            return "upstream", "add"
        if want is None:
            return "upstream", "remove"
        return "upstream", "replace"
    if want == old:
        return "local", "preserve"
    return "conflict", "review"


def ignore(name):
    return name in {".git", "__pycache__", ".DS_Store"} or name.endswith((".pyc", ".pyo"))


def packaged_markdown(program):
    """Map packaged Markdown bytes to their source bytes using the installed receipt."""
    path = member(program, "package-manifest.json")
    if kind(path) == "missing":
        return {}
    if kind(path) != "file":
        raise ValueError("Недоступен манифест установленного пакета.")
    manifest = json.loads(path.read_bytes())
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or not isinstance(manifest.get("files"), dict) or not isinstance(manifest.get("source_files", {}), dict)):
        raise ValueError("Неизвестный формат манифеста установленного пакета.")
    mapped = {}
    for name, source in manifest.get("source_files", {}).items():
        installed = manifest["files"].get(name)
        if not name.endswith(".md") or not all(isinstance(value, str) and len(value) == 64 for value in (installed, source)):
            raise ValueError("Некорректная контрольная сумма Markdown в манифесте.")
        mapped[name] = bytes.fromhex(installed), bytes.fromhex(source)
    return mapped


class Comparison:
    def __init__(self, args):
        self.args = args
        self.counts = Counter({name: 0 for name in STATUSES})
        self.items = []
        self.differences = 0
        self.packaged = {}

    def record(self, component, display, status, action):
        self.counts[status] += 1
        if status == "identical":
            return
        if self.args.offset <= self.differences < self.args.offset + self.args.limit:
            self.items.append(dict(component=component, path=display.as_posix(), status=status, action=action))
        self.differences += 1

    def walk(self, component, display, roots, relative):
        try:
            paths = [member(root, relative) for root in roots]
            types = {kind(path) for path in paths} - {"missing"}
            if not types:
                return
            if types == {"directory"}:
                names = set()
                # Only release-owned subtrees belong to the installed skill set.
                skill_catalog = relative == SKILLS
                for path in (paths[1:] if skill_catalog else paths):
                    if kind(path) == "directory":
                        names.update(entry.name for entry in path.iterdir() if not ignore(entry.name)
                                     and (not skill_catalog or entry.name.startswith("brain-")))
                if relative == Path("plugins/obsidian-gantt"):
                    names.discard("data.json")
                for name in sorted(names):
                    self.walk(component, display / name, roots, relative / name)
                return
            if types == {"file"}:
                have, want, old = [file_digest(path) for path in paths]
                installed_path = Path(".runtime") / relative if component == "runtime" else display
                hashes = self.packaged.get(installed_path.as_posix())
                if hashes:
                    packaged, source = hashes
                    if want == source:
                        want = packaged
                    if old == source:
                        old = packaged
                status, action = classify(have, want, old, self.args.base is not None)
            else:
                status, action = "unsafe", "review"
        except (OSError, ValueError):
            status, action = "unsafe", "review"
        self.record(component, display, status, action)

    def compare(self):
        args = self.args
        vault = args.vault
        if kind(vault) != "directory":
            raise ValueError("Выберите обычную папку хранилища.")
        program = args.program_dir or vault / ".runtime"
        for source in (args.target, args.base):
            if source is None:
                continue
            canonical = source.resolve()
            if vault.resolve() in (canonical, *canonical.parents):
                raise ValueError("Выберите снимок исходников вне хранилища.")
            if kind(source) != "directory" or kind(member(source, "second_brain/__main__.py")) != "file":
                raise ValueError("В снимке исходников нужен обычный файл second_brain/__main__.py.")
        try:
            prefix = program.relative_to(vault)
        except ValueError:
            prefix = Path(".")
        else:
            # Handle explicitly selected nested directories with a linked ancestor.
            try:
                member(vault, prefix)
            except ValueError:
                program = None
                self.record("runtime", prefix, "unsafe", "review")
        runtime_type = kind(program)
        installation = "unrecognized"
        components = {"runtime": "missing", "skills": "missing", "plugin": "missing"}
        missing_parts = []
        if runtime_type == "directory":
            if any(source is not None and program.samefile(source) for source in (args.target, args.base)):
                raise ValueError("Для сравнения нужна копия исходников отдельно от установленной программы.")
            components["runtime"] = "present"
            try:
                self.packaged = packaged_markdown(program)
            except (OSError, ValueError):
                self.record("runtime", prefix / "package-manifest.json", "unsafe", "review")
            for part in RUNTIME_PARTS:
                try:
                    target_type = kind(member(args.target, part))
                    installed_type = kind(member(program, part))
                except ValueError:
                    target_type, installed_type = "directory", "unsafe"
                if target_type != "missing" and installed_type != target_type:
                    missing_parts.append(part)
                self.walk("runtime", prefix / part, (program, args.target, args.base), Path(part))
            if program != vault:
                # Older bundles and separate source checkouts may have their own
                # copy. The canonical single-vault layout installs only the active one.
                try:
                    program_skills = kind(member(program, SKILLS))
                except ValueError:
                    program_skills = "unsafe"
                if program_skills != "missing":
                    self.walk("runtime", prefix / SKILLS, (program, args.target, args.base), SKILLS)
            try:
                entry_point = kind(member(program, "second_brain/__main__.py"))
            except ValueError:
                entry_point = "unsafe"
            if entry_point != "file" and "second_brain" not in missing_parts:
                missing_parts.append("second_brain/__main__.py")
            installation = "partial" if missing_parts else "recognized"
        elif runtime_type != "missing":
            self.record("runtime", prefix, "unsafe", "review")
            components["runtime"] = "unsafe"
        elif program is None:
            components["runtime"] = "unsafe"

        skills = SKILLS
        try:
            sources = [member(root, skills) for root in (args.target, args.base)]
            names = set()
            for source in sources:
                if kind(source) == "directory":
                    names.update(entry.name for entry in source.iterdir() if entry.name.startswith("brain-"))
                elif kind(source) != "missing":
                    raise ValueError("Недоступен каталог навыков.")
            active = member(vault, skills)
            if kind(active) not in {"missing", "directory"}:
                raise ValueError("Недоступен каталог навыков.")
            has_active = any(kind(member(active, name)) != "missing" for name in names)
            if has_active:
                components["skills"] = "present"
            canonical = runtime_type == "directory" and program in (vault, vault / ".runtime")
            if names and canonical and not has_active:
                installation = "partial"
            if has_active or canonical:
                for name in sorted(names):
                    self.walk("skills", skills / name, (vault, args.target, args.base), skills / name)
        except (OSError, ValueError):
            components["skills"] = "unsafe"
            self.record("skills", skills, "unsafe", "review")

        try:
            plugin = member(vault, PLUGIN_DIR)
            if kind(plugin) == "directory":
                components["plugin"] = "present"
                sources = [member(root, "plugins/obsidian-gantt") for root in (args.target, args.base)]
                for name in sorted(PLUGIN_FILES):
                    self.walk("plugin", Path(PLUGIN_DIR) / name, (plugin, *sources), Path(name))
            elif kind(plugin) != "missing":
                raise ValueError("Недоступен каталог плагина.")
        except (OSError, ValueError):
            components["plugin"] = "unsafe"
            self.record("plugin", Path(PLUGIN_DIR), "unsafe", "review")

        return dict(installation=installation, components=components, missingRuntimeParts=missing_parts,
                    baseKnown=args.base is not None, totals=dict(self.counts), differenceCount=self.differences,
                    offset=args.offset, limit=args.limit, items=self.items)


def absolute(value):
    return Path(os.path.abspath(os.path.expanduser(value)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("vault", "target", "base", "program-dir"):
        parser.add_argument("--" + name, type=absolute, required=name in ("vault", "target"))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    try:
        if not 1 <= args.limit <= 100 or args.offset < 0:
            raise ValueError("Укажите limit от 1 до 100 и неотрицательный offset.")
        result = Comparison(args).compare()
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
