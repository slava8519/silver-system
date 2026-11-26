"""HTTP adapters for the same sourced OKR operations used by brain-plan."""

from . import brief, query, storage
from .okr import KeyResults


def periods(root):
    from .cycles import closed_metadata, frozen_rows
    service = KeyResults(root)
    with storage._vault_lock(root, write=False):
        plans = {}
        for path, _, record, _ in service.planning._entries("plan"):
            closed = closed_metadata(record)
            plan = service.planning._plan_summary(path, record, closed)
            goals = []
            if closed:
                frozen_rows(record, closed)
                goals = (closed.get("okr") or {}).get("objectives", [])
            plans[record["id"]] = {**plan, "objectives": len(goals), "results": sum(len(g["results"]) for g in goals),
                                   "hierarchyRecorded": not closed or "okr" in closed}
        live = {p["id"] for p in plans.values() if p["status"] == "active"}
        owners = {}
        for goal in service._objectives(live):
            plans[goal["planId"]]["objectives"] += 1
            owners[goal["id"]] = goal["planId"]
        if owners:
            for _, _, result, _ in service._entries(owners):
                plans[owners[result["objective_id"]]]["results"] += 1
    return {"name": root.name, "items": list(plans.values())}


def _fields(data, required, optional=()):
    if not set(required) <= data.keys() or data.keys() - set(required) - set(optional):
        raise ValueError("Проверьте поля операции и версию карточки.")


def source_page(root, action, data):
    limit, offset = int(data.get("limit", 10)), int(data.get("offset", 0))
    if not 1 <= limit <= 20 or offset < 0:
        raise ValueError("Выберите от 1 до 20 строк и неотрицательное смещение.")
    match = data.get("match", "").strip().casefold()
    if action == "sources":
        rows = ({"id": r["id"], "title": r["title"], "path": p.relative_to(root).as_posix()}
                for p, r in storage.iter_records(root) if r["type"] in ("note", "project")
                and match in (r["title"] + " " + p.relative_to(root).as_posix()).casefold())
        return brief.page(rows, limit, offset)
    path = storage.find_record(root, data.get("id"))
    record = storage.read_record(path, include_raw=True)
    if record["type"] not in ("project", "note"):
        raise ValueError("Выберите заметку или проект с подтверждением.")
    def candidates():
        for _, fragment in query._state_sources(root, path, record):
            yield fragment["start"], fragment["lines"][0]["text"], fragment["ref"]
        for number, line in enumerate(record["body"].splitlines(), record["body_start"]):
            yield number, line, query._line_ref(line)
    def excerpts():
        for number, line, ref in candidates():
            content = line.strip()
            pos = content.casefold().find(match)
            if content and pos >= 0 and not content.startswith("<!--"):
                start = max(0, pos - 100) if len(content) > 600 else 0
                yield {"line": number, "quote": content[start:start + 600], "ref": ref}
    return brief.page(excerpts(), limit, offset)


def write(root, action, data):
    from .okr_progress import Progress
    common = {"id", "revision"}
    kr = KeyResults(root)
    if action == "criterion":
        optional = ("unit", "operator", "target", "primary", "baseline")
        _fields(data, common | {"title", "kind", "requestId"}, optional)
        return kr.add_criterion(data["id"], title=data["title"], kind=data["kind"], revision=data["revision"],
                                request_id=data["requestId"], **{k: data[k] for k in optional if k in data})
    if action == "criterion-target":
        _fields(data, common | {"criterionId", "target", "operator"})
        return kr.set_criterion_target(data["id"], data["criterionId"], operator=data["operator"],
                                       target=data["target"], revision=data["revision"])
    if action in ("measure", "observe"):
        fields = {"value", "date", "source", "revision"}
        required = fields | {"id"}
        if action == "observe":
            _fields(data, required | {"criterionId"}, ("baseline",))
            return kr.observe_criterion(data["id"], data["criterionId"], baseline=data.get("baseline", False),
                                        **{k: data[k] for k in fields})
        _fields(data, required)
        return kr.measure(data["id"], **{k: data[k] for k in fields})
    if action == "weight":
        _fields(data, common | {"weight"}, ("taskId",))
        return Progress(root).set_weight(data["id"], weight=data["weight"], revision=data["revision"],
                                         task_id=data.get("taskId"))
    _fields(data, common | {"value", "date", "kind"}, ("reason", "source", "ownerConfirmed"))
    return Progress(root).set_readiness(data["id"], value=data["value"], date=data["date"], kind=data["kind"],
                                        revision=data["revision"], actor="manual", reason=data.get("reason"),
                                        source=data.get("source"), owner_confirmed=data.get("ownerConfirmed", False))
