"""Close a plan by publishing one dated, readable Markdown snapshot in the plan."""

from datetime import datetime, timezone
import hashlib
import json
import re

from . import storage


_META = "<!-- brain-cycle: "
_ROW = " <!-- brain-cycle-row: "
_END = "<!-- /brain-cycle -->"
_DATES = ("start", "due", "forecast", "completed")
_TABLE = "| Работа | Тип | Начало | Согласованный срок | Прогноз | Факт завершения | Карточка |"
_DIVIDER = "|---|---|---|---|---|---|---|"
CYCLE_SEAL = "brain_cycle_snapshot"


def _digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def closed_metadata(plan):
    """Older plans have neither a seal in YAML nor a reserved snapshot block."""
    body = plan["body"]
    if CYCLE_SEAL not in plan["field_lines"] and _META not in body and _END not in body:
        return None
    headers = [line for line in body.splitlines() if line.startswith(_META)]
    try:
        if len(headers) != 1 or not headers[0].endswith(" -->"):
            raise ValueError
        meta = json.loads(headers[0][len(_META):-4])
        if (meta["version"] != 1 or meta["id"] != plan["id"] or not isinstance(meta["title"], str)
                or not re.fullmatch("[0-9a-f]{64}", meta["revision"])
                or not re.fullmatch("[0-9a-f]{64}", plan.get(CYCLE_SEAL, ""))):
            raise ValueError
        if datetime.fromisoformat(meta["closedAt"]).tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, KeyError) as error:
        raise storage.ConflictError("Проверьте отметку закрытия в файле плана.") from error
    return meta


def _cells(row):
    title = storage.markdown_label(row.get("title") or row["id"]).replace("|", "&#124;")
    kind = "Нет карточки" if row.get("missing") else ("Веха" if row["kind"] == "milestone" else "Задача")
    return "| " + " | ".join([title, kind, *(row[k] or "—" for k in _DATES)]) + " | "


def frozen_rows(plan, meta):
    """Return original values with their *current* evidence line in the snapshot."""
    rows, numbers = [], []
    try:
        opened = ended = False
        for number, line in enumerate(plan["body"].splitlines(), plan["body_start"]):
            if line.startswith(_META):
                opened = True
            elif line == _END:
                if not opened or ended:
                    raise ValueError
                ended = True
            elif opened and not ended and _ROW in line:
                visible, encoded = line.rsplit(_ROW, 1)
                if not encoded.endswith(" --> |"):
                    raise ValueError
                row = json.loads(encoded[:-6])
                if not visible.startswith(_cells(row)):
                    raise ValueError
                rows.append(row)
                numbers.append(number)
            elif opened and not ended and line.strip() and line not in (_TABLE, _DIVIDER):
                raise ValueError
        if not ended or _digest([meta, rows]) != plan[CYCLE_SEAL]:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise storage.ConflictError("Содержимое снимка изменилось. Проверьте закрытый план в Markdown.") from error
    return [{**row, "snapshotLine": n} for row, n in zip(rows, numbers)]


class Cycles:
    def __init__(self, root):
        from .planning import Planning
        self.planning = Planning(root)

    def _preview(self, entry):
        path, _, plan, _ = entry
        view = self.planning._view(path, plan, validate_dates=True)
        if view["status"] == "closed":
            return {**view, "revision": closed_metadata(plan)["revision"]}
        self.planning._group_titles(view)
        return {**view, "revision": _digest(["close-plan-v1", view])}

    def preview(self, plan_id):
        with storage._vault_lock(self.planning.root, write=False):
            return self._preview(self.planning._find("plan", plan_id))

    def _confirmed(self, entry, revision):
        try:
            preview = self._preview(entry)
        except (ValueError, FileNotFoundError) as error:
            raise storage.ConflictError(f"Повторите предпросмотр закрытия. {error}") from error
        if preview["revision"] != revision:
            raise storage.ConflictError("Итог плана изменился. Повторите предпросмотр закрытия.")
        return preview

    def close(self, plan_id, revision):
        if not isinstance(revision, str) or not re.fullmatch("[0-9a-f]{64}", revision):
            raise ValueError("Укажите версию предварительного итога.")
        with storage._write_lock(self.planning.root):
            entry = self.planning._find("plan", plan_id)
            path, original, plan, header = entry
            preview = self._confirmed(entry, revision)
            if preview["status"] == "closed":
                return {**preview, "replayed": True}

            rows = preview["items"]
            closed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            meta = {"version": 1, "id": plan_id, "title": plan["title"], "revision": revision, "closedAt": closed_at}
            newline = "\r\n" if header.startswith(b"---\r\n") else "\n"
            lines = ["", "## Итог цикла", "", "План — " + storage.markdown_label(plan["title"]), "",
                     "Момент закрытия (UTC) — " + closed_at, "",
                     _META + storage.comment_json(meta) + " -->", "", _TABLE, _DIVIDER]
            for row in rows:
                link = (storage.markdown_link("Открыть", self.planning.root / row["path"], path.parent)
                        if row["path"] else "—")
                encoded = storage.comment_json(row).replace("|", "\\u007c")
                lines.append(_cells(row) + link + _ROW + encoded + " --> |")
            lines.extend([_END, ""])
            body = plan["body"]
            if body and not body.endswith(("\n", "\r")):
                body += newline
            header = storage._patch_header(header, plan, {CYCLE_SEAL: _digest([meta, rows])})
            saved = header + (body + newline.join(lines)).encode()
            record, _ = storage._parse_record(path, saved, kinds=("plan",), optional_scalars=(CYCLE_SEAL,))
            result = self.planning._view(path, record)
            # Native Markdown editors do not take the CLI lock. Recheck the other
            # files once the snapshot is ready; replace_bytes guards the plan itself.
            self._confirmed(entry, revision)
            storage.replace_bytes(path, original, saved)
            return {**result, "revision": revision, "replayed": False}
