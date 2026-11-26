"""Create an isolated reporting desk with fictional, verifiable records."""

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from second_brain import demo_cleanup, reports, storage


def populate(root):
    def direction(title):
        return storage.read_record(storage.create(root, "direction", title))["id"]

    def evidence(area, title, statements):
        note = storage.create(root, "note", title, "\n".join(statements), direction=area)
        first = storage.read_record(note)["body_start"]
        return [{"path": note.relative_to(root).as_posix(), "line": first + i, "quote": statement}
                for i, statement in enumerate(statements)]

    work = direction("Управление ИТ-проектами")
    home = direction("Личное")
    workshop = direction("Парфюмерия")
    for title in ("Восстановление базы заказов", "Запуск кабинета поддержки"):
        storage.create(root, "project", title, direction=work)

    proof = evidence(work, "Проверка восстановления на стенде", [
        "19 ноября 2026 года команда восстановила базу заказов на стенде за 18 минут.",
        "Ориентир для стенда — 15 минут. 23 ноября команда согласовала повторную проверку после настройки диска.",
    ])
    series = reports.create_series(root, title="Новости сервисной команды", template_id="team-digest",
                                   cadence="weekly", start="2026-11-16")
    reports.save_issue(
        root, title="Восстановление базы заказов · 16–22 ноября", template_id="team-digest",
        template_version=1, series_id=series["id"], start=series["nextStart"], end=series["nextEnd"],
        as_of="2026-11-23",
        body=("На неделе с 16 по 22 ноября восстановление базы заказов на стенде заняло "
              "на три минуты больше ориентира.\n\n"
              "## Восстановление базы\n\n- 19 ноября базу восстановили за 18 минут при ориентире 15 минут.\n"
              "  - [ToDo] Повторить проверку после настройки диска."),
        sources=[dict(proof[0], claim="Результат и условия проверки"),
                 dict(proof[1], claim="Ориентир и согласованный следующий шаг")])
    digest = reports.get_template(root, "team-digest")
    reports.save_template(
        root, id=digest["id"], revision=digest["revision"], title=digest["title"],
        audience=digest["audience"], body=digest["body"],
        guidance=digest["guidance"] + "\n\nУкажите дату замера, фактическое значение и ориентир в одной строке.")

    proof = evidence(home, "Сверка маршрута", [
        "25 ноября 2026 года организаторы подтвердили два варианта сбора группы: у вокзала или у музея.",
        "До бронирования автобуса группе предстоит выбрать место сбора.",
    ])
    reports.save_issue(
        root, title="Где собраться перед поездкой", template_id="meeting-agenda", template_version=1,
        body=("Цель встречи — выбрать место сбора группы до бронирования автобуса.\n\n"
              "## Главное к встрече\n\n- **Маршрут**\n"
              "  - 25 ноября организаторы подтвердили два возможных места сбора.\n\n"
              "## Вопросы для решения\n\n- **Место сбора**\n"
              "  - Где встречаемся — у вокзала или у музея?"),
        sources=[dict(proof[0], claim="Два возможных места сбора"),
                 dict(proof[1], claim="Место нужно выбрать до бронирования")])

    proof = evidence(workshop, "Дневник кедровой пробы", [
        "24 ноября 2026 года кедровая нота на контрольном блоттере различалась три с половиной часа.",
    ])
    reports.save_issue(
        root, title="Стойкость кедровой ноты", template_id="free-report", template_version=1,
        body=("В пробе 24 ноября кедровая нота различалась на контрольном блоттере "
              "три с половиной часа. Наблюдение относится к одной пробе."),
        sources=[dict(proof[0], claim="Стойкость на контрольном блоттере")])


def generate(destination):
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Выберите новый каталог для учебного хранилища: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        root = storage.initialize(Path(staging) / "vault")
        populate(root)
        with (root / "Home.md").open("a", encoding="utf-8") as page:
            page.write("\n## Учебная редакция\n\nВсе данные в этом хранилище вымышлены. "
                       "В админке откройте «Отчёты», выберите выпуск и сверьте текст с основанием.\n")
        demo_cleanup.record_seed(root)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)
        root.rename(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    try:
        print(generate(parser.parse_args().destination))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
