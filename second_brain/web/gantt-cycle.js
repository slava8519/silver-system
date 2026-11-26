import {dateLabels, dateText, dayOf} from "./gantt-axis.js";
import {cardLinks} from "./gantt-detail.js";
import {readinessText} from "./gantt-detail-text.js";

const stamp = new Intl.DateTimeFormat("ru-RU", {dateStyle: "long", timeStyle: "short"});

export function closedStamp(value) {
  return stamp.format(new Date(value));
}

export function closedDay(value) {
  const at = new Date(value);
  return dayOf([String(at.getFullYear()).padStart(4, "0"), String(at.getMonth() + 1).padStart(2, "0"),
    String(at.getDate()).padStart(2, "0")].join("-"));
}

// A historical import can have a known export day and no recorded day of actual closure.
export function archiveStamp(plan) {
  if (!plan.archiveDates) return null;
  const {closedOn, exportedOn} = plan.archiveDates;
  if (closedOn) return {text: "Архивный цикл закрыт · " + dateText(closedOn), date: closedOn, label: "Закрытие"};
  if (exportedOn) return {text: "Архивный снимок · выгрузка " + dateText(exportedOn), date: exportedOn, label: "Выгрузка"};
  return {text: "Архивный снимок", date: null, label: "Фиксация снимка"};
}

export function cyclePreview(ui, planId, root, cancel, showClosed) {
  const {node, button, counted, api, notice} = ui;
  const markdownLink = cardLinks(ui, root);
  const panel = node("section", "cycle-preview");
  const title = node("h2", "", "Итог перед закрытием");
  title.tabIndex = -1;
  const content = node("div");
  const actions = node("div", "work-tools");
  const feedback = node("div");
  feedback.setAttribute("role", "status");
  const confirm = button("Закрыть цикл и сохранить итог", "button button-primary", save);
  const dismiss = button("Отмена", "button button-secondary", cancel);
  actions.append(confirm, dismiss);
  panel.append(title, content, actions, feedback);
  let revision = null;

  async function load() {
    confirm.disabled = true;
    revision = null;
    content.replaceChildren();
    feedback.textContent = "Читаем работы плана…";
    try {
      const proposal = await api("plan/close/preview", {id: planId});
      if (!panel.isConnected) return;
      if (proposal.status === "closed") { showClosed(); return; }
      const caption = node("p", "gantt-context", "В снимок войдут состав плана и даты на момент закрытия.");
      if (proposal.okrCounts) {
        const {objectives, results} = proposal.okrCounts;
        caption.append(" Вместе с работами сохранятся " + counted(objectives, ["цель", "цели", "целей"]) +
          " и " + counted(results, ["ключевой результат", "ключевых результата", "ключевых результатов"]) +
          " с последними замерами.");
      }
      const table = node("table", "cycle-preview-table");
      const head = table.createTHead().insertRow();
      for (const label of ["Работа", ...Object.values(dateLabels)]) {
        const th = node("th", "", label); th.scope = "col"; head.append(th);
      }
      const body = table.createTBody();
      for (const row of proposal.items) {
        const tr = body.insertRow();
        const name = node("th"); name.scope = "row";
        if (row.path) {
          const source = markdownLink(row.title || row.id, row.path);
          source.title = "Открыть в Obsidian · " + row.path; name.append(source);
        } else name.textContent = row.title || row.id;
        if (row.missing) name.append(node("small", "", "Карточка недоступна"));
        if (row.kind === "milestone") name.prepend(node("span", "gantt-milestone-label", "Веха"), " ");
        tr.append(name);
        for (const [key, label] of Object.entries(dateLabels)) {
          const cell = tr.insertCell();
          cell.dataset.label = label; cell.textContent = row[key] ? dateText(row[key]) : "—";
        }
      }
      const tableBox = node("div", "cycle-preview-rows");
      tableBox.tabIndex = 0; tableBox.setAttribute("role", "region");
      tableBox.setAttribute("aria-label", "Работы перед закрытием");
      tableBox.append(table);
      feedback.replaceChildren();
      const counts = [counted(proposal.items.length, ["работа", "работы", "работ"])];
      if (proposal.includedProjectCount) {
        counts.push("Явно включено проектов — " + proposal.includedProjectCount);
      }
      content.append(caption, node("p", "cycle-count", counts.join(" · ")));
      if (proposal.items.length) content.append(tableBox);
      else content.append(node("p", "gantt-empty", "В этом плане пока нет выбранных работ."));
      revision = proposal.revision;
      confirm.disabled = false;
    } catch (error) { retry(error); }
  }

  function retry(error) {
    revision = null;
    confirm.disabled = true;
    feedback.replaceChildren(notice(error.message),
      button("Обновить предпросмотр", "gantt-action", load));
  }

  async function save() {
    if (!revision || confirm.disabled) return;
    confirm.disabled = dismiss.disabled = true;
    try {
      await api("plan/close/apply", {id: planId, revision});
      if (panel.isConnected) showClosed();
    } catch (error) {
      if (panel.isConnected) retry(error);
    } finally { dismiss.disabled = false; }
  }
  load();
  return panel;
}

