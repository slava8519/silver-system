"""Materialize independent, editable examples using the regular storage API."""

import json
import os
from pathlib import Path
import tempfile
from urllib.parse import quote

from . import demo_cleanup, storage


def _link(root, path, title):
    return f"[{title}]({quote(path.relative_to(root).as_posix())})"


def create_examples(root):
    if os.path.lexists(root):
        raise FileExistsError(f"Для примеров нужен новый путь. Уже существует {root}")
    fixture = Path(__file__).resolve().parent.parent / "examples" / "scenarios.json"
    scenarios = json.loads(fixture.read_text(encoding="utf-8"))
    root.parent.mkdir(parents=True, exist_ok=True)
    # Keep partially created examples invisible if generation fails or is interrupted.
    with tempfile.TemporaryDirectory(dir=root.parent) as temporary:
        staged = Path(temporary).resolve() / "vault"
        storage.initialize(staged)
        home = ["# Три направления в одном хранилище", "",
                "Откройте запись или задайте вопрос о ней в новом чате.", ""]
        for scenario in scenarios:
            title = scenario["direction"]
            direction = storage.create(staged, "direction", title, scenario["description"])
            direction_id = storage.read_record(direction)["id"]
            home.extend([f"## {_link(staged, direction, title)}", ""])
            for record in scenario["records"]:
                path = storage.create(staged, record["type"], record["title"], record["body"],
                                      aliases=record["aliases"], direction=direction_id)
                home.append(f"- {_link(staged, path, record['title'])} — {record['question']}")
            home.append("")
        home.extend(["[Как добавить своё направление](Start-Here.md)", ""])
        (staged / "Home.md").write_text("\n".join(home), encoding="utf-8")
        demo_cleanup.record_seed(staged)
        if os.path.lexists(root):
            raise FileExistsError(f"Путь для примеров уже занят: {root}")
        staged.rename(root)
