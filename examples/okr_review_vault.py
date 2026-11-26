"""Create an isolated practice period with criteria, weighted work and a work breakdown."""

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from examples.okr_vault import generate as generate_basic
from second_brain import demo_cleanup, storage
from second_brain.gantt import Gantt
from second_brain.okr import KeyResults
from second_brain.okr_progress import Progress
from second_brain.plan_tree import set_parent
from second_brain.planning import Planning
from second_brain.schedule import Schedule


def populate(root):
    root = Path(root).resolve()
    planning, results, progress = Planning(root), KeyResults(root), Progress(root)
    quarter = next(p for p in planning.plans() if p["title"] == "OKR на IV квартал")
    board = results.board(quarter["id"])
    for goal in board["objectives"]:
        card, = goal["results"]
        operator = ">=" if card["unit"] == "ч" else "<="
        results.add_criterion(card["id"], title=card["title"], kind="numeric", primary=True, operator=operator,
                              request_id="practice-metric", revision=card["revision"])
    area = next(r["id"] for _, r in storage.iter_records(root)
                if r["type"] == "direction" and r["title"] == "Управление ИТ-проектами")
    project = storage.read_record(storage.create(
        root, "project", "Проверка сервиса заказов",
        "Команда проверяет замены, самовывоз и наборы заказов для контрольной серии.", direction=area))["id"]
    warehouse = storage.read_record(storage.create(
        root, "project", "Подключение учебного склада",
        "Проект завершится, когда контрольный заказ пройдёт через сервис и складскую систему.", direction=area))["id"]
    goal = results.create_objective("Подготовить сервис заказов к сезону", plan_id=quarter["id"], direction=area)
    gantt, schedule = Gantt(root), Schedule(root)
    tasks = []
    for title, task_project, weight, ready, start, due, forecast, reason in (
        ("Проверить замены в заказах", project, "0.25", "0", "2026-12-02", "2026-12-16", "2026-12-21",
         "Проверка замен начнётся после получения новой версии каталога."),
        ("Добавить самовывоз в сценарии", project, "0.15", "0", "2026-11-30", "2026-12-11", "2026-12-14",
         "Команда согласовала окно, подготовка сценариев ещё не началась."),
        ("Подготовить данные для прогона", project, "0.10", "80", "2026-11-09", "2026-11-26", "2026-11-25",
         "Четыре из пяти наборов заказов проверены."),
        ("Настроить склад на стенде", warehouse, "0.25", "100", "2026-11-02", "2026-11-18", "2026-11-17",
         "Учебный склад принимает заявки на стенде, настройки проверены."),
        ("Провести прогон со складом", warehouse, "0.25", "70", "2026-11-16", "2026-12-04", "2026-12-08",
         "Пробные отправки прошли, сквозной контрольный прогон ещё предстоит."),
    ):
        task = planning.create_task(title, project=task_project, due=due)
        gantt.edit({"id": task["id"], "field": "start", "value": start, "revision": task["revision"], "actor": "manual"})
        evidence = {"actor": "manual", "reason": reason}
        proposal = schedule.preview(task["id"], "forecast", forecast, **evidence)
        dated = schedule.apply(task["id"], "forecast", forecast, proposal["revision"], **evidence)
        progress.set_readiness(task["id"], value=ready, date="2026-11-23", kind="estimate", actor="manual",
                               reason=reason, revision=dated["taskRevision"])
        tasks.append((task["id"], weight))
    card = results.create("КР 1.2 · Покрыть сценарии заказов", objective_id=goal["id"], unit="%", target=5,
                          task_ids=[identifier for identifier, _ in tasks])
    note = storage.create(root, "note", "Учебный протокол исходного замера",
                          "2026-09-29. Непокрытые сценарии составляют 11% заказов.", direction=area)
    baseline = {"date": "2026-09-29", "value": 11,
                "source": {"path": note.relative_to(root).as_posix(), "line": storage.read_record(note)["body_start"]}}
    card = results.add_criterion(card["id"], title="Доля непокрытых заказов", kind="numeric", primary=True,
                                 operator="<=", target=5, baseline=baseline, revision=card["revision"], request_id="coverage")
    card = results.add_criterion(card["id"], title="Складская система WMS участвует в прогоне",
                                 kind="boolean", revision=card["revision"], request_id="warehouse")
    for context_id in (project, warehouse):
        card = results.context_project(card["id"], context_id, present=True, revision=card["revision"])
    revision = progress.set_weight(card["id"], weight="0.3", revision=card["revision"])["revision"]
    for identifier, weight in tasks:
        revision = progress.set_weight(card["id"], task_id=identifier, weight=weight, revision=revision)["revision"]
    check = planning.create_task("Замерить время выдачи на учебной точке", project=project, due="2026-12-09")
    other = results.create("КР 1.1 · Сократить время выдачи заказа", objective_id=goal["id"],
                           unit="мин", target=12, task_ids=[check["id"]])
    other = results.add_criterion(other["id"], title="Время от обращения до выдачи", kind="numeric", primary=True,
                                  operator="<=", request_id="pickup-time", revision=other["revision"])
    other = progress.set_weight(other["id"], weight="0.7", revision=other["revision"])
    progress.set_weight(other["id"], task_id=check["id"], weight="1", revision=other["revision"])
    milestone = planning.create_task("Проверить достижение порога покрытия", project=project, kind="milestone")
    planning.add_task(quarter["id"], milestone["id"])
    set_parent(planning, quarter["id"], milestone["id"], tasks[0][0], planning.view(quarter["id"])["revision"])
    evidence = {"actor": "manual", "reason": "Учебный прогноз контрольной вехи"}
    proposal = schedule.preview(milestone["id"], "forecast", "2027-01-10", **evidence)
    schedule.apply(milestone["id"], "forecast", "2027-01-10", proposal["revision"], **evidence)
    storage.create(root, "note", "Учебный протокол для ввода нового замера",
                   "2026-11-24. Доля непокрытых заказов составила 4%.\n"
                   "2026-11-24. Участие складской системы WMS в контрольном прогоне подтверждено: да.", direction=area)
    return quarter["id"], card["id"]


def generate(destination):
    target = Path(destination).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Выберите новый каталог для учебного хранилища: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as staging:
        root = generate_basic(Path(staging) / "vault")
        plan_id, result_id = populate(root)
        demo_cleanup.record_seed(root)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Учебный каталог уже занят: {target}")
        root.rename(target)
    return target.resolve(), plan_id, result_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    vault, plan, result = generate(parser.parse_args().destination)
    print(f"{vault}\n#okr?plan={plan}\n#okr/{result}?plan={plan}")
