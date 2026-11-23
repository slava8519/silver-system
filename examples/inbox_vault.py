"""Create an independent, synthetic Inbox practice vault with the three example directions."""

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from second_brain import storage
from second_brain.examples import create_examples
from second_brain.inbox import Inbox
from second_brain.routing import Routing


RULES = {
    "Управление ИТ-проектами": (
        "Веди состояние проектов по названиям и рабочим именам. Переноси утверждённый статус, "
        "срок или следующий шаг в указанный проект. Явную ответственность участника сохраняй как общую роль.",
        ["status", "due", "next_step"], ["shared"]),
    "Личное": (
        "Сохраняй личные договорённости о поездках в карточке названного человека с личным доступом. "
        "Если человека невозможно определить, уточни у владельца.",
        [], ["personal"]),
    "Парфюмерия": (
        "По пробным сериям переноси согласованный следующий шаг в проект серии. "
        "Указанную по имени ответственную за пробы запиши как общую роль.",
        ["next_step"], ["shared"]),
}

INCOMING = {
    "01 — Планёрка.txt": (
        "У проекта «Маяк» утверждён новый статус «готов к переключению».\n"
        "Для «Орбиты» согласовали следующий шаг «проверить вход по одноразовой ссылке».\n"
        "Лидия Белова отвечает за проверку переключения «Маяка».\n"),
    "02 — Поездка.md": (
        "Личная договорённость для поездки: Лидия Белова хранит запасную бирку в лиловом конверте.\n"),
    "03 — Пробная серия.md": (
        "Для проекта «Кедровая серия» согласовали следующий шаг «повторить пробу на блоттере».\n"
        "Ева Ланская отвечает за разведение образцов проекта «Кедровая серия».\n"),
    "04 — Без названия.txt": (
        "Согласовали новый срок 2026-12-18. Название проекта в сообщении пропущено.\n"),
}


def _add_teams(root, directions):
    """Give the optional people exercise an explicitly fictional owner and three groups."""
    from second_brain.people import People
    from second_brain.teams import Teams

    teams, inbox = Teams(root), Inbox(root)
    owner = teams.create_person("Александра Орлова", request_id="people-practice/owner")
    lydia, = People(root).candidates("Лидия Белова")
    eva = teams.create_person("Ева Ланская", request_id="people-practice/eva")
    for person, name, number, direction, kind, visibility in (
        (lydia, "01 — Планёрка.txt", 3, "Управление ИТ-проектами", "role", "shared"),
        (lydia, "02 — Поездка.md", 1, "Личное", "fact", "personal"),
        (eva, "03 — Пробная серия.md", 2, "Парфюмерия", "role", "shared"),
    ):
        path = "Inbox/" + name
        preview = inbox.preview(path, start=number, limit=1)
        inbox.process({"path": path, "revision": preview["revision"], "person": {"id": person["id"]},
                       "facts": [{"kind": kind, "direction": directions[direction], "visibility": visibility,
                                  "line": number, "text": preview["lines"][0]["text"]}]})

    for name, members in (("Команда запуска", [lydia]), ("Попутчики", [owner, lydia]), ("Мастерская", [eva])):
        group = teams.create(name, request_id="people-practice/" + name)
        for person in members:
            group = teams.member(group["id"], person["id"], present=True, revision=group["revision"])
        if name == "Команда запуска":
            teams.set_context(revision=teams.context()["revision"], owner=owner["id"], team=group["id"])


def generate(destination, *, with_teams=False):
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Выберите новый каталог для учебного хранилища: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        root = Path(temporary) / "vault"
        create_examples(root)
        directions = {record["title"]: record["id"] for _, record in storage.iter_records(root)
                      if record["type"] == "direction"}
        for title, (instructions, fields, scopes) in RULES.items():
            Routing(root).set_rule(directions[title], instructions, fields, scopes)
        storage.create(root, "project", "Выпуск кедровой серии", "Цель — подготовить пробную серию.",
                       direction=directions["Парфюмерия"], aliases=["Кедровая серия"])
        previous = "Inbox/00 — Знакомство.md"
        introduction = "Лидия Белова участвует в проекте «Маяк»."
        (root / previous).write_text(introduction + "\n", encoding="utf-8")
        inbox = Inbox(root)
        inbox.process({"path": previous, "revision": inbox.preview(previous)["revision"],
                       "person": {"name": "Лидия Белова", "new": True},
                       "facts": [{"kind": "fact", "direction": directions["Управление ИТ-проектами"],
                                  "visibility": "shared", "line": 1, "text": introduction}]})
        for name, content in INCOMING.items():
            (root / "Inbox" / name).write_text(content, encoding="utf-8")
        if with_teams:
            _add_teams(root, directions)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"Каталог учебного хранилища уже занят: {destination}")
        root.rename(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--teams", action="store_true", help="добавить учебные команды, роли и профиль владельца")
    try:
        args = parser.parse_args()
        print(generate(args.destination, with_teams=args.teams))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")
