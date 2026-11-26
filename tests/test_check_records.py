from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from examples import inbox_vault, okr_review_vault, report_vault
from second_brain import reports, storage
from second_brain.check_records import check_records
from second_brain.cycles import Cycles
from second_brain.gantt import Gantt
from second_brain.planning import Planning
from second_brain.teams import Teams
from second_brain.vault_check import check_vault


class Report:
    def __init__(self):
        self.issues = []
        self.unavailable = []
        self.facts = []
        self.records = set()

    def add(self, code, path, line, message):
        self.issues.append((code, path, line, message))

    def unreadable(self, code, path, line, message):
        self.unavailable.append((code, path, line, message))

    def fact_source(self, path, fact):
        self.facts.append((path, fact))

    def record_id(self, kind, identifier):
        self.records.add((kind, identifier))


class RecordCheckTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")

    def scan(self):
        result = Report()
        count = check_records(self.root, result)
        return count, result

    def test_three_directions_teams_okr_and_versioned_reports_are_clean_and_read_only(self):
        scenarios = (("intake", lambda p: inbox_vault.generate(p, with_teams=True)),
                     ("quarter", okr_review_vault.generate), ("series", report_vault.generate))
        for name, generate in scenarios:
            with self.subTest(name=name):
                root = self.root.parent / name
                generate(root)
                before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
                result = Report()
                self.assertGreater(check_records(root, result), 5)
                self.assertEqual((result.issues, result.unavailable), ([], []))
                self.assertEqual({p: p.read_bytes() for p in root.rglob("*") if p.is_file()}, before)
                if name == "intake":
                    self.assertTrue(result.facts)
                    self.assertIn("sourceId", result.facts[0][1])

    def test_broken_headers_do_not_hide_other_duplicate_ids_and_wrong_type(self):
        bad = self.root / "Tasks/First.md"
        bad.write_text('---\ntype: task\ntitle: example\nschema_version: 1\n---\n')
        private = "личный фрагмент"
        unreadable_header = self.root / "Notes/Corrupt.md"
        unreadable_header.write_text('---\nid: example\ntype: note\ntitle: "' + private)
        area = storage.create(self.root, "direction", "Дача")
        duplicate = self.root / "Directions/Copy.md"
        duplicate.write_bytes(area.read_bytes())
        note = storage.create(self.root, "note", "Карточка")
        task = Planning(self.root).create_task("Купить краску")
        path = self.root / task["path"]
        raw = path.read_bytes().replace(b"---\n#", b'project: "' + storage.read_record(note)["id"].encode() + b'"\n---\n#')
        path.write_bytes(raw)
        (self.root / "Notes/Old.md").write_text("---\ntype: journal\nid: my-entry\nschema_version: 2\n---\n")
        (self.root / "Notes/Free.md").write_text("---\ntype: note\n---\n# Собственные метаданные")
        self.assertEqual(self.scan()[0], 6)
        result = self.scan()[1]
        self.assertEqual({(c, p) for c, p, _, _ in result.issues},
                         {("record.header", p) for p in (bad, unreadable_header)}
                         | {("record.duplicate_id", p) for p in (area, duplicate)} | {("record.reference", path)})
        self.assertEqual(result.unavailable, [])
        self.assertNotIn(private, repr(result.issues))
        for code, cited, line, message in result.issues:
            if code == "record.reference":
                self.assertIn("project:", cited.read_text().splitlines()[line - 1])
            if code == "record.duplicate_id":
                self.assertIn("id:", cited.read_text().splitlines()[line - 1])
                self.assertIn("Directions/", message)

    def test_current_membership_uses_id_while_closed_plan_keeps_snapshot(self):
        plan = Planning(self.root)
        task = plan.create_task("Сдать образец")
        active = plan.create_plan("План следующих проб")
        old = plan.create_plan("Первая проба")
        for item in (active, old):
            plan.add_task(item["id"], task["id"])
        seal = Cycles(self.root)
        seal.close(old["id"], seal.preview(old["id"])["revision"])
        moved = (self.root / task["path"]).rename(self.root / "Tasks/moved.md")
        self.assertEqual(self.scan()[1].issues, [])
        moved.unlink()
        missing, = self.scan()[1].issues
        self.assertEqual(missing[:2], ("record.reference", self.root / active["path"]))
        self.assertIn(task["id"], missing[1].read_text().splitlines()[missing[2] - 1])
        snapshot = self.root / old["path"]
        snapshot.write_bytes(snapshot.read_bytes().replace(b'<!-- brain-cycle: {', b'<!-- brain-cycle: {"broken":1,'))
        self.assertIn(("record.structure", snapshot), {(c, p) for c, p, _, _ in self.scan()[1].issues})

    def test_team_members_and_owner_use_explicit_ids_and_leave_history_alone(self):
        service = Teams(self.root)
        person = service.create_person("Тёзка", request_id="owner")
        service.create_person("Тёзка", request_id="other")
        group = service.create("Семья", request_id="family")
        service.member(group["id"], person["id"], present=True, revision=group["revision"])
        service.set_context(revision=service.context()["revision"], owner=person["id"], team=group["id"])
        self.assertEqual(self.scan()[1].issues, [])
        (self.root / person["path"]).unlink()
        result = self.scan()[1]
        self.assertEqual({(c, p) for c, p, _, _ in result.issues},
                         {("record.reference", self.root / group["path"]),
                          ("record.reference", self.root / "Settings/Owner.md")})
        for _, path, line, _ in result.issues:
            self.assertIn(person["id"], path.read_text().splitlines()[line - 1])

    def test_unavailable_folder_continues_and_ordinary_notes_are_outside_the_scope(self):
        path = self.root / "Tasks"
        path.rmdir()
        path.symlink_to(self.root.parent, target_is_directory=True)
        (self.root / "Notes/plain.md").write_bytes(b"---\nLegacy note\n")
        (self.root / "Notes/another.md").symlink_to(self.root.parent / "outside.md")
        note = storage.create(self.root, "note", "Обычная карточка")
        count, result = self.scan()
        self.assertEqual(count, 1)
        self.assertEqual(len(result.unavailable), 1)
        self.assertEqual(result.unavailable[0][1], path)
        self.assertEqual(result.records, {("note", storage.read_record(note)["id"])})

    def test_one_id_can_have_several_report_template_versions(self):
        original = reports.get_template(self.root, "team-digest")
        reports.save_template(self.root, id=original["id"], revision=original["revision"],
                              title="Новый порядок", audience="Владелец", guidance="", body="## Итоги")
        self.assertEqual(len(list((self.root / reports.TEMPLATES).rglob("*.md"))), 2)
        count, checked = self.scan()
        self.assertEqual((count, checked.issues), (2, []))

    def test_gantt_read_and_vault_check_locate_the_same_unclosed_task_entry(self):
        task = Planning(self.root).create_task("Первая проба")
        gantt = Gantt(self.root)
        secret = "Личная формулировка результата"
        gantt.edit({"id": task["id"], "revision": task["revision"], "field": "brief", "text": secret,
                    "actor": "manual"})
        path = self.root / task["path"]
        self.assertEqual(check_vault(self.root)["status"], "ok")
        text = path.read_text().replace("<!-- /brain-gantt -->", "")
        path.write_text(text)
        line = next(n for n, s in enumerate(text.splitlines(), 1) if s.startswith("<!-- brain-gantt: "))
        with self.assertRaises(ValueError):
            gantt.detail(task["id"])
        result = check_vault(self.root)
        problem, = result["issues"]["items"]
        self.assertEqual((result["status"], problem["code"], problem["path"], problem["line"]),
                         ("issues", "record.structure", task["path"], line))
        self.assertNotIn(secret, repr(result))

    def test_issue_read_and_vault_check_locate_invalid_source_metadata_and_unclosed_footer(self):
        issue = reports.save_issue(self.root, title="Проба", body="Взяли первый образец.",
                                   template_id="free-report", template_version=1,
                                   sources=[{"path": "Home.md", "line": 1}])
        path = self.root / issue["path"]
        text = path.read_text()
        path.write_bytes(text.replace("\n", "\r\n").encode())
        self.assertEqual(check_vault(self.root)["status"], "ok")
        self.assertEqual(reports.get_issue(self.root, issue["id"])["body"], issue["body"])
        source_line = next(line for line in text.splitlines() if line.startswith(reports._SOURCE))
        secret = "приватный фрагмент"
        cases = [(text.replace(reports._END, ""), reports._FOOTER),
                 (text.replace(source_line, reports._SOURCE + secret + " -->"), source_line),
                 (text.replace(source_line, source_line[:-4]), source_line)]
        for invalid, original in cases:
            with self.subTest(original=original):
                path.write_text(invalid)
                line = text.splitlines().index(original) + 1
                with self.assertRaisesRegex(ValueError, f"строка {line}"):
                    reports.get_issue(self.root, issue["id"])
                result = check_vault(self.root)
                problem, = result["issues"]["items"]
                self.assertEqual((result["status"], problem["code"], problem["path"], problem["line"]),
                                 ("issues", "record.structure", issue["path"], line))
                self.assertNotIn(secret, repr(result))
