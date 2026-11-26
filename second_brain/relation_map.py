"""Paged, one-hop views of relationships explicitly recorded in Markdown."""
from collections import defaultdict
from dataclasses import dataclass

from . import brief, planning, storage
from .cycles import _META, _ROW, closed_metadata, frozen_rows
from .okr_links import _connections
from .project_membership import _members as project_members


GROUPS = {
    "direction": ("directions", "Направления"),
    "project": ("projects", "Проекты"),
    "note": ("notes", "Заметки"),
    "task": ("tasks", "Задачи и вехи"),
    "plan": ("plans", "Планы"),
}
SPECIAL = {"unassigned": "Без направления", "broken": "Направление не найдено"}
LABELS = {
    "direction": {"project": "Проекты направления", "note": "Заметки направления", "task": "Работы направления"},
    "project": {"direction": "Направление проекта", "task": "Работы проекта", "plan": "Включён в планы"},
    "note": {"direction": "Направление заметки"},
    "task": {"direction": "В направлении", "project": "В проекте", "plan": "Входит в планы"},
    "plan": {"project": "Проекты плана", "task": "Работы плана"},
}


@dataclass
class Edge:
    target: str
    kind: str
    relation: str
    source: dict
    snapshot: dict = None


def _plan_links(record, closed):
    """Yield each direct target with its evidence line and optional frozen values."""
    body = record["body"]
    lines = body.splitlines()
    start = record["body_start"]
    if closed is not None:
        rows = frozen_rows(record, closed)
        for row in rows:
            yield row["id"], "task", row["snapshotLine"], {
                "title": row.get("title"), "kind": row.get("kind"), "missing": row.get("missing", False)}
        projects = closed.get("includedProjects", [])
        if projects:
            first = start + lines.index("<!-- brain-cycle-projects -->") + 3
            for index, project in enumerate(projects, first):
                yield project["id"], "project", index, {"title": project.get("title")}
        return
    direct, _ = planning._membership(body)
    if direct:
        members, _ = _connections(lines, (planning._BEGIN, planning._END), "task", location="плане")
        for identifier in direct:
            yield identifier, "task", start + members[identifier][0], None
    projects, _ = project_members(lines)
    for identifier, (index, _) in projects.items():
        yield identifier, "project", start + index, None


