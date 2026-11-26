import {dateText} from "./gantt-axis.js";
import {cardLinks} from "./gantt-detail.js";
import {assignmentEditor, linkEditor, taskScope} from "./gantt-okr-forms.js";
import {boardURL, checkSummary, contributionPoints, fractionPercent, planName, primaryOf, quantity, reading, readiness, symbols, threshold} from "./gantt-okr-values.js";
import {evidence} from "./gantt-okr-source.js";
import {criterionEditor, observationEditor, readinessEditor, relationsEditor, thresholdEditor, weightEditor} from "./gantt-okr-tracking.js";

function section(ui, title) {
  const panel = ui.node("section", "okr-detail-section");
  const h = ui.node("h2", "work-heading", title); h.tabIndex = -1;
  panel.append(h); return panel;
}
function addControls(ui, where, ...controls) {
  const present = controls.filter(Boolean);
  if (!present.length) return;
  const box = ui.node("div", "okr-manage");
  box.append(...present); where.append(box);
}
function checkReading(ui, entry, criterion, root) {
  const box = ui.node("div", "okr-check-value");
  if (!entry) { box.append("—"); return box; }
  const value = criterion.kind === "boolean" ? (entry.value ? "Да" : "Нет") : quantity(entry.value, criterion.unit);
  box.append(ui.node("strong", "", value), ui.node("small", "okr-sub", dateText(entry.date)));
  if (entry.source) box.append(evidence(ui, root, entry.source));
  return box;
}
function table(ui, labels, cls) {
  const t = ui.node("table", "okr-detail-table " + cls);
  const tr = t.createTHead().insertRow();
  for (const label of labels) { const th = ui.node("th", "", label); th.scope = "col"; tr.append(th); }
  return t;
}
function cell(ui, tr, label, ...children) {
  const td = ui.node("td"); td.dataset.label = label; td.append(...children); tr.append(td); return td;
}

function criteriaPanel(ui, card, historical, done) {
  const {node} = ui;
  const box = section(ui, "Критерии и измерения");
  const criteria = card.criteria || [];
  if (criteria.length) {
    const t = table(ui, ["Критерий","Исходная точка","Условие","Последняя проверка","Итог"], "okr-criteria-table");
    const body = t.createTBody();
    for (const c of criteria) {
      const tr = body.insertRow();
      const name = node("th","",c.title); name.scope = "row";
      if (c.primary) name.append(node("small", "okr-sub","Основная метрика"));
      tr.append(name);
      cell(ui,tr,"Исходная точка",checkReading(ui,c.baseline,c,card.root));
      cell(ui,tr,"Условие",c.kind === "boolean" ? "Да" : c.target == null ? "—" : symbols[c.operator] + " " + quantity(c.target,c.unit));
      cell(ui,tr,"Последняя проверка",checkReading(ui,c.measurement,c,card.root));
      cell(ui,tr,"Итог",node("span", c.met === true ? "okr-met" : c.met === false ? "okr-unmet" : "okr-sub",
        c.met === null || c.met === undefined ? "Не подтверждено" : c.met ? "Выполнен" : "Не выполнен"));
    }
    box.append(t);
  } else if (!card.unit) box.append(node("p","gantt-context","Критерии пока не заданы."));
  if (card.unit && !primaryOf(card)) {
    const legacy = node("div","okr-direct-metric");
    legacy.append(node("strong","","Метрика КР · " + card.unit), checkReading(ui,card.measurement,{unit:card.unit},card.root));
    box.append(legacy);
  }
  if (!historical) {
    addControls(ui,box,observationEditor(ui,card,done),criterionEditor(ui,card,done),thresholdEditor(ui,card,done));
    const history = node("details","okr-audit");
    const records = [];
    function past(name, rows, c, label) {
      for (const r of (rows || []).slice(0,-1).reverse()) {
        const li = node("li"); li.append(node("span","", name + " · " + label),checkReading(ui,r,c,card.root)); records.push(li);
      }
    }
    if (!primaryOf(card)) past(card.title,card.history,{unit:card.unit},"замер");
    for (const c of criteria) {
      past(c.title,c.history,c,"проверка"); past(c.title,c.baselineHistory,c,"исходная точка");
      for (const change of (c.targetHistory || []).slice().reverse()) {
        records.push(node("li","", c.title + " · " + dateText(change.changedAt.slice(0,10)) + " · " +
          (symbols[change.fromOperator] ? symbols[change.fromOperator] + " " : "") + (change.fromTarget == null ? "—" : quantity(change.fromTarget,c.unit)) + " → " + symbols[change.operator] + " " + quantity(change.target,c.unit)));
      }
    }
    if (records.length) {
      const list = node("ul"); list.append(...records);
      history.append(node("summary","text-link","История измерений и порогов"),list); box.append(history);
    }
  }
  return box;
}

