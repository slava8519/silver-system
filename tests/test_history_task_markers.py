from pathlib import Path
import tempfile
import unittest

from second_brain import history_archive, storage


class HistoryTaskMarkerTests(unittest.TestCase):
    def test_archive_preserves_task_markers_and_keeps_unresolved_links_in_project(self):
        checklist = "- [ ] Купить грунт\n  + [x] Полить мяту\n* [X] Проверить [прогноз](https://example.org/)"
        ambiguous = "- [ ] Сверить [замеры][таблица]"
        body = (f"## История статусов\n### 2021-01-01 — сад\n{checklist}\n"
                f"### 2021-01-02 — наблюдения\n{ambiguous}\n")
        with tempfile.TemporaryDirectory() as directory:
            root = storage.initialize(Path(directory))
            project = storage.create(root, "project", "Сад", body)
            project_id = storage.read_record(project)["id"]
            options = dict(as_of="2026-11-28", min_kept=0)
            plan = history_archive.rotate(root, project_id, **options)
            self.assertEqual((plan["moved"], plan["skippedLinks"]), (1, 1))
            history_archive.rotate(root, project_id, apply=plan["revision"], **options)
            archived = (root / plan["archives"][0]["path"]).read_bytes()
            self.assertIn(checklist.encode(), archived)
            self.assertNotIn(ambiguous.encode(), archived)
            self.assertIn(ambiguous.encode(), project.read_bytes())

    def test_task_like_reference_syntax_remains_protected(self):
        examples = (
            "Статус [x] требует пояснения",
            "- [x]: ../Notes/Источник.md",
            "- [x](../Notes/Источник.md)",
            "- [x][источник]",
            "- [ ] Проверить [x] сегодня",
            "- [веб](https://example.org/) [x] сегодня",
        )
        with tempfile.TemporaryDirectory() as directory:
            for text in examples:
                with self.subTest(text=text):
                    self.assertFalse(history_archive._portable(Path(directory), text, {}))
