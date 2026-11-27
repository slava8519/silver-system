import {openObsidianSource} from "./obsidian-workspace.js";
import {resultURL} from "./gantt-okr-values.js";
import {recordedSpan} from "./gantt-rows.js";
import {workEvents} from "./gantt-work-events.js";
import {progressText, dateLabels, dateText, dayOf, isoDay, todayDay} from "./gantt-axis.js";
import {dependencySection, scenarioForm} from "./gantt-dependencies.js";
import {inspectorText, readinessText} from "./gantt-detail-text.js";

function authorText(entry) {
  if (!entry) return "";
  if (entry.acceptedByOwner) return "ИИ · принято владельцем";
  if (entry.author === "ai") return "ИИ";
  if (entry.manual || entry.author === "manual") return "Вручную";
  return "";
}

export function cardLinks(ui, root) {
  return function markdownLink(name, path, source, line, subpath) {
    const href = "obsidian://open?path=" + encodeURIComponent(root + "/" + path + (subpath || ""));
    const a = ui.link(name, href, "text-link");
    if (ui.embedded && window.parent !== window) a.addEventListener("click", async event => {
      event.preventDefault();
      try {
        const ref = source ? await ui.api("citation", {id: source.id, line: source.line, ref: source.ref}) : {path, start: line};
        openObsidianSource(ref.path, ref.start, subpath);
      } catch (error) { a.after(ui.notice(error.message)); }
    });
    return a;
  };
}

export function evidenceLink(ui, root, source, label) {
  if (source.unavailable) return null;
  let href;
  if (source.path.startsWith("Sources/Originals/")) {
    const match = /^Sources\/Originals\/([a-f0-9]{64})\.(?:md|txt)$/.exec(source.path);
    if (!match || source.id !== match[1] || !Number.isSafeInteger(source.line) || source.line < 1) return null;
    href = `/original/${source.id}?start=${source.line}`;
    label = "Открыть оригинал";
  } else if (ui.embedded) {
    return cardLinks(ui, root)(label || "Открыть строку в Obsidian ↗", source.path, source);
  } else {
    if (!source.href) return null;
    let url;
    try { url = new URL(source.href, location.href); } catch { return null; }
    if (url.origin !== location.origin || !url.pathname.startsWith("/source/")) return null;
    href = url.href;
  }
  const a = ui.link(label || "Проверить источник ↗", href, "text-link");
  // The Obsidian iframe can navigate to the verified original but cannot open a popup.
  if (!ui.embedded) { a.target = "_blank"; a.rel = "noopener"; }
  return a;
}

