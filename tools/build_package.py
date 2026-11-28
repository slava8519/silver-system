"""Build the standalone Second Brain installation ZIP from the current source tree."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
from tempfile import NamedTemporaryFile
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from zipfile import ZIP_STORED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "plugins/obsidian-gantt"
PLUGIN_FILES = ("main.js", "manifest.json", "server.js", "styles.css")

# The release inventory also works in a downloaded source ZIP, without Git.
# Add new public files here; drafts in the same directories stay out of releases.
RUNTIME_FILES = {
    "": "README.md ROADMAP.md",
    "docs": "concept.md demo-scenarios.md entities-and-folders.md gantt.md guide.md vault-check.md",
    "examples": """
        gantt_vault.py inbox_vault.py meeting.md okr_review_vault.py okr_vault.py
        planning_vault.py project_activity_vault.py report_vault.py scenarios.json working_vault.py
    """,
    "second_brain": """
        __init__.py __main__.py batch.py brief.py capture.py check_records.py check_snapshots.py
        check_sources.py cycles.py demo.py demo_cleanup.py dependencies.py direction_overview.py
        evidence.py examples.py gantt.py gantt_brief_format.py gantt_feedback.py gantt_group_cards.py
        gantt_groups.py inbox.py inbox_activity.py inbox_archive.py intake_cli.py legacy_examples.py markdown_sections.py okr.py
        okr_cli.py okr_criteria.py okr_links.py okr_progress.py okr_progress_cli.py okr_web.py
        people.py plan_dates.py plan_readiness.py plan_transaction.py plan_tree.py planning.py planning_cli.py
        project_activity.py project_activity_cli.py project_feedback.py project_gantt.py
        project_membership.py project_updates.py project_updates_cli.py query.py questions.py
        refresh.py refresh_people.py refresh_planning.py refresh_projects.py relation_map.py
        relation_map_cli.py report_cli.py report_defaults.py report_sources.py report_web.py
        reports.py routing.py schedule.py search.py search_index.py storage.py teams.py teams_cli.py vault_check.py
        webserver.py weekly_evidence.py weekly_report.py work_control.py workspace.py
    """,
    "second_brain/web": """
        about-map.css about.css about.html activity-help.css activity-help.js app.css app.js
        batch.js capture.js demo-cleanup.js direction-states.js gantt-axis.js gantt-cycle.js
        gantt-dependencies.js gantt-detail-text.js gantt-detail.js gantt-inspector.css gantt-layers.css
        gantt-layers.js gantt-okr-detail.js gantt-okr-forms.js gantt-okr-source.js gantt-okr-tracking.js
        gantt-okr-values.js gantt-okr.js gantt-rows.js gantt-tree.js gantt-work-events.js gantt.css
        gantt.js inbox-activity.js index.html intake.js note-links.js obsidian-workspace.js overview-commands.js
        overview.css project-activity.js project-participation.js project-updates.js record-document.js
        relation-map-help.js relation-map.css relation-map.js report-document.js report-forms.js
        report-template-changes.js reports.css reports.js teams.js vault-check.js workspace.css
        workspace.html workspace.js
    """,
    "second_brain/web/images": "desk.jpg perfume.jpg personal.jpg work.jpg",
    PLUGIN: "README.md " + " ".join(PLUGIN_FILES),
}
SKILLS = {
    "brain-ask": "",
    "brain-intake": "references/queue.md",
    "brain-overview": "",
    "brain-person": "references/teams.md",
    "brain-plan": """
        references/cycles.md references/dependencies.md references/gantt.md references/okr-progress.md
        references/okr.md references/readiness-estimates.md references/schedule.md
    """,
    "brain-record": "",
    "brain-report": "references/formats.md references/review.md",
    "brain-start": "",
    "brain-update": "scripts/compare.py",
    "brain-vault": "",
    "brain-weekly-review": "",
}
QUICK_START = """# Установка «Второго мозга»

Распакуйте ZIP целиком и откройте терминал в папке с `install.py`.
Понадобится Python 3.9 или новее с SQLite FTS5. Выберите папку для своего хранилища.

На macOS и Linux выполните `python3 install.py "/путь/к/MyVault"`.
На Windows выполните `py -3 install.py "C:\\путь\\к\\MyVault"`.
Для учебного хранилища добавьте к команде `--demo`.

