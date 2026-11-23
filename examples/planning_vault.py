"""Create a separate planning practice vault for IT, personal life and perfumery."""

import argparse
from pathlib import Path
import sys
import tempfile
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from second_brain import storage
from second_brain.examples import create_examples
from second_brain.planning import Planning


def generate(destination):
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Выберите новый каталог для учебного хранилища: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        root = Path(temporary) / "vault"
        create_examples(root)
        records = {record["title"]: record for _, record in storage.iter_records(root)}
        personal = records["Личное"]["id"]
        perfume = records["Парфюмерия"]["id"]
        mayak = records["Переезд базы заказов"]["id"]
        series_path = storage.create(root, "project", "Выпуск кедровой серии",
                                     "Цель — подготовить пробную серию.", direction=perfume,
                                     aliases=["Кедровая серия"])
        series = storage.read_record(series_path)["id"]

        planning = Planning(root)
        week = planning.create_plan("План недели")
        launch = planning.create_plan("Запуск Маяка")
        travel = planning.create_plan("Подготовка к поездке")
        trials = planning.create_plan("Пробы кедровой серии")
        recovery = planning.create_task("Проверить восстановление базы", project=mayak, due="2026-11-25")
        travel_kit = planning.create_task("Забрать дорожный набор", direction=personal, due="2026-11-26")
        compare = planning.create_task("Сравнить кедровые пробы", project=series, due="2026-11-27")
        spare = planning.create_task("Выбрать чехол для документов", direction=personal)
        for task, plan in ((recovery, launch), (travel_kit, travel), (compare, trials)):
            planning.add_task(week["id"], task["id"])
            planning.add_task(plan["id"], task["id"])
        planning.add_task(travel["id"], spare["id"])

        with (root / "Home.md").open("a", encoding="utf-8") as home:
            home.write("\n## Учебные планы\n\n"
                       "Сроки в примерах вымышлены. В плане недели собраны задачи всех трёх направлений.\n\n")
            for plan in (week, launch, travel, trials):
                home.write(f'- [{plan["title"]}]({quote(plan["path"])})\n')
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
