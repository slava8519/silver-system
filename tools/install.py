"""Install the verified contents of an extracted Second Brain package (Python 3.9+)."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unicodedata


BASE = ".runtime/package-manifest.json"
PLUGIN = ".obsidian/plugins/obsidian-gantt/"
PLUGIN_FILES = {"main.js", "server.js", "styles.css", "manifest.json"}
SKILLS = ".agents/skills/"
LEGACY = ".runtime/" + SKILLS
HEX = re.compile(r"[0-9a-f]{64}\Z")


class InstallError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def relative(name):
    if not isinstance(name, str) or not name:
        raise InstallError("Пустой путь в комплекте.")
    for part in name.split("/"):
        reserved = re.fullmatch(r"(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³])", part.split(".")[0], re.I)
        if (not part or part in (".", "..") or part[-1] in " ." or reserved
                or re.search(r'[<>:"\\|?*\x00-\x1f]', part)):
            raise InstallError(f"Недопустимый путь: {name!r}")
    return name


def managed(name):
    parts = relative(name).split("/")
    if name.startswith(PLUGIN) and name[len(PLUGIN):] in PLUGIN_FILES:
        return True
    if name.startswith(SKILLS) and len(parts) >= 4 and parts[2].startswith("brain-"):
        return True
    return (name in (".runtime/README.md", ".runtime/ROADMAP.md") or
            (len(parts) >= 3 and parts[0] == ".runtime" and parts[1] in ("second_brain", "docs", "examples", "plugins")))


def no_links(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (stat.S_ISLNK(info.st_mode) or
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise InstallError(f"Ссылка вместо обычного файла или каталога: {path}")


def inside(root, name):
    path = root
    for part in relative(name).split("/"):
        no_links(path)
        if path.exists() and not path.is_dir():
            raise InstallError(f"Ожидался каталог: {path}")
        path = path / part
    no_links(path)
    return path


def content(path):
    no_links(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise InstallError(f"Ожидался обычный файл: {path}")
    return path.read_bytes()


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InstallError(f"Повтор ключа в манифесте: {key}")
        result[key] = value
    return result


def manifest(raw):
    doc = json.loads(raw, object_pairs_hook=unique_keys)
    if not isinstance(doc, dict) or type(doc.get("schema_version")) is not int or doc["schema_version"] != 1:
        raise InstallError("Неизвестная версия манифеста.")
    files = doc.get("files")
    if not isinstance(files, dict) or not files:
        raise InstallError("В манифесте нет управляемых файлов.")
    for name, value in files.items():
        if not managed(name) or not isinstance(value, str) or not HEX.fullmatch(value):
            raise InstallError(f"Неожиданное назначение или SHA-256 в манифесте: {name}")
    sources = doc.get("source_files", {})
    if (not isinstance(sources, dict) or not sources.keys() <= files.keys()
            or any(not isinstance(h, str) or not HEX.fullmatch(h) for h in sources.values())):
        raise InstallError("Некорректный список контрольных сумм исходников.")
    return doc


def portable_paths(names):
    seen, files = {}, set()
    for name in names:
        relative(name)
        parts = name.split("/")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            key = unicodedata.normalize("NFC", prefix).casefold()
            if seen.setdefault(key, prefix) != prefix or (i < len(parts) and key in files):
                raise InstallError(f"Несовместимые пути: {prefix}")
            if i == len(parts):
                if any(k.startswith(key + "/") for k in seen):
                    raise InstallError(f"Файл совпадает с каталогом: {prefix}")
                files.add(key)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_payload(package):
    data = manifest(content(inside(package, "manifest.json")) or b"{}")
    root = inside(package, "payload")
    files = {}
    portable_paths(data["files"])
    for name, expected in data["files"].items():
        value = content(inside(root, name))
        if digest(value) != expected:
            raise InstallError(f"Содержимое не соответствует manifest.json: {name}")
        files[name] = value
    required = {PLUGIN + name for name in PLUGIN_FILES} | {".runtime/second_brain/__main__.py"}
    if required - files.keys():
        raise InstallError("В манифесте отсутствуют обязательные файлы: " + ", ".join(sorted(required - files.keys())))
    return data, files


def migration_files(root):
    """Relocate the former skill tree, including skills and resources owned by the user."""
    directory = inside(root, LEGACY.rstrip("/"))
    if not directory.exists():
        return {}, set()
    if not directory.is_dir():
        raise InstallError(f"Ожидался каталог: {directory}")
    files = {}
    directories = {directory, directory.parent}
    for skill in sorted(directory.iterdir()):
        no_links(skill)
        if skill.is_dir():
            for parent, dirs, names in os.walk(skill, onerror=raise_error):
                directories.add(Path(parent))
                for child in dirs:
                    no_links(Path(parent) / child)
                for filename in names:
                    path = Path(parent) / filename
                    name = SKILLS + path.relative_to(directory).as_posix()
                    relative(name)
                    files[name] = content(path)
    return files, directories


def raise_error(error):
    raise error


def initialize_template(staging, payload, demo):
    # Run only manifest-verified runtime bytes, separate from the owner's installed code.
    runtime = staging / "program"
    for name, value in payload.items():
        if name.startswith((".runtime/second_brain/", ".runtime/examples/")):
            path = runtime / name[len(".runtime/"):]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
    template = staging / "vault"
    launcher = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('second_brain',run_name='__main__')"
    subprocess.run([sys.executable, "-I", "-B", "-c", launcher, str(runtime), "--vault", str(template),
                    "examples" if demo else "init"], cwd=staging, check=True, stdout=subprocess.DEVNULL)
    return template


class Plan:
    def __init__(self, root):
        self.root = root
        self.changes = {}
        self.modes = {}
        self.directories = set()
        self.backup = {}
        self.conflicts = []

    def file(self, name, before, after):
        if before != after:
            self.changes[name] = (before, after)

    def default(self, name, value):
        if content(inside(self.root, name)) is None:
            self.file(name, None, value)

    def program(self, data, payload, previous, legacy, backup_requested):
        old = previous["files"] if previous else {}
        for name in sorted(payload.keys() | old.keys() | legacy.keys()):
            current = content(inside(self.root, name))
            current_hash, prior = digest(current), old.get(name)
            wanted = payload.get(name)
            supplied = data["files"].get(name)
            nested = legacy.get(name)
            preserve = False
            if nested is not None:
                old_name = ".runtime/" + name
                # When only one copy differs from a known distribution, keep that copy.
                standard = {h for h in (supplied, prior, data.get("source_files", {}).get(name),
                                        (previous or {}).get("source_files", {}).get(name)) if h}
                nested_hash = digest(nested)
                if current is None or current == nested:
                    if nested_hash not in standard:
                        wanted, preserve = nested, True
                elif nested_hash in standard:
                    preserve = current_hash not in standard
                    if preserve:
                        wanted = current
                elif current_hash in standard:
                    wanted, preserve = nested, True
                else:
                    self.conflicts.append(f"{name} и {old_name}: две разные ручные версии")
                    continue
                if supplied is None and prior is None:
                    wanted, preserve = current if current is not None else nested, True
                self.file(old_name, nested, None)
                if backup_requested:
                    self.backup[old_name] = nested
            if preserve and prior and supplied != prior and not (current_hash == supplied == digest(wanted)):
                self.conflicts.append(f"{name}: ручная версия навыка и изменение в поставке")
                continue
            if not preserve and current is not None and current != wanted:
                if prior and current_hash != prior:
                    if supplied == prior or supplied is None:
                        wanted = current  # Keep an override, including one retired upstream.
                    else:
                        self.conflicts.append(f"{name}: изменены локальная версия и поставка")
                        continue
                elif prior is None:
                    if supplied is None:
                        wanted = current
                    elif not backup_requested:
                        self.conflicts.append(f"{name}: прежняя версия неизвестна; для замены укажите --backup-dir")
                        continue
            if backup_requested and current is not None and current != wanted:
                self.backup[name] = current
            self.file(name, current, wanted)
            if nested is not None and wanted == nested and current != wanted:
                self.modes[name] = stat.S_IMODE(inside(self.root, ".runtime/" + name).stat().st_mode)

    def instruction(self, name, block, linked, first_install):
        before = content(inside(self.root, name))
        if before is None:
            self.file(name, None, block)
        elif first_install and not linked(before):
            newline = b"\r\n" if b"\r\n" in before else b"\n"
            extra = block.replace(b"\n", newline)
            self.file(name, before, before + (newline * 2 if before else b"") + extra)

    def seed(self, template):
        for path in template.rglob("*"):
            name = path.relative_to(template).as_posix()
            target = inside(self.root, name)
            if path.is_dir():
                if target.exists() and not target.is_dir():
                    raise InstallError(f"Путь каталога уже занят: {target}")
                self.directories.add(name)
            else:
                self.default(name, path.read_bytes())
    def configure(self, python, first_install):
        self.instruction("AGENTS.md", (
            "# Второй мозг\n\nРабочее хранилище — папка с этим файлом. Выбирай навыки из "
            "`.agents/skills/` и читай соответствующий `SKILL.md`. Для команд используй Python "
            f"`{python}` из рабочего каталога `.runtime` с аргументами `-m second_brain --vault ..`. "
            "Пути записей отсчитывай от корня хранилища.\n").encode("utf-8"),
            lambda text: b".agents/skills" in text and (b".runtime" in text or b"./brain" in text), first_install)
        self.instruction("CLAUDE.md", b"@AGENTS.md\n", lambda text: any(
            line.strip() in (b"@AGENTS.md", b"@./AGENTS.md") for line in text.splitlines()), first_install)
        self.default(PLUGIN + "data.json", json_bytes({"programDir": str(self.root / ".runtime"), "python": python}))


def replace(path, expected, value):
    if content(path) != expected:
        raise InstallError(f"Файл изменился во время установки: {path}")
    if value is None:
        path.unlink()
        return
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".install-", suffix=".tmp")
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        if expected is None:
            os.link(tmp, path)  # Never overwrite a concurrently created file.
        else:
            tmp.chmod(stat.S_IMODE(path.stat().st_mode))
            if content(path) != expected:
                raise InstallError(f"Файл изменился во время установки: {path}")
            os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def commit(plan):
    directories = set(plan.directories)
    for name, (_, after) in plan.changes.items():
        if after is not None:
            directories.update(p.as_posix() for p in Path(name).parents if p != Path("."))
    created, applied = [], []
    try:
        if not plan.root.exists():
            plan.root.mkdir()
            created.append(plan.root)
        for name in sorted(directories, key=lambda s: (s.count("/"), s)):
            path = inside(plan.root, name)
            if not path.exists():
                path.mkdir()
                created.append(path)
        # The previous manifest owns upstream retirements. Publish the new baseline
        # after those, but before discarding the last evidence of a legacy override.
        order = sorted(plan.changes, key=lambda k: (k.startswith(LEGACY), k == BASE, plan.changes[k][1] is None,
                                                   k.startswith(".runtime/"), k))
        for name in order:
            before, after = plan.changes[name]
            path = inside(plan.root, name)
            mode = stat.S_IMODE(path.stat().st_mode) if before is not None else None
            replace(path, before, after)
            applied.append((name, mode))
            if name in plan.modes:
                path.chmod(plan.modes[name])
    except (Exception, KeyboardInterrupt):
        for name, mode in reversed(applied):
            before, after = plan.changes[name]
            path = inside(plan.root, name)
            replace(path, after, before)
            if mode is not None:
                path.chmod(mode)
        for path in reversed(created):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        raise


def write_backup(backup, plan, data):
    marker = inside(backup, "backup-manifest.json")
    originals = {name: digest(raw) for name, raw in plan.backup.items()}
    identity = {"schema_version": 1, "vault": str(plan.root), "package_sha256": digest(json_bytes(data))}
    previous = content(marker)
    if previous is None:
        if backup.exists() and (not backup.is_dir() or any(backup.iterdir())):
            raise InstallError("Для резервной копии нужен новый или пустой каталог.")
        backup.mkdir(parents=True, exist_ok=True)
        replace(marker, None, json_bytes({**identity, "files": originals}))
    else:
        recorded = json.loads(previous, object_pairs_hook=unique_keys)
        if not isinstance(recorded, dict) or any(recorded.get(k) != v for k, v in identity.items()):
            raise InstallError("Резервная копия относится к другому хранилищу или комплекту.")
        saved = recorded.get("files")
        if not isinstance(saved, dict) or any(saved.get(k) != v for k, v in originals.items()):
            raise InstallError("Исходные версии расходятся с прежней резервной копией.")
        originals = saved
    for name, sha in originals.items():
        if not (managed(name) or name.startswith(LEGACY) and len(relative(name)[len(LEGACY):].split("/")) >= 2
                or name.startswith(SKILLS) and len(relative(name)[len(SKILLS):].split("/")) >= 2):
            raise InstallError(f"Неожиданный путь в резервной копии: {name}")
        path = inside(backup, name)
        raw = content(path)
        if raw is None and name in plan.backup:
            path.parent.mkdir(parents=True, exist_ok=True)
            replace(path, None, plan.backup[name])
        elif raw is None or not isinstance(sha, str) or digest(raw) != sha:
            raise InstallError(f"Не совпадает сохранённая версия резервной копии: {name}")


def root_path(path):
    path = Path(path).expanduser()
    no_links(path)
    return path.resolve()


@contextmanager
def install_lock(root):
    key = hashlib.sha256(os.fsencode(os.path.normcase(str(root)))).hexdigest()
    path = Path(tempfile.gettempdir()) / ("second-brain-install-" + key + ".lock")
    no_links(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise InstallError("Неподходящий файл блокировки установщика.")
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def install(package, destination, demo=False, backup_dir=None):
    root = root_path(destination)
    package = root_path(package)
    payload_root = package / "payload"
    if root == package or root.is_relative_to(payload_root) or package.is_relative_to(root):
        raise InstallError("Выберите отдельную от установочных файлов папку хранилища.")
    if root.exists() and not root.is_dir():
        raise InstallError(f"Ожидался каталог хранилища: {root}")
    backup = root_path(backup_dir) if backup_dir is not None else None
    if backup and (backup.is_relative_to(root) or root.is_relative_to(backup) or
                   backup.is_relative_to(package) or package.is_relative_to(backup)):
        raise InstallError("Резервной копии нужен отдельный каталог вне хранилища и комплекта.")
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
    data, files = read_payload(package)
    root.parent.mkdir(parents=True, exist_ok=True)
    with install_lock(root), tempfile.TemporaryDirectory(dir=root.parent, prefix=".second-brain-stage-") as temporary:
        stage = Path(temporary)
        previous_raw = content(inside(root, BASE))
        previous = manifest(previous_raw) if previous_raw is not None else None
        legacy, legacy_directories = migration_files(root)
        portable_paths(files.keys() | (previous or {}).get("files", {}).keys() | legacy.keys())
        empty = not root.exists() or not any(root.iterdir())
        seed_demo = demo and empty
        if demo and not seed_demo and not inside(root, ".second-brain-demo/seed.json").is_file():
            raise InstallError("Для демо выберите новый или пустой каталог. Рабочие записи уже существуют.")
        plan = Plan(root)
        plan.program(data, files, previous, legacy, bool(backup))
        if plan.conflicts:
            raise InstallError("Установка требует сверки:\n" + "\n".join(plan.conflicts))
        first_install = previous is None and not inside(root, ".runtime/second_brain/__main__.py").is_file()
        if first_install:
            plan.seed(initialize_template(stage, files, seed_demo))
        if previous is None:
            plan.configure(sys.executable, first_install)
        plan.file(BASE, previous_raw, json_bytes(data))
        if backup and (plan.backup or inside(backup, "backup-manifest.json").exists()):
            write_backup(backup, plan, data)
        if empty:
            plan.root = stage / "ready"
            commit(plan)
            if root.exists():
                root.rmdir()  # Fails if the owner added anything since preflight.
            plan.root.rename(root)
        else:
            commit(plan)
        # Only remove directories emptied by this migration.
        for parent in sorted(legacy_directories, key=lambda p: len(p.parts), reverse=True):
            path = inside(root, parent.relative_to(root).as_posix())
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        return {"vault": str(root), "filesChanged": len(plan.changes), "demoCreated": seed_demo,
                "backup": str(backup) if backup and plan.backup else None}


def main():
    parser = argparse.ArgumentParser(description="Установка и обновление Второго мозга")
    parser.add_argument("vault", type=Path)
    parser.add_argument("--demo", action="store_true", help="создать примеры в новом хранилище")
    parser.add_argument("--backup-dir", type=Path, help="сохранить заменяемые файлы прежней установки вне хранилища")
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 9):
            raise InstallError("Нужен Python 3.9 или новее.")
        result = install(Path(__file__).resolve().parent, args.vault, args.demo, args.backup_dir)
    except (ValueError, OSError, sqlite3.Error, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Ошибка установки: {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
