import {dateText, dayOf} from "./gantt-axis.js";
import {cardLinks} from "./gantt-detail.js";
import {planEditor} from "./gantt-okr-forms.js";
import {boardURL, resultURL} from "./gantt-okr-values.js";
import {relationshipFacts} from "./project-participation.js";

const scopes = {all:"Все", projects:"Проекты", plans:"Планы"};
const order = new Intl.Collator("ru", {numeric:true, sensitivity:"base"});
const planURL = id => "#plan/" + encodeURIComponent(id);
const projectURL = id => "#project-gantt/" + encodeURIComponent(id);
const normalize = value => (value || "").toLocaleLowerCase("ru").replace(/ё/g, "е");
const byTitle = (a, b) => order.compare(a.title,b.title) || order.compare(a.id,b.id);
const planOrder = (a, b) => Number(a.status === "closed") - Number(b.status === "closed") || byTitle(a,b);

function spanOf(row) {
  if (row.start && row.end && row.start !== row.end) return dateText(row.start) + " — " + dateText(row.end);
  return row.start || row.end ? dateText(row.start || row.end) : "";
}

export function projectDates(row) {
  const dates = [];
  const due = dayOf(row.due) === null ? row.due : dateText(row.due);
  if (row.projectStart && due) dates.push("Период проекта · " + dateText(row.projectStart) + " — " + due);
  else if (row.projectStart) dates.push("Начало проекта · " + dateText(row.projectStart));
  else if (due) dates.push("Срок проекта · " + due);
  const span = spanOf(row);
  if (span) dates.push("Даты работ · " + span);
  return dates;
}

function matches(row, terms) {
  const title = normalize(row.title + " " + (row.objective || ""));
  return terms.every(term => title.includes(term));
}

const hasOKR = plan => plan.objectiveCount > 0 || plan.resultCount > 0;

export function catalogRows(scope, rows) {
  if (scope === "all") return rows;
  if (scope === "okr") return {plans: rows.plans.filter(hasOKR)};
  return {[scope]: rows[scope]};
}

