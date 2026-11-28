import {openEditorDialog} from "./editor-dialog.js";
import {markdownEditor} from "./markdown-editor.js";
import {dateText} from "./gantt-axis.js";
import {recordDocument} from "./record-document.js";
import {openObsidianSource} from "./obsidian-workspace.js";

export function projectUpdatesPanel(ui, project, afterSave) {
  const {node, button, notice} = ui;
  const panel = node("section", "project-updates");
  panel.setAttribute("aria-label", "Состояние и события проекта");
  const current = project.projectUpdates;
  for (const warning of current.warnings || []) panel.append(notice(warning));
  const brief = node("section", "project-brief");
  const header = node("div", "project-updates-heading");
  header.append(node("h2", "work-heading", "Коротко"));
  if (afterSave) {
    const edit = button("Изменить", "text-link", () => briefEditor(ui, project, edit, afterSave));
    header.append(edit);
  }
  brief.append(header);
  if (!current.brief?.text) brief.append(node("p", "muted", current.warnings?.length
    ? "Сводка недоступна." : "Сводка проекта пока не записана."));
  else {
    brief.append(recordDocument(project, current.brief.text, ui));
    const byline = current.brief.acceptedByOwner ? "ИИ · принято владельцем"
      : current.brief.author === "manual" ? "Формулировка владельца" : "Сводка ИИ";
    brief.append(node("small", "muted", byline));
    appendSources(ui, brief, current.brief);
  }
  panel.append(brief, projectEvents(ui, project));
  return panel;
}

function appendSources({node, link}, parent, entry) {
  for (const source of entry.sources || []) {
    const evidence = node("details", "project-source");
    const title = source.path.split("/").pop();
    const archived = source.path.startsWith("Sources/Originals/");
    const label = archived ? "Оригинал" : title;
    evidence.append(node("summary", "", label + " · строка " + source.line));
    if (source.quote) evidence.append(node("blockquote", "", source.quote));
    if (source.available === false) evidence.append(node("small", "", "Источник изменился или недоступен."));
    else if (/^\/(?:original|source)\//.test(source.href || "")) {
      evidence.append(link(archived ? "Открыть полный оригинал" : "Открыть источник", source.href, "text-link"));
    }
    parent.append(evidence);
  }
}

export function projectEvents(ui, project, includeArchived = false) {
  const {node, button, link, notice, api} = ui;
  const current = project.projectUpdates;
  const history = node("section", "project-history");
  history.append(node("h2", "work-heading", "История событий"));
  const events = node("ol", "project-event-list");
  function appendEvents(rows) {
    for (const row of rows) {
      const li = node("li");
      const stamp = node(row.date ? "time" : "small", "project-event-date", dateText(row.date));
      if (row.date) stamp.dateTime = row.date;
      const location = row.archiveId ? {type:"markdown", path:row.path, vaultName:project.vaultName} : project;
      li.append(stamp, recordDocument(location, row.text, ui));
      appendSources(ui, li, row);
      if (row.archiveId) {
        const label = "Архив · строка " + row.line;
        li.append(ui.compact ? button(label, "text-link", () => openObsidianSource(row.path, row.line)) :
          link(label, "#record?" + new URLSearchParams({path:row.path, line:row.line}), "text-link"));
      }
      events.append(li);
    }
  }
  appendEvents(current.events);
  history.append(events);
  if (!current.total) history.append(node("p", "muted", current.warnings?.length
    ? "Нет доступных записей." : "Событий пока нет."));
  const messages = node("div");
  if (events.children.length < current.total) {
    const more = button("Показать ещё", "text-link", async () => {
      more.disabled = true;
      messages.replaceChildren();
      try {
        const params = new URLSearchParams({id:project.id, offset:events.children.length, limit:5});
        if (!includeArchived) params.set("includeArchived", "0");
        const page = await api("project-updates?" + params);
        if (!more.isConnected) return;
        if (page.revision !== project.revision) {
          messages.append(notice("Карточка изменилась. Обновите страницу, чтобы открыть актуальную историю."));
          return;
        }
        appendEvents(page.events);
        more.hidden = events.children.length >= page.total;
      } catch (error) {
        messages.append(notice(error.message));
      } finally {
        more.disabled = false;
      }
    });
    history.append(more, messages);
  }
  if (current.archives?.length) {
    const archives = node("section", "project-archives");
    archives.append(node("h3", "", "Архив по кварталам"));
    const list = node("ul");
    for (const archive of current.archives) {
      const item = node("li");
      const open = ui.compact ? button(archive.quarter, "text-link", () => openObsidianSource(archive.path)) :
        link(archive.quarter, "#record?" + new URLSearchParams({path:archive.path}), "text-link");
      open.title = archive.path;
      item.append(open);
      list.append(item);
    }
    archives.append(list);
    history.append(archives);
  }
  return history;
}

