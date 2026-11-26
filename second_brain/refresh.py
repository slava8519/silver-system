"""One vault-wide pass over supported summaries and sourced histories."""

import sqlite3

from . import brief, search, storage
from .refresh_people import refresh_people
from .refresh_planning import refresh_planning
from .refresh_projects import refresh_projects
from .workspace import Workspace


def _other_cards(root, issues):
    """Count source cards and historical snapshots that have no generated summary."""
    cards = dict.fromkeys(("direction", "note", "group", "okr", "weeklyReview"), 0)
    for folders, kinds in (
        (("Directions", "Notes"), {"direction": "direction", "note": "note"}),
        (("People",), {"team": "group"}),
        (("Plans/OKR",), {"objective": "okr", "key_result": "okr"}),
        (("Reports",), {"weekly_review": "weeklyReview"}),
    ):
        seen = {}
        for path in storage.iter_markdown_paths(root, folders=folders):
            relative = path.relative_to(root).as_posix()
            try:
                record, _ = storage._parse_record(path, path.read_bytes(), kinds=kinds)
            except storage.UnmanagedMarkdown:
                continue
            except (ValueError, OSError) as error:
                issues.append({"path": relative, "reason": str(error)})
                continue
            identifier = record["id"]
            cards[kinds[record["type"]]] += 1
            if identifier in seen:
                issues.append({"path": relative, "reason": "ID совпадает с " + seen[identifier]})
            else:
                seen[identifier] = relative
    return cards


def refresh_all(root, *, limit=brief.DEFAULT_LIMIT, offset=0):
    root = Workspace(root).root
    issues = []
    with storage._write_lock(root):
        cards = _other_cards(root, issues)
        projects = refresh_projects(root)
        work = refresh_planning(root)
        people = refresh_people(root)
        try:
            rebuilt = search.refresh_index(root)
        except (ValueError, OSError, sqlite3.Error) as error:
            issues.append({"path": ".second-brain/search.sqlite3", "reason": str(error)})
            rebuilt = False

    cards.update(project=projects["examined"], task=work["examined"], plan=work["plans"], person=people["examined"])
    changes = {"projects": projects, "work": work, "people": people}
    for result in changes.values():
        issues.extend(result.pop("issues", []))
    # The inventory and the evidence pass can encounter the same invalid card.
    unique = {(issue["path"], issue["reason"]): issue for issue in issues}
    page = brief.page(unique.values(), limit, offset)
    for issue in page["items"]:
        brief.clip_fields(issue, ("reason",))
    return {
        "cards": cards, "totalCards": sum(cards.values()),
        "changedCards": sum(result["changed"] for result in changes.values()),
        "eventsAdded": projects["eventsAdded"] + work["eventsAdded"],
        "summariesUpdated": projects["summariesUpdated"] + work["summariesUpdated"],
        "personHistoryAdded": people["historyAdded"],
        "details": changes, "issues": page,
        "indexRebuilt": rebuilt,
    }
