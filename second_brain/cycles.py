"""Close a plan by publishing one dated, readable Markdown snapshot in the plan."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re

from . import storage


_META = "<!-- brain-cycle: "
_ROW = " <!-- brain-cycle-row: "
_END = "<!-- /brain-cycle -->"
_DATES = ("start", "due", "forecast", "completed")
_TABLE = "| Работа | Тип | Начало | Согласованный срок | Прогноз | Факт завершения | Карточка |"
_DIVIDER = "|---|---|---|---|---|---|---|"
CYCLE_SEAL = "brain_cycle_snapshot"


def _digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def closed_metadata(plan):
    """Older plans have neither a seal in YAML nor a reserved snapshot block."""
    body = plan["body"]
    if CYCLE_SEAL not in plan["field_lines"] and _META not in body and _END not in body:
        return None
    headers = [line for line in body.splitlines() if line.startswith(_META)]
    try:
        if len(headers) != 1 or not headers[0].endswith(" -->"):
            raise ValueError
        meta = json.loads(headers[0][len(_META):-4])
        if (meta["version"] != 1 or meta["id"] != plan["id"] or not isinstance(meta["title"], str)
                or not re.fullmatch("[0-9a-f]{64}", meta["revision"])
                or not re.fullmatch("[0-9a-f]{64}", plan.get(CYCLE_SEAL, ""))):
            raise ValueError
        if datetime.fromisoformat(meta["closedAt"]).tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, KeyError) as error:
        raise storage.ConflictError("Проверьте отметку закрытия в файле плана.") from error
    return meta


def _cells(row):
    title = storage.markdown_label(row.get("title") or row["id"]).replace("|", "&#124;")
    kind = "Нет карточки" if row.get("missing") else ("Веха" if row["kind"] == "milestone" else "Задача")
    return "| " + " | ".join([title, kind, *(row[k] or "—" for k in _DATES)]) + " | "


def _project_lines(meta):
    projects = meta.get("includedProjects", [])
    if not projects:
        return []
    return ["<!-- brain-cycle-projects -->", "### Проекты, включённые в план на момент закрытия", "",
            *["- " + storage.markdown_label(p["title"] or p["id"]) for p in projects],
            "<!-- /brain-cycle-projects -->", ""]


def _okr_lines(meta, rows):
    structure = meta.get("okr")
    if not structure or not structure["objectives"]:
        return []
    tasks = {row["id"]: row for row in rows}
    lines = ["<!-- brain-cycle-okr -->", "### Цели и ключевые результаты на момент закрытия", ""]
    def progress_text(entry):
        progress = entry.get("progress")
        if not progress:
            return ""
        value = progress.get("value")
        if value is None:
            return " · готовность не рассчитана"
        return " · готовность " + value + "%" + (" (с оценками)" if progress["hasEstimates"] else "")

    def observation_text(obs, unit):
        if not obs:
            return "нет записи"
        value = obs["value"]
        if type(value) is bool:
            value = "да" if value else "нет"
        source = obs["source"]
        label = f'{source["path"]}, строка {source["line"]}'
        link = storage.markdown_link(label, source["path"], PurePosixPath(meta["path"]).parent)
        return " ".join(filter(None, (str(value), unit, "·", obs["date"], "·", link)))

    if structure.get("progress"):
        lines.extend(["Период" + progress_text(structure), ""])
    for goal in structure["objectives"]:
        heading = "#### " + storage.markdown_label(goal["title"])
        if goal.get("directionTitle"):
            heading += " · " + storage.markdown_label(goal["directionTitle"])
        lines.extend([heading + progress_text(goal), ""])
        for result in goal["results"]:
            unit = storage.markdown_label(result["unit"] or "")
            line = "- " + storage.markdown_label(result["title"])
            if result.get("weight") is not None:
                line += " · вес КР " + result["weight"]
            if result["target"] is not None:
                line += f' · ориентир {result["target"]} {unit}'
            last = result["measurement"]
            if last:
                line += f' · {last["date"]} · {last["value"]} {unit}'
                source = last["source"]
                label = f'{source["path"]}, строка {source["line"]}'
                link = storage.markdown_link(label, source["path"], PurePosixPath(meta["path"]).parent)
                line += " · " + link
            lines.append(line + progress_text(result))
            for project in result.get("contextProjects", []):
                title = storage.markdown_label(project.get("title") or project["id"])
                lines.append("  - Контекстный проект · " + title)
            for criterion in result.get("criteria", []):
                rule = "подтверждение" if criterion["kind"] == "boolean" else " ".join(
                    str(s) for s in (criterion["operator"], criterion["target"], criterion["unit"]) if s is not None)
                label = "  - " + storage.markdown_label(criterion["title"]) + " · " + storage.markdown_label(rule)
                unit_label = storage.markdown_label(criterion.get("unit") or "")
                if criterion.get("baseline"):
                    label += " · база " + observation_text(criterion["baseline"], unit_label)
                lines.append(label + " · факт " + observation_text(criterion.get("measurement"), unit_label))
            for task_id in result["taskIds"]:
                task = tasks[task_id]
                label = "  - " + storage.markdown_label(task.get("title") or task_id)
                weight = result.get("taskWeights", {}).get(task_id)
                if weight is not None:
                    label += " · вес " + weight
                if task.get("readiness"):
                    label += " · готовность " + task["readiness"]["value"] + "%"
                lines.append(label)
        lines.append("")
    return [*lines, "<!-- /brain-cycle-okr -->", ""]


def frozen_rows(plan, meta):
    """Return original values with their *current* evidence line in the snapshot."""
    rows, numbers = [], []
    try:
        opened = ended = False
        for number, line in enumerate(plan["body"].splitlines(), plan["body_start"]):
            if line.startswith(_META):
                opened = True
            elif line == _END:
                if not opened or ended:
                    raise ValueError
                ended = True
            elif opened and not ended and _ROW in line:
                visible, encoded = line.rsplit(_ROW, 1)
                if not encoded.endswith(" --> |"):
                    raise ValueError
                row = json.loads(encoded[:-6])
                if not visible.startswith(_cells(row)):
                    raise ValueError
                rows.append(row)
                numbers.append(number)
            elif opened and not ended and line.strip() and line not in (_TABLE, _DIVIDER):
                raise ValueError
        if not ended or _digest([meta, rows]) != plan[CYCLE_SEAL]:
            raise ValueError
        for expected in (_project_lines(meta), _okr_lines(meta, rows)):
            if expected:
                lines = plan["body"].splitlines()
                start = lines.index(expected[0])
                if lines[start:start + len(expected) - 1] != expected[:-1]:
                    raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise storage.ConflictError("Содержимое снимка изменилось. Проверьте закрытый план в Markdown.") from error
    return [{**row, "snapshotLine": n} for row, n in zip(rows, numbers)]


class Cycles:
    def __init__(self, root):
        from .planning import Planning
        self.planning = Planning(root)

    def _preview(self, entry):
        path, _, plan, _ = entry
        closed = closed_metadata(plan)
        if closed is not None:
            return {**self.planning._view(path, plan), "revision": closed["revision"]}
        from .okr import KeyResults
        from .project_membership import included_projects
        view, structure = KeyResults(self.planning.root)._active_board(path, plan, validate_dates=True)
        included = included_projects(self.planning.root, plan)
        if included:
            view["includedProjects"] = included
        from .gantt_groups import GanttGroups
        journals = GanttGroups(self.planning.root).snapshot(entry)
        if journals:
            view["groupJournals"] = journals
        return {**view, "okr": structure, "revision": _digest(["close-plan-okr", view, structure])}

    def preview(self, plan_id):
        with storage._vault_lock(self.planning.root, write=False):
            view = self._preview(self.planning._find("plan", plan_id))
            if "includedProjects" in view:
                view["includedProjectCount"] = len(view.pop("includedProjects"))
            view.pop("groupJournals", None)
            structure = view.pop("okr", None)
            if structure and structure["objectives"]:
                goals = structure["objectives"]
                view["okrCounts"] = {"objectives": len(goals), "results": sum(len(g["results"]) for g in goals)}
            return view

    def _confirmed(self, entry, revision):
        try:
            preview = self._preview(entry)
        except (ValueError, FileNotFoundError) as error:
            raise storage.ConflictError(f"Повторите предпросмотр закрытия. {error}") from error
        if preview["revision"] != revision:
            raise storage.ConflictError("Итог плана изменился. Повторите предпросмотр закрытия.")
        return preview

    def close(self, plan_id, revision):
        if not isinstance(revision, str) or not re.fullmatch("[0-9a-f]{64}", revision):
            raise ValueError("Укажите версию предварительного итога.")
        with storage._write_lock(self.planning.root):
            entry = self.planning._find("plan", plan_id)
            path, original, plan, header = entry
            preview = self._confirmed(entry, revision)
            if preview["status"] == "closed":
                return {**preview, "replayed": True}

            rows = preview["items"]
            closed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            meta = {"version": 1, "id": plan_id, "title": plan["title"], "revision": revision,
                    "closedAt": closed_at, "okr": preview["okr"]}
            if preview.get("groupJournals"):
                meta["groupJournals"] = preview["groupJournals"]
            if preview.get("includedProjects"):
                meta["includedProjects"] = preview["includedProjects"]
            if preview["okr"]["objectives"]:
                meta["path"] = path.relative_to(self.planning.root).as_posix()
            newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
            lines = ["", "## Итог цикла", "", "План — " + storage.markdown_label(plan["title"]), "",
                     "Момент закрытия (UTC) — " + closed_at, "",
                     *_project_lines(meta), *_okr_lines(meta, rows),
                     _META + storage.comment_json(meta) + " -->", "", _TABLE, _DIVIDER]
            for row in rows:
                link = (storage.markdown_link("Открыть", self.planning.root / row["path"], path.parent)
                        if row["path"] else "—")
                encoded = storage.comment_json(row).replace("|", "\\u007c")
                lines.append(_cells(row) + link + _ROW + encoded + " --> |")
            lines.extend([_END, ""])
            body = plan["body"]
            if body and not body.endswith(("\n", "\r")):
                body += newline
            header = storage._patch_header(header, plan, {CYCLE_SEAL: _digest([meta, rows])})
            saved = header + (body + newline.join(lines)).encode()
            record, _ = storage._parse_record(path, saved, kinds=("plan",), optional_scalars=(CYCLE_SEAL,))
            result = self.planning._view(path, record)
            # Native Markdown editors do not take the CLI lock. Recheck the other
            # files once the snapshot is ready; replace_bytes guards the plan itself.
            self._confirmed(entry, revision)
            storage.replace_bytes(path, original, saved)
            return {**result, "revision": revision, "replayed": False}
