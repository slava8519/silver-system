from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from examples.report_vault import generate
from second_brain import demo_cleanup, reports, storage
from second_brain.report_defaults import BUILTIN_TEMPLATES


ADDITIONAL_FORMATS = ("meeting-summary", "decision-brief", "test-results", "project-status", "retrospective")


class ReportTemplateCleanup(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "Vault"

    def clean(self):
        preview = demo_cleanup.preview(self.root)
        return demo_cleanup.apply(self.root, preview["revision"])

    def versions(self):
        return {p.relative_to(self.root / reports.TEMPLATES).as_posix()
                for p in (self.root / reports.TEMPLATES).rglob("*.md")}

    def test_clean_install_and_full_demo_withdrawal_expose_all_starter_formats(self):
        storage.initialize(self.root)
        self.assertFalse(demo_cleanup.preview(self.root)["available"])
        before = set(self.root.rglob("*.md"))
        starters = {key: reports.get_template(self.root, key) for key in BUILTIN_TEMPLATES}
        catalog = {item["id"] for item in reports.list_templates(self.root)["items"]}
        self.assertTrue(set(ADDITIONAL_FORMATS).issubset(catalog))
        self.assertEqual(set(self.root.rglob("*.md")), before)
        self.root = generate(self.root.parent / "Demo")
        self.assertEqual(reports.get_template(self.root, "team-digest")["version"], 2)
        cleaned = self.clean()
        self.assertEqual(cleaned["keepCount"], 0)
        self.assertFalse(self.versions())
        self.assertEqual(reports.list_issues(self.root)["total"], 0)
        self.assertEqual(reports.list_series(self.root)["total"], 0)
        self.assertEqual(catalog, {item["id"] for item in reports.list_templates(self.root)["items"]})
        for key, original in starters.items():
            self.assertEqual(reports.get_template(self.root, key), original)
        with ZipFile(self.root / cleaned["archive"]) as archive:
            self.assertIn(reports.TEMPLATES + "/team-digest/v2.md", archive.namelist())

    def test_owner_issue_keeps_its_pinned_version_and_ancestry(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.root = generate(self.root.parent / f"WithIssue{version}")
                issue = reports.save_issue(self.root, title="Итоги кружка", template_id="team-digest",
                                           template_version=version, body="Участники выбрали дату встречи.")
                pinned = reports.get_template(self.root, "team-digest", version)
                self.clean()
                self.assertTrue({f"team-digest/v{i}.md" for i in range(1, version + 1)}.issubset(self.versions()))
                self.assertEqual(reports.get_template(self.root, "team-digest", version)["revision"], pinned["revision"])
                saved = reports.get_issue(self.root, issue["id"])
                self.assertEqual(saved["body"], issue["body"])
                self.assertFalse(saved["templateChanged"])
                self.assertEqual(reports.list_issues(self.root)["total"], 1)

    def test_author_template_keeps_manual_version_and_both_ancestors(self):
        generate(self.root)
        prior = reports.get_template(self.root, "team-digest")
        format_ = {k: prior[k] for k in ("title", "body", "audience", "guidance")}
        format_["guidance"] += "\nПроверьте время встречи с читателями."
        author = reports.save_template(self.root, **format_, id=prior["id"], revision=prior["revision"])
        self.assertEqual(author["version"], 3)
        self.clean()
        self.assertEqual(reports.get_template(self.root, "team-digest"), author)
        self.assertTrue({"team-digest/v1.md", "team-digest/v2.md", "team-digest/v3.md"}.issubset(self.versions()))
        self.assertEqual(reports.list_issues(self.root)["total"], 0)

    def test_customized_starter_and_its_issue_survive_removing_examples(self):
        generate(self.root)
        starter = reports.get_template(self.root, "one-on-one")
        fields = {k: starter[k] for k in ("title", "body", "audience", "guidance")}
        fields["body"] += "\n\n## Согласованные действия"
        author = reports.save_template(self.root, **fields, id=starter["id"], revision=starter["revision"])
        self.assertEqual(author["version"], 2)
        issue = reports.save_issue(self.root, title="Личная встреча", body="Согласуем график дежурств.",
                                   template_id=author["id"], template_version=2)
        files = {self.root / r["path"] for r in (author, issue, reports.get_template(self.root, starter["id"], 1))}
        before = {path: path.read_bytes() for path in files}
        self.clean()
        self.assertEqual(before, {path: path.read_bytes() for path in files})
        self.assertEqual(author, reports.get_template(self.root, author["id"]))
        self.assertFalse(reports.get_issue(self.root, issue["id"])["templateChanged"])

    def test_live_series_and_ambiguous_owner_links_still_protect_references(self):
        generate(self.root)
        series = reports.create_series(self.root, title="Совет кружка", template_id="team-digest",
                                       cadence="monthly", start="2027-01-01")
        personal = storage.create(self.root, "note", "Моя ссылка", "Подготовить встречу по [[v1.md]].")
        initial = self.versions()
        self.clean()
        self.assertTrue(personal.is_file())
        self.assertEqual(reports.get_series(self.root, series["id"])["nextStart"], "2027-01-01")
        self.assertEqual(reports.get_template(self.root, "team-digest")["version"], 2)
        self.assertTrue({f for f in initial if f.endswith("/v1.md")}.issubset(self.versions()))

    def test_personal_revisions_and_links_survive_for_each_additional_format(self):
        for identifier in ADDITIONAL_FORMATS:
            with self.subTest(template=identifier):
                self.root = storage.initialize(self.root.parent / identifier)
                demo = reports.save_issue(self.root, template_id=identifier, template_version=1,
                                          title="Учебный выпуск", body="Участники обсудили срок проверки.")
                initial = reports.get_template(self.root, identifier, 1)
                demo_cleanup.record_seed(self.root)
                self.assertEqual(demo_cleanup.preview(self.root)["removeCount"], 2)

                fields = {k: initial[k] for k in ("title", "body", "audience", "guidance")}
                fields["guidance"] += "\nСверьте согласованные действия с протоколом."
                edited = reports.save_template(self.root, id=identifier, revision=initial["revision"], **fields)
                self.assertEqual(edited["version"], 2)
                mine = reports.save_issue(self.root, template_id=identifier, template_version=2,
                                          title="Мой выпуск", body="Владелец согласовал срок проверки.")
                first = self.root / initial["path"]
                link = storage.markdown_link("Исходный формат", first, self.root / "Notes")
                note = storage.create(self.root, "note", "Мои форматы", link)
                paths = (first, note, self.root / edited["path"], self.root / mine["path"])
                before = {p: p.read_bytes() for p in paths}

                self.clean()
                self.assertFalse((self.root / demo["path"]).exists())
                self.assertEqual(before, {p: p.read_bytes() for p in paths})
                self.assertIn(link, note.read_text(encoding="utf-8"))
                self.assertEqual(reports.get_template(self.root, identifier, 1)["revision"], initial["revision"])
                self.assertEqual(reports.get_template(self.root, identifier), edited)
                self.assertFalse(reports.get_issue(self.root, mine["id"])["templateChanged"])
