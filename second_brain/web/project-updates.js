import {openEditorDialog} from "./editor-dialog.js";
import {markdownEditor} from "./markdown-editor.js";
import {dateText} from "./gantt-axis.js";
import {recordDocument} from "./record-document.js";

export function projectUpdatesPanel(ui, project, afterSave) {
  const {node, button, link, notice, api} = ui;
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
    appendSources(brief, current.brief);
  }

  function appendSources(parent, entry) {
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

  const history = node("section", "project-history");
  history.append(node("h2", "work-heading", "История событий"));
  const events = node("ol", "project-event-list");
  function appendEvents(rows) {
    for (const row of rows) {
      const li = node("li");
      const stamp = node(row.date ? "time" : "small", "project-event-date", dateText(row.date));
      if (row.date) stamp.dateTime = row.date;
      li.append(stamp, recordDocument(project, row.text, ui));
      appendSources(li, row);
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
  panel.append(brief, history);
  return panel;
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