class _Catalog:
    """In-memory reading of one vault generation. Bodies stay out of the response."""

    def __init__(self, service, source_id=None):
        self.root = service.root
        self.cards = {}
        self.aliases = {}
        self.edges = defaultdict(lambda: defaultdict(list))
        self.unassigned = []
        self.issues = {}
        self.direction_links = {}
        self.source_text = None
        with storage._vault_lock(self.root, write=False):
            for path, record in storage.iter_records(self.root):
                self._add(path, record)
                if record["id"] == source_id:
                    raw = storage.read_record(path, include_raw=True)
                    if raw["revision"] != record["revision"]:
                        raise storage.ConflictError("Карточка изменилась. Обновите карту.")
                    self.source_text = raw["raw"]
            for kind in planning.FOLDERS:
                for path, data, record, _ in service._read_entries(kind):
                    self._add(path, record)
                    if record["id"] == source_id:
                        self.source_text = data.decode()
        self.broken = [key for key, target in self.direction_links.items()
                       if self.cards.get(target, {}).get("type") != "direction"]

    def _add(self, path, record):
        key, kind = record["id"], record["type"]
        if key in self.cards:
            raise ValueError(f"Один ID найден в нескольких файлах: {key}")
        relative = path.relative_to(self.root).as_posix()
        source = {"id": key, "path": relative, "line": record["field_lines"]["title"], "revision": record["revision"]}
        card = {"id": key, "type": kind, "title": record["title"], "path": relative, "source": source}
        self.cards[key] = card
        self.aliases[key] = record["aliases"]
        if record["aliases"]:
            card["aliases"] = [alias[:brief.EXCERPT_LENGTH] for alias in record["aliases"][:2]]
        if kind == "task":
            card["kind"] = "milestone" if record.get("task_kind") == "milestone" else "task"
        if kind == "plan":
            self._plan(card, record, source)
        if kind in ("note", "project", "task"):
            direction = record.get("direction")
            if direction:
                self.direction_links[key] = direction
                self._edge(card, direction, "direction", {**source, "line": record["field_lines"]["direction"]},
                           "Относится к направлению", "В этом направлении")
            elif not record.get("project"):
                self.unassigned.append(key)
            if kind == "task" and record.get("project"):
                self._edge(card, record["project"], "project", {**source, "line": record["field_lines"]["project"]},
                           "Работа проекта", "Работа проекта")

    def _plan(self, card, record, source):
        try:
            closed = closed_metadata(record)
            # Validate the whole membership before publishing any reverse or forward edge.
            links = list(_plan_links(record, closed))
        except ValueError as error:
            card["status"] = "unavailable"
            self.issues[card["id"]] = {"path": card["path"], "message": str(error)[:brief.EXCERPT_LENGTH]}
            return
        card["status"] = "closed" if closed else "active"
        if closed:
            card["title"] = closed["title"]
            if record["title"] != closed["title"]:
                lines = record["body"].splitlines()
                meta_line = next(i for i, value in enumerate(lines) if value.startswith(_META))
                visible = "План — " + storage.markdown_label(closed["title"])
                index = next((i for i in range(meta_line - 1, -1, -1) if lines[i] == visible), meta_line)
                card["source"] = {**source, "line": record["body_start"] + index}
        for target, kind, line, snapshot in links:
            if snapshot is not None:
                label = "В снимке плана"
            elif kind == "project":
                label = "Проект включён в план"
            else:
                label = "Работа включена в план"
            self._edge(card, target, kind, {**source, "line": line}, label, label, snapshot)

    def warnings(self, focus_id):
        if not self.issues:
            return {}
        keys = list(self.issues)
        if focus_id in self.issues:
            keys.remove(focus_id)
            keys.insert(0, focus_id)
        return {"warnings": [self.issues[key] for key in keys[:3]], "warningTotal": len(keys)}

    def _edge(self, card, target, kind, source, forward, reverse, snapshot=None):
        # Include the expected source type. A stale ID pointing at another kind is still a broken link.
        self.edges[card["id"], card["type"]][kind].append(Edge(target, kind, forward, source, snapshot))
        self.edges[target, kind][card["type"]].append(Edge(card["id"], card["type"], reverse, source))

    def grouped_edges(self, card):
        return self.edges.get((card["id"], card["type"]), {})

    def neighbors(self, card):
        for edges in self.grouped_edges(card).values():
            yield from edges

    def label(self, card, kind, edges):
        if card.get("status") == "closed":
            return {"task": "Работы на момент закрытия", "project": "Проекты на момент закрытия"}.get(kind, GROUPS[kind][1])
        if kind == "plan" and edges:
            statuses = {self.cards[edge.target]["status"] for edge in edges}
            if statuses == {"closed"}:
                return "В снимках закрытых планов"
            if "closed" in statuses:
                return "Действующие и закрытые планы"
        return LABELS[card["type"]].get(kind, GROUPS[kind][1])

    def focus(self, key):
        if not key:
            raise ValueError("Укажите ID карточки для карты.")
        if key not in self.cards:
            raise FileNotFoundError(f"Карточка не найдена: {key}")
        card = self.cards[key].copy()
        if card["type"] == "task":
            for edge in self.neighbors(card):
                target = self.cards.get(edge.target, {})
                if target.get("type") == "project" and edge.kind == "project":
                    card["projectId"] = edge.target
                elif edge.kind == "plan" and target.get("status") == "active":
                    card.setdefault("planId", edge.target)
        brief.clip_fields(card, ("title",))
        return card

    def related(self, edge):
        target = self.cards.get(edge.target)
        if not target or target["type"] != edge.kind:
            target = {"id": edge.target, "type": edge.kind, "path": None, "title": edge.target, "missing": True}
        item = {**target, "relation": edge.relation, "source": edge.source}
        if edge.snapshot is not None:
            item["title"] = edge.snapshot.get("title") or edge.target
            if edge.kind == "task":
                item["kind"] = edge.snapshot.get("kind")
            if edge.snapshot.get("missing") or not edge.snapshot.get("title"):
                item["missingAtClose"] = True
            item.pop("aliases", None)
        return item

    def special_items(self, group):
        if group == "unassigned":
            return (self.cards[key] for key in self.unassigned)
        return ({**self.cards[key], "relation": "Направление не найдено",
                 "source": next(edge.source for edge in self.neighbors(self.cards[key]) if edge.kind == "direction")}
                for key in self.broken)

    def page(self, items, search, offset, limit):
        term = search.strip().casefold()
        def aliases_for(card):
            # Only current, resolved cards expose aliases. Snapshots carry their own titles.
            return self.aliases[card["id"]] if card.get("aliases") else ()

        def order(card):
            title = card["title"].casefold()
            if not term or term == title:
                rank = 0
            else:
                aliases = [name.casefold() for name in aliases_for(card)]
                if term in aliases:
                    rank = 1
                elif any(value.startswith(term) for value in [title, *aliases]):
                    rank = 2
                elif any(term in value for value in [title, *aliases]):
                    rank = 3
                else:
                    return None
            return rank, title, card["type"], card["id"]

        scored = [(sort_key, card) for card in items if (sort_key := order(card)) is not None]
        scored.sort(key=lambda entry: entry[0])
        results = [dict(card) for _, card in scored[offset:offset + limit]]
        for item in results:
            if term and term != item["title"].casefold():
                aliases = aliases_for(item)
                alias = next((name for name in aliases if term == name.casefold()), None)
                if alias is None and term not in item["title"].casefold():
                    alias = next((name for name in aliases if term in name.casefold()), None)
                if alias:
                    item["matchedAlias"] = alias
                    brief.clip_fields(item, ("matchedAlias",))
            brief.clip_fields(item, ("title",))
        return {"name": self.root.name, "items": results, "total": len(scored), "offset": offset, "limit": limit}