export function projectArchiveControl({node, button, notice, api, counted}, project, refresh) {
  const control = node("section", "project-archive-control");
  const result = node("div", "project-archive-preview");
  result.setAttribute("aria-live", "polite");
  const endpoint = "project-history/archive";
  const preview = button("Архивировать историю", "button button-secondary", async () => {
    preview.disabled = true;
    result.replaceChildren();
    try {
      const plan = await api(endpoint + "?" + new URLSearchParams({id:project.id}));
      if (!control.isConnected) return;
      result.append(node("p", "", "К переносу в архив — " + counted(plan.moved, ["запись", "записи", "записей"]) + "."));
      if (plan.skippedLinks) {
        result.append(node("p", "", "Останутся в карточке из-за ссылок или якорей — " +
          counted(plan.skippedLinks, ["запись", "записи", "записей"]) + "."));
      }
      if (plan.archives.length) {
        const list = node("ul");
        for (const archive of plan.archives) {
          list.append(node("li", "", archive.quarter + " · " + counted(archive.entries, ["запись", "записи", "записей"])));
        }
        result.append(list);
      }
      const apply = button("Подтвердить перенос", "button button-primary", async () => {
        apply.disabled = cancel.disabled = true;
        try {
          const saved = await api(endpoint, {id:project.id, revision:plan.revision, asOf:plan.asOf});
          if (!control.isConnected) return;
          if (!saved.applied) throw new Error("Проверьте актуальный предпросмотр.");
          await refresh();
        } catch (error) {
          if (!control.isConnected) return;
          result.replaceChildren(notice(error.message));
          preview.disabled = false;
          preview.focus();
        }
      });
      apply.disabled = !plan.moved;
      const cancel = button("Отмена", "text-link", () => {
        result.replaceChildren();
        preview.disabled = false;
        preview.focus();
      });
      result.append(apply, cancel);
      (apply.disabled ? cancel : apply).focus();
    } catch (error) {
      result.append(notice(error.message));
      preview.disabled = false;
    }
  });
  control.append(preview, result);
  return control;
}

function briefEditor(ui, project, trigger, afterSave) {
  const {node, button, api, notice} = ui;
  const form = node("form", "project-brief-editor");
  const input = node("textarea");
  input.maxLength = 2400;
  input.required = true;
  input.value = project.projectUpdates.brief?.text || "";
  const messages = node("div");
  const save = node("button", "button button-primary", "Сохранить");
  save.type = "submit";
  const actions = node("div", "work-tools");
  actions.append(save, button("Отмена", "button button-secondary", () => dialog.requestClose()));
  form.append(markdownEditor(ui, input, {label:"Коротко о проекте · одна фраза до 2400 символов", render:value => recordDocument(project, value, ui)}),
    actions, messages);
  const dialog = openEditorDialog({title:"Редактировать сводку проекта", context:project.title, trigger,
    content:form, initialFocus:input, isBusy:() => save.disabled});
  let revision = project.revision;
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (save.disabled) return;
    form.inert = save.disabled = true;
    messages.replaceChildren();
    try {
      await api("project-brief", {id:project.id, revision, text:input.value.trim()});
      if (form.isConnected) {
        dialog.close();
        await afterSave();
      }
    } catch (error) {
      if (!form.isConnected) return;
      messages.append(notice(error.message));
      if (error.status === 409) {
        try {
          const fresh = await api("project-updates?" + new URLSearchParams({id:project.id, limit:1}));
          messages.append(node("p", "", "Текущая сводка: " + (fresh.brief?.text || "пока не записана")));
          revision = fresh.revision;
          save.textContent = "Сохранить после сверки";
        } catch (readError) { messages.append(notice(readError.message)); }
      }
    } finally { form.inert = save.disabled = false; }
  });
}
