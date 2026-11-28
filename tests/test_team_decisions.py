import json
from pathlib import Path
import tempfile
import unittest

from examples.inbox_vault import generate
from second_brain.__main__ import command_line, run
from second_brain.people import People
from second_brain.teams import Teams
from second_brain.workspace import Workspace


class TeamDecisionTests(unittest.TestCase):
    def test_model_receives_selected_team_and_only_shared_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = generate(Path(temporary) / "Учебные команды", with_teams=True)
            parser = command_line()
            def ask(*args):
                return run(parser.parse_args(["--vault", str(root), "ask", *args]))

            question = "За что отвечает Лидия Белова по проекту Маяк?"
            plain = ask(question, "--brief")
            annotated = ask(question, "--brief", "--with-owner-context")
            context = annotated.pop("ownerContext")
            self.assertEqual(annotated, plain)
            self.assertEqual(context["owner"]["name"], "Александра Орлова")
            self.assertEqual(context["team"]["name"], "Команда запуска")
            member, = context["members"]
            self.assertEqual(member["name"], "Лидия Белова")
            self.assertIn(member["id"], (root / context["team"]["path"]).read_text().splitlines()[member["line"] - 1])
            profile = (root / context["path"]).read_text().splitlines()
            self.assertIn(context["owner"]["id"], profile[context["ownerLine"] - 1])
            self.assertIn(context["team"]["id"], profile[context["teamLine"] - 1])
            self.assertNotIn("лиловом конверте", json.dumps([context, annotated], ensure_ascii=False))

            roster = Teams(root)
            self.assertEqual({g["name"] for g in roster.for_person(member["id"])["items"]},
                             {"Команда запуска", "Попутчики"})
            facts = People(root).detail(member["id"])["facts"]
            self.assertTrue(any("отвечает за проверку переключения" in f["text"] for f in facts))
            workshop, = roster.list(match="Мастерская")["items"]
            roster.set_context(revision=context["revision"], team=workshop["id"])
            new = ask(question, "--with-owner-context")["ownerContext"]
            self.assertEqual(new["owner"]["id"], context["owner"]["id"])
            self.assertEqual([p["name"] for p in new["members"]], ["Ева Ланская"])
            overview = Workspace(root).overview()
            self.assertEqual(overview["peopleCount"], 3)
            self.assertIn({"path": "People/Groups", "count": 3, "files": 3}, overview["folders"])

    def test_existing_vault_has_no_implicit_owner_or_team(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = generate(Path(temporary) / "Без настроек")
            args = command_line().parse_args(["--vault", str(root), "ask", "Кто я?", "--brief", "--with-owner-context"])
            context = run(args)["ownerContext"]
            self.assertIsNone(context["owner"])
            self.assertIsNone(context["team"])
            self.assertEqual(context["members"], [])
            self.assertFalse((root / "Settings/Owner.md").exists())
            self.assertFalse((root / "People/Groups").exists())
