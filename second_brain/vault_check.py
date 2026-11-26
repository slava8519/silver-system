"""Read-only integrity checks for a vault or one committed Inbox source."""

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re

from . import brief, check_snapshots, inbox, storage
from .check_records import check_records
from .check_sources import check_quotes, check_sources
from .routing import Routing, _rule_parts


class Findings:
    def __init__(self, root):
        self.root = root
        self.items = []
        self.incomplete = False
        self._ids = defaultdict(set)
        self._fact_sources = defaultdict(lambda: defaultdict(list))
        self._destinations = []

    def add(self, code, path, line, message):
        self.items.append({"code": code, "path": path.relative_to(self.root).as_posix(),
                           "line": line, "message": message})

    def unreadable(self, code, path, line, message):
        self.incomplete = True
        self.add(code, path, line, message)

    def record_id(self, kind, identifier):
        self._ids[kind].add(identifier)

    def fact_source(self, path, fact):
        self.record_id("fact", fact["id"])
        source_path = fact["sourcePath"]
        if (not re.fullmatch("[0-9a-f]{64}", fact["sourceId"])
                or source_path not in (f"Sources/Originals/{fact['sourceId']}{ext}" for ext in inbox.EXTENSIONS)):
            self.add("fact_source_path", path, fact["cardLine"], "Проверьте ссылку факта на архивный текст.")
            return
        self._fact_sources[source_path][fact["line"]].append((fact["quote"], path, fact["cardLine"]))

    def destination(self, path, line, kind, identifier):
        self._destinations.append((path, line, kind, identifier))

    def check_evidence(self):
        for path, line, kind, identifier in self._destinations:
            if identifier not in self._ids[kind]:
                self.add("receipt_target_missing", path, line,
                         "Не удалось найти указанную в разборе карточку или факт по сохранённому ID.")
        for relative, citations in self._fact_sources.items():
            try:
                original = storage._inside(self.root, relative)
                if not original.is_file():
                    for entries in citations.values():
                        for _, path, line in entries:
                            self.add("fact_source_missing", path, line, "Отсутствует текстовый оригинал факта.")
                    continue
                check_quotes(original, citations, self, "fact_quote_mismatch")
            except (ValueError, OSError):
                self.unreadable("fact_source_unreadable", self.root / relative, 1,
                                "Не удалось прочитать оригинал для сверки фактов.")

    def check_rules(self):
        """Inspect the same file that routing uses for each live direction."""
        routing = Routing(self.root)
        count = 0
        for direction in self._ids["direction"]:
            path = self.root / "Settings/Intake"
            try:
                path = routing._rule_path(direction)
            except (OSError, ValueError):
                self.unreadable("intake_rule_unreadable", path, 1, "Не удалось открыть путь правила разбора Inbox.")
                continue
            try:
                if not path.exists():
                    continue  # This direction uses the default rule.
                count += 1
                if not path.is_file():
                    raise OSError
                fields, _, lines = _rule_parts(path.read_bytes())
                if fields["direction"] != direction:
                    self.add("intake_rule_direction", path, lines["direction"],
                             "ID направления в правиле расходится с ID его карточки.")
            except OSError:
                self.unreadable("intake_rule_unreadable", path, 1, "Не удалось прочитать правило разбора Inbox.")
            except (ValueError, TypeError):
                self.add("intake_rule_invalid", path, 1, "Проверьте поля, разрешения и текст правила разбора Inbox.")
        return count


def _scan(root, source_id):
    report = Findings(root)
    checked = dict(cards=0, originals=0, receipts=0)
    if source_id is None:
        checked["rules"] = 0
    try:
        with storage._vault_lock(root, write=False):
            folders = ["Sources/Processed", "Sources/Originals"]
            if source_id is None:
                folders.extend(["Inbox", *storage.KINDS.values()])
            for folder in folders:
                try:
                    valid = storage._inside(root, folder).is_dir()
                except (OSError, ValueError):
                    valid = False
                if not valid:
                    report.unreadable("vault_folder", root / folder, 0,
                                      "Для проверки нужна доступная папка хранилища.")
            checked.update(check_sources(root, report, source_id=source_id))
            if source_id is None:
                checked["cards"] = check_records(root, report)
                checked["rules"] = report.check_rules()
                report.check_evidence()
    except (ValueError, OSError):
        report.unreadable("check_interrupted", root, 0, "Не удалось завершить чтение хранилища.")
    status = "incomplete" if report.incomplete else "issues" if report.items else "ok"
    report.items.sort(key=lambda item: (item["path"], item["line"], item["code"], item["message"]))
    return {"status": status, "scope": "source" if source_id is not None else "vault", "checked": checked}, report.items


def check_vault(root, *, limit=brief.DEFAULT_LIMIT, offset=0, snapshot=None):
    if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
        raise ValueError("Укажите размер страницы от 1 до 20 и неотрицательное смещение.")
    root = Path(root).expanduser().resolve()
    if snapshot is not None and snapshot != "new":
        return check_snapshots.read(root, snapshot, limit, offset)
    summary, issues = _scan(root, None)
    if snapshot == "new":
        summary["checkedAt"] = datetime.now(timezone.utc).isoformat()
        try:
            summary = check_snapshots.save(root, summary, issues)
        except (OSError, ValueError):
            summary.update(snapshot=None, snapshotWarning="Не удалось сохранить проверку в локальном кеше. "
                           "Открытие следующей страницы потребует нового прохода.")
    return {**summary, "issues": brief.page(issues, limit, offset)}


def check_after_intake(root, source_id):
    """Check the archived version returned by a successful write or replay."""
    if not isinstance(source_id, str) or not re.fullmatch("[0-9a-f]{64}", source_id):
        raise ValueError("Для проверки разбора нужен ID сохранённого оригинала.")
    summary, issues = _scan(Path(root).expanduser().resolve(), source_id)
    return {**summary, "issues": brief.page(issues, 1)}
