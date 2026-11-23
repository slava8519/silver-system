import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from second_brain.__main__ import command_line, run
from second_brain.brief import EXCERPT_LENGTH


class TeamsCLI(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Хранилище"
        self.parser = command_line()
        self.cli("init")

    def cli(self, *args):
        return run(self.parser.parse_args(["--vault", str(self.root), *args]))

    def test_context_selection_and_membership_sources(self):
        before = self.cli("owner-context")
        self.assertIsNone(before["owner"])
        self.assertIsNone(before["team"])
        owner = self.cli("person-create", "Марина", "--request-id", "owner")
        self.assertEqual(owner, self.cli("person-create", "Марина", "--request-id", "owner"))
        member = self.cli("person-create", "Вера", "--request-id", "member")
        self.assertEqual(self.cli("people", "--match", "Вера")["items"][0]["id"], member["id"])
        # The group context contains membership evidence, never a person's note body.
        with (self.root / member["path"]).open("a", encoding="utf-8") as stream:
            stream.write("\nЛичная заметка о встрече.\n")
        team = self.cli("team-create", "Мастерская", "--request-id", "team")
        for person in (member, owner):
            team = self.cli("team-member", team["id"], person["id"], "--add", "--revision", team["revision"])
        self.assertIsNone(self.cli("owner-context")["team"])
        selected = self.cli("owner-context", "--owner", owner["id"], "--revision", before["revision"])
        self.assertIsNone(selected["team"])
        selected = self.cli("owner-context", "--team", team["id"], "--revision", selected["revision"])
        self.assertEqual(selected["owner"]["id"], owner["id"])
        self.assertNotIn("members", selected)
        command = [sys.executable, "-m", "second_brain", "--vault", str(self.root), "owner-context", "--limit", "1"]
        process = subprocess.run(command, capture_output=True, text=True, check=True, timeout=15)
        self.assertEqual(len(process.stdout.splitlines()), 1)
        self.assertNotIn("Личная заметка", process.stdout)
        first = json.loads(process.stdout)
        second = self.cli("owner-context", "--limit", "1", "--offset", "1")
        self.assertEqual((first["total"], first["truncated"], second["truncated"]), (2, True, False))
        member_rows = first["members"] + second["members"]
        self.assertEqual({p["id"] for p in member_rows}, {owner["id"], member["id"]})
        profile_lines = (self.root / first["path"]).read_text(encoding="utf-8").splitlines()
        self.assertIn(owner["id"], profile_lines[first["ownerLine"] - 1])
        self.assertIn(team["id"], profile_lines[first["teamLine"] - 1])
        team_lines = (self.root / first["team"]["path"]).read_text(encoding="utf-8").splitlines()
        for row in member_rows:
            self.assertIn(row["id"], team_lines[row["line"] - 1])
            self.assertTrue((self.root / row["path"]).is_file())
        for options in (("--clear-owner",), ("--revision", first["revision"]),
                        ("--clear-team", "--revision", before["revision"])):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.cli("owner-context", *options)
        cleared = self.cli("owner-context", "--clear-team", "--revision", first["revision"])
        self.assertIsNone(cleared["team"])
        self.assertEqual(cleared["owner"]["id"], owner["id"])
        self.assertIsNone(self.cli("owner-context", "--clear-owner", "--revision", cleared["revision"])["owner"])
        self.assertEqual(self.cli("team-show", team["id"])["total"], 2)

    def test_group_filter_combines_with_membership_before_pagination(self):
        person = self.cli("person-create", "Рен", "--request-id", "ren")
        for name in ("Альфа", "Лаборатория 1", "Лаборатория 2"):
            team = self.cli("team-create", name, "--request-id", name)
            self.cli("team-member", team["id"], person["id"], "--add", "--revision", team["revision"])
        self.cli("team-create", "Лаборатория 3", "--request-id", "empty")
        filters = ("--match", "ЛАБОРАТОРИЯ", "--person", person["id"], "--limit", "1")
        first = self.cli("team-list", *filters)
        second = self.cli("team-list", *filters, "--offset", "1")
        self.assertEqual((first["total"], first["truncated"], second["truncated"]), (2, True, False))
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        group = second["items"][0]
        renamed = self.cli("team-rename", group["id"], "Студия", "--revision", group["revision"])
        self.assertEqual(self.cli("team-list", "--match", "Студия")["items"][0]["id"], group["id"])
        removed = self.cli("team-member", group["id"], person["id"], "--remove", "--revision", renamed["revision"])
        self.assertEqual(removed["total"], 0)
        self.assertEqual(self.cli("team-list", "--person", person["id"], "--match", "Студия")["total"], 0)
        self.assertEqual(self.cli("team-show", group["id"])["members"], [])

    def test_long_names_in_cli_and_full_owner_context(self):
        person_name = "Д" * (EXCERPT_LENGTH + 30)
        team_name = "М" * (EXCERPT_LENGTH + 50)
        person = self.cli("person-create", person_name, "--request-id", "owner")
        team = self.cli("team-create", team_name, "--request-id", "team")
        membership = self.cli("team-member", team["id"], person["id"], "--add", "--revision", team["revision"])
        initial = self.cli("owner-context")
        saved = self.cli("owner-context", "--owner", person["id"], "--team", team["id"], "--revision", initial["revision"])
        show = self.cli("team-show", team["id"])
        brief_context = self.cli("ask", "Кто участвует?", "--brief", "--with-owner-context")["ownerContext"]
        self.assertEqual(self.cli("owner-context"), brief_context)
        for row, name in ((person, person_name), (team, team_name), (membership, team_name),
                          (saved["owner"], person_name), (saved["team"], team_name),
                          (show, team_name), (show["members"][0], person_name),
                          (self.cli("team-list")["items"][0], team_name),
                          (self.cli("team-list", "--person", person["id"])["items"][0], team_name),
                          (brief_context["owner"], person_name), (brief_context["team"], team_name),
                          (brief_context["members"][0], person_name)):
            self.assertEqual(row["name"], name[:EXCERPT_LENGTH])
            self.assertEqual(row["truncatedFields"], ["name"])
        full = self.cli("ask", "Кто участвует?", "--with-owner-context")["ownerContext"]
        self.assertEqual(full["owner"]["name"], person_name)
        self.assertEqual(full["team"]["name"], team_name)
        self.assertEqual(full["members"][0]["name"], person_name)