class RelationMap:
    def __init__(self, root):
        self.planning = planning.Planning(root)

    def query(self, action="home", id=None, group=None, query="", offset=0, limit=5):
        if action not in ("home", "focus", "group", "search"):
            raise ValueError("Выберите обзор, поиск, карточку или группу связей.")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Укажите неотрицательное смещение и от 1 до 20 результатов.")
        if not isinstance(query, str):
            raise ValueError("Строка поиска должна быть текстом.")
        catalog = _Catalog(self.planning)
        warnings = catalog.warnings(id)
        if action == "search":
            return {**catalog.page(catalog.cards.values(), query, offset, limit), **warnings}
        if action == "home":
            result = catalog.page((card for card in catalog.cards.values() if card["type"] == "direction"),
                                  query, offset, limit)
            for card in result["items"]:
                counts = dict.fromkeys(("project", "note", "task"), 0)
                for kind, edges in catalog.grouped_edges(card).items():
                    counts[kind] = len(edges)
                card["counts"] = counts
            return {**result, "unassigned": len(catalog.unassigned), "broken": len(catalog.broken), **warnings}
        if action == "group" and group in SPECIAL and not id:
            return {**catalog.page(catalog.special_items(group), query, offset, limit), "label": SPECIAL[group],
                    "focus": None, **warnings}
        focus = catalog.focus(id)
        groups = catalog.grouped_edges(focus)
        if action == "group":
            for kind, (key, _) in GROUPS.items():
                if group == key:
                    edges = groups.get(kind, ())
                    rows = (catalog.related(edge) for edge in edges)
                    return {**catalog.page(rows, query, offset, limit), "label": catalog.label(focus, kind, edges),
                            "focus": focus, **warnings}
            raise ValueError("Выберите существующую группу связей.")
        preview = []
        for kind, (key, _) in GROUPS.items():
            edges = groups.get(kind, ())
            if not edges:
                continue
            items = []
            if len(edges) <= 3:
                items = catalog.page((catalog.related(edge) for edge in edges), "", 0, 3)["items"]
            preview.append({"key": key, "label": catalog.label(focus, kind, edges), "total": len(edges), "items": items})
        return {"name": catalog.root.name, "focus": focus, "groups": preview, **warnings}

    def read_source(self, id, line, revision=None):
        if type(line) is not int or line < 1:
            raise ValueError("Укажите положительный номер строки.")
        catalog = _Catalog(self.planning, source_id=id)
        card = catalog.focus(id)
        sources = [edge.source for edge in catalog.neighbors(card) if edge.source["id"] == id]
        if card.get("source"):
            sources.append(card["source"])
        if revision is not None and (not sources or sources[0]["revision"] != revision):
            raise storage.ConflictError("Источник изменился. Обновите карту и откройте строку заново.")
        if not any(source["line"] == line for source in sources):
            raise ValueError("Строка не является источником карточки или её прямой связи.")
        text = catalog.source_text.splitlines()[line - 1].partition(_ROW)[0]
        result = {"id": id, "path": card["path"], "line": line, "text": text}
        brief.clip_fields(result, ("text",))
        return result
