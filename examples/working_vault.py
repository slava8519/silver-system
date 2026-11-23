"""Create a separate synthetic working vault for trying the normal product."""

import argparse
from pathlib import Path
import sys
import tempfile

# Allow 'python3 examples/working_vault.py ...' from a source checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from second_brain import storage
from second_brain.inbox import Inbox
from second_brain.workspace import Workspace


def add_meetings(root, printshop, audio):
    """Populate only the two synthetic inbox files and confirm the first meeting."""
    meeting = (Path(__file__).parent / "meeting.md").read_bytes()
    relative = "Inbox/Встреча перед выходом атласа.md"
    followup = ("# Сверка перед выездной записью\n\nУчебная запись.\n\n"
                "Дарья Миронова согласовала запись у пешеходного моста.\n")
    for path, content in ((root / relative, meeting), (root / "Inbox/Сверка перед выездом.md", followup.encode())):
        try:
            with path.open("xb") as target:
                target.write(content)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError(f"Учебный файл уже изменён: {path}")
    reader = Inbox(root)
    preview = reader.preview(relative)
    lines = meeting.decode("utf-8").splitlines()
    facts = [{"kind": kind, "direction": direction, "visibility": visibility,
              "line": line, "text": lines[line - 1]} for kind, direction, visibility, line in (
                  ("role", printshop, "shared", 5), ("role", audio, "shared", 6),
                  ("observation", audio, "personal", 7))]
    return reader.process({"path": relative, "revision": preview["revision"],
                           "person": {"new": True, "name": "Дарья Миронова"}, "facts": facts})


def generate(destination):
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Выберите новый каталог для учебных данных: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        root = storage.initialize(Path(temporary) / "vault")
        workspace = Workspace(root)

        def create(kind, title, body, direction=None, **fields):
            return workspace.create_record({"type": kind, "title": title, "body": body,
                                           "direction": direction, **fields})["id"]

        printshop = create("direction", "Городская типография", "Издаём небольшие серии печатных материалов.")
        garden = create("direction", "Дом и сад", "Собираю наблюдения и веду домашние проекты.")
        audio = create("direction", "Полевые записи", "Коллекция звуков города и заметки о записи.")
        create("direction", "Изучение языков", "Пока только определяю круг тем.")

        atlas = create("project", "Выпуск атласа маршрутов",
                       "Цель — подготовить атлас к печати.\n\nРешили печатать карты на матовой бумаге.",
                       printshop, aliases=["Меридиан"], status="В работе", due="2026-12-15",
                       nextStep="Сверить цвет на пробном оттиске.")
        create("project", "Серия городских открыток",
               "Цель — выпустить серию открыток.\n\nСогласовали двухцветную печать для первого тиража.",
               printshop, aliases=["Палитра"], status="На согласовании")
        create("note", "Формат макетов", "Перед отправкой добавляем вылеты по три миллиметра.",
               printshop, aliases=["Вылеты"])
        create("project", "Полка для рассады",
               "Цель — освободить место у окна.\n\nРешили закрепить полку на двух кронштейнах.",
               garden, aliases=["Стеллаж"], nextStep="Измерить ширину оконной ниши.")
        create("note", "Запас семян", "Семена тимьяна лежат в жестяной коробке.", garden)
        create("project", "Звуковая прогулка", "Цель — собрать запись прогулки по набережной.",
               audio, aliases=["Прибой"])
        create("note", "Шум дождя", "Для дождя взяли поролоновую ветрозащиту.", audio)
        create("note", "Заметка без направления", "Карточку можно привязать к направлению позже.")
        add_meetings(root, printshop, audio)

        # Synthetic extension fields model a working card with its own vocabulary.
        # Their values and all the records above were written specifically for this sample.
        path = storage.find_record(root, atlas)
        original = path.read_text(encoding="utf-8")
        path.write_text(original.replace("\n---\n", '\nphase: "Цветопроба"\nhealth: "Ожидаем образец"\n---\n', 1),
                        encoding="utf-8")
        (root / "Home.md").write_text(
            "# Учебное хранилище\n\nВсе карточки, решения, статусы и сроки в этой папке вымышлены.\n\n"
            "Для проверки общего вопроса спросите: «Что решили по Меридиану и Палитре?»\n",
            encoding="utf-8")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)
        root.rename(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    try:
        print(generate(parser.parse_args().destination))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")
