"""Explicit finish-to-start links and forecast scenarios over shared task cards."""

from collections import defaultdict, deque
from datetime import date, datetime, timezone
from graphlib import CycleError, TopologicalSorter
import json
import re
from urllib.parse import unquote

from . import plan_transaction, schedule, storage
from .planning import Planning, validate_due


_BEGIN = "<!-- brain-predecessors -->"
_END = "<!-- /brain-predecessors -->"
_EDGE = re.compile(r"^\s*[-*+]\s+.+\s+<!-- predecessor-id: ([A-Za-z0-9._~%\-]+) -->\s*$")
_CHANGE = " <!-- brain-dependency-change: "


def _section(record):
    lines = record["body"].splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == _BEGIN]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == _END]
    if not starts and not ends:
        return lines, {}, None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError(f'Проверьте раздел предшественников в «{record["title"]}».')
    edges = {}
    for i in range(starts[0] + 1, ends[0]):
        if "<!-- predecessor-id:" not in lines[i]:
            continue
        match = _EDGE.fullmatch(lines[i])
        if not match:
            raise ValueError(f'Проверьте ссылку на предшественника в «{record["title"]}».')
        identifier = unquote(match[1], errors="strict")
        if identifier in edges:
            raise ValueError(f'Предшественник дважды указан в «{record["title"]}».')
        edges[identifier] = i
    return lines, edges, ends[0]


def predecessor_ids(record):
    return list(_section(record)[1])


def dependency_history(record, path):
    entries = []
    for number, line in enumerate(record["body"].splitlines(), record["body_start"]):
        if _CHANGE not in line:
            continue
        try:
            if not line.startswith("- ") or not line.endswith(" -->"):
                raise ValueError
            change = json.loads(line.rsplit(_CHANGE, 1)[1][:-4])
            if (change["action"] not in ("add", "remove") or change["actor"] not in ("ai", "manual")
                    or not isinstance(change["predecessor"], str) or not change["predecessor"]
                    or not isinstance(change["predecessorTitle"], str)
                    or datetime.fromisoformat(change["updated"]).tzinfo is None):
                raise ValueError
        except (KeyError, TypeError, ValueError) as error:
            raise storage.ConflictError(f"Проверьте запись истории связей в {path}, строка {number}.") from error
        entries.append({**change, "path": path, "line": number})
    return entries


def _log_link(lines, newline, predecessor, remove, actor):
    stamp = datetime.now(timezone.utc)
    change = {"action": "remove" if remove else "add", "actor": actor, "updated": stamp.isoformat(),
              "predecessor": predecessor["id"], "predecessorTitle": predecessor["title"]}
    author = "владелец" if actor == "manual" else "ИИ"
    verb = "Снят" if remove else "Добавлен"
    entry = (f'- {stamp:%Y-%m-%d %H:%M} UTC · {author} · {verb} предшественник '
             + storage.markdown_label(predecessor["title"]) + _CHANGE + storage.comment_json(change) + " -->" + newline)
    last = next((i for i in reversed(range(len(lines))) if _CHANGE in lines[i]), None)
    index = len(lines) if last is None else last + 1
    if last is None:
        entry = newline + "## История связей" + newline + newline + entry
    if index and not lines[index - 1].endswith(("\n", "\r")):
        entry = newline + entry
    lines.insert(index, entry)


def _reachable(origins, adjacency):
    result = set()
    queue = deque(origins)
    while queue:
        key = queue.popleft()
        if key not in result:
            result.add(key)
            queue.extend(adjacency.get(key, ()))
    return result


