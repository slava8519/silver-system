from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote
import unittest

from second_brain import storage
from second_brain.planning import Planning


class PlanningTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name) / "Vault")
        self.planning = Planning(self.root)

    def test_two_ordered_plans_read_one_task_after_obsidian_edits(self):
        area = storage.read_record(storage.create(self.root, "direction", "ИТ-проекты"))
        other = storage.read_record(storage.create(self.root, "direction", "Запуск"))
        project = storage.read_record(storage.create(self.root, "project", "Маяк", direction=area["id"]))
        task = self.planning.create_task("Проверить перенос (архив)", project=project["id"], due="2026-12-01")
        additional = self.planning.create_task("Подобрать дату встречи")
        week = self.planning.create_plan("Неделя")
        launch = self.planning.create_plan("Запуск")
        self.planning.add_task(week["id"], additional["id"])
        self.planning.add_task(week["id"], task["id"])
        self.planning.add_task(launch["id"], task["id"])

        path = self.root / task["path"]
        self.assertNotIn("direction:", path.read_text())
        for plan in (week, launch):
            plan_text = unquote((self.root / plan["path"]).read_text())
            self.assertIn("../" + task["path"] + ")", plan_text)
            self.assertNotIn("2026-12-01", plan_text)
        path.write_text(path.read_text().replace("2026-12-01", "2026-12-04"), encoding="utf-8")
        new_path = path.rename(path.with_name("Другое имя.md"))
        storage.edit(self.root, project["id"], project["revision"], {"direction": other["id"]})
        for plan in (week, launch):
            item = self.planning.view(plan["id"])["items"][-1]
            self.assertEqual((item["id"], item["direction"], item["due"]), (task["id"], other["id"], "2026-12-04"))
            self.assertEqual((self.root / item["path"]), new_path)
            self.assertIn("2026-12-04", new_path.read_text().splitlines()[item["dueLine"] - 1])
        self.assertEqual([r["id"] for r in self.planning.view(week["id"])["items"]],
                         [additional["id"], task["id"]])
        self.assertEqual({p["id"] for p in self.planning.plans_for_task(task["id"])}, {week["id"], launch["id"]})

    def test_optional_layers_and_dates(self):
        home = storage.read_record(storage.create(self.root, "direction", "Дом"))
        work = self.planning.create_task("Забрать заказ", direction=home["id"])
        self.assertEqual((work["direction"], work["project"], work["due"], work["dueLine"]),
                         (home["id"], None, None, None))
        self.assertIn("../Directions/", (self.root / work["path"]).read_text())
        free = self.planning.create_task("Встреча", due="2028-02-29")
        self.assertIsNone(free["project"])
        self.assertIsNone(free["direction"])
        for invalid in ("", "2026-02-29", "17 ноября", "20261201", "2026-1-01", 5):
            with self.subTest(due=invalid), self.assertRaises(ValueError):
                self.planning.create_task("Неверная дата", due=invalid)
        with self.assertRaises(ValueError):
            self.planning.create_task("Чужой тип", project=home["id"])
        with self.assertRaises(FileNotFoundError):
            self.planning.create_task("Несуществующая связь", direction="missing")
        self.assertEqual(len(self.planning.tasks()), 2)

    def test_request_replay_survives_rename_title_change_and_moved_project(self):
        area = storage.read_record(storage.create(self.root, "direction", "Город"))
        project = storage.read_record(storage.create(self.root, "project", "Атлас", direction=area["id"]))
        options = {"project": project["id"], "direction": area["id"], "request_id": "zz"}
        task = self.planning.create_task("Сверить листы", **options)
        path = self.root / task["path"]
        path.write_text(path.read_text().replace('title: "Сверить листы"', 'title: "Проверить листы"'))
        renamed = path.rename(path.with_name("Проверка.md"))
        storage.edit(self.root, project["id"], project["revision"], {"direction": None})
        replay = self.planning.create_task("Сверить листы", **options)
        self.assertEqual((replay["id"], replay["title"], self.root / replay["path"]),
                         (task["id"], "Проверить листы", renamed))
        with self.assertRaises(storage.ConflictError):
            self.planning.create_task("Другая задача", **options)
        original = self.planning.create_plan("План", request_id="shared-key")
        copy = self.planning.create_plan("План")
        self.assertEqual(self.planning.create_plan("План", request_id="shared-key")["id"], original["id"])
        self.assertNotEqual(original["path"], copy["path"])

    def test_membership_preserves_manual_notes_and_concurrent_additions(self):
        plan = self.planning.create_plan("Серия")
        member = self.planning.create_task("Проба 1")
        other = self.planning.create_task("Проба 2")
        path = self.root / plan["path"]
        manual = (path.read_text().replace("schema_version: 1", "custom: [one, two]\nschema_version: 1")
                  + "\n## Авторы\nТекст владельца\n").replace("\n", "\r\n").encode()
        path.write_bytes(manual)
        with self.assertRaises(storage.ConflictError):
            self.planning.add_task(plan["id"], member["id"], expected_revision=plan["revision"])
        self.assertEqual(path.read_bytes(), manual)
        fresh = self.planning.view(plan["id"])
        written = self.planning.add_task(plan["id"], member["id"], expected_revision=fresh["revision"])
        replay = self.planning.add_task(plan["id"], member["id"], expected_revision=fresh["revision"])
        self.assertEqual(written, replay)
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda _: self.planning.add_task(plan["id"], other["id"]), range(3)))
        self.assertEqual([row["id"] for row in self.planning.view(plan["id"])["items"]], [member["id"], other["id"]])
        current = path.read_bytes()
        self.assertEqual(b"".join(line for line in current.splitlines(keepends=True) if b"<!-- task-id:" not in line),
                         manual)

    def test_unmanaged_notes_missing_members_and_symlinks(self):
        (self.root / "Plans/Sketch.md").write_text("# Unmanaged")
        tasks = self.root / "Tasks"
        if tasks.exists():
            tasks.rmdir()
        self.assertEqual(self.planning.plans(), [])
        self.assertEqual(self.planning.tasks(), [])
        self.assertFalse(tasks.exists())  # Opening a pre-planning vault is read-only.
        member = self.planning.create_task("Лабораторная проба")
        plan = self.planning.create_plan("Серия")
        self.planning.add_task(plan["id"], member["id"])
        self.assertEqual(len(list(self.planning.card_paths())), 2)
        path = self.root / member["path"]
        copy = path.with_name("Копия.md")
        copy.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, "ID"):
            self.planning.view(plan["id"])
        copy.unlink()
        path.unlink()
        missing, = self.planning.view(plan["id"])["items"]
        self.assertTrue(missing["missing"])
        self.assertEqual(missing["id"], member["id"])
        with self.assertRaises(FileNotFoundError):
            self.planning.add_task(plan["id"], member["id"])
        tasks.rmdir()
        tasks.symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Символическая"):
            self.planning.create_task("Чужая папка")