function initiativesPanel(ui, card, rows, plan, historical, options, planURL, done) {
  const {node, link} = ui;
  const box = section(ui,"Инициативы");
  if (card.progress) {
    const coverage = node("p","okr-coverage");
    const parts = [];
    if (card.progress.weightSum != null) parts.push("Распределено веса: " + fractionPercent(card.progress.weightSum));
    if (card.progress.missingWeights.length) parts.push("Без веса: " + card.progress.missingWeights.length);
    if (card.progress.missingValues.length) parts.push("Без готовности: " + card.progress.missingValues.length);
    coverage.textContent = parts.join(" · "); box.append(coverage);
  }
  if (rows.length) {
    const t = table(ui,["Инициатива","Вес в КР","Готовность","Вклад в КР"], "okr-initiative-table");
    const body = t.createTBody();
    for (const row of rows) {
      const r = body.insertRow();
      const th = node("th"); th.scope = "row";
      if (row.missing) th.textContent = "Карточка недоступна · " + row.id;
      else if (plan) th.append(link(row.title,planURL(plan.id,row.id),"text-link"));
      else th.append(cardLinks(ui,card.root)(row.title,row.path));
      const scope = taskScope(row); if (scope) th.append(node("small","okr-sub",scope));
      if (row.plans?.length) {
        const plans = node("details","okr-other-plans"); plans.append(node("summary","text-link","В планах"));
        for (const p of row.plans) plans.append(link(planName(p),planURL(p.id,row.id),"text-link"));
        th.append(plans);
      }
      r.append(th);
      const weight = card.taskWeights?.[row.id];
      cell(ui,r,"Вес в КР",fractionPercent(weight));
      const state = row.readiness;
      const actual = cell(ui,r,"Готовность",state ? quantity(state.value,"%") : "—");
      if (state) {
        actual.append(node("small","okr-sub",(state.kind === "estimate" ? "Оценка" : "Факт") + " · " + dateText(state.date)));
        if (state.reason) {
          const note = node("details","okr-proof"); note.append(node("summary","text-link","Основание"),node("p","",state.reason));
          actual.append(note);
        }
        if (state.source) actual.append(evidence(ui,card.root,state.source));
      }
      cell(ui,r,"Вклад в КР",contributionPoints(card.progress,row.id));
    }
    box.append(t);
  } else box.append(node("p","gantt-context","Связанных инициатив пока нет."));
  if (!historical) {
    const tasks = options.tasks || [];
    addControls(ui, box, linkEditor(ui,card,tasks,() => done("Инициатива привязана.","initiatives")),
      (card.objectiveId || rows.length) && weightEditor(ui,card,rows,done),readinessEditor(ui,rows,done),relationsEditor(ui,card,rows,options,done));
  }
  return box;
}