class _Graph:
    def __init__(self, planning):
        self.root = planning.root
        self.cards = {}
        self.parents = {}
        self.children = defaultdict(list)
        for entry in planning._entries("task"):
            record = entry[2]
            key = record["id"]
            self.cards[key] = entry
            self.parents[key] = predecessor_ids(record)
            for parent in self.parents[key]:
                self.children[parent].append(key)

    def card(self, key):
        try:
            return self.cards[key]
        except KeyError as error:
            raise FileNotFoundError(f"Карточка не найдена: {key}") from error

    def describe(self, key):
        if key not in self.cards:
            return {"id": key, "title": key, "kind": None, "path": None, "missing": True}
        path, _, record, _ = self.cards[key]
        return {"id": key, "title": record["title"], "path": path.relative_to(self.root).as_posix(),
                "kind": "milestone" if record.get("task_kind") == "milestone" else "task"}

    def order(self, subset):
        graph = {key: [p for p in self.parents.get(key, ()) if p in subset] for key in sorted(subset)}
        try:
            return list(TopologicalSorter(graph).static_order())
        except CycleError as error:
            path = " → ".join(self.describe(key)["title"] for key in error.args[1])
            raise ValueError(f"Цикл зависимостей: {path}.") from error

    def links(self, key):
        self.card(key)
        def linked(other, successor, predecessor):
            record = self.cards[successor][2]
            return {**self.describe(other), "linkPath": self.describe(successor)["path"],
                    "linkLine": record["body_start"] + _section(record)[1][predecessor]}
        return {"id": key,
                "predecessors": [linked(p, key, p) for p in self.parents[key]],
                "successors": [linked(c, c, key) for c in self.children[key]]}


def _plan_memberships(planning, members):
    plans = defaultdict(list)
    for summary, ids in planning.active_memberships():
        for key in members.intersection(ids):
            plans[key].append({k: summary[k] for k in ("id", "title", "path")})
    return plans


def _finish(record, header):
    return schedule._current_field(record, header, "completed") or schedule._current_field(record, header, "forecast")


def _slack(due, finish):
    if due and finish:
        return (date.fromisoformat(due) - date.fromisoformat(finish)).days
    return None


def _independent_forecast(record, path, before):
    """A prior calculation is not an independent lower bound on the next one."""
    if not before or '"scenarioRevision"' not in record["body"]:
        return before
    for change in reversed(schedule.schedule_history(record, path)):
        if change["field"] != "forecast":
            continue
        if (change["after"] == before and change.get("scenarioRevision")
                and change.get("scenarioRoot") != record["id"]):
            return validate_due(change.get("independentForecast", change["before"]))
        break
    return before


def _project_successor(record, kind, floor, finishes):
    if any(value is None for value in finishes):
        return None, "Нужен прогноз или факт завершения каждого предшественника."
    earliest = max(finishes)
    if kind == "milestone":
        return max(earliest, floor or earliest), "Веха возможна после завершения всех предшественников."
    start, due = validate_due(record.get("start")), validate_due(record.get("due"))
    if not start or not due or start > due:
        return None, "Для расчёта длительности нужны начало и согласованный срок задачи."
    # Use the recorded planned span every time; using a shifted forecast here
    # would incorrectly lengthen the task after each subsequent simulation.
    span = date.fromisoformat(due) - date.fromisoformat(start)
    if earliest <= start and floor and floor >= earliest:
        return floor, "Предшественники укладываются до начала задачи."
    try:
        end = (date.fromisoformat(max(earliest, start)) + span).isoformat()
    except OverflowError as error:
        raise ValueError("Расчёт выходит за пределы допустимых календарных дат.") from error
    return max(end, floor or end), f"После предшественников сохраняется длительность {span.days} календ. дн."


