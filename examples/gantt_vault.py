"""Create a synthetic practice vault for the same Gantt in the browser and Obsidian."""

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from examples.planning_vault import generate as generate_plans
from second_brain import demo_cleanup, storage
from second_brain.batch import Batch
from second_brain.capture import Capture
from second_brain.gantt import Gantt
from second_brain.planning import Planning


def seed_schedule_practice(root):
    """Prepare starting estimates and dated evidence for the synthetic Gantt exercise."""
    from second_brain.schedule import Schedule

    root = root.resolve()
    tasks = {task["title"]: task for task in Planning(root).tasks()}
    schedule = Schedule(root)
    for task_title, note_title, initial, first, update in (
        ("Проверить восстановление базы", "Окно проверки на стенде", "2026-11-24",
         "20 ноября. По первой оценке восстановление базы проверим 24 ноября 2026 года.",
         "23 ноября. Стенд освободится позже. Новый прогноз проверки — 27 ноября 2026 года. Согласованный срок остаётся 25 ноября."),
        ("Забрать дорожный набор", "Сообщение о выдаче набора", "2026-11-26",
         "21 ноября. Ожидаю забрать дорожный набор 26 ноября 2026 года.",
         "23 ноября. Пункт изменил время выдачи. Теперь рассчитываю забрать набор 28 ноября 2026 года."),
        ("Сравнить кедровые пробы", "Окно сравнения кедровых проб", "2026-11-29",
         "22 ноября. Первую оценку двух проб рассчитываю закончить 29 ноября 2026 года.",
         "23 ноября. Для сравнения освободилось более раннее окно. Новый прогноз — 26 ноября 2026 года."),
    ):
        task = tasks[task_title]
        note = storage.create(root, "note", note_title, first + "\n\n" + update, direction=task["direction"],
                              request_id="gantt-practice/" + note_title)
        if task.get("forecast") or any(c["field"] == "forecast" for c in schedule.history(task["id"])):
            continue
        number = next(n for n, line in enumerate(note.read_text(encoding="utf-8").splitlines(), 1) if line == first)
        evidence = [{"path": note.relative_to(root).as_posix(), "line": number, "quote": first}]
        preview = schedule.preview(task["id"], "forecast", initial, actor="ai", sources=evidence)
        schedule.apply(task["id"], "forecast", initial, preview["revision"], actor="ai", sources=evidence)

    backup = tasks["Подготовить резервную копию"]
    storage.create(root, "note", "Акт резервного копирования",
                   "20 ноября 2026 года подготовку резервной копии завершили. Копия сохранена, контрольная сумма сверена.",
                   direction=backup["direction"], request_id="gantt-practice/backup")


def seed_milestone_practice(root):
    """Add the three fictional acceptance criteria to a Gantt practice vault."""
    from second_brain.dependencies import Dependencies
    from second_brain.schedule import Schedule

    root = root.resolve()
    planning, gantt, schedule = Planning(root), Gantt(root), Schedule(root)
    dependencies = Dependencies(root)
    tasks = {task["title"]: task for task in planning.tasks()}
    plans = {plan["title"]: plan["id"] for plan in planning.plans()}
    for before, title, plan, due, criterion in (
        ("Проверить восстановление базы", "Готовность к переключению", "Запуск Маяка", "2026-11-29",
         "Для переключения нужна успешная проверка восстановления базы на стенде."),
        ("Забрать дорожный набор", "Всё готово к выезду", "Подготовка к поездке", "2026-11-30",
         "Последнее условие перед выездом — получить собранный дорожный набор."),
        ("Сравнить кедровые пробы", "Выбрать образец для серии", "Пробы кедровой серии", "2026-11-28",
         "Для выбора образца нужны результаты сравнения двух кедровых проб."),
    ):
        task = tasks[before]
        key = "milestone-practice/" + title
        scope = {"project": task["project"]} if task["project"] else {"direction": task["direction"]}
        milestone = planning.create_task(title, kind="milestone", due=due, request_id=key, **scope)
        identifier = milestone["id"]
        for name in (plan, "План недели"):
            planning.add_task(plans[name], identifier)
        dependencies.link(identifier, task["id"])
        agreement = (f"В учебном плане согласована дата вехи {due}. "
                     "Первый прогноз вехи совпадает с прогнозом её единственной предшествующей работы.")
        note = storage.create(root, "note", "Условие вехи — " + title, f"{criterion}\n\n{agreement}",
                              direction=task["direction"], request_id=key)
        lines = note.read_text(encoding="utf-8").splitlines()

        def source(line):
            return {"path": note.relative_to(root).as_posix(), "line": lines.index(line) + 1, "quote": line}

        card = gantt.detail(identifier)
        if not card["brief"]["text"] and not card["brief"]["manual"]:
            gantt.edit({"id": identifier, "revision": card["revision"], "field": "brief", "actor": "ai",
                        "text": criterion, "sources": [source(criterion)]})
        if task["forecast"] and not card["forecast"] and not any(
            change["field"] == "forecast" for change in schedule.history(identifier)
        ):
            options = {"actor": "ai", "reason": f"Первый прогноз по работе «{before}»",
                       "sources": [source(agreement)]}
            baseline = schedule.preview(identifier, "forecast", task["forecast"], **options)
            schedule.apply(identifier, "forecast", task["forecast"], baseline["revision"], **options)