export async function cardPage(ui, view, id, planId, planURL) {
  const {api, node, link, pageHead} = ui;
  let currentPlan = planId, version = 0;
  async function load(message = "", focus) {
    const call = ++version;
    let board, card, options, goalProgress;
    const getBoard = key => api("plan/okr-board?id=" + encodeURIComponent(key));
    const getCard = () => api("plan/okr?id=" + encodeURIComponent(id));
    if (currentPlan) board = await getBoard(currentPlan);
    if (!board?.historical) {
      card = await getCard();
      if (!currentPlan && card.planId) { currentPlan = card.planId; board = await getBoard(currentPlan); }
    }
    if (board) {
      const parent = board.objectives.find(g => g.results.some(r => r.id === id));
      if (!parent) {
        if (view.isConnected && call === version) view.replaceChildren(link("← Цели периода",boardURL(currentPlan),"back-link"),
          ui.notice("В выбранном плане нет этого ключевого результата."));
        return;
      }
      const periodResult = parent.results.find(r => r.id === id);
      goalProgress = parent.progress;
      card = {...periodResult, ...(!board.historical && card), root:board.root, directionTitle:parent.directionTitle, objective:parent.title,
        progress:periodResult.progress, weight:periodResult.weight, taskWeights:periodResult.taskWeights, taskIds:periodResult.taskIds};
    }
    const historical = Boolean(board?.historical);
    if (!historical) options = await api("plan/okr-options");
    if (!view.isConnected || call !== version) return;
    card.taskIds ||= card.tasks.map(row => row.id);
    const physical = new Map((board?.items || options.tasks || []).map(row => [row.id,row]));
    const details = new Map((card.tasks || []).map(row => [row.id,row]));
    const rows = card.taskIds.map(key => ({id:key,...details.get(key),...physical.get(key)}));
    const plan = board?.plan;
    const nav = node("nav","okr-detail-nav work-tools");
    nav.append(link(plan ? "← " + plan.title : "← Проектная деятельность",boardURL(currentPlan),"back-link"));
    if (plan) nav.append(link("Ветвь КР в Ганте",planURL(plan.id) + "?" + new URLSearchParams({okr:id}),"text-link"));
    const actions = [];
    if (!historical) actions.push(cardLinks(ui,card.root)("Карточка в Obsidian ↗",card.path));
    const header = pageHead("Цель · " + card.objective,card.title,...actions);header.classList.add("okr-period-head");
    const status = node("p","gantt-status",message);status.setAttribute("role","status");
    const overview = node("dl","okr-overview");
    function metric(label, value, small) {
      const part = node("div");part.append(node("dt","",label),node("dd","",value));
      if (small) part.append(node("small","okr-sub",small)); overview.append(part);
    }
    const last = primaryOf(card)?.measurement || card.measurement;
    if (card.unit || primaryOf(card)) {
      metric("Последний замер", reading(card), last ? dateText(last.date) : "Замер ещё не внесён");
      metric(primaryOf(card) ? "Порог" : "Ориентир",threshold(card));
    }
    if (card.criteria?.length) metric("Проверка критериев",checkSummary(card),
      card.criteriaMet === null ? "Проверены не все условия" : card.criteriaMet ? "Все условия выполнены" : "Есть невыполненное условие");
    if (card.progress) metric("Готовность инициатив",readiness(card.progress),card.progress?.value != null ?
      (card.progress.hasEstimates ? "С учётом оценок" : "По фактической готовности") : "Для расчёта нужны веса и готовность");
    if (card.weight != null) metric("Вес КР в цели",fractionPercent(card.weight),goalProgress ?
      "Вклад в готовность цели · " + contributionPoints(goalProgress,card.id) : "");
    const criteria = criteriaPanel(ui,card,historical,load);
    const initiatives = initiativesPanel(ui,card,rows,plan,historical,options,planURL,load);
    view.replaceChildren(nav,header,status);
    if (historical) view.append(node("p","cycle-stamp","Снимок на момент закрытия · " + dateText(plan.closedAt.slice(0,10))));
    if (overview.children.length) view.append(overview);
    view.append(criteria,initiatives);
    if (card.contextProjects?.length) {
      const context = section(ui,"Контекстные проекты");
      const list = node("ul","okr-work-list");
      for (const project of card.contextProjects) {
        const item = node("li");
        if (project.missing) item.textContent = "Карточка проекта недоступна · " + project.id;
        else item.append(ui.embedded ? cardLinks(ui,card.root)(project.title,project.path)
          : link(project.title,"#record/" + encodeURIComponent(project.id),"text-link"));
        list.append(item);
      }
      context.append(list); view.append(context);
    }
    if (!historical && !card.objectiveId) view.append(assignmentEditor(ui,card,options));
    if (focus) (focus === "criteria" ? criteria : initiatives).querySelector("h2").focus();
  }
  await load();
}
