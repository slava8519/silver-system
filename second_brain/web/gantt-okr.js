import {archiveStamp} from "./gantt-cycle.js";
import {dateText} from "./gantt-axis.js";
import {objectiveEditor, resultEditor, taskScope} from "./gantt-okr-forms.js";
import {boardURL, resultURL, planName, threshold, reading, readiness, checkSummary} from "./gantt-okr-values.js";
import {cardPage} from "./gantt-okr-detail.js";
import {activityCatalog} from "./project-activity.js";

const catalogTitle = "Цели и ключевые результаты";
export function okrLink(ui, planId) {
  return ui.link(planId ? catalogTitle : "Проекты и Гант", planId ? boardURL(planId) : "#activity", "text-link");
}

async function boardPage(ui, view, planId, planURL) {
  const {api, node, link, labelled, option, pageHead, counted} = ui;
  const board = await api("plan/okr-board?id=" + encodeURIComponent(planId));
  if (!view.isConnected) return;
  const options = board.historical ? {plans: (await api("plans")).items} : await api("plan/okr-options");
  if (!view.isConnected) return;
  function draw(data, message = "", focusId) {
    if (!view.isConnected) return;
    const {plan, objectives} = data;
    const historical = data.historical;
    const chooser = node("select");
    for (const entry of options.plans) chooser.append(option(entry.id, planName(entry)));
    chooser.value = plan.id;
    chooser.addEventListener("change", () => ui.go(boardURL(chooser.value)));
    const head = pageHead(catalogTitle, plan.title, labelled("План или период", chooser),
      link("Общий Гант плана", planURL(plan.id), "button button-secondary"));
    head.classList.add("okr-period-head");
    const status = node("p", "gantt-status", message); status.setAttribute("role", "status");
    const content = node("section", "okr-directory");
    view.replaceChildren(link("← Проекты и Гант", "#activity", "back-link"), head, status, content);
    const all = objectives.flatMap(row => row.results);
    const initiatives = new Set(all.flatMap(row => row.taskIds));
    const bar = node("div", "okr-directory-bar");
    const totals = node("p", "okr-totals");
    for (const value of [counted(objectives.length, ["цель","цели","целей"]),
      counted(all.length, ["ключевой результат","ключевых результата","ключевых результатов"]),
      counted(initiatives.size, ["инициатива","инициативы","инициатив"])]) totals.append(node("span", "", value));
    bar.append(totals);
    if (data.progress?.value != null) bar.append(node("span","okr-period-progress",
      "Готовность инициатив · " + readiness(data.progress) + (data.progress.hasEstimates ? " с оценками" : "")));
    content.append(bar);
    if (historical) content.append(node("p", "cycle-stamp", archiveStamp(plan)?.text || "Снимок на момент закрытия" + (plan.closedAt ? " · " + dateText(plan.closedAt.slice(0,10)) : "")));
    async function created(saved, text) { draw(await api("plan/okr-board?id=" + encodeURIComponent(plan.id)), text, saved.id); }
    const targetNodes = new Map();
    if (objectives.length) {
      const table = node("table", "okr-table");
      const caption = node("caption", "okr-sr", "Цели периода, ключевые результаты, факты, условия и готовность инициатив");
      const labels = ["Цель", "Ключевой результат", "Факт / проверки", "Порог", "Инициативы"];
      const header = table.createTHead().insertRow();
      for (const label of labels) { const cell = node("th","",label); cell.scope = "col"; header.append(cell); }
      table.prepend(caption);
      for (const objective of objectives) {
        const body = table.createTBody();
        const goal = node("th", "okr-goal"); goal.scope = "rowgroup"; goal.rowSpan = Math.max(1, objective.results.length);
        const title = node("span", "okr-goal-name", objective.title); title.tabIndex = -1;
        goal.append(title); targetNodes.set(objective.id, title);
        if (objective.directionTitle) goal.append(node("small", "okr-sub", objective.directionTitle));
        if (objective.progress?.value != null) goal.append(node("small", "okr-sub", "Готовность " + readiness(objective.progress)));
        for (const [index, result] of (objective.results.length ? objective.results : [null]).entries()) {
          const row = body.insertRow();
          if (!index) row.append(goal);
          if (!result) { const empty = node("td", "okr-empty-cell", "Ключевых результатов пока нет."); empty.colSpan=4; row.append(empty); continue; }
          const name = node("th","okr-kr"); name.scope="row";
          const a = link(result.title, resultURL(result.id, plan.id), "text-link");
          name.append(a); targetNodes.set(result.id, a); row.append(name);
          function cell(index, value) {
            const td = node("td", "", value); td.dataset.label = labels[index]; row.append(td); return td;
          }
          const check = checkSummary(result);
          const fact = reading(result);
          const current = cell(2, fact === "—" && check ? check : fact);
          if (fact !== "—" && check) current.append(node("small", "okr-sub", check));
          cell(3, threshold(result));
          const work = cell(4, counted(result.taskIds.length, ["работа","работы","работ"]));
          if (result.progress?.value != null) work.append(node("small", "okr-sub", readiness(result.progress) + (result.progress.hasEstimates ? " с оценками" : "")));
        }
      }
      content.append(table);
    } else content.append(node("p", "gantt-empty", historical && data.hierarchyRecorded === false ?
      "В снимке этого цикла цели и результаты не были зафиксированы." : "В этом плане пока нет целей."));
    if (!historical) {
      const manage = node("div", "okr-manage");
      if (options.directions.length) manage.append(objectiveEditor(ui, plan.id, options.directions, saved => created(saved, "Цель создана.")));
      else {
        const empty = node("p","gantt-context","Для первой цели нужно направление. ");
        empty.append(ui.compact ? "Создайте его в рабочей админке или через Codex." : link("Создать направление", "#new/direction","text-link"));
        manage.append(empty);
      }
      if (objectives.length) manage.append(resultEditor(ui, objectives, options.tasks, saved => created(saved, "Ключевой результат создан.")));
      content.append(manage);
    }
    if (data.gaps.unlinkedTaskIds.length && data.hierarchyRecorded !== false) {
      const box = node("details", "okr-extra-work");
      const rows = new Map(data.items.map(row => [row.id,row]));
      box.append(node("summary", "text-link", "Работы периода без связи с КР · " + data.gaps.unlinkedTaskIds.length));
      const list = node("ul", "okr-work-list");
      for (const id of data.gaps.unlinkedTaskIds) {
        const row = rows.get(id); const li = node("li");
        if (row && !row.missing) li.append(link(row.title,planURL(plan.id,id),"text-link"), node("small","okr-sub", taskScope(row)));
        else li.textContent = "Карточка задачи недоступна · " + id;
        list.append(li);
      }
      box.append(list); content.append(box);
    }
    targetNodes.get(focusId)?.focus();
  }
  draw(board);
}

export async function openOKR(ui, view, id, planURL, planId = new URLSearchParams(location.hash.split("?")[1]).get("plan")) {
  if (id) return cardPage(ui, view, id, planId, planURL);
  if (planId) return boardPage(ui, view, planId, planURL);
  return activityCatalog(ui, view);
}
