"""Build a separate practice vault with sourced key results in three directions."""

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from examples.planning_vault import generate as generate_plans
from second_brain import demo_cleanup, storage
from second_brain.okr import KeyResults
from second_brain.planning import Planning


def _populate(root, *, legacy=False):
    root = root.resolve()
    planning = Planning(root)
    tasks = {task["title"]: task for task in planning.tasks()}
    results = KeyResults(root)
    cards = []
    if not legacy:
        quarter = planning.create_plan("OKR на IV квартал")
        planning.add_task(quarter["id"], tasks["Выбрать чехол для документов"]["id"])
    for task_title, objective, metric, unit, target, actual, note_title, observation in (
        ("Проверить восстановление базы", "Ускорить восстановление Маяка",
         "Время восстановления базы", "мин", "15", "18", "Протокол восстановления Маяка",
         "2026-11-22. Восстановление базы на стенде заняло 18 мин."),
        ("Забрать дорожный набор", "Облегчить багаж для поездки",
         "Масса дорожного набора", "кг", "4", "4.2", "Взвешивание дорожного набора",
         "2026-11-22. Масса собранного дорожного набора — 4,2 кг."),
        ("Сравнить кедровые пробы", "Увеличить стойкость кедровой композиции",
         "Стойкость кедровой ноты", "ч", "6", "3.5", "Наблюдение за кедровой пробой",
         "2026-11-22. Кедровая нота на контрольном блоттере различалась 3,5 ч."),
    ):
        task = tasks[task_title]
        source = storage.create(root, "note", note_title, observation, direction=task["direction"])
        if legacy:
            parent = {"objective": objective, "direction": task["direction"]}
        else:
            goal = results.create_objective(objective, plan_id=quarter["id"], direction=task["direction"])
            parent = {"objective_id": goal["id"]}
        card = results.create(metric, unit=unit, target=target, task_ids=[task["id"]], **parent)
        cards.append(results.measure(
            card["id"], value=actual, date="2026-11-22", revision=card["revision"],
            source={"path": source.relative_to(root).as_posix(),
                    "line": storage.read_record(source)["body_start"], "quote": observation},
        ))
    with (root / "Home.md").open("a", encoding="utf-8") as home:
        home.write("\n## Цели и ключевые результаты\n\n"
                   "Все замеры учебные. Откройте результат, чтобы сверить значение с протоколом "
                   "и перейти к связанной задаче.\n\n")
        for card in cards:
            home.write("- " + storage.markdown_link(card["title"], root / card["path"], root) + "\n")
        if not legacy:
            home.write("\n## Общий Гант квартала\n\n")
            home.write(storage.markdown_link(quarter["title"], root / quarter["path"], root) +
                       " объединяет три цели. Откройте этот план в рабочей оболочке, чтобы увидеть "
                       "две проектные работы, личное дело и отдельную задачу без связи с КР.\n")


def generate(destination, *, legacy=False):
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Выберите новый каталог для учебного хранилища: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        root = generate_plans(Path(staging) / "vault")
        _populate(root, legacy=legacy)
        demo_cleanup.record_seed(root)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"Каталог учебного хранилища уже занят: {destination}")
        root.rename(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    try:
        print(generate(parser.parse_args().destination))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")
