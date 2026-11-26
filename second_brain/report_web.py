"""HTTP arguments and the cited Markdown fragment for the reports workspace."""

from html import escape


def _fields(data, required=(), optional=()):
    if set(required) - data.keys() or data.keys() - set(required) - set(optional):
        raise ValueError("Проверьте обязательные и дополнительные поля запроса к отчётам.")


def _page(data):
    limit, offset = int(data.get("limit", 20)), int(data.get("offset", 0))
    if not 1 <= limit <= 20 or offset < 0:
        raise ValueError("Укажите от 1 до 20 записей и неотрицательное смещение.")
    return {"limit": limit, "offset": offset}


def read(root, name, data):
    from . import reports

    if name == "issues":
        _fields(data, optional=("limit", "offset", "seriesId", "match"))
        return reports.list_issues(root, series_id=data.get("seriesId"), match=data.get("match"), **_page(data))
    if name == "issue":
        _fields(data, ("id",))
        return reports.get_issue(root, data["id"])
    if name == "templates":
        _fields(data, optional=("limit", "offset"))
        return reports.list_templates(root, **_page(data))
    if name == "template":
        _fields(data, ("id",), ("version",))
        version = int(data["version"]) if "version" in data else None
        return reports.get_template(root, data["id"], version=version)
    if name == "series" and "id" in data:
        _fields(data, ("id",))
        return reports.get_series(root, data["id"])
    if name == "series":
        _fields(data, optional=("limit", "offset", "dueOn"))
        return reports.list_series(root, due_on=data.get("dueOn"), **_page(data))
    raise FileNotFoundError()


def write(root, name, data):
    from . import reports

    if name == "template":
        _fields(data, ("title", "audience", "guidance", "body"), ("id", "revision", "requestId"))
        return reports.save_template(root, title=data["title"], audience=data["audience"], guidance=data["guidance"],
                                     body=data["body"], id=data.get("id"), revision=data.get("revision"),
                                     request_id=data.get("requestId"))
    if name == "series":
        _fields(data, ("title", "templateId", "cadence", "start"), ("requestId",))
        return reports.create_series(root, title=data["title"], template_id=data["templateId"], cadence=data["cadence"],
                                     start=data["start"], request_id=data.get("requestId"))
    if name == "issue":
        _fields(data, ("title", "templateId", "templateVersion", "body"),
                ("sources", "seriesId", "start", "end", "asOf", "requestId", "templateRevision"))
        return reports.save_issue(root, title=data["title"], template_id=data["templateId"],
                                  template_version=data["templateVersion"], body=data["body"], sources=data.get("sources"),
                                  template_revision=data.get("templateRevision"),
                                  series_id=data.get("seriesId"), start=data.get("start"), end=data.get("end"),
                                  as_of=data.get("asOf"),
                                  request_id=data.get("requestId"))
    raise FileNotFoundError()


def source_html(source):
    content = f'<p class="source-path">{escape(source["path"])}</p>'
    if not source["available"]:
        return (content + '<p>Не удалось подтвердить строку в текущем файле. В выпуске сохранена цитата.</p>'
                + f'<blockquote>{escape(source["quote"])}</blockquote>')
    rows = []
    for row in source["lines"]:
        attributes = ('class="source-line highlight" id="cited-line"' if row["n"] == source["line"]
                      else 'class="source-line"')
        rows.append(f'<div {attributes}><span class="line-number">{row["n"]}</span>'
                    f'<code class="line-text">{escape(row["text"])}</code></div>')
    return (content + f'<p class="source-range">Строка {source["line"]}</p><div class="source-lines">'
            + "".join(rows) + "</div>")
