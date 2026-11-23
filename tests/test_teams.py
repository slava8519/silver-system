from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from second_brain import storage
from second_brain.inbox import Inbox
from second_brain.people import People
from second_brain.search import fragment
from second_brain.teams import Teams


class TeamTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / "Vault")
        self.teams = Teams(self.root)

    def person(self, name, key=None):
        return self.teams.create_person(name, request_id=key or name)

    def group(self, name):
        return self.teams.create(name, request_id=name)

    def test_empty_context_reads_without_creating_files_or_picking_someone(self):
        before = set(self.root.rglob("*"))
        blank = self.teams.context()
        self.assertEqual((blank["owner"], blank["team"], blank["members"], blank["ownerLine"]), (None, None, [], None))
        self.assertEqual(len(blank["revision"]), 64)
        with self.assertRaises(ValueError):
            self.teams.set_context(revision=None, owner=None)
        self.assertEqual(self.teams.list()["total"], 0)
        self.assertEqual(set(self.root.rglob("*")), before)
        first, second = self.person("Мира", "a"), self.person("Мира", "b")
        group = self.group("Съёмочная группа")
        self.assertEqual({p["id"] for p in People(self.root).candidates("Мира")}, {first["id"], second["id"]})
        self.assertEqual(self.teams.context(), blank)
        self.assertEqual(self.person("Мира", "a"), first)
        with self.assertRaises(storage.ConflictError):
            self.person("Другое имя", "a")
        selected = self.teams.set_context(revision=blank["revision"], team=group["id"], owner=second["id"])
        self.assertEqual(selected["owner"], second)
        self.assertEqual(selected["team"]["id"], group["id"])
        self.assertEqual(selected["members"], [])
        self.assertEqual(self.teams.for_person(second["id"])["total"], 0)

    def test_one_person_in_three_groups_joins_existing_inbox_identity(self):
        inbox = Inbox(self.root)
        path = self.root / "Inbox/Встреча.md"
        path.write_text("Согласовывает эксперименты. Личная подробность.")
        area = storage.read_record(storage.create(self.root, "direction", "Парфюмерия"))
        person = self.person("Аня")
        facts = [{"kind": "role", "text": "Согласовывает эксперименты", "visibility": "shared"},
                 {"kind": "observation", "text": "Личная подробность", "visibility": "personal"}]
        inbox.process({"path": "Inbox/Встреча.md", "revision": inbox.preview("Inbox/Встреча.md")["revision"],
                       "person": {"id": person["id"]},
                       "facts": [{**f, "line": 1, "direction": area["id"]} for f in facts]})
        person_file = self.root / person["path"]
        unchanged = person_file.read_bytes()
        groups = [self.group(name) for name in ("ИТ-проекты", "Семья", "Парфюмерия")]
        for group in groups:
            added = self.teams.member(group["id"], person["id"], present=True, revision=group["revision"])
            self.assertEqual(added["total"], 1)
            line = (self.root / added["path"]).read_text().splitlines()[added["members"][0]["line"] - 1]
            self.assertIn(person["id"], line)
            self.assertIn(unquote(person_file.name), unquote(line))
            self.assertEqual(self.teams.member(group["id"], person["id"], present=True, revision=group["revision"]),
                             added)
        self.assertEqual(unchanged, person_file.read_bytes())
        self.assertEqual(len(People(self.root).detail(person["id"])["facts"]), 1)
        self.assertEqual(len(list(self.teams.card_paths())), 3)
        self.assertEqual(list(People(self.root).card_paths()), [person_file])
        page = self.teams.for_person(person["id"], limit=1, offset=1)
        self.assertEqual((len(page["items"]), page["total"], page["truncated"]), (1, 3, True))
        profile = self.teams.set_context(revision=self.teams.context()["revision"],
                                         owner=person["id"], team=groups[2]["id"])
        self.assertNotIn("Личная подробность", repr(profile))
        self.assertNotIn("эксперименты", repr(profile))
        self.assertEqual(set(profile["members"][0]), {"id", "name", "path", "line"})

    def test_file_moves_missing_cards_and_explicit_clear(self):
        person = self.person("Алекс")
        group = self.group("Студия")
        added = self.teams.member(group["id"], person["id"], present=True, revision=group["revision"])
        chosen = self.teams.set_context(revision=self.teams.context()["revision"], owner=person["id"], team=group["id"])
        folder = self.root / "People/Old"
        folder.mkdir()
        current_person = (self.root / person["path"]).rename(folder / "Участник.md")
        current_group = (self.root / group["path"]).rename(folder / "Состав.md")
        current_person.write_text(current_person.read_text().replace('title: "Алекс"', 'title: "Алексей"'))
        self.teams.rename(group["id"], "Мастерская", added["revision"])
        actual = self.teams.context()
        self.assertEqual(actual["owner"]["name"], "Алексей")
        self.assertEqual(actual["team"]["name"], "Мастерская")
        self.assertEqual(self.root / actual["members"][0]["path"], current_person)
        self.assertEqual(self.root / actual["team"]["path"], current_group)
        self.assertEqual(self.group("Студия")["name"], "Мастерская")
        self.assertEqual(self.person("Алекс")["name"], "Алексей")
        current_person.unlink()
        absent = self.teams.context()
        self.assertTrue(absent["owner"]["missing"])
        self.assertTrue(absent["members"][0]["missing"])
        self.assertEqual(absent["members"][0]["id"], person["id"])
        purged = self.teams.member(group["id"], person["id"], present=False, revision=self.teams.show(group["id"])["revision"])
        self.assertEqual(purged["total"], 0)
        current_group.unlink()
        self.assertTrue(self.teams.context()["team"]["missing"])
        cleared = self.teams.set_context(revision=chosen["revision"], owner=None)
        self.assertIsNone(cleared["owner"])
        self.assertEqual(cleared["team"]["id"], group["id"])
        self.assertIsNone(self.teams.set_context(revision=cleared["revision"], team=None)["team"])

    def test_owner_prose_crlf_protected_and_profile_update_is_atomic(self):
        p1, p2 = self.person("Ада"), self.person("Зоя")
        group = self.group("Работа")
        profile = self.root / "Settings/Owner.md"
        original = "---\r\ncustom: [one, two]\r\n---\r\n# Для себя\r\nНадо спросить.".encode()
        profile.write_bytes(original)
        before = self.teams.context()
        with self.assertRaises(FileNotFoundError):
            self.teams.set_context(revision=before["revision"], owner=p1["id"], team="missing")
        self.assertEqual(profile.read_bytes(), original)
        first = self.teams.set_context(revision=before["revision"], owner=p1["id"], team=group["id"])
        self.assertTrue(profile.read_bytes().startswith(original))
        self.assertNotIn(b"\n", profile.read_bytes().replace(b"\r\n", b""))
        profile.write_bytes(b"\r\n" + profile.read_bytes())
        updated = self.teams.context()
        self.assertEqual((updated["ownerLine"], updated["teamLine"]), (first["ownerLine"] + 1, first["teamLine"] + 1))
        with self.assertRaises(storage.ConflictError):
            self.teams.set_context(revision=first["revision"], owner=p2["id"])
        preserved = profile.read_bytes()
        with patch.object(storage, "replace_bytes", side_effect=OSError("write failed")), self.assertRaises(OSError):
            self.teams.set_context(revision=updated["revision"], owner=p2["id"])
        self.assertEqual(profile.read_bytes(), preserved)
        changed = self.teams.set_context(revision=updated["revision"], owner=p2["id"])
        self.assertEqual(changed["team"], updated["team"])
        self.assertEqual(changed["owner"], p2)
        for segment in (preserved, profile.read_bytes()):
            self.assertIn(original, segment)
        def without_new_history(content):
            return [p for p in content.split(b"<!-- people-history -->")[0].splitlines() if b"<!-- owner-id:" not in p]
        self.assertEqual(without_new_history(preserved), without_new_history(profile.read_bytes()))

    def test_competing_group_writers_preserve_manual_edits(self):
        a, b = self.person("А"), self.person("Б")
        group = self.group("Лаборатория")
        path = self.root / group["path"]
        text = path.read_text().replace('title: "Лаборатория"', 'title: "Лаборатория" # шапка')
        text = text.replace("# Лаборатория", "# Мой собственный заголовок")
        path.write_text(text + "\n## Наблюдения\nТекст владельца.\n")
        with self.assertRaises(storage.ConflictError):
            self.teams.rename(group["id"], "Клуб", group["revision"])
        fresh = self.teams.show(group["id"])
        with ThreadPoolExecutor(2) as pool:
            writes = [pool.submit(self.teams.member, group["id"], p["id"], present=True, revision=fresh["revision"])
                      for p in (a, b)]
        self.assertEqual(sum(isinstance(f.exception(), storage.ConflictError) for f in writes), 1)
        saved = self.teams.show(group["id"])
        renamed = self.teams.rename(group["id"], "Клуб", saved["revision"])
        self.assertEqual(saved["members"], renamed["members"])
        self.assertIn('title: "Клуб"  # шапка', path.read_text())
        self.assertIn("# Мой собственный заголовок", path.read_text())
        self.assertIn("Текст владельца.", path.read_text())

    def test_decision_history_is_separate_from_current_links_and_idempotent(self):
        p1, p2 = self.person("Нина"), self.person("Лев")
        group = self.group("Дом")
        added = self.teams.member(group["id"], p1["id"], present=True, revision=group["revision"])
        renamed = self.teams.rename(group["id"], "Семья", added["revision"])
        removed = self.teams.member(group["id"], p1["id"], present=False, revision=renamed["revision"])
        group_path = self.root / group["path"]
        after = group_path.read_bytes()
        self.assertEqual(removed["members"], [])
        self.assertEqual(self.teams.for_person(p1["id"])["items"], [])
        self.assertEqual(self.teams.member(group["id"], p1["id"], present=False, revision=renamed["revision"]), removed)
        self.assertEqual(self.teams.rename(group["id"], "Семья", removed["revision"]), removed)
        self.assertEqual(group_path.read_bytes(), after)
        log = group_path.read_text().partition("<!-- people-history -->")[2]
        self.assertEqual(log.count(p1["id"]), 2)
        self.assertIn("Добавлен участник", log)
        self.assertIn("Исключён участник", log)
        self.assertIn("«Дом» → «Семья»", log)
        before = self.teams.context()
        first = self.teams.set_context(revision=before["revision"], owner=p1["id"], team=group["id"])
        switched = self.teams.set_context(revision=first["revision"], owner=p2["id"])
        final = self.teams.set_context(revision=switched["revision"], team=None)
        profile = self.root / final["path"]
        text = profile.read_text()
        self.assertEqual((final["owner"], final["team"], final["teamLine"]), (p2, None, None))
        self.assertIn(p2["id"], text.splitlines()[final["ownerLine"] - 1])
        self.assertEqual(self.teams.set_context(revision=switched["revision"], team=None), final)
        self.assertEqual(profile.read_text(), text)
        log = text.partition("<!-- people-history -->")[2].splitlines()
        changes = [line for line in log if " → " in line]
        self.assertEqual(len(changes), 3)
        self.assertIn(p1["id"], changes[1])
        self.assertIn(p2["id"], changes[1])
        self.assertIn(group["id"], changes[2])
        self.assertTrue(changes[2].endswith("→ не выбрано"))

    def test_read_cites_only_current_group_and_profile_links_after_moves_and_private_annotations(self):
        member = self.person("Ася")
        group = self.group("Мастерская")
        self.teams.member(group["id"], member["id"], present=True, revision=group["revision"])
        profile = self.teams.set_context(revision=self.teams.context()["revision"], owner=member["id"], team=group["id"])
        group_path = self.root / group["path"]
        group_path.write_text(group_path.read_text().replace("- [Ася]", "- Моя личная пометка. [Ася]", 1))
        owner_path = self.root / profile["path"]
        owner_path.write_text(owner_path.read_text().replace(" <!-- owner-id:", " моя закрытая запись <!-- owner-id:", 1))
        profile_text = owner_path.read_text()
        team_marker = storage.id_marker("team", group["id"])
        owner_path.write_text(profile_text.replace(team_marker, team_marker + " Внутреннее замечание.", 1))
        def read(row, key, path):
            return fragment(self.root, path, row[key], row[key])
        member_row = self.teams.show(group["id"])["members"][0]
        text = read(member_row, "line", group["path"])
        self.assertIn("[Ася]", text)
        self.assertIn(member["id"], text)
        self.assertNotIn("личная", text)
        self.assertEqual(read(profile, "ownerLine", profile["path"]), storage.id_marker("owner", member["id"]))
        team_excerpt = read(profile, "teamLine", profile["path"])
        self.assertIn("[Мастерская]", team_excerpt)
        self.assertNotIn("замечание", team_excerpt)
        for path, start, end, hint in [(group["path"], 1, 2, "team-show"), (group["path"], 1, 1, "team-show"),
                                       (profile["path"], 1, 1, "owner-context"), (profile["path"], 1, 5, "owner-context")]:
            with self.subTest(path=path, end=end), self.assertRaisesRegex(ValueError, hint):
                fragment(self.root, path, start, end)
        # Move both types of card. A person can live in Groups and a team outside it.
        group_path.rename(self.root / "People/Другое имя.md")
        person_path = (self.root / member["path"]).rename(self.root / "People/Groups/Ася.md")
        group_path = self.root / "People/Другое имя.md"
        group_path.write_text(group_path.read_text().replace("## Участники", "Заметка владельца.\n\n## Участники"))
        current = self.teams.context()
        self.assertEqual(current["team"]["path"], "People/Другое имя.md")
        current_row = current["members"][0]
        self.assertEqual(current_row["line"], member_row["line"] + 2)
        self.assertEqual(read(current_row, "line", current["team"]["path"]), storage.id_marker("person", member["id"]))
        inbox = Inbox(self.root)
        direction = storage.read_record(storage.create(self.root, "direction", "Работа"))
        (self.root / "Inbox/Источник.md").write_text("Общий факт. Личная деталь.")
        inbox.process({"path": "Inbox/Источник.md", "revision": inbox.preview("Inbox/Источник.md")["revision"],
                       "person": {"id": member["id"]},
                       "facts": [{"kind": "role", "text": "Общий факт", "visibility": "shared",
                                  "line": 1, "direction": direction["id"]},
                                 {"kind": "observation", "text": "Личная деталь", "visibility": "personal",
                                  "line": 1, "direction": direction["id"]}]})
        individual = People(self.root).detail(member["id"], personal=True)
        self.assertEqual(self.root / individual["path"], person_path)
        common, personal = individual["facts"]
        self.assertEqual(read(common, "cardLine", individual["path"]), "Общий факт")
        with self.assertRaisesRegex(ValueError, "общую строку карточки человека"):
            read(personal, "cardLine", individual["path"])
        (self.root / "People/Link.md").symlink_to(group_path)
        for bad in ("People/Link.md", "People/../Settings/Owner.md", "Settings/../Settings/Owner.md"):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                fragment(self.root, bad, current_row["line"], current_row["line"])

        saved = self.teams.set_context(revision=current["revision"], team=None)
        self.assertIsNone(saved["team"])
        self.assertIn("Внутреннее замечание.", owner_path.read_text())
        self.assertNotIn("замечание", owner_path.read_text().partition("<!-- people-history -->")[2])

    def test_duplicate_ids_damaged_markers_and_symlinks_fail_closed(self):
        group = self.group("Сад")
        path = self.root / group["path"]
        duplicate = path.with_name("Другая группа.md")
        duplicate.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, "ID"):
            self.teams.show(group["id"])
        duplicate.unlink()
        original = path.read_text()
        path.write_text(original.replace("<!-- /team-members -->", ""))
        with self.assertRaises(storage.ConflictError):
            self.teams.show(group["id"])
        profile = self.root / "Settings/Owner.md"
        outside = self.root.parent / "Private.md"
        outside.write_text("Не менять")
        profile.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Символическая"):
            self.teams.context()
        self.assertEqual(outside.read_text(), "Не менять")
        path.write_text(original)
        elsewhere = (self.root / "People").rename(self.root.parent / "Moved")
        (self.root / "People").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Символическая"):
            self.teams.rename(group["id"], "Чужая запись", group["revision"])
        self.assertEqual((elsewhere / "Groups" / path.name).read_text(), original)