export async function activityCatalog(ui, view) {
  const {api, node, link, button, option, labelled, counted} = ui;
  const data = await api("plan/activity");
  if (!view.isConnected) return;
  const directions = new Map(data.directions.map(item => [item.id,item.title]));
  const projects = [...data.projects].sort(byTitle);
  const plans = [...data.plans].sort(planOrder);
  const legacy = [...data.unassignedResults].sort(byTitle);
  const [route, query] = location.hash.split("?");
  const params = new URLSearchParams(query);
  const requestedView = params.get("view");
  let scope = ["okr", "unassigned"].includes(requestedView) || Object.hasOwn(scopes, requestedView) ? requestedView : "all";
  let page = Math.max(1,Number.parseInt(params.get("page"),10) || 1);
  const selectedDirection = params.get("direction");
  const createProject = link("Создать проект", "#new/project", "button button-primary");
  const createPlan = planEditor(ui,saved=>ui.go(scope === "okr" ? boardURL(saved.id) : planURL(saved.id)));
  const help = link("О разделе", "#activity-help", "text-link activity-help-link");
  const helpIcon = node("span", "about-icon", "i");
  helpIcon.setAttribute("aria-hidden", "true");
  help.prepend(helpIcon);
  const heading = ui.pageHead(data.name, "Проектная деятельность", help,createProject,createPlan);
  heading.classList.add("activity-heading");
  const toolbar = node("div","activity-toolbar");
  const tabs = node("div","activity-scopes");
  tabs.setAttribute("role","group"); tabs.setAttribute("aria-label","Вид записей");
  const buttons = new Map();
  for (const [key,title] of Object.entries(scopes)) {
    const b = button(title,"activity-tab",()=>choose(key)); buttons.set(key,b); tabs.append(b);
  }
  const withOKR = node("input");
  withOKR.type = "checkbox";
  withOKR.addEventListener("change", () => choose(withOKR.checked ? "okr" : "plans", withOKR));
  const filter = node("label", "activity-okr-filter");
  filter.append(withOKR, node("span", "", "С OKR"));
  const typeFilters = node("div", "activity-type-filters");
  typeFilters.append(tabs, filter);
  const search = node("input");
  search.type = "search"; search.placeholder = "Проект, план или результат"; search.value = params.get("q") || "";
  const direction = node("select"); direction.append(option("", "Все направления"));
  for (const row of [...data.directions].sort(byTitle)) direction.append(option(row.id,row.title));
  if (directions.has(selectedDirection)) direction.value = selectedDirection;
  toolbar.append(typeFilters,labelled("Поиск по названию",search));
  if (directions.size) toolbar.append(labelled("Направление",direction));
  const results = node("div","activity-results");
  const announce = node("p","okr-sr"); announce.setAttribute("role","status");
  view.replaceChildren(heading,toolbar,announce,results);

  function choose(value, focus = buttons.get(value)) {
    scope = value; page = 1; draw();
    (focus || results.querySelector("h2"))?.focus({preventScroll:true});
  }
  function persist() {
    const state = new URLSearchParams();
    if (scope !== "all") state.set("view",scope);
    if (direction.value) state.set("direction",direction.value);
    if (search.value.trim()) state.set("q",search.value.trim());
    if (scope !== "all" && page > 1) state.set("page",String(page));
    const next = route + (state.size ? "?" + state : "");
    help.href = "#activity-help" + (state.size ? "?" + state : "");
    if (next !== location.hash) history.replaceState(null,"",next);
  }
  function update() { page = 1; draw(); }
  search.addEventListener("input",update); direction.addEventListener("change",update);

  const workCount = n => counted(n,["работа","работы","работ"]);
  function cardLink(item) {
    if (ui.embedded) return cardLinks(ui,data.root)("Карточка",item.path);
    return link("Карточка","#record/" + encodeURIComponent(item.id),"text-link");
  }
  function facts(...parts) {
    const line = node("p","activity-facts");
    for (const part of parts.filter(Boolean)) line.append(node("span","",part));
    return line;
  }
  function relations(entries, fromProject, plan) {
    const manage = plan && plan.status !== "closed" ? link("Выбрать проекты плана", planURL(plan.id) + "?projects=1", "text-link") : null;
    if (!entries.length) return manage;
    const box = node("details","activity-relations");
    box.append(node("summary","text-link",(fromProject ? "Связанные планы" : "Связанные проекты") + " · " + entries.length));
    box.addEventListener("toggle", () => {
      if (!box.open || box.querySelector("ul")) return;
      const list = node("ul");
      for (const record of [...entries].sort(byTitle)) {
        const item = node("li");
        const title = record.title || record.id;
        item.append(record.missing ? node("span","",title) :
          link(title,fromProject ? planURL(record.id) : projectURL(record.id),"text-link"));
        if (fromProject && (record.okrTaskCount || record.contextResultCount)) {
          item.append(link("OKR",boardURL(record.id),"text-link activity-relation-okr"));
        }
        const kinds = relationshipFacts(record, counted);
        if (kinds.length) item.append(node("small","",kinds.join(" · ")));
        list.append(item);
      }
      box.append(list);
      if (manage) box.append(manage);
    });
    return box;
  }
  function itemNode(title,href) {
    const item = node("li","activity-row");
    const name = node("h3"); name.append(link(title,href,"activity-name"));
    const body = node("div","activity-row-body");
    const actions = node("div","activity-links");
    body.append(name); item.append(body,actions);
    return {item,body,actions};
  }
  function projectItem(row) {
    const {item,body,actions} = itemNode(row.title,projectURL(row.id));
    if (row.directionTitle || row.status) body.append(facts(row.directionTitle,row.status));
    body.append(facts(workCount(row.taskCount),row.completedCount ? "завершено " + row.completedCount : "",
      row.okrTaskCount ? "в КР: " + row.okrTaskCount : ""));
    for (const date of projectDates(row)) body.append(facts(date));
    const refs = relations(row.plans,true); if (refs) body.append(refs);
    actions.append(link("Гант проекта",projectURL(row.id),"activity-open"),cardLink(row));
    return item;
  }
  function planItem(row) {
    const okr = hasOKR(row);
    const {item,body,actions} = itemNode(row.title,scope === "okr" ? boardURL(row.id) : planURL(row.id));
    if (row.status === "closed") body.append(facts("Закрыт" + (row.closedAt ? " · " + dateText(row.closedAt.slice(0,10)) : "")));
    body.append(facts(workCount(row.taskCount),row.completedCount ? "завершено " + row.completedCount : "",
      okr ? counted(row.objectiveCount,["цель","цели","целей"]) + " · " + row.resultCount + " КР" : ""));
    const span = spanOf(row);
    if (span) body.append(facts("Даты работ · " + span));
    const refs = relations(row.projects,false,row); if (refs) body.append(refs);
    const gantt = link("Гант плана",planURL(row.id),scope === "okr" ? "text-link" : "activity-open");
    if (scope === "okr") actions.append(link("Открыть OKR",boardURL(row.id),"activity-open"),gantt);
    else {
      actions.append(gantt);
      if (okr || row.status !== "closed") actions.append(link(okr ? "Открыть OKR" : "Добавить цели",boardURL(row.id),"text-link"));
    }
    return item;
  }
  function oldItem(row) {
    const {item,body,actions} = itemNode(row.title,resultURL(row.id));
    body.append(facts(row.directionTitle,row.objective));
    if (row.missingObjective) body.append(facts("Карточка цели недоступна"));
    actions.append(link("Открыть КР",resultURL(row.id),"activity-open"));
    return item;
  }

  function group(title, entries, renderItem, total, more) {
    const box = node("section","activity-group");
    const head = node("header","activity-group-head");
    const h = node("h2","",title); h.tabIndex = -1;
    head.append(h,node("span","activity-count",total)); box.append(head);
    const list = node("ul","activity-list");
    for (const item of entries) list.append(renderItem(item));
    box.append(list);
    if (more) box.append(more);
    return box;
  }

  function draw() {
    const dir = direction.value;
    const terms = normalize(search.value.trim()).split(/\s+/).filter(Boolean);
    const areaFilter = row => !dir || row.direction === dir;
    const projectRows = projects.filter(row => areaFilter(row) && matches(row,terms));
    const planRows = plans.filter(row => (!dir || row.directionIds.includes(dir)) && matches(row,terms));
    const resultRows = legacy.filter(row => areaFilter(row) && matches(row,terms));
    const lists = catalogRows(scope, {projects:projectRows, plans:planRows, unassigned:resultRows});
    const sections = {
      projects: {title:"Проекты", render:projectItem, more:"Все проекты"},
      plans: {title:scope === "okr" ? "Планы с OKR" : "Планы и периоды", render:planItem, more:"Все планы"},
      unassigned: {title:"Результаты вне планов", render:oldItem, more:"Все результаты вне планов"}
    };

    const activeType = scope === "okr" ? "plans" : scope === "unassigned" ? "all" : scope;
    for (const [key,b] of buttons) b.setAttribute("aria-pressed",String(activeType === key));
    withOKR.checked = scope === "okr";
    const href = "#new/project" + (dir ? "?" + new URLSearchParams({direction:dir}) : "");
    createProject.href = href;
    const total = Object.values(lists).reduce((sum,rows)=>sum+rows.length,0);
    const size = 12;
    const pages = Math.max(1,Math.ceil(total / size));
    page = Math.min(page,pages);
    persist();
    announce.textContent = "Записей в каталоге: " + total;
    results.replaceChildren();
    const grid = node("div","activity-grid");
    grid.dataset.scope = scope;
    for (const [key, rows] of Object.entries(lists)) {
      const section = sections[key];
      const preview = scope === "all";
      const entries = preview ? rows.slice(0,3) : rows.slice((page-1)*size,page*size);
      if (preview && key === "plans" && !entries.some(hasOKR)) {
        const firstOKR = rows.find(hasOKR);
        if (firstOKR) entries[entries.length-1] = firstOKR;
      }
      if (!entries.length) continue;
      let more;
      if (preview && rows.length > entries.length) {
        more = button(section.more + " (" + rows.length + ")", "text-link activity-more", () => choose(key));
      }
      const box = group(section.title,entries,section.render,rows.length,more);
      box.dataset.kind = key; grid.append(box);
    }
    if (grid.children.length) results.append(grid);
    else {
      const empty = node("p","activity-empty");
      if (terms.length || dir) empty.append("По заданным условиям ничего не найдено. ",
        button("Сбросить поиск и направление","text-link",()=>{search.value=""; direction.value=""; update(); search.focus();}));
      else if (scope === "okr") {
        empty.append("Планов с OKR пока нет.");
        if (plans.some(row => row.status !== "closed")) empty.append(" ",button("Выбрать план для OKR","text-link",()=>choose("plans")));
      } else if (scope === "unassigned") empty.append("Ключевых результатов вне планов нет.");
      else empty.append(scope === "projects" ? "В хранилище пока нет проектов." : scope === "plans" ? "Планов пока нет." : "В хранилище пока нет проектов и планов.");
      results.append(empty);
    }
    if (scope !== "all" && pages > 1) {
      const nav = node("nav","activity-pages"); nav.setAttribute("aria-label","Страницы каталога");
      function change(delta) {page += delta; draw(); results.querySelector("h2")?.focus();}
      const prev = button("Назад","button button-secondary",()=>change(-1));
      const next = button("Дальше","button button-secondary",()=>change(1)); prev.disabled = page === 1; next.disabled = page === pages;
      nav.append(prev,node("span","","Страница " + page + " из " + pages),next); results.append(nav);
    }
    if (scope === "okr" && resultRows.length) {
      const query = new URLSearchParams({view:"unassigned"});
      if (dir) query.set("direction",dir);
      if (search.value.trim()) query.set("q",search.value.trim());
      results.append(link("Все результаты вне планов (" + resultRows.length + ")",route + "?" + query,"text-link"));
    }
  }
  draw();
}