export function rowInspector(ui, settings) {
  const {node, button, labelled, link, api, notice} = ui;
  const {task, row, root, refresh, deselect} = settings;
  const group = settings.group;
  const readOnly = !!task.readOnly;
  const milestone = row?.kind === "milestone";
  const journal = settings.journal || {
    current: () => api("plan/task?id=" + encodeURIComponent(task.id)),
    events: offset => api("plan/events?" + new URLSearchParams({id:task.id, offset})),
    edit: payload => api("plan/task/edit", payload)
  };
  const panel = node("section", "gantt-inspector");
  panel.setAttribute("aria-label", group?.label || (milestone ? "Выбранная веха" : "Выбранная задача"));
  const identity = node("div", "gantt-pane gantt-identity");
  const heading = node("div", "gantt-pane-heading");
  const title = node("h2", "", task.title);
  title.tabIndex = -1;
  const close = button("×", "gantt-close", deselect);
  close.setAttribute("aria-label", "Снять выделение");
  heading.append(title, close);
  const sectionHeader = node("div", "gantt-inspector-head");
  sectionHeader.append(heading);
  identity.append(sectionHeader);
  const facts = node("div", "gantt-identity-body");
  identity.append(facts);
  const scope = [row?.directionTitle, row?.projectTitle].filter(Boolean).join(" / ");
  if (milestone || scope) facts.append(node("p", "gantt-context", [milestone ? "Веха" : "", scope].filter(Boolean).join(" · ")));
  const markdownLink = cardLinks(ui, root);
  const prose = (text, style, journalEntry) => inspectorText(ui, style, text, task.textPath || task.path, markdownLink, journalEntry);
  const cardActions = node("div", "work-tools");
  if (task.path && task.stored !== false) cardActions.append(markdownLink(readOnly ? "Открыть снимок в Obsidian ↗" : "Открыть карточку в Obsidian ↗", task.path));
  if (!ui.compact && !readOnly && group?.kind !== "group") cardActions.append(link("Показать связи", "#map?" + new URLSearchParams({id:task.id}), "text-link"));

  function byline(entry) {
    const parts = [authorText(entry)];
    if (entry.updated) {
      const stamp = new Date(entry.updated);
      if (!Number.isNaN(stamp.valueOf())) parts.push(stamp.toLocaleDateString("ru-RU", {day: "numeric", month: "short", year: "numeric"}));
    }
    return parts.filter(Boolean).join(" · ");
  }

  function evidence(entry) {
    const sources = node("div", "gantt-evidence");
    for (const source of entry.sources || []) {
      const details = node("details");
      const name = source.path.startsWith("Sources/Originals/") ? "Оригинал" : source.path.split("/").pop().replace(/\.md$/i, "");
      const summary = node("summary", "", name + " · строка " + source.line);
      summary.title = source.path;
      details.append(summary);
      if (source.quote) details.append(node("blockquote", "", source.quote));
      if (source.unavailable) details.append(node("p", "gantt-byline", "Исходная строка недоступна."));
      const a = evidenceLink(ui, root, source);
      if (a) details.append(a);
      sources.append(details);
    }
    return sources;
  }

  let editDate;
  if (group) {
    facts.append(node("p", "gantt-context", readOnly ? group.label + " · снимок плана" : group.label));
    const measures = node("dl", "gantt-facts");
    if (group.progress?.value != null) {
      const value = (group.progress.hasEstimates ? "≈" : "") + progressText(group.progress.value);
      measures.append(node("dt", "", "Готовность работ"), node("dd", "", value));
    }
    measures.append(node("dt", "", "Работ в составе"), node("dd", "", String(group.rows.length)));
    const span = recordedSpan(group.rows);
    if (span) measures.append(node("dt", "", "Даты работ"), node("dd", "", dateText(span.start) + " — " + dateText(span.end)));
    if (group.kind === "key_result") cardActions.append(link("Критерии и замеры", resultURL(group.id, settings.plan.id), "text-link"));
    facts.append(measures, cardActions);
    if (group.rows.length) {
      const members = node("details", "gantt-members");
      members.append(node("summary", "", "Работы в составе"));
      members.addEventListener("toggle", () => {
        if (!members.open || members.querySelector("ul")) return;
        const list = node("ul");
        for (const item of group.rows) {
          const line = node("li");
          if (item.missing && !readOnly) line.append(node("span", "gantt-context", item.title || "Карточка недоступна"));
          else line.append(button(item.title || item.id, "gantt-action", () => settings.selectMember(item.id)));
          list.append(line);
        }
        members.append(list);
      });
      facts.append(members);
    }
  } else appendTaskFacts();

  function appendTaskFacts() {
    const dates = node("dl", "gantt-facts");
    const state = task.readiness;
    const readiness = node("dd", "", readinessText(state));
    if (state?.reason || state?.source) {
      const basis = node("details", "gantt-schedule-history");
      basis.append(node("summary", "", "Основание готовности"));
      if (state.reason) basis.append(prose(state.reason, "gantt-event-text"));
      if (state.source) basis.append(evidence({sources: [state.source]}));
      readiness.append(basis);
    }
    dates.append(node("dt", "", "Готовность"), readiness);
    const detailLabels = task.baseline
      ? {start: dateLabels.start, baseline: "Базовая дата плана", ...dateLabels}
      : dateLabels;
    for (const [field, label] of Object.entries(detailLabels)) {
      if (field === "start" && milestone) continue;
      const value = node("dd", task[field] ? "" : "gantt-missing-date", task[field] ? dateText(task[field]) : "Нет даты");
      const day = dayOf(task[field]), due = dayOf(task.due);
      if (day !== null && due !== null && (field === "forecast" || field === "completed")) {
        const delta = day - due;
        const difference = !delta ? "Совпадает со сроком" : (delta > 0 ? "Позже на " : "Раньше на ") + Math.abs(delta) + " дн.";
        value.append(node("small", "gantt-difference", difference));
      }
      const baseline = dayOf(task.baseline);
      if (field === "forecast" && day !== null && baseline !== null) {
        const days = day - baseline;
        const offset = days > 0 ? "+" + days : String(days);
        value.append(node("small", "gantt-difference", "От базовой даты · " + offset + " дн."));
      }
      dates.append(node("dt", "", label), value);
    }
    editDate = button("Изменить даты", "gantt-action", () => dateForm());
    const dateActions = node("div", "gantt-date-actions");
    const scenario = button("Что если…", "gantt-action", () => {
      const form = openForm("Что изменится при переносе прогноза?");
      if (form) scenarioForm(ui, {task, form, actions, trigger: scenario, markdownLink, refresh});
    });
    dateActions.append(editDate);
    if (!task.completed) dateActions.append(scenario);
    facts.append(dates, dateActions, cardActions);
    const recorded = ["baseline", "due", "forecast", "completed"].filter(field => task[field] && task[field + "Line"]);
    if (recorded.length || task.scheduleHistory?.length) {
      const basis = node("details", "gantt-schedule-history");
      basis.append(node("summary", "", "Основания дат"));
      for (const field of recorded) {
        basis.append(node("p", "", detailLabels[field] + " · " + task.path + " · строка " + task[field + "Line"]));
      }
      if (task.scheduleHistory?.length) {
        const shown = task.scheduleHistory.length;
        const title = shown < task.scheduleHistoryTotal ? "Последние изменения · " + shown + " из " + task.scheduleHistoryTotal : "История изменений";
        basis.append(node("strong", "", title));
      }
      for (const change of task.scheduleHistory || []) {
        const article = node("article");
        article.append(node("strong", "", dateLabels[change.field] + " · " + dateText(change.before) + " → " + dateText(change.after)));
        const author = byline({author: change.actor, updated: change.updated});
        if (author) article.append(node("p", "gantt-byline", author));
        if (change.reason) article.append(node("p", "", change.reason));
        article.append(evidence(change));
        basis.append(article);
      }
      facts.append(basis);
    }
  }
  const brief = node("div", "gantt-pane gantt-brief");
  const briefHead = node("div", "gantt-pane-heading");
  const briefTitle = node("div", "gantt-section-title");
  briefTitle.append(node("h3", "", "Коротко"));
  const attribution = task.brief?.text && byline(task.brief);
  if (attribution) briefTitle.append(node("span", "gantt-byline", attribution));
  briefHead.append(briefTitle);
  if (!readOnly) briefHead.append(button("Изменить", "gantt-action", () => textForm("brief", task.brief)));
  brief.append(briefHead);
  if (task.brief?.text) {
    brief.append(prose(task.brief.text, "gantt-summary-text"), evidence(task.brief));
  } else brief.append(node("p", "gantt-empty", readOnly ? "В снимке нет сводки этой строки." : "Краткая сводка пока не записана."));

  const latest = node("div", "gantt-pane gantt-history");
  const latestHead = node("div", "gantt-pane-heading");
  latestHead.append(node("h3", "", "История"));
  if (!readOnly) latestHead.append(button("Добавить", "gantt-action", () => textForm("latest", null)));
  latest.append(latestHead);
  const events = node("div", "gantt-events");
  latest.append(events);
  function eventCard(item) {
    const article = node("article", "gantt-event");
    const meta = node("div", "gantt-event-head");
    const operation = /^(сводка|прогноз|готовность|заметка|старт|план|риск(?: снят)?|зависимость) · /i.exec(item.text);
    if (operation) meta.append(node("strong", "gantt-event-kind", operation[1]));
    const date = node(item.date ? "time" : "span", "gantt-event-date", dateText(item.date));
    if (item.date) date.dateTime = item.date;
    meta.append(date);
    const author = authorText(item);
    if (author) meta.append(node("span", "gantt-byline", author));
    if (!readOnly) meta.append(button("Исправить", "gantt-action", () => textForm("latest", item)));
    article.append(meta, prose(item.text.slice(operation?.[0].length || 0), "gantt-event-text", !!operation), evidence(item));
    return article;
  }
  events.append(...task.latest.map(eventCard));
  let shown = task.latest.length;
  if (shown < task.latestTotal) {
    const output = node("div", "gantt-page-feedback");
    const more = button("Предыдущие события", "gantt-action", async () => {
      more.disabled = true; output.replaceChildren();
      try {
        const page = await journal.events(shown);
        if (!more.isConnected) return;
        if (page.revision !== task.revision) throw Error("История изменилась. Нажмите «Обновить», чтобы перечитать строку.");
        const cards = page.items.map(eventCard);
        events.append(...cards);
        shown += cards.length;
        if (!page.truncated) more.hidden = true;
        if (cards.length) {
          cards[0].tabIndex = -1; cards[0].focus({preventScroll: true});
          cards[0].scrollIntoView({block: "nearest"});
        }
      } catch (error) { output.replaceChildren(notice(error.message)); }
      finally { more.disabled = false; }
    });
    latest.append(more, output);
  }
  if (!task.latest.length) latest.append(node("p", "gantt-empty", group ? (readOnly ? "В снимке нет событий этой строки." : "Событий по этой строке пока нет.") : "Здесь появятся события по задаче."));
  if (group?.rows.length && !readOnly && journal.workEvents) {
    latest.append(workEvents(ui, {load:journal.workEvents, open:task.latestTotal === 0,
      selectMember:settings.selectMember, evidence, markdownLink, authorText}));
  }
  const editor = node("div", "gantt-editor-place");

  const sections = node("div", "gantt-inspector-switch");
  sections.setAttribute("role", "group");
  sections.setAttribute("aria-label", "Раздел выбранной работы");
  const mobilePanes = [["work", group?.label || "Работа", facts], ["brief", "Коротко", brief], ["history", "История", latest]];
  function selectPane(name) {
    panel.setAttribute("data-pane", name);
    for (const control of sections.children) control.setAttribute("aria-pressed", String(control.value === name));
    if (sections.offsetWidth) panel.closest(".gantt-detail-place")?.scrollTo(0, 0);
  }
  for (const [name, label, pane] of mobilePanes) {
    pane.id = "gantt-" + task.id + "-" + name;
    const control = button(label, "gantt-inspector-tab", () => selectPane(name));
    control.value = name;
    control.setAttribute("aria-controls", pane.id);
    sections.append(control);
  }
  selectPane("brief");
  sectionHeader.append(sections);
  panel.append(identity, brief, latest);
  if (!group) panel.append(dependencySection(ui, {...settings, markdownLink, openForm, actions}));
  panel.append(editor);

  function canLeave() {
    if (!editor.firstChild) return true;
    const feedback = editor.querySelector(".gantt-form-feedback");
    feedback.textContent = "Сохраните правку или нажмите «Отмена».";
    editor.querySelector(".work-tools button")?.focus();
    return false;
  }
  function openForm(title) {
    if (!canLeave()) return null;
    const form = node("form", "gantt-form");
    form.append(node("h3", "", title));
    editor.replaceChildren(form);
    return form;
  }
  function actions(form, label, cancelFocus) {
    const commands = node("div", "work-tools");
    const submit = node("button", "button button-primary", label);
    submit.type = "submit";
    commands.append(submit, button("Отмена", "button button-secondary", () => {
      editor.replaceChildren();
      cancelFocus.focus();
    }));
    const feedback = node("div", "gantt-form-feedback");
    feedback.setAttribute("aria-live", "polite");
    form.append(commands, feedback);
    return {submit, feedback};
  }
  function textForm(field, entry) {
    const trigger = document.activeElement;
    const form = openForm(field === "brief" ? "Редактировать сводку" : entry ? "Исправить событие" : "Новое событие");
    if (!form) return;
    const text = node("textarea");
    text.name = "text"; text.rows = 3; text.value = entry?.text || "";
    text.required = field === "latest";
    form.append(labelled(field === "brief" ? "Коротко" : "Текст события", text));
    const date = node("input");
    if (field === "latest") {
      date.type = "date"; date.name = "date"; date.value = entry?.date || "";
      form.append(labelled("Дата события, если известна", date));
    }
    const references = node("details", "gantt-edit-sources");
    const paths = node("textarea");
    paths.name = "sources"; paths.rows = 2;
    paths.value = (entry?.sources || []).map(s => s.path + ":" + s.line).join("\n");
    const initialPaths = paths.value;
    references.append(node("summary", "", "Источники"), labelled("Путь.md:строка, один источник на строку", paths));
    form.append(references);
    const {submit, feedback} = actions(form, "Сохранить", trigger);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      submit.disabled = true;
      feedback.replaceChildren();
      try {
        const payload = {id: task.id, revision: task.revision, field, text: text.value};
        if (field === "latest") {
          if (entry) payload.entryId = entry.id;
          payload.date = date.value || null;
        }
        if (paths.value !== initialPaths) payload.sources = paths.value.split("\n").filter(v => v.trim()).map(v => {
          const parts = v.trim().match(/^(.+):(\d+)$/);
          if (!parts || +parts[2] < 1) throw Error("Укажите путь и номер строки для каждого источника.");
          return {path: parts[1], line: Number(parts[2])};
        });
        await journal.edit(payload);
        await refresh("Изменения сохранены.");
      } catch (error) {
        await displayError(error, feedback);
      } finally { submit.disabled = false; }
    });
    text.focus();
  }
  async function displayError(error, feedback) {
    feedback.replaceChildren(notice(error.message));
    if (error.status !== 409) return;
    const current = await journal.current().catch(() => null);
    if (!current || !feedback.isConnected) return;
    const info = node("details", "gantt-conflict");
    info.open = true;
    info.append(node("summary", "", "Текущая карточка"));
    if (!group) info.append(node("p", "", Object.entries(dateLabels).map(([field, label]) => label + " · " + dateText(current[field])).join(" / ")));
    info.append(prose(current.brief?.text || "Сводка пока не записана.", "gantt-summary-text"));
    for (const entry of current.latest) info.append(prose(dateText(entry.date) + " · " + entry.text, "gantt-event-text"));
    feedback.append(info, button("Загрузить текущую карточку", "button button-secondary", () => refresh("Карточка перечитана.")));
  }

  function dateForm(dragged) {
    if (dragged) selectPane("work");
    const form = openForm(milestone ? "Даты вехи" : "Даты задачи");
    if (!form) return;
    const field = node("select");
    for (const key of milestone ? ["due", "forecast", "completed"] : ["due", "forecast", "completed", "start"]) field.append(ui.option(key, dateLabels[key]));
    if (dragged) field.value = dragged.field;
    const date = node("input"); date.type = "date"; date.name = "value";
    const reason = node("input"); reason.type = "text"; reason.maxLength = 600; reason.name = "reason";
    const reasonBox = labelled("Основание", reason);
    const preview = node("div", "gantt-date-preview");
    form.append(labelled("Поле карточки", field), labelled("Дата", date), reasonBox, preview);
    const {submit, feedback} = actions(form, "", editDate);
    let proposal = null;
    function invalidate() {
      proposal = null;
      preview.replaceChildren();
      feedback.replaceChildren();
      const labels = {start: "Сохранить начало", due: "Проверить перенос", forecast: "Проверить прогноз", completed: "Проверить факт"};
      submit.textContent = labels[field.value];
    }
    function fill() {
      date.value = task[field.value] || "";
      date.min = field.value === "due" ? task.start || "" : "";
      date.max = field.value === "start" ? task.due || "" : "";
      if (field.value === "completed") date.max = isoDay(todayDay());
      reason.value = "";
      reason.required = field.value === "completed";
      reasonBox.hidden = !["forecast", "completed"].includes(field.value);
      reasonBox.querySelector("span").textContent = reason.required ? "Основание изменения факта завершения" : "Основание прогноза (необязательно)";
      invalidate();
    }
    fill();
    if (dragged) date.value = dragged.value;
    field.addEventListener("change", fill);
    date.addEventListener("input", invalidate);
    reason.addEventListener("input", invalidate);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (submit.disabled) return;
      submit.disabled = field.disabled = date.disabled = reason.disabled = true;
      try {
        if (field.value === "start") {
          await api("plan/task/edit", {id: task.id, revision: task.revision, field: "start", value: date.value || null});
          await refresh("Дата начала сохранена.");
          return;
        }
        const agreed = field.value === "due";
        const route = agreed ? "plan/due/" : "plan/schedule/";
        const payload = agreed ? {id: task.id, due: date.value || null} :
          {id: task.id, field: field.value, value: date.value || null, reason: reason.value.trim()};
        if (proposal) {
          await api(route + "apply", {...payload, revision: proposal.revision});
          await refresh(dateLabels[field.value] + " · изменение сохранено.");
          return;
        }
        const result = await api(route + "preview", payload);
        if (!result.changed) { feedback.textContent = "Это значение уже записано."; return; }
        proposal = result;
        preview.replaceChildren(node("strong", "", dateLabels[field.value] + " · " + dateText(result.before) + " → " + dateText(result.after)));
        if (!agreed) preview.append(node("p", "", "Согласованный срок · " + dateText(result.due)));
        if (result.plans.length) preview.append(node("p", "", "Изменение увидят планы «" + result.plans.map(p => p.title).join("», «") + "»."));
        const confirm = {due: "Подтвердить перенос", forecast: "Сохранить прогноз", completed: "Подтвердить факт"};
        submit.textContent = confirm[field.value];
      } catch (error) {
        invalidate();
        await displayError(error, feedback);
      } finally { submit.disabled = field.disabled = date.disabled = reason.disabled = false; }
    });
    date.focus();
  }
  return {element: panel, editing: () => !!editor.firstChild, canLeave, dateForm};
}

export {rowInspector as taskInspector};
