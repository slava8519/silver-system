import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from second_brain import storage
from second_brain.cycles import Cycles
from second_brain.okr import KeyResults
from second_brain.project_activity import ProjectActivity
from second_brain.project_feedback import ProjectFeedback
from second_brain.project_membership import ProjectMembership


class ProjectMembershipTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.members = ProjectMembership(self.root)
        self.planning = self.members.planning
        self.plan = self.planning.create_plan("План года")

    def card(self, kind, title, **fields):
        return storage.read_record(storage.create(self.root, kind, title, **fields))["id"]

    def change(self, project, present=True, plan=None):
        plan = plan or self.plan
        return self.members.set(plan["id"], project, present=present, revision=plan["revision"], actor="manual")

    def test_projects_of_three_directions_can_be_selected_before_work_is_chosen(self):
        revision = self.plan["revision"]
        areas = []
        for area, title in (("ИТ", "Запуск"), ("Личное", "Ремонт"), ("Парфюмерия", "Новая композиция")):
            area_id = self.card("direction", area)
            areas.append(area_id)
            project = self.card("project", title, direction=area_id)
            self.planning.create_task("Первый шаг " + title, project=project)
            result = self.members.set(self.plan["id"], project, present=True, revision=revision)
            revision = result["revision"]
        catalog = ProjectActivity(self.root).catalog()
        plan, = catalog["plans"]
        self.assertEqual(set(plan["directionIds"]), set(areas))
        self.assertEqual([p["taskCount"] for p in plan["projects"]], [0, 0, 0])
        self.assertEqual(plan["taskCount"], 0)
        self.assertEqual(self.planning.view(self.plan["id"])["items"], [])
        for project in catalog["projects"]:
            self.assertEqual(project["taskCount"], 1)
            relation, = project["plans"]
            self.assertTrue(relation["explicitlyIncluded"])
            self.assertEqual(relation["taskCount"], 0)

    def test_remove_only_direct_project_link_preserves_task_and_context(self):
        area = self.card("direction", "ИТ")
        project = self.card("project", "Проект", direction=area)
        service = KeyResults(self.root)
        task = self.planning.create_task("Работа", project=project)
        goal = service.create_objective("Цель", plan_id=self.plan["id"], direction=area)
        kr = service.create("Результат", objective_id=goal["id"], unit="шт.", task_ids=[task["id"]])
        service.context_project(kr["id"], project, revision=kr["revision"], present=True)
        added = self.change(project)
        self.assertFalse(self.change(project)["changed"])
        duplicate = self.members.add_task(self.plan["id"], project, task["id"], revision=added["revision"])
        self.assertFalse(duplicate["changed"])
        before = ProjectActivity(self.root).catalog()["plans"][0]["projects"][0]
        self.members.set(self.plan["id"], project, present=False, revision=added["revision"])
        after = ProjectActivity(self.root).catalog()["plans"][0]["projects"][0]
        self.assertEqual(before, {**after, "explicitlyIncluded": True})
        self.assertEqual((after["explicitlyIncluded"], after["taskCount"], after["contextResultCount"]), (False, 1, 1))
        self.assertEqual(self.planning.view(self.plan["id"])["items"][0]["id"], task["id"])

    def test_task_selection_revision_and_audit_are_atomic(self):
        project = self.card("project", "Устройство выставки")
        another = self.card("project", "Съёмка")
        first = self.planning.create_task("Эскиз", project=project)["id"]
        second = self.planning.create_task("Подписи", project=project)["id"]
        wrong = self.planning.create_task("Кадр", project=another)["id"]
        added = self.change(project)
        path = self.root / added["path"]
        before = path.read_bytes()
        def choose(task, revision):
            return self.members.add_task(self.plan["id"], project, task, revision=revision, actor="manual")
        with self.assertRaises(ValueError):
            choose(wrong, added["revision"])
        with self.assertRaises(storage.ConflictError):
            choose(first, self.plan["revision"])
        with patch("second_brain.project_feedback.append_event", side_effect=ValueError("Ошибка записи истории")):
            with self.assertRaises(ValueError):
                choose(first, added["revision"])
        self.assertEqual(path.read_bytes(), before)
        chosen = choose(first, added["revision"])
        self.assertTrue(chosen["changed"])
        self.assertFalse(choose(first, added["revision"])["changed"])
        with self.assertRaises(storage.ConflictError):
            choose(second, added["revision"])
        self.assertEqual([row["id"] for row in self.planning.view(self.plan["id"])["items"]], [first])
        events = ProjectFeedback(self.root).history(self.plan["id"], pending=True, limit=10, offset=0)["items"]
        self.assertEqual([event["action"] for event in events], ["add-task", "include"])
        self.assertEqual(events[0]["taskId"], first)

    def test_excluding_a_missing_project_preserves_owner_notes_and_other_plans(self):
        area = self.card("direction", "Личное")
        with self.assertRaises(ValueError):
            self.change(area)
        project = self.card("project", "Архив", direction=area)
        first = self.change(project)
        other = self.planning.create_plan("Архив на следующей неделе")
        second = self.change(project, plan=other)
        second_path = self.root / second["path"]
        second_before = second_path.read_bytes()
        file = self.root / first["path"]
        marker = storage.id_marker("project", project)
        annotated = file.read_text().replace(marker, marker + " — обсудили 10 ноября")
        file.write_bytes(annotated.replace("\n", "\r\n").encode())
        storage.find_record(self.root, project).unlink()
        with self.assertRaises(storage.ConflictError):
            self.members.set(self.plan["id"], project, present=False, revision=first["revision"])
        current = self.planning.view(self.plan["id"])
        self.change(project, False, current)
        self.assertEqual(second_path.read_bytes(), second_before)
        self.assertIn("обсудили 10 ноября", file.read_text())
        self.assertNotIn(marker, file.read_text())
        self.assertNotIn(b"\n", file.read_bytes().replace(b"\r\n", b""))
        plans = {p["id"]: p for p in ProjectActivity(self.root).catalog()["plans"]}
        self.assertEqual(plans[self.plan["id"]]["projects"], [])
        self.assertTrue(plans[other["id"]]["projects"][0]["missing"])

    def test_close_freezes_name_direction_and_link_even_with_zero_tasks(self):
        area = self.card("direction", "Личное")
        project = self.card("project", "Мастерская", direction=area)
        service = Cycles(self.root)
        preview = service.preview(self.plan["id"])
        added = self.change(project)
        with self.assertRaises(storage.ConflictError):
            service.close(self.plan["id"], preview["revision"])
        preview = service.preview(self.plan["id"])
        self.assertEqual(preview["includedProjectCount"], 1)
        project_path = storage.find_record(self.root, project)
        storage.edit(self.root, project, storage.read_record(project_path)["revision"], {"title":"Обновление мастерской"})
        with self.assertRaises(storage.ConflictError):
            service.close(self.plan["id"], preview["revision"])
        preview = service.preview(self.plan["id"])
        service.close(self.plan["id"], preview["revision"])
        project_path.unlink()
        plan_path = self.root / added["path"]
        before = plan_path.read_bytes()
        with self.assertRaises(storage.ConflictError):
            self.members.set(self.plan["id"], project, present=False, revision=added["revision"])
        self.assertEqual(plan_path.read_bytes(), before)
        plan, = ProjectActivity(self.root).catalog()["plans"]
        relation, = plan["projects"]
        self.assertEqual((relation["title"], relation["explicitlyIncluded"], relation["missing"], plan["directionIds"]),
                         ("Обновление мастерской", True, True, [area]))
        self.assertEqual(relation["taskCount"], 0)
        self.assertIn("- Обновление мастерской", before.decode())


if __name__ == "__main__":
    unittest.main()
