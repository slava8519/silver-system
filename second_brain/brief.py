"""Small CLI views over the same records and citations used by the browser."""

import argparse

from . import storage


DEFAULT_LIMIT = 5
EXCERPT_LENGTH = 600


def result_limit(value):
    size = int(value)
    if not 1 <= size <= 20:
        raise argparse.ArgumentTypeError("Число результатов должно быть от 1 до 20.")
    return size


def offset(value):
    skip = int(value)
    if skip < 0:
        raise argparse.ArgumentTypeError("Смещение должно быть неотрицательным.")
    return skip


def page(rows, limit, skip=0):
    items = []
    total = 0
    for row in rows:
        if total >= skip and len(items) < limit:
            items.append(row)
        total += 1
    return {"items": items, "total": total, "truncated": total > skip + len(items)}


def answer(result, limit):
    items = []
    for item in result["items"][:limit]:
        citation = item["source"]
        excerpt = item["answer"]
        entry = {"id": item["id"], "label": item["label"], "answer": excerpt[:EXCERPT_LENGTH],
                 "source": {key: citation[key] for key in ("path", "start", "end")}}
        if "evidence" in citation:
            entry["source"]["evidence"] = citation["evidence"]
        if "origin" in citation:
            entry["source"]["origin"] = origin_location(citation["origin"])
        for key in ("kind", "date"):
            if key in item:
                entry[key] = item[key]
        if len(excerpt) > EXCERPT_LENGTH:
            entry["answerTruncated"] = True
        clip_fields(entry, ("label",))
        items.append(entry)
    total = result.get("total", len(result["items"]))
    response = {"items": items, "total": total, "truncated": total > len(items), "message": result["message"]}
    clip_fields(response, ("message",))
    return response


def clip_fields(item, fields):
    for field in fields:
        value = item[field]
        if isinstance(value, str) and len(value) > EXCERPT_LENGTH:
            item[field] = value[:EXCERPT_LENGTH]
            item.setdefault("truncatedFields", []).append(field)


def named_card(card):
    result = dict(card)
    clip_fields(result, ("name",))
    return result


def owner_context(context):
    result = dict(context)
    for key in ("owner", "team"):
        if result[key] is not None:
            result[key] = named_card(result[key])
    if "members" in result:
        result["members"] = [named_card(member) for member in result["members"]]
    return result


def overview(full, direction_id):
    counts = dict.fromkeys(storage.KINDS, 0)
    result = {"name": full["name"], "counts": counts}
    if direction_id is None and full["peopleCount"]:
        result["peopleCount"] = full["peopleCount"]
    for record in full["records"]:
        if direction_id is not None:
            if record["id"] == direction_id and record["type"] == "direction":
                result["direction"] = {key: record[key] for key in ("id", "title", "path")}
                clip_fields(result["direction"], ("title",))
            elif record["direction"] != direction_id:
                continue
        counts[record["type"]] += 1
    if direction_id is not None and "direction" not in result:
        raise ValueError("Для --direction требуется ID существующего направления.")
    return result


def record(detail):
    result = {key: detail[key] for key in ("id", "type", "title", "direction", "path", "revision",
                                            "status", "due", "nextStep")}
    if detail["type"] == "project":
        result["projectStart"] = detail["projectStart"]
        updates = detail["projectUpdates"]
        result["projectUpdates"] = {
            "brief": project_entry(updates["brief"]) if updates["brief"] else None,
            "events": [project_entry(event) for event in updates["events"]],
            "total": updates["total"],
        }
        if updates.get("warnings"):
            result["projectUpdates"]["warnings"] = updates["warnings"]
    clip_fields(result, (key for key in ("title", "status", "projectStart", "due", "nextStep") if key in result))
    aliases = detail["aliases"]
    result["aliases"] = [name[:EXCERPT_LENGTH] for name in aliases[:DEFAULT_LIMIT]]
    if result["aliases"] != aliases:
        result.setdefault("truncatedFields", []).append("aliases")
    result["fieldSources"] = {key: detail["fieldSources"][key]
                              for key in ("title", "aliases", "direction", "status", "projectStart", "due", "nextStep")
                              if key in detail["fieldSources"]}
    return result


def project_entry(entry):
    result = {key: entry[key] for key in ("text", "date", "line", "author") if entry.get(key) is not None}
    if entry["end"] != entry["line"]:
        result["end"] = entry["end"]
    clip_fields(result, ("text",))
    if entry.get("sources"):
        origin = next((s for s in entry["sources"] if s["available"]), entry["sources"][0])
        result["origin"] = origin_location(origin)
    return result


def origin_location(source):
    result = {key: source[key] for key in ("path", "line")}
    if source.get("available") is False:
        result["available"] = False
    return result
