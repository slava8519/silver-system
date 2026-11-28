import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from second_brain import archive_reader, evidence, history_archive, storage


class ReviewedHistoryLinksTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = storage.initialize(Path(temporary.name) / "vault")
        self.original = self.root / "Sources/Originals/Осмотр сада.md"
        self.original.parent.mkdir(parents=True, exist_ok=True)
        self.original.write_text("Отметили состояние яблони.\n")
        storage.create(self.root, "note", "Осмотр",
                       "## Вопросы\nПервый осмотр.\n## Вопросы\nИтог осмотра. ^answer\n")
        self.old = "[[Notes/Осмотр#Вопросы]]"
        self.new = "[[Notes/Осмотр#^answer|Вопросы]]"
        self.sentence = "Решение принято по записи " + self.old
        self.footer = storage.markdown_link("Оригинал осмотра", self.original, self.root / "Projects")
        body = ("## История статусов\n### 2020-03-04 — осмотр\n" + self.sentence +
                "\n\n" + self.footer + "\n## Участники\nВладелец сада.\n")
        self.project = storage.create(self.root, "project", "Яблоня", body)
        self.relative = self.project.relative_to(self.root).as_posix()
        self.project_id = storage.read_record(self.project)["id"]

    def review(self):
        record = storage.read_record(self.project)
        lines = self.project.read_text().splitlines()
        return {
            "revision": record["revision"],
            "wikiLink": {"line": next(i for i, text in enumerate(lines, 1) if self.old in text),
                         "old": self.old, "new": self.new},
            "sourceFooter": {"line": lines.index(self.footer) + 1,
                             "target": self.original.relative_to(self.root).as_posix()},
        }

    def rotate(self, **options):
        return history_archive.rotate(self.root, self.project_id, as_of="2026-11-28", min_kept=0, **options)

    def test_reviewed_archive_preserves_live_original_and_old_citation_bytes(self):
        self.project.write_bytes(self.project.read_bytes().replace(b"\n", b"\r\n"))
        before = self.project.read_bytes()
        review = self.review()
        old_source, _ = evidence.verify_source_line(
            self.root, {"path": self.relative, "line": review["wikiLink"]["line"]})
        original_source, _ = evidence.verify_source_line(
            self.root, {"path": self.relative, "line": review["sourceFooter"]["line"]})
        self.assertEqual(self.rotate()["moved"], 0)
        plan = self.rotate(reviewed_links=review)
        self.assertEqual((plan["moved"], plan["skippedLinks"]), (1, 0))
        self.assertEqual(self.project.read_bytes(), before)
        self.rotate(reviewed_links=review, apply=plan["revision"])

        archived = archive_reader.read_path(self.root, plan["archives"][0]["path"])
        archived_lines = archived["raw"].splitlines(keepends=True)
        rendered = self.sentence.replace(self.old, self.new) + "\r\n"
        number = archived_lines.index(rendered)
        self.assertEqual(archive_reader.original_lines(archived_lines, archived["archiveOrigin"])[number],
                         self.sentence + "\r\n")
        current_lines = self.project.read_bytes().splitlines(keepends=True)
        self.assertEqual(current_lines.count((self.footer + "\r\n").encode()), 1)
        self.assertNotIn(self.footer + "\r\n", archived["body"])
        resolved = evidence.resolve_source(self.root, old_source)
        self.assertEqual((resolved["path"], resolved["line"], resolved["ref"], resolved["quote"]),
                         (archived["path"], number + 1, old_source["ref"], self.sentence))
        kept = evidence.resolve_source(self.root, original_source)
        self.assertEqual((kept["path"], kept["ref"], kept["quote"]),
                         (self.relative, original_source["ref"], self.footer))
        self.assertEqual(self.project.read_text().splitlines()[kept["line"] - 1], self.footer)

    def test_manual_target_must_be_unique_and_based_on_current_bytes(self):
        before = self.project.read_bytes()
        review = self.review()
        with self.assertRaises(ValueError):
            self.rotate(reviewed_links={**review, "revision": "0" * 64})
        for value in ("[[Notes/Осмотр#^absent|Вопросы]]", "[[Notes/Осмотр#Вопросы|Вопросы]]"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.rotate(reviewed_links={**review, "wikiLink": {**review["wikiLink"], "new": value}})
        self.assertEqual(self.project.read_bytes(), before)
        self.project.write_text(self.project.read_text().replace(self.sentence, self.sentence + " и " + self.old))
        with self.assertRaises(ValueError):
            self.rotate(reviewed_links=self.review())

    def test_only_a_verified_separate_final_source_link_can_stay_in_project(self):
        before = self.project.read_text()
        for replacement in (self.sentence + "\n" + self.footer,
                            self.sentence + "\n\n" + self.footer + "\nЕсть продолжение.",
                            self.sentence + "\n\n" + self.footer + "\n### 2021-03-04 — новое\nДанные осмотра."):
            with self.subTest(replacement=replacement):
                self.project.write_text(before.replace(self.sentence + "\n\n" + self.footer, replacement))
                with self.assertRaises(ValueError):
                    self.rotate(reviewed_links=self.review())
        self.project.write_text(before)
        review = self.review()
        with self.assertRaises(ValueError):
            self.rotate(reviewed_links={**review, "sourceFooter": {**review["sourceFooter"], "target": "Notes/Осмотр.md"}})
        self.assertFalse((self.root / archive_reader.BASE).exists())

    def test_second_unresolved_link_in_reviewed_snapshot_keeps_all_its_original_lines(self):
        self.project.write_text(self.project.read_text().replace(self.sentence,
                                self.sentence + "\nПрикреплён [план][уточнить]."))
        before = self.project.read_bytes()
        review = self.review()
        plan = self.rotate(reviewed_links=review)
        self.assertEqual((plan["moved"], plan["skippedLinks"]), (0, 1))
        self.rotate(reviewed_links=review, apply=plan["revision"])
        self.assertEqual(self.project.read_bytes(), before)
        self.assertFalse((self.root / archive_reader.BASE).exists())

    def test_review_of_one_entry_does_not_move_another_with_an_unknown_source(self):
        self.project.write_text(self.project.read_text().replace("### 2020-03-04",
                                "### 2019-05-06 — опыт\nСм. [схему][неизвестно].\n### 2020-03-04"))
        review = self.review()
        plan = self.rotate(reviewed_links=review)
        self.assertEqual((plan["moved"], plan["skippedLinks"]), (1, 1))
        self.rotate(reviewed_links=review, apply=plan["revision"])
        self.assertIn("См. [схему][неизвестно].", self.project.read_text())

    def test_cli_preview_reads_review_file_and_respects_retention(self):
        newer = "".join(f"### 2025-04-0{day} — осмотр\nНовые сведения.\n" for day in range(1, 6))
        self.project.write_text(self.project.read_text().replace("### 2020-03-04", newer + "### 2020-03-04"))
        before = self.project.read_bytes()
        review_file = self.root.parent / "review.json"
        review_file.write_text(json.dumps(self.review(), ensure_ascii=False))
        result = subprocess.run([sys.executable, "-m", "second_brain", "--vault", str(self.root),
                                 "project-history-archive", self.project_id, "--as-of", "2026-11-28",
                                 "--reviewed-links", str(review_file)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual((plan["moved"], plan["skippedLinks"], plan["applied"]), (1, 0, False))
        self.assertEqual(self.project.read_bytes(), before)

    def test_source_heading_keeps_existing_heading_bookmark_unique(self):
        with self.project.open("a", encoding="utf-8") as stream:
            stream.write("\n## Источник карточки\nСсылка из письма.\n")
        review = self.review()
        plan = self.rotate(reviewed_links=review)
        self.rotate(reviewed_links=review, apply=plan["revision"])
        lines = self.project.read_text().splitlines()
        self.assertEqual(lines.count("## Источник карточки"), 1)
        self.assertIn("## Источник карточки (2)", lines)