export function snapshotInspector(ui, {plan, row, root, urlFor, deselect}) {
  const {node, link, button, notice, api} = ui;
  const markdownLink = cardLinks(ui, root);
  const panel = node("section", "cycle-row");
  panel.setAttribute("aria-label", "Работа на момент закрытия");
  const heading = node("div", "gantt-pane-heading");
  const title = node("h2", "", row.title || row.id); title.tabIndex = -1;
  const close = button("×", "gantt-close", deselect);
  close.setAttribute("aria-label", "Снять выделение");
  heading.append(title, close);
  panel.append(heading);
  const context = [row.kind === "milestone" ? "Веха" : null, row.directionTitle, row.projectTitle].filter(Boolean);
  if (context.length) panel.append(node("p", "gantt-context", context.join(" · ")));
  const columns = node("div", "cycle-columns");
  const saved = node("div");
  saved.append(node("h3", "", "Даты при закрытии"));
  if (row.missing) saved.append(node("p", "gantt-context", "На момент закрытия карточка была недоступна."));
  else {
    const dates = node("dl", "gantt-facts");
    if (row.readiness?.value != null) {
      dates.append(node("dt", "", "Готовность при закрытии"), node("dd", "", readinessText(row.readiness)));
    }
    for (const [field, label] of Object.entries(dateLabels)) {
      if (field === "start" && row.kind === "milestone") continue;
      dates.append(node("dt", "", label), node("dd", "", row[field] ? dateText(row[field]) : "Нет даты"));
    }
    saved.append(dates);
  }
  if (row.snapshotLine) {
    const source = markdownLink("Снимок в Obsidian · строка " + row.snapshotLine + " ↗", plan.path, null, row.snapshotLine);
    source.title = plan.path; saved.append(source);
  }

  const current = node("details", "cycle-current");
  current.append(node("summary", "", "Текущая карточка и активные планы"));
  const paths = node("div");
  current.append(paths);
  current.addEventListener("toggle", async () => {
    if (!current.open) return;
    paths.textContent = "Ищем карточку по ID…";
    try {
      const location = await api("plan/task-location?id=" + encodeURIComponent(row.id));
      if (!current.isConnected) return;
      paths.replaceChildren(markdownLink("Открыть текущую карточку в Obsidian ↗", location.path));
      if (location.plans.length) {
        const list = node("ul");
        for (const item of location.plans) {
          const li = node("li"); li.append(link(item.title, urlFor(item.id, row.id), "text-link")); list.append(li);
        }
        paths.append(node("p", "", "Открыть эту работу в активном плане"), list);
      } else paths.append(node("p", "gantt-context", "Работа пока не входит в активный план."));
    } catch (error) {
      paths.replaceChildren(notice(error.status === 404 ? "Текущая карточка с этим ID не найдена." : error.message));
    }
  });
  columns.append(saved, current); panel.append(columns);
  return panel;
}
