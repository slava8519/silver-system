"""Recognize older, unmanifested fixtures from their published generator output."""

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import tempfile

from . import people, storage


_UUID = rb"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_IDS = re.compile(_UUID)
_CARD_ID = re.compile(rb'^id: "(' + _UUID + rb')"$', re.MULTILINE)
_TITLE = re.compile(rb'^title: (".*")$', re.MULTILINE)
_EXAMPLES = "# Три направления в одном хранилище\n\nОткройте запись или задайте вопрос о ней в новом чате."
_WORKSHOP = "# Учебное хранилище\n\nВсе карточки, решения, статусы и сроки в этой папке вымышлены."


def _read(root, relative):
    try:
        path = storage._inside(root, relative)
        if path.is_file() and path.stat().st_size <= 1024 * 1024:
            return path.read_bytes()
    except (ValueError, OSError):
        pass
    return b""


def _catalog_ids(data):
    try:
        catalog = json.loads(data)
        if not isinstance(catalog, dict) or not re.fullmatch(_UUID, str(catalog.get("vaultId", "")).encode()):
            return {}
        result = {}
        for group in ("records", "directions"):
            for title, value in catalog.get(group, {}).items():
                if isinstance(value, str) and re.fullmatch(_UUID, value.encode()):
                    result[title] = value.encode()
        return result
    except (ValueError, AttributeError):
        return {}


def _family(root, home, catalog):
    if home.startswith(_WORKSHOP.encode()):
        return "workshop"
    if not home.startswith(_EXAMPLES.encode()) and not catalog:
        return None
    # These hints select a reference generator only. Every candidate still
    # needs its generator's complete file contents, including its relationships.
    if "## Общий Гант квартала".encode() in home:
        return "okr-plan"
    if "## Цели и ключевые результаты".encode() in home:
        return "okr"
    if "## Учебные планы".encode() in home:
        if _read(root, "Notes/Сверка восстановления Маяка.md"):
            return "gantt"
        return "planning"
    if _read(root, "Inbox/00 — Знакомство.md"):
        return "teams" if _read(root, "People/Groups/Команда запуска.md") else "inbox"
    return "examples"


@lru_cache(maxsize=8)
def _template(family):
    from examples import gantt_vault, inbox_vault, okr_vault, planning_vault, working_vault
    from .examples import create_examples

    generators = {"examples": create_examples, "workshop": working_vault.generate, "planning": planning_vault.generate,
                  "gantt": gantt_vault.generate, "okr": okr_vault.generate, "okr-plan": okr_vault.generate, "inbox": inbox_vault.generate,
                  "teams": inbox_vault.generate}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "reference"
        options = {"with_teams": True} if family == "teams" else {}
        if family == "okr":
            options["legacy"] = True
        generators[family](root, **options)
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
                if p.is_file() and p.suffix in (".md", ".txt") and p.name != "Start-Here.md"
                and not any(part.startswith(".") for part in p.relative_to(root).parts)}


def _header(data):
    if not data.startswith(b"---\n"):
        return b""
    header, end, _ = data[4:].partition(b"\n---\n")
    return header if end else b""


def _early_header(original, current):
    # The first released example generator wrote id immediately after
    # schema_version. Reconstruct that exact alternative serialization.
    old = _header(current).splitlines()
    if len(old) < 3 or not old[2].startswith(b"id: "):
        return original
    header = _header(original)
    lines = header.splitlines()
    row = _CARD_ID.search(header)
    if not row or lines[:2] != old[:2]:
        return original
    lines.remove(row[0])
    lines.insert(2, row[0])
    return original.replace(header, b"\n".join(lines), 1)


def legacy_seed(root):
    """Return expected hashes, never a fingerprint learned from an owner's current text."""
    root = Path(root)
    home = _read(root, "Home.md").replace(b"\r\n", b"\n")
    catalog = _catalog_ids(_read(root, "Settings/Demo/catalog.json"))
    family = _family(root, home, catalog)
    if not family:
        return None
    template = _template(family)
    bindings = {}
    catalog_matches = 0
    for path, data in template.items():
        header = _header(data)
        reference = _CARD_ID.search(header)
        # UUID5 is tied to a specific generator request or meeting receipt.
        # Only UUID4 was assigned afresh on each run of an old generator.
        if not reference or reference[1][14:15] != b"4":
            continue
        title = _TITLE.search(header)
        known = catalog.get(json.loads(title[1])) if title else None
        actual = _CARD_ID.search(_header(_read(root, path)))
        if known and actual and actual[1] == known:
            catalog_matches += 1
        if known or actual:
            bindings[reference[1]] = known or actual[1]
    if not home.startswith((_WORKSHOP.encode(), _EXAMPLES.encode())) and catalog_matches < 2:
        return None
    if len(set(bindings.values())) != len(bindings):
        return None

    # Meeting fact IDs include the direction ID. Recompute them from the
    # generator's facts; this also recreates the original processing receipts.
    for path, data in template.items():
        if not path.startswith("People/") or b'type: "person"' not in _header(data).splitlines():
            continue
        record, _ = storage._parse_record(Path(path), data, kinds={"person": "People"})
        for fact in people._parse_facts(record).values():
            direction = fact["direction"].encode()
            original = {**fact, "direction": bindings.get(direction, direction).decode()}
            identifier = people.fact_id(record["id"], fact["sourceId"], original)
            bindings[fact["id"].encode()] = identifier.encode()

    expected = {}
    verified_areas = set()
    def substitute(match):
        return bindings.get(match[0], match[0])

    for path, data in template.items():
        relative = _IDS.sub(substitute, path.encode()).decode()
        current = _read(root, relative)
        if not current:
            continue
        original = _early_header(_IDS.sub(substitute, data), current)
        expected[relative] = hashlib.sha256(original).hexdigest()
        folder = relative.split("/", 1)[0]
        if current == original and folder in {"Directions", "Notes", "Projects"}:
            verified_areas.add(folder)
    # A matching title, folder name, copied Home, or arbitrary local catalog
    # cannot qualify a vault by itself. Confirm independent fixture records.
    return expected if len(verified_areas) >= 2 else None