def _populate(root):
    root = root.resolve()
    planning, gantt = Planning(root), Gantt(root)
    plans = {row["title"]: row["id"] for row in planning.plans()}
    tasks = {row["title"]: row for row in planning.tasks()}
    records = {r["title"]: r for _, r in storage.iter_records(root)}
    launch, week = plans["Запуск Маяка"], plans["План недели"]
    mayak = records["Переезд базы заказов"]["id"]
    orbit = records["Запуск кабинета поддержки"]["id"]
    for title, project, due, start, memberships in (
        ("Подготовить резервную копию", mayak, "2026-11-23", "2026-11-19", (launch, week)),
        ("Согласовать окно переключения", mayak, "2026-11-30", None, (launch, week)),
        ("Выбрать время повторной проверки", mayak, None, None, (launch,)),
        ("Проверить вход по одноразовой ссылке", orbit, "2026-11-26", "2026-11-23", (week,)),
    ):
        task = planning.create_task(title, project=project, due=due)
        for plan in memberships:
            planning.add_task(plan, task["id"])
        if start:
            gantt.edit({"id": task["id"], "revision": task["revision"], "field": "start",
                        "actor": "manual", "value": start})
    planning.add_task(week, tasks["Выбрать чехол для документов"]["id"])

    examples = [
        ("Проверить восстановление базы", "2026-11-23", "Управление ИТ-проектами", "Сверка восстановления Маяка",
         "2026-11-20", "Резервная копия подготовлена. Проверку восстановления договорились провести на стенде.",
         "Копия готова. Следующий шаг — восстановить базу на стенде."),
        ("Забрать дорожный набор", None, "Личное", "Подготовка дорожного набора",
         "2026-11-21", "Дорожный набор собран. При получении нужно проверить, что запасной ключ в синем чехле.",
         "Набор собран. При получении проверить запасной ключ."),
        ("Сравнить кедровые пробы", "2026-11-22", "Парфюмерия", "Дневник кедровой серии",
         "2026-11-22", "Подписаны два блоттера. Для сравнения нужно записать впечатления от обеих проб через два часа.",
         "Два блоттера подписаны. Сравнить древесную ноту через два часа."),
    ]
    for title, start, direction, source_title, date, fact, summary in examples:
        task = tasks[title]
        note = storage.create(root, "note", source_title, f"{date}. {fact}", direction=records[direction]["id"])
        line = next(n for n, text in enumerate(note.read_text(encoding="utf-8").splitlines(), 1) if fact in text)
        refs = [{"path": note.relative_to(root).as_posix(), "line": line, "quote": f"{date}. {fact}"}]
        revision = task["revision"]
        if start:
            revision = gantt.edit({"id": task["id"], "revision": revision, "field": "start",
                                   "actor": "manual", "value": start})["revision"]
        saved = gantt.edit({"id": task["id"], "revision": revision, "field": "brief",
                            "actor": "ai", "text": summary, "sources": refs})
        gantt.edit({"id": task["id"], "revision": saved["revision"], "field": "latest", "date": date,
                    "actor": "ai", "text": fact, "sources": refs})
    seed_schedule_practice(root)
    seed_milestone_practice(root)


def _project_updates(root):
    path, project = next((path, record) for path, record in storage.iter_records(root)
                         if record["type"] == "project" and "Маяк" in record["aliases"])
    batch = Batch(root)
    rule = next(rule for rule in batch.routing.rules() if rule["direction"] == project["direction"])
    capture = Capture(root)
    for title, date, quote, summary in (
        ("Акт резервного копирования Маяка", "2026-11-20",
         "Команда «Маяка» подготовила резервную копию базы заказов и сверила контрольную сумму.",
         "Резервная копия готова, контрольная сумма проверена."),
        ("Протокол приёмки Маяка", "2026-11-24",
         "После восстановления базы «Маяка» должны совпасть число заказов и сумма оплат. Команда утвердила этот критерий.",
         "Критерий проверки согласован. После восстановления сравним число заказов и сумму оплат."),
        ("Решение перед переключением Маяка", "2026-11-26",
         "Участники «Маяка» согласовали порядок отката. До переключения нужно проверить восстановление базы на стенде.",
         "Порядок отката согласован. До переключения нужно проверить восстановление базы на стенде."),
    ):
        source_line = f"{date}. {quote}"
        saved = capture.save_text(f"# {title}\n\n{source_line}\n", title)
        source = batch.inbox.preview(saved["path"])
        line = next(row["n"] for row in source["lines"] if row["text"] == source_line)
        action = {"kind": "project_event", "project": project["id"], "direction": project["direction"],
                  "rule_revision": rule["revision"], "expected_revision": storage.read_record(path)["revision"],
                  "line": line, "quote": source_line, "date": date, "text": quote, "summary": summary}
        result = batch.apply({"path": saved["path"], "revision": source["revision"], "actions": [action]})
        if result["triage"] or len(result["applied"]) != 1 or result["applied"][0]["summaryStatus"] != "updated":
            raise ValueError(f"Проверьте разбор учебного протокола «{title}».")
    project_link = storage.markdown_link("Переезд базы заказов", path, root)
    with (root / "Home.md").open("a", encoding="utf-8") as home:
        home.write(f"\n## Что изменилось по Маяку\n\nВ проекте {project_link} откройте «Коротко» "
                   "и три события за 20–26 ноября 2026 года. Под каждым событием есть ссылка на учебный протокол. "
                   "В Ганте проекта выберите «Проверить восстановление базы», чтобы увидеть сводку самой работы.\n")


def generate(destination, *, with_project_updates=False):
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Выберите новый каталог для учебного хранилища: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        root = generate_plans(Path(temp) / "vault")
        _populate(root)
        if with_project_updates:
            _project_updates(root)
        demo_cleanup.record_seed(root)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"Каталог учебного хранилища уже занят: {destination}")
        root.rename(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--project-updates", action="store_true",
                        help="разобрать три учебных протокола и показать события в Ганте Маяка")
    args = parser.parse_args()
    try:
        print(generate(args.destination, with_project_updates=args.project_updates))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")
