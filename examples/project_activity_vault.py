"""Create an isolated example of a year's project and its narrower quarter OKR."""

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from examples.okr_review_vault import generate as generate_quarter
from second_brain import demo_cleanup, storage
from second_brain.cycles import Cycles
from second_brain.gantt import Gantt
from second_brain.okr import KeyResults
from second_brain.planning import Planning
from second_brain.project_membership import ProjectMembership


def generate(destination):
    target = Path(destination).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Выберите новый каталог для учебного хранилища: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as staging:
        root, quarter, _ = generate_quarter(Path(staging) / "vault")
        planning, results, gantt = Planning(root), KeyResults(root), Gantt(root)
        area_path = storage.create(root, "direction", "Городская мастерская")
        area = storage.read_record(area_path)["id"]
        project_path = storage.create(root, "project", "Обновление мастерской — 2026",
                                      "В течение года мастерская обновляет рабочие места и выдачу инструментов.",
                                      direction=area, status="В работе", project_start="2026-01-12", due="2026-12-18",
                                      next_step="Проверить выдачу инструмента на вечерней смене.")
        project = storage.read_record(project_path)["id"]
        storage.create(root, "project", "Таблички для выставочных полок", direction=area,
                       status="Идея", next_step="Выбрать помещение для выставки.")
        new_plan = planning.create_plan("Годовой план мастерской · 2026")
        annual = new_plan["id"]
        ProjectMembership(root).set(annual, project, present=True, revision=new_plan["revision"])
        task_ids = []
        for title, start, due in (
            ("Описать рабочие места", "2026-02-02", "2026-03-12"),
            ("Промаркировать ячейки выдачи", "2026-10-05", "2026-10-30"),
            ("Проверить выдачу в вечернюю смену", "2026-11-09", "2026-12-04"),
            ("Подготовить итоговую схему мастерской", "2026-12-07", "2026-12-18"),
        ):
            task = planning.create_task(title, project=project, due=due)
            gantt.edit({"id": task["id"], "revision": task["revision"], "field": "start",
                        "actor": "manual", "value": start})
            planning.add_task(annual, task["id"])
            task_ids.append(task["id"])

        goal = results.create_objective("Ускорить выдачу инструмента", plan_id=quarter, direction=area)
        result = results.create("Сократить время поиска инструмента", objective_id=goal["id"],
                                unit="мин", target=3, task_ids=task_ids[1:3])
        result = results.add_criterion(result["id"], title="Время поиска инструмента", kind="numeric",
                                       primary=True, operator="<=", request_id="tool-search", revision=result["revision"])
        results.context_project(result["id"], project, present=True, revision=result["revision"])

        review = planning.create_plan("Инвентаризация рабочих мест · весна 2026")["id"]
        planning.add_task(review, task_ids[0])
        cycles = Cycles(root)
        cycles.close(review, cycles.preview(review)["revision"])
        demo_cleanup.record_seed(root)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Учебный каталог уже занят: {target}")
        root.rename(target)
    return target.resolve(), project, annual, quarter


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    vault, project, annual, quarter = generate(parser.parse_args().destination)
    print(f"{vault}\n#activity\n#project-gantt/{project}\n#plan/{annual}\n#okr?plan={quarter}")