class Dependencies:
    def __init__(self, root):
        self.planning = Planning(root)
        self.root = self.planning.root

    def links(self, task_id):
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("Укажите ID работы.")
        return _Graph(self.planning).links(task_id)

    def history(self, task_id):
        path, _, record, _ = self.planning._find("task", task_id)
        return dependency_history(record, path.relative_to(self.root).as_posix())

    def link(self, successor_id, predecessor_id, *, remove=False, actor="ai"):
        if not all(isinstance(key, str) and key.strip() for key in (successor_id, predecessor_id)):
            raise ValueError("Укажите ID работы и предшественника.")
        if type(remove) is not bool or actor not in ("ai", "manual"):
            raise ValueError("Выберите действие и укажите автора связи.")
        with storage._write_lock(self.root):
            graph = _Graph(self.planning)
            path, previous, record, header = graph.card(successor_id)
            lines, edges, end = _section(record)
            if not remove:
                parent, _, parent_record, _ = graph.card(predecessor_id)
            if (remove and predecessor_id not in edges) or (not remove and predecessor_id in edges):
                return graph.links(successor_id)
            newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
            dependency_history(record, path.relative_to(self.root).as_posix())
            if remove:
                del lines[edges[predecessor_id]]
            else:
                # Test the proposed edge before touching either Markdown card.
                graph.parents[successor_id].append(predecessor_id)
                graph.order(_reachable([successor_id], graph.parents))
                link = storage.markdown_link(parent_record["title"], parent, path.parent)
                entry = f"- {link} {storage.id_marker('predecessor', predecessor_id)}{newline}"
                if end is None:
                    if lines and not lines[-1].endswith(("\n", "\r")):
                        lines[-1] += newline
                    lines.extend([newline, "## Предшественники" + newline + newline, _BEGIN + newline,
                                  entry, _END + newline])
                else:
                    lines.insert(end, entry)
            _log_link(lines, newline, graph.describe(predecessor_id), remove, actor)
            storage.replace_bytes(path, previous, header + "".join(lines).encode())
            return _Graph(self.planning).links(successor_id)

    def _scenario(self, graph, task_id, forecast, actor):
        _, _, origin, origin_header = graph.card(task_id)
        if schedule._current_field(origin, origin_header, "completed"):
            raise ValueError("У работы уже записан факт завершения. Для сценария выберите открытую работу.")
        affected = _reachable([task_id], graph.children)
        inputs = affected.union(*(graph.parents[key] for key in affected))
        order = [key for key in graph.order(_reachable(affected, graph.parents)) if key in affected]
        plans = _plan_memberships(self.planning, affected)
        ends = {}
        for key in inputs - affected:
            if key in graph.cards:
                _, _, record, header = graph.card(key)
                ends[key] = _finish(record, header)
        rows = []
        for key in order:
            _, _, record, header = graph.card(key)
            info = graph.describe(key)
            before = schedule._current_field(record, header, "forecast")
            floor = _independent_forecast(record, info["path"], before)
            completed = schedule._current_field(record, header, "completed")
            due = validate_due(record.get("due"))
            parents = graph.parents[key]
            finishes = [ends.get(p) for p in parents]
            if key == task_id:
                if any(end and end > forecast for end in finishes):
                    raise ValueError("Предложенный прогноз раньше завершения предшественника.")
                result, reason = forecast, "Прогноз указан в сценарии."
                if None in finishes:
                    reason += " Для части предшественников ещё нет даты завершения или прогноза."
            elif completed:
                result, reason = before, "Работа уже завершена. Для следующих работ учтён факт."
            else:
                result, reason = _project_successor(record, info["kind"], floor, finishes)
            ends[key] = completed or result
            if completed:
                status = "completed"
            elif result is None:
                status = "blocked"
            else:
                status = "changed" if result != before else "unchanged"
            rows.append({**info, "due": due, "completed": completed, "before": before,
                         "after": before if status == "blocked" else result,
                         "slackDays": _slack(due, ends[key]), "status": status,
                         "reason": reason, "independentForecast": floor, "plans": plans[key]})
        versions = [(k, graph.card(k)[2]["revision"] if k in graph.cards else None) for k in sorted(inputs)]
        revision = schedule._hash(["finish-to-start-v1", task_id, forecast, actor, versions, rows])
        return {"id": task_id, "forecast": forecast, "revision": revision, "rows": rows,
                "changed": sum(row["status"] == "changed" for row in rows)}

    @staticmethod
    def _validate(task_id, forecast, actor):
        if not isinstance(task_id, str) or not task_id.strip() or not validate_due(forecast) or actor not in ("manual", "ai"):
            raise ValueError("Для сценария укажите задачу, дату прогноза и автора.")

    def preview(self, task_id, forecast, *, actor="ai"):
        self._validate(task_id, forecast, actor)
        with storage._vault_lock(self.root, write=False):
            return self._scenario(_Graph(self.planning), task_id, forecast, actor)

    def _replayed(self, graph, task_id, forecast, revision, actor):
        rows = []
        expected_counts = set()
        # A receipt may be on a successor only, when the root already had the
        # requested date. Resolve by ID even if a card has since been renamed.
        for key, (_, _, record, _) in graph.cards.items():
            if revision not in record["body"]:
                continue
            info = graph.describe(key)
            for entry in schedule.schedule_history(record, info["path"]):
                if entry.get("scenarioRevision") != revision:
                    continue
                if (entry.get("scenarioRoot"), entry.get("scenarioForecast"), entry["actor"]) != (task_id, forecast, actor):
                    raise storage.ConflictError("Эта версия принадлежит другому сценарию.")
                if "scenarioChanged" in entry:
                    expected_counts.add(entry["scenarioChanged"])
                current, due, completed = (record.get(k) for k in ("forecast", "due", "completed"))
                rows.append({**info, "due": due, "completed": completed, "before": current, "after": current,
                             "appliedForecast": entry["after"], "status": "completed" if completed else "unchanged",
                             "slackDays": _slack(due, completed or current),
                             "reason": "Сценарий уже применён. Показаны текущие даты карточки.", "plans": []})
        if rows:
            if expected_counts and expected_counts != {len(rows)}:
                raise storage.ConflictError("В карточках сохранилась только часть записей сценария. Повторите расчёт.")
            plans = _plan_memberships(self.planning, {r["id"] for r in rows})
            for row in rows:
                row["plans"] = plans[row["id"]]
            return {"id": task_id, "forecast": forecast, "revision": revision, "changed": 0, "replayed": True, "rows": rows}
        return None

    def apply(self, task_id, forecast, revision, *, actor="ai"):
        self._validate(task_id, forecast, actor)
        if not isinstance(revision, str) or not re.fullmatch("[0-9a-f]{64}", revision):
            raise ValueError("Укажите версию из предварительного расчёта.")
        with storage._write_lock(self.root):
            graph = _Graph(self.planning)
            replay = self._replayed(graph, task_id, forecast, revision, actor)
            if replay is not None:
                return replay
            try:
                proposal = self._scenario(graph, task_id, forecast, actor)
            except (ValueError, FileNotFoundError) as error:
                raise storage.ConflictError("Условия изменились. Повторите расчёт сценария.") from error
            if proposal["revision"] != revision:
                raise storage.ConflictError("Работы, связи или планы изменились. Повторите расчёт сценария.")
            changes = []
            stamp = datetime.now(timezone.utc).isoformat()
            for row in proposal["rows"]:
                if row["status"] != "changed":
                    continue
                key = row["id"]
                path, previous, record, header = graph.card(key)
                reason = f'Сценарий «{graph.describe(task_id)["title"]}» на {forecast}. ' + row["reason"]
                change = {"field": "forecast", "before": row["before"], "after": row["after"],
                          "actor": actor, "ownerConfirmed": False, "sources": [], "updated": stamp, "reason": reason,
                          "scenarioRevision": revision, "scenarioRoot": task_id, "scenarioForecast": forecast,
                          "scenarioChanged": proposal["changed"],
                          "independentForecast": row["independentForecast"],
                          "predecessors": graph.parents[key], "revision": schedule._hash([revision, key]),
                          "requestHash": schedule._hash(["scenario", revision, key])}
                history = schedule.schedule_history(record, row["path"])
                updated = schedule._with_change(self.root, path, record, header, change, history)
                changes.append((path, previous, updated))
            plan_transaction.publish(self.root, changes)
            return {**proposal, "replayed": False}