При обновлении используйте `--backup-dir` с путём к новому каталогу вне хранилища
для копий заменяемых компонентов. Список параметров доступен через `install.py --help`
с тем же Python. После установки откройте выбранную папку в Codex или Obsidian
и начните с `Home.md`.
"""


def inventory():
    """Map each source to its canonical installed location for Markdown links."""
    result = {}
    for folder, names in RUNTIME_FILES.items():
        for name in names.split():
            source = posixpath.join(folder, name)
            result[source] = ".runtime/" + source
    for skill, extra in SKILLS.items():
        for name in ["SKILL.md", *extra.split()]:
            source = posixpath.join(".agents/skills", skill, name)
            result[source] = source
    return result


def source_bytes(root, name):
    path = root
    for part in PurePosixPath(name).parts:
        path = path / part
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Символическая ссылка в составе поставки: {name}")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Для сборки нужен обычный файл: {name}")
    return path.read_bytes()


# Inline links as used in the shipped Markdown. Leave fenced examples and quoted
# snippets intact so users can copy the original commands or Markdown syntax.
INLINE = re.compile(r"`+[^`\n]*`+|\[(?P<label>[^\]\n]+)\]\((?P<url><[^>\n]+>|[^()\s]+)\)")
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def installed_markdown(data, source, locations):
    def rewrite(match):
        target = match.group("url")
        if not target:
            return match[0]
        parts = urlsplit(target.strip("<>"))
        if not parts.path or parts.scheme or parts.netloc or parts.path.startswith("/"):
            return match[0]
        relative = unquote(parts.path)
        original = posixpath.normpath(posixpath.join(posixpath.dirname(source), relative))
        if original == "AGENTS.md" or original.split("/")[0] in ("tests", "tools"):
            return f"{match['label']} (в исходниках, `{original}`)"
        if original not in locations:
            raise ValueError(f"{source}: ссылка ведёт за пределы поставки: {target}")
        moved = posixpath.relpath(locations[original], posixpath.dirname(locations[source]))
        if relative.endswith("/"):
            moved += "/"
        if moved == relative:
            return match[0]
        url = urlunsplit(("", "", quote(moved), parts.query, parts.fragment))
        return f"[{match['label']}]({url})"

    lines = []
    fence = None
    for line in data.decode("utf-8").splitlines(keepends=True):
        marker = FENCE.match(line)
        if marker and fence is None:
            fence = marker[1]
        elif fence:
            if marker and marker[1].startswith(fence) and not line[marker.end():].strip():
                fence = None
        else:
            line = INLINE.sub(rewrite, line)
        lines.append(line)
    return "".join(lines).encode("utf-8")


def build_package(root, output):
    sources = inventory()
    locations = dict(sources)
    for source, dest in sources.items():
        for original, installed in zip(PurePosixPath(source).parents, PurePosixPath(dest).parents):
            if str(original) != ".":
                locations[str(original)] = str(installed)

    archive = {"install.py": source_bytes(root, "tools/install.py"), "START-HERE.md": QUICK_START.encode()}
    files, source_files = {}, {}
    for source, dest in sources.items():
        raw = source_bytes(root, source)
        data = installed_markdown(raw, source, locations) if source.endswith(".md") else raw
        digest = hashlib.sha256(data).hexdigest()
        archive["payload/" + dest] = data
        files[dest] = digest
        if data != raw:
            source_files[dest] = hashlib.sha256(raw).hexdigest()
    for name in PLUGIN_FILES:
        source = ".runtime/" + PLUGIN + "/" + name
        dest = ".obsidian/" + PLUGIN + "/" + name
        archive["payload/" + dest] = archive["payload/" + source]
        files[dest] = files[source]
    manifest = dict(schema_version=1, files=files, source_files=source_files)
    archive["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()

    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=output.parent, prefix=".second-brain-", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with ZipFile(temporary, "w") as package:
            for name, data in sorted(archive.items()):
                # Stored entries make bytes independent of the platform's zlib.
                entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                package.writestr(entry, data, compress_type=ZIP_STORED)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Собрать установочный ZIP «Второго мозга».")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/second-brain.zip", help="путь к готовому ZIP")
    args = parser.parse_args()
    try:
        build_package(ROOT, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Не удалось собрать ZIP: {exc}\n")
    print(args.output.absolute())


if __name__ == "__main__":
    main()
