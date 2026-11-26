"""Record observed edits of existing shared person facts during a vault refresh."""

from . import people, storage
from .inbox import Inbox


def refresh_people(root):
    """The caller holds the vault write lock. Personal facts stay outside this pass."""
    directory = people.People(root)
    inbox = None
    result = {"examined": 0, "changed": 0, "historyAdded": 0, "issues": []}
    seen, pending = {}, {}
    for path in directory.paths():
        relative = path.relative_to(directory.root).as_posix()
        try:
            raw = path.read_bytes()
            record, _ = storage._parse_record(path, raw, kinds={"person"})
            result["examined"] += 1
            identifier = record["id"]
            if identifier in seen:
                pending.pop(identifier, None)
                result["issues"].append({"path": relative, "reason": "ID человека совпадает с " + seen[identifier]})
                continue
            seen[identifier] = relative
            history = []
            for fact in people._parse_facts(record).values():
                if fact["visibility"] != "shared" or not fact["editedInFile"]:
                    continue
                try:
                    if inbox is None:
                        inbox = Inbox(directory.root)
                    proof = inbox.evidence(fact["sourceId"], fact["line"])
                    if proof["path"] != fact["sourcePath"] or fact["quote"] not in proof["text"]:
                        raise ValueError("Цитата изменилась.")
                except (ValueError, OSError):
                    result["issues"].append({"path": relative, "reason":
                        f"Проверьте исходную цитату факта в строке {fact['cardLine']}."})
                    continue
                history.append(people._snapshot(fact, fact["history"][-1]["text"], fact["text"], "observed"))
            if history:
                # Verify that this person's ID is unique before committing the append.
                pending[identifier] = path, raw, history
        except storage.UnmanagedMarkdown:
            continue
        except (ValueError, OSError):
            result["issues"].append({"path": relative, "reason": "Проверьте структуру карточки человека."})

    for identifier, (path, raw, history) in pending.items():
        newline = b"\r\n" if raw.startswith(b"---\r\n") else b"\n"
        separator = b"" if raw.endswith((b"\r", b"\n")) else newline
        appendix = "".join(history).encode().replace(b"\n", newline)
        try:
            storage.replace_bytes(path, raw, raw + separator + appendix)
        except (ValueError, OSError):
            result["issues"].append({"path": path.relative_to(directory.root).as_posix(),
                                     "reason": "Не удалось сохранить историю. Перечитайте карточку человека."})
            continue
        result["changed"] += 1
        result["historyAdded"] += len(history)
        result.setdefault("example", {"id": identifier, "path": path.relative_to(directory.root).as_posix()})
    return result
