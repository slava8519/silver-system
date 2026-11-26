import json
import tempfile
import unittest
from pathlib import Path

from second_brain import storage
from second_brain.cycles import Cycles
from second_brain.okr import KeyResults
from second_brain.planning import Planning
from second_brain.project_membership import ProjectMembership
from second_brain.relation_map import RelationMap


class RelationMapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name) / "Vault")
        self.service = RelationMap(self.root)
        self.planning = Planning(self.root)

    def card(self, kind, title, **fields):
        path = storage.create(self.root, kind, title, **fields)
        return storage.read_record(path)["id"]

    def group(self, key, group):
        return self.service.query("group", id=key, group=group)["items"]

    def source(self, item):
        source = item["source"]
        return self.service.read_source(source["id"], source["line"], source["revision"])["text"]

    def test_direct_edges_work_in_both_directions_across_three_domains(self):
        for title in ("ИТ", "Дом", "Парфюмерия"):
            direction = self.card("direction", title)
            project = self.card("project", "Подготовка " + title, direction=direction)
            note = self.card("note", "Дневник " + title, direction=direction)
            task = self.planning.create_task("Отдельное дело " + title, direction=direction)
            nested = self.planning.create_task("Завершение " + title, project=project, kind="milestone")
            scope = self.service.query("home", query=title)["items"][0]
            self.assertEqual(scope["counts"], {"project": 1, "note": 1, "task": 1})
            self.assertEqual([item["id"] for item in self.group(direction, "tasks")], [task["id"]])
            self.assertEqual(self.group(nested["id"], "directions"), [])
            parent, = self.group(nested["id"], "projects")
            self.assertEqual(parent["id"], project)
            child, = self.group(project, "tasks")
            self.assertEqual(child["kind"], "milestone")
            self.assertEqual(parent["source"], child["source"])
            self.assertIn(project, self.source(child))
            attached, = self.group(note, "directions")
            self.assertIn(direction, self.source(attached))
            self.assertEqual(self.service.query("focus", id=nested["id"])["focus"]["projectId"], project)

    def test_plan_history_keeps_real_sources_and_snapshot_names(self):
        area = self.card("direction", "Сад")
        project = self.card("project", "Весенний сад", direction=area)
        task = self.planning.create_task("Выбрать ветку", project=project, kind="milestone")
        week = self.planning.create_plan("Неделя")
        season = self.planning.create_plan("Сезон")
        self.planning.add_task(season["id"], task["id"])
        week = self.planning.add_task(week["id"], task["id"])
        ProjectMembership(self.root).set(week["id"], project, present=True, revision=week["revision"])
        explicit, = self.group(project, "plans")
        self.assertEqual(explicit["id"], week["id"])
        self.assertIn(project, self.source(explicit))
        cycles = Cycles(self.root)
        cycles.close(week["id"], cycles.preview(week["id"])["revision"])
        # Manual membership edits outside the sealed section cannot change its snapshot.
        week_path = self.root / week["path"]
        week_text = week_path.read_text().replace('title: "Неделя"', 'title: "Текущее имя"')
        week_path.write_text(week_text.replace("<!-- task-id: " + task["id"], "<!-- task-id: other"))
        task_path = self.root / task["path"]
        changed = task_path.read_text().replace('title: "Выбрать ветку"', 'title: "Проверить саженец"\naliases: [Отросток]')
        task_path.write_text(changed.replace('task_kind: "milestone"', 'task_kind: "task"'))
        frozen, = self.group(week["id"], "tasks")
        self.assertEqual((frozen["title"], frozen["kind"]), ("Выбрать ветку", "milestone"))
        self.assertEqual(self.service.query("group", id=week["id"], group="tasks", query="Отросток")["total"], 0)
        self.assertEqual(self.service.query("group", id=season["id"], group="tasks", query="Отросток")["total"], 1)
        self.assertIn("| Выбрать ветку |", self.source(frozen))
        self.assertNotIn("brain-cycle-row", self.source(frozen))
        self.assertEqual(self.group(season["id"], "tasks")[0]["title"], "Проверить саженец")
        item = self.service.query("focus", id=week["id"])["focus"]
        self.assertEqual((item["title"], item["status"]), ("Неделя", "closed"))
        self.assertIn("Неделя", self.source(item))
        self.assertEqual(self.service.query("focus", id=task["id"])["focus"]["planId"], season["id"])
        self.assertEqual(self.service.query("group", id=task["id"], group="plans")["label"], "Действующие и закрытые планы")
        self.assertEqual(self.service.query("group", id=week["id"], group="tasks")["label"], "Работы на момент закрытия")
        storage.find_record(self.root, project).unlink()
        snapshot_project, = self.group(week["id"], "projects")
        self.assertEqual((snapshot_project["title"], snapshot_project["missing"]), ("Весенний сад", True))
        self.assertEqual(self.source(snapshot_project), "- Весенний сад")
        task_path.unlink()
        removed, = self.group(week["id"], "tasks")
        self.assertEqual((removed["missing"], removed["kind"]), (True, "milestone"))

    def test_missing_direction_is_distinct_from_unassigned_and_source_tracks_manual_edits(self):
        area = self.card("direction", "Маршрут")
        note = self.card("note", "Наблюдение", direction=area)
        project = self.card("project", "Отдельный проект")
        self.planning.create_task("Задача проекта", project=project)
        self.planning.create_plan("Без собственного направления")
        old, = self.group(note, "directions")
        note_path = storage.find_record(self.root, note)
        note_path.write_text(note_path.read_text().replace("direction:", "# пояснение\ndirection:"))
        with self.assertRaises(storage.ConflictError):
            self.source(old)
        current, = self.group(note, "directions")
        self.assertEqual(current["source"]["line"], old["source"]["line"] + 1)
        storage.find_record(self.root, area).unlink()
        renamed = note_path.rename(note_path.with_name("Новый путь.md"))
        home = self.service.query()
        self.assertEqual((home["total"], home["broken"], home["unassigned"]), (0, 1, 1))
        broken, = self.group(None, "broken")
        self.assertEqual(broken["id"], note)
        self.assertTrue(broken["source"]["path"].endswith(renamed.name))
        self.assertIn(area, self.source(broken))
        unassigned, = self.group(None, "unassigned")
        self.assertEqual(unassigned["id"], project)
        unavailable, = self.group(note, "directions")
        self.assertTrue(unavailable["missing"])
        with self.assertRaises(ValueError):
            self.service.read_source(note, 1)
        with self.assertRaises(FileNotFoundError):
            self.service.read_source("../../Other/Private.md", 1)

    def test_membership_via_key_result_does_not_create_direct_edges(self):
        area = self.card("direction", "ИТ")
        project = self.card("project", "Маяк", direction=area)
        task = self.planning.create_task("Проверить", project=project)
        plan = self.planning.create_plan("Год")
        okr = KeyResults(self.root)
        goal = okr.create_objective("Надёжность", plan_id=plan["id"], direction=area)
        okr.create("Успешная проверка", objective_id=goal["id"], unit="шт.", task_ids=[task["id"]])
        self.assertEqual(self.group(plan["id"], "tasks"), [])
        self.assertEqual(self.group(project, "plans"), [])
        self.assertNotIn("planId", self.service.query("focus", id=task["id"])["focus"])
        self.assertEqual([row["id"] for row in self.planning.view(plan["id"])["items"]], [task["id"]])

    def test_exact_title_and_alias_remain_on_first_page(self):
        for i in range(7):
            self.card("note", "Атлас %d Маяк" % i)
        other = self.card("project", "Янтарь", aliases=["Маяк"])
        exact = self.card("project", "Маяк")
        results = self.service.query("search", query="маЯК", limit=2)
        self.assertEqual([row["id"] for row in results["items"]], [exact, other])
        self.assertEqual(results["items"][1]["matchedAlias"], "Маяк")
        self.assertEqual(results["total"], 9)

    def test_invalid_plans_do_not_publish_partial_edges_or_hide_unrelated_cards(self):
        direction = self.card("direction", "ИТ")
        project = self.card("project", "Опыт", direction=direction)
        task = self.planning.create_task("Проверка", project=project)
        plan = self.planning.create_plan("Архив")
        self.planning.add_task(plan["id"], task["id"])
        cycles = Cycles(self.root)
        cycles.close(plan["id"], cycles.preview(plan["id"])["revision"])
        path = self.root / plan["path"]
        path.write_text(path.read_text().replace("| Проверка | Задача |", "| Чужое имя | Задача |"))
        bad_ids = [plan["id"]]
        for index in range(3):
            active = self.planning.create_plan(f"Неполный {index}")
            # A valid task section and a broken project section must produce no edges.
            self.planning.add_task(active["id"], task["id"])
            path = self.root / active["path"]
            path.write_text(path.read_text() + "\n<!-- plan-projects -->\n")
            bad_ids.append(active["id"])
        home = self.service.query()
        self.assertEqual((home["warningTotal"], len(home["warnings"])), (4, 3))
        self.assertEqual(self.group(project, "tasks")[0]["id"], task["id"])
        self.assertEqual(self.group(task["id"], "plans"), [])
        self.assertEqual(self.service.query("search", query="Проверка")["total"], 1)
        for identifier in bad_ids:
            view = self.service.query("focus", id=identifier)
            self.assertEqual(view["focus"]["status"], "unavailable")
            self.assertEqual(view["groups"], [])
            self.assertEqual(view["warnings"][0]["path"], view["focus"]["path"])
            self.assertTrue(view["warnings"][0]["message"])

    def test_pages_aliases_bounded_responses_and_read_only_queries(self):
        area = self.card("direction", "Коллекция")
        # Direct Markdown simulates external Obsidian edits without per-row CLI write costs.
        for i in range(105):
            text = ("---\ntype: note\nschema_version: 1\nid: note-%d\ntitle: Карточка %03d\n"
                    "aliases: [Код-%03d]\ndirection: %s\n---\n" % (i, i, i, area))
            (self.root / "Notes" / f"Элемент {i}.md").write_text(text + "Личное содержание\n" * 20)
        before = {p: p.stat().st_mtime_ns for p in self.root.rglob("*") if p.is_file()}
        home = self.service.query()
        self.assertEqual(home["items"][0]["counts"]["note"], 105)
        focused = self.service.query("focus", id=area)
        group, = focused["groups"]
        self.assertEqual((group["total"], len(group["items"]), group["label"]), (105, 0, "Заметки направления"))
        last = self.service.query("group", id=area, group="notes", query="каРТОчка", limit=5, offset=102)
        self.assertEqual([item["id"] for item in last["items"]], ["note-102", "note-103", "note-104"])
        search = self.service.query("search", query="код-015")
        self.assertEqual(search["items"][0]["id"], "note-15")
        self.assertEqual(search["items"][0]["matchedAlias"], "Код-015")
        for response in (home, focused, last, search):
            output = json.dumps(response, ensure_ascii=False).encode()
            self.assertLess(len(output), 4000)
            self.assertNotIn("Личное содержание".encode(), output)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in self.root.rglob("*") if p.is_file()})
        for values in ({"limit": 21}, {"offset": -1}, {"action": "group", "id": area, "group": "unknown"}):
            with self.assertRaises(ValueError):
                self.service.query(**values)


if __name__ == "__main__":
    unittest.main()
