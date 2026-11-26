import {calendarYear, dateLabels, dateText, dayOf, fitWindow, isoDay, scheduleMarks, shortDay, ticks, todayDay, windowTitle} from "./gantt-axis.js";
import {cardLinks, taskInspector} from "./gantt-detail.js";
import {newWorkForm} from "./gantt-dependencies.js";
import {closedDay, closedStamp, cyclePreview, snapshotInspector} from "./gantt-cycle.js";

import {branchDates, treeRows} from "./gantt-tree.js";
import {okrLink, openOKR} from "./gantt-okr.js";
import {activityCatalog} from "./project-activity.js";
import {planProjects} from "./project-participation.js";
import {projectUpdatesPanel} from "./project-updates.js";

function urlFor(id, task, scope) {
  const params = new URLSearchParams();
  if (task) params.set("task", task);
  if (scope) params.set("okr", scope);
  return "#plan/" + encodeURIComponent(id) + (params.size ? "?" + params : "");
}
const endpoint = (path, id) => path + "?id=" + encodeURIComponent(id);

export function planningView(ui) {
  const {node, link, button, option, counted, api, pageHead, notice, go} = ui;
  let dispose = () => {};
  return async function open(view, id, selectedId, page) {
    dispose();
    if (page === "okr" || page === "activity" || (page === "project-gantt" && !id)) return openOKR(ui, view, id, urlFor);
    if (!id) return activityCatalog(ui, view, "plans");
    const projectView = page === "project-gantt";
    const planId = projectView ? null : id;
    const source = endpoint(projectView ? "plan/project-gantt" : "plan", id);
    const chartURL = (task, scope) => projectView ? "#project-gantt/" + encodeURIComponent(id)
      + (task ? "?" + new URLSearchParams({task}) : "") : urlFor(planId, task, scope);
    const life = new AbortController();
    dispose = () => life.abort();
    const [catalog, initial] = await Promise.all([projectView ? null : api("plans"), api(source)]);
    if (!view.isConnected) return;
    const {name: vault, root} = catalog || initial;
    let plan = initial, selected = selectedId, detail = null, inspector = null;
    const closed = !projectView && plan.status === "closed";
    const today = closed ? closedDay(plan.closedAt) : todayDay();
    const cursorLabel = closed ? "Закрытие" : "Сегодня";
    let grouping = projectView ? "none" : (plan.objectives?.length || plan.items.some(row => row.parentId)) ? "tree" : "direction";
    const fit = () => fitWindow(plan.items, today, plan.project);
    let period = fit(), mode = "chart", refreshVersion = 0;
    let scope = new URLSearchParams(location.hash.split("?")[1]).get("okr") || "", scopeRows = plan.items;
    let dragging = false;
    const folded = new Set();
    const stage = node("section", "gantt-stage");
    const sizing = new ResizeObserver(() => drawLinks());
    life.signal.addEventListener("abort", () => sizing.disconnect(), {once: true});
    const createPlace = node("div");
    const detailPlace = node("div", "gantt-detail-place");
    const feedback = node("p", "gantt-status");
    feedback.setAttribute("role", "status");
    const headerActions = [];
    if (!projectView) {
      const chooser = node("select");
      chooser.setAttribute("aria-label", "Выбрать план");
      for (const p of catalog.items) chooser.append(option(p.id, p.status === "closed" ? p.title + " · закрыт" : p.title));
      chooser.value = planId;
      chooser.addEventListener("change", () => {
        if (discard()) go(urlFor(chooser.value));
        else chooser.value = planId;
      });
      headerActions.push(chooser);
    }
    const refreshButton = button("Обновить", "button button-secondary", () => {
      if (discard()) refresh("Данные обновлены.").catch(report);
    });
    const connector = pageHead(projectView ? "Гант проекта · " + vault : vault, plan.title, ...headerActions, refreshButton);
    connector.classList.add("gantt-heading");
    const projectFacts = node("p", "gantt-project-facts");
    const projectUpdates = node("div");
    function showProjectFacts() {
      projectFacts.replaceChildren();
      for (const [key, label] of [["status", "Статус"], ["projectStart", "Начало проекта"], ["due", "Срок проекта"]]) {
        const value = plan.project[key];
        if (!value) continue;
        const rendered = key !== "status" && dayOf(value) !== null ? dateText(value) : value;
        const text = label + " · " + rendered;
        const field = plan.project.stateSources[key];
        const item = ui.compact ? cardLinks(ui, root)(text, plan.path, null, field?.line)
          : link(text, "#record/" + encodeURIComponent(id) + "?" + new URLSearchParams({field: key}), "text-link");
        projectFacts.append(item);
      }
      projectFacts.hidden = !projectFacts.childElementCount;
      const updates = plan.project.projectUpdates;
      projectUpdates.replaceChildren();
      if (updates?.brief?.text || updates?.total || updates?.warnings?.length) {
        const project = {...plan.project, projectUpdates:{...updates, events:updates.events.slice(0,1)}};
        projectUpdates.append(projectUpdatesPanel(ui, project));
      }
    }
    const navigation = node("div", "work-tools");
    if (projectView) {
      connector.querySelector("h1").after(projectFacts);
      showProjectFacts();
      const card = ui.compact ? cardLinks(ui, root)("Карточка проекта ↗", plan.path)
        : link("Карточка проекта", "#record/" + encodeURIComponent(id), "text-link");
      navigation.append(link("← Проектная деятельность", "#activity", "back-link"), card);
    } else navigation.append(link("← Все планы", "#plans", "back-link"), okrLink(ui, planId));
    if (!ui.compact) navigation.append(link("Показать связи", "#map?" + new URLSearchParams({id}), "text-link"));
    view.append(navigation, connector);
    if (projectView) view.append(projectUpdates);
    if (!projectView) {
      const params = new URLSearchParams(location.hash.split("?")[1]);
      view.append(planProjects(ui, planId, {open:params.get("projects") === "1",
        currentPlan:() => plan, canEdit:discard, refreshChart:() => refresh()}));
    }
    view.append(createPlace, stage, detailPlace, feedback);
    const cycleStatus = closed ? node("div", "cycle-heading-status") : null;
    function showCycleLinks() {
      const openNote = cardLinks(ui, root);
      const stamp = node("time", "cycle-stamp", "Цикл закрыт · " + closedStamp(plan.closedAt));
      stamp.dateTime = plan.closedAt;
      cycleStatus.replaceChildren(stamp, openNote("Снимок в Obsidian ↗", plan.path));
      for (const issue of plan.weeklyReviews || []) {
        cycleStatus.append(openNote(issue.title + " ↗", issue.path));
      }
      if (plan.weeklyReviewError) cycleStatus.append(node("small", "", plan.weeklyReviewError));
    }
    if (closed) {
      showCycleLinks();
      connector.querySelector("h1").after(cycleStatus);
    } else if (!projectView) {
      const close = button("Закрыть цикл…", "button button-secondary", () => {
        if (!discard()) return;
        stage.hidden = detailPlace.hidden = close.disabled = true;
        const panel = cyclePreview(ui, planId, root, () => {
          createPlace.replaceChildren();
          stage.hidden = detailPlace.hidden = close.disabled = false;
          close.focus();
        }, () => go(chartURL(selected)));
        createPlace.replaceChildren(panel); panel.querySelector("h2").focus();
      });
      connector.querySelector(".work-tools").append(close);
    }
    function report(error) { if (view.isConnected) feedback.replaceChildren(notice(error.message)); }
    function discard() {
      if (createPlace.firstChild) {
        createPlace.querySelector("input,h2")?.focus();
        return false;
      }
      return !inspector || inspector.canLeave();
    }
    document.addEventListener("click", event => {
      const a = event.target.closest?.('a[href^="#"]');
      if (a && a.target !== "_blank" && view.isConnected && !event.metaKey && !event.ctrlKey && !discard()) event.preventDefault();
    }, {capture: true, signal: life.signal});
    function setSelected(id) {
      selected = id;
      history.replaceState(null, "", chartURL(id, scope));
    }
    async function readDetail(id) {
      const [task, relations] = await Promise.all([api(endpoint("plan/task", id)), api(endpoint("plan/dependencies", id))
        .catch(error => ({error: error.message}))]);
      return {...task, relations};
    }
    async function refresh(message = "") {
      const sequence = ++refreshVersion;
      const fresh = await api(source);
      if (!view.isConnected || sequence !== refreshVersion) return;
      if (!projectView && fresh.status !== plan.status) { go(chartURL(selected)); return; }
      const task = selected && !closed ? await readDetail(selected).catch(error => {
        if (error.status !== 404) throw error;
        return null;
      }) : null;
      if (!view.isConnected || sequence !== refreshVersion || (!message && inspector?.editing())) return;
      if (!message && detail?.revision === task?.revision && JSON.stringify(detail?.relations) === JSON.stringify(task?.relations) && JSON.stringify(plan) === JSON.stringify(fresh)) return;
      plan = fresh;
      if (closed) showCycleLinks();
      if (projectView) showProjectFacts();
      if (!period.zoom) period = fit();
      connector.querySelector("h1").textContent = plan.title;
      if (selected && ((!closed && !task) || !plan.items.some(r => r.id === selected))) setSelected(null);
      detail = selected ? task : null;
      drawChart(); drawInspector();
      if (message) {
        feedback.textContent = message;
        detailPlace.querySelector(".gantt-pane-heading h2")?.focus({preventScroll: true});
      }
    }
    function onFocus() {
      if (!view.isConnected) { dispose(); return; }
      if (!inspector?.editing() && !createPlace.firstChild && !dragging && !document.hidden) refresh().catch(report);
    }
    window.addEventListener("focus", onFocus, {signal: life.signal});
    async function choose(id, proposal) {
      if (createPlace.firstChild) { discard(); return; }
      const wasEditing = inspector?.editing();
      if (wasEditing && (id !== selected || proposal) && !discard()) return;
      if (id === selected && (detail || closed) && !proposal) return;
      const previous = selected;
      const sequence = ++refreshVersion;
      try {
        const next = id && !closed ? await readDetail(id) : null;
        if (!view.isConnected || sequence !== refreshVersion) return;
        setSelected(id);
        detail = next;
        feedback.textContent = "";
        markSelection(); drawInspector();
        if (proposal) inspector.dateForm(proposal);
        else if (!id && previous) {
          [...stage.querySelectorAll("tr[data-task]")].find(tr => tr.dataset.task === previous)?.querySelector(".gantt-task").focus();
        } else if (id && window.matchMedia("(max-width: 600px)").matches) {
          detailPlace.querySelector("h2")?.focus({preventScroll: true});
          detailPlace.scrollIntoView({block: "start"});
        }
      } catch (error) { report(error); }
    }
    function markSelection() {
      const predecessors = new Set(detail?.relations.predecessors?.map(item => item.id));
      const successors = new Set(detail?.relations.successors?.map(item => item.id));
      for (const tr of stage.querySelectorAll("[data-task]")) {
        const active = tr.dataset.task === selected;
        tr.classList.toggle("is-selected", active);
        const task = tr.querySelector(".gantt-task");
        task.setAttribute("aria-pressed", String(active));
        task.querySelector(".gantt-related")?.remove();
        const relation = predecessors.has(tr.dataset.task) ? "Предшественник" : successors.has(tr.dataset.task) ? "Следом" : "";
        if (relation) task.append(node("small", "gantt-related", relation));
      }
      drawLinks();
    }
    function drawLinks() {
      const sheet = stage.querySelector(".gantt-chart-sheet");
      if (!sheet) return;
      sheet.querySelector("svg")?.remove();
      if (!detail || detail.relations.error) return;
      // Anchor only to recorded dates on rows visible in the current grouping and window.
      const rows = new Map([...sheet.querySelectorAll("tr[data-task]")].map(tr => [tr.dataset.task, tr]));
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("aria-hidden", "true");
      const bounds = sheet.getBoundingClientRect();
      function point(id, finish) {
        const tr = rows.get(id);
        if (!tr) return null;
        const fields = finish || tr.classList.contains("gantt-milestone") ? ["completed", "forecast", "due"] : ["start", "range"];
        for (const field of fields) {
          const mark = tr.querySelector(".gantt-" + field);
          if (!mark || mark.classList.contains("cut-start")) continue;
          const rect = mark.getBoundingClientRect();
          return {x: rect.left - bounds.left + (field === "range" ? 0 : rect.width / 2), y: rect.top - bounds.top + rect.height / 2};
        }
        return null;
      }
      function connect(from, to) {
        const a = point(from, true), b = point(to, false);
        if (!a || !b) return;
        const direction = b.y > a.y ? 1 : -1;
        const mid = (a.y + b.y) / 2, end = b.y - 8 * direction;
        const path = document.createElementNS(svg.namespaceURI, "path");
        path.setAttribute("d", "M " + a.x + " " + (a.y + 7 * direction) + " V " + mid + " H " + b.x + " V " + end
          + " m -3 " + (-3 * direction) + " l 3 " + (3 * direction) + " l 3 " + (-3 * direction));
        svg.append(path);
      }
      for (const entry of detail.relations.predecessors) connect(entry.id, selected);
      for (const entry of detail.relations.successors) connect(selected, entry.id);
      if (svg.childElementCount) sheet.append(svg);
    }
    function updateChart(action, control) {
      action();
      drawChart();
      if (control === "today") {
        const board = stage.querySelector(".gantt-board");
        const lane = stage.querySelector(".gantt-lane");
        if (lane && board.scrollWidth > board.clientWidth) {
          const titleWidth = stage.querySelector("th").offsetWidth;
          board.scrollLeft = lane.offsetWidth * ((today - period.start + .5) / period.size) - (board.clientWidth - titleWidth) / 2;
        }
      }
      if (control) stage.querySelector('[data-control="' + control + '"]')?.focus({preventScroll: true});
    }

    function drawChart() {
      const controls = node("div", "gantt-toolbar");
      const views = node("div", "gantt-mode-switch");
      views.setAttribute("role", "group"); views.setAttribute("aria-label", projectView ? "Представление проекта" : "Представление плана");
      for (const [value, title] of [["chart", "Гант"], ["list", "Таблица"]]) {
        const b = button(title, "", () => updateChart(() => { mode = value; }, value));
        b.dataset.control = value; b.setAttribute("aria-pressed", String(mode === value));
        views.append(b);
      }
      controls.append(views);
      const group = node("select");
      if (plan.tree?.length) group.append(option("tree", "Дерево плана"));
      for (const [value, title] of [["direction", "По направлениям"], ["project", "По проектам"], ["none", "Без группировки"]]) group.append(option(value, title));
      group.setAttribute("aria-label", "Группировка задач");
      group.dataset.control = "group"; group.value = grouping;
      group.addEventListener("change", () => updateChart(() => { grouping = group.value; }, "group"));
      if (!projectView) controls.append(group);
      if (grouping === "tree") {
        const treeKeys = plan.tree.filter(entry => entry.kind === "result" || entry.kind === "outside").map(entry => entry.key);
        if (treeKeys.length) {
          const collapsed = treeKeys.every(key => folded.has(key));
          const toggle = button(collapsed ? "Раскрыть работы" : "Свернуть до КР", "gantt-tool", () =>
            updateChart(() => {
              if (collapsed) folded.clear();
              else for (const key of treeKeys) folded.add(key);
            }, "tree-depth"));
          toggle.dataset.control = "tree-depth";
          controls.append(toggle);
        }
      }
      scopeRows = plan.items;
      if (plan.objectives?.length) {
        const filter = node("select", "gantt-okr-filter");
        filter.setAttribute("aria-label", "Работы по цели или ключевому результату");
        filter.dataset.control = "scope";
        filter.append(option("", "Весь план"));
        const linked = new Set();
        let included;
        for (const goal of plan.objectives) {
          const group = node("optgroup");
          group.label = goal.title;
          group.append(option(goal.id, "Цель · " + goal.title));
          const goalTasks = new Set();
          for (const kr of goal.results) {
            group.append(option(kr.id, "КР · " + kr.title));
            for (const id of kr.taskIds) { linked.add(id); goalTasks.add(id); }
            if (scope === kr.id) included = new Set(kr.taskIds);
          }
          if (scope === goal.id) included = goalTasks;
          filter.append(group);
        }
        filter.append(option("unlinked", "Работы без связи с КР"));
        filter.value = scope;
        if (filter.selectedIndex < 0) { scope = ""; filter.value = scope; }
        if (scope === "unlinked") scopeRows = plan.items.filter(row => !linked.has(row.id));
        else if (included) scopeRows = plan.items.filter(row => included.has(row.id));
        filter.addEventListener("change", () => {
          if (!discard()) { filter.value = scope; return; }
          updateChart(() => { scope = filter.value; folded.clear(); }, "scope");
          history.replaceState(null, "", chartURL(selected, scope));
          if (selected && !scopeRows.some(row => row.id === selected)) choose(null);
          else if (!selected) drawInspector();
        });
        controls.append(filter);
      } else scope = "";
      const addWork = button(projectView ? "+ Работа" : "+ Веха", "gantt-tool", () => {
        if (!discard()) return;
        createPlace.replaceChildren(newWorkForm(ui, plan, async item => {
          createPlace.replaceChildren();
          if (item) {
            setSelected(item.id);
            await refresh(projectView ? "Работа добавлена в проект." : "Веха добавлена в план.");
          } else stage.querySelector(".gantt-add-milestone")?.focus();
        }));
        createPlace.querySelector("input").focus();
      });
      addWork.classList.add("gantt-add-milestone");
      addWork.setAttribute("aria-label", projectView ? "Добавить работу в проект" : "Добавить веху");
      if (!closed) controls.append(addWork);
      if (mode === "chart") {
        const calendarTools = node("div", "gantt-toolbar-end");
        const zoom = node("select");
        for (const [value, title] of [["fit", projectView ? "Весь проект" : "Весь план"], ["14", "Две недели"], ["42", "Шесть недель"], ["91", "Квартал"], ["year", "Год"]]) zoom.append(option(value, title));
        zoom.dataset.control = "window"; zoom.setAttribute("aria-label", "Окно шкалы");
        zoom.value = period.zoom || "fit";
        zoom.addEventListener("change", () => updateChart(() => {
          if (zoom.value === "fit") period = fit();
          else if (zoom.value === "year") period = calendarYear(period.start + Math.floor(period.size / 2));
          else {
            const size = Number(zoom.value), center = period.start + Math.floor(period.size / 2);
            period = {start: center - Math.floor(size / 2), size, zoom: zoom.value};
          }
        }, "window"));
        const travel = (title, label, id, fn) => {
          const b = button(title, "gantt-tool", () => updateChart(fn, id));
          b.setAttribute("aria-label", label); b.title = label; b.dataset.control = id; return b;
        };
        const shift = direction => {
          if (period.zoom === "year") { period = calendarYear(period.start, direction); return; }
          // Panning from a fitted plan enters a fixed window, so «Весь план» can reset it.
          const size = period.zoom ? period.size : 14;
          period = {start: period.start + direction * size, size, zoom: String(size)};
        };
        calendarTools.append(zoom,
          travel("‹", "Предыдущее окно", "prev", () => shift(-1)),
          travel(cursorLabel, closed ? "Показать дату закрытия" : "Показать сегодня", "today", () => {
            if (period.zoom === "year") { period = calendarYear(today); return; }
            const size = period.zoom ? period.size : 14;
            period = {start: today - Math.floor(size / 2), size, zoom: String(size)};
          }), travel("›", "Следующее окно", "next", () => shift(1)));
        controls.append(calendarTools);
      }
      const board = node("div", period.zoom ? "gantt-board" : "gantt-board gantt-fit");
      board.tabIndex = 0;
      board.setAttribute("role", "region"); board.setAttribute("aria-label", projectView ? "Работы проекта" : "Задачи плана");
      const table = node("table", mode === "chart" ? "gantt-grid" : "gantt-list-grid");
      const head = table.createTHead().insertRow();
      const th = title => { const cell = node("th", "", title); cell.scope = "col"; return cell; };
      head.append(th("Работа"));
      const scaleTicks = ticks(period);
      const todayOnScale = (today - period.start + .5) / period.size;
      if (mode === "chart") {
        const axis = th("");
        axis.className = "gantt-axis";
        axis.append(node("div", "gantt-axis-title", windowTitle(period)));
        if (projectView) {
          const first = dayOf(plan.project.projectStart), last = dayOf(plan.project.due);
          if (first !== null || last !== null) {
            const bounds = node("div", "gantt-project-range");
            bounds.setAttribute("aria-hidden", "true");
            if (first !== null && last !== null && first <= last) {
              const left = Math.max(first, period.start), right = Math.min(last + 1, period.start + period.size);
              if (right > left) {
                const band = node("i", "gantt-project-band");
                band.style.left = (left - period.start) / period.size * 100 + "%";
                band.style.width = (right - left) / period.size * 100 + "%";
                bounds.append(band);
              }
            }
            for (const [value, label] of [[first, "Начало проекта"], [last, "Срок проекта"]]) {
              if (value === null || value < period.start || value >= period.start + period.size) continue;
              const mark = node("i", "gantt-project-boundary");
              mark.style.left = (value - period.start + .5) / period.size * 100 + "%";
              mark.title = label + " · " + dateText(isoDay(value));
              bounds.append(mark);
            }
            axis.append(bounds);
          }
        }
        const scale = node("div", "gantt-ticks");
        for (const tick of scaleTicks) {
          const mark = node("span", "", tick.label); mark.style.left = tick.at * 100 + "%";
          mark.title = dateText(isoDay(tick.day)); scale.append(mark);
        }
        axis.append(scale); head.append(axis);
      } else head.append(...Object.values(dateLabels).map(th));
      const body = table.createTBody();
      function toggleButton(key, label, className = "gantt-fold") {
        const open = !folded.has(key);
        const toggle = button("", className, () => {
          if (folded.has(key)) folded.delete(key); else folded.add(key);
          drawChart();
          [...stage.querySelectorAll("[data-group]")].find(b => b.dataset.group === key)?.focus();
        });
        toggle.dataset.group = key;
        toggle.setAttribute("aria-expanded", String(open));
        toggle.setAttribute("aria-label", (open ? "Свернуть " : "Раскрыть ") + label);
        toggle.append(node("span", "gantt-disclosure", open ? "▾" : "▸"));
        return toggle;
      }
      function groupRow(key, label, depth, caption, meta = "", span = null, expandable = true) {
        const tr = body.insertRow();
        tr.className = "gantt-group";
        const fold = expandable ? toggleButton(key, label) : node("span", "gantt-fold");
        if (depth) fold.style.paddingInlineStart = (12 + depth * 14) + "px";
        if (caption) fold.append(node("span", "gantt-tree-kind", caption));
        fold.append(node("span", "", label));
        if (meta) fold.append(node("small", "", meta));
        tr.insertCell().append(fold);
        const rest = tr.insertCell();
        rest.colSpan = mode === "chart" ? 1 : Object.keys(dateLabels).length;
        if (mode === "chart") {
          const lane = track(true);
          if (span) {
            const start = Math.max(period.start, dayOf(span.start)), end = Math.min(period.start + period.size, dayOf(span.end) + 1);
            lane.title = "Диапазон известных дат работ · " + dateText(span.start) + " — " + dateText(span.end);
            if (end > start) {
              const band = node("span", "gantt-tree-span");
              band.style.left = (start - period.start) / period.size * 100 + "%";
              band.style.width = (end - start) / period.size * 100 + "%";
              lane.append(band);
            }
          }
          rest.append(lane);
        }
      }
      if (grouping === "tree") {
        const entries = treeRows(plan, scope, folded);
        const all = treeRows(plan, scope, new Set());
        const spans = branchDates(all, Object.keys(dateLabels));
        scopeRows = [...new Map(all.filter(e => e.row && !e.contextOnly).map(e => [e.id, e.row])).values()];
        for (const entry of entries) {
          if (entry.row) taskRow(entry.row, entry);
          else groupRow(entry.key, entry.title, entry.level, {objective:"Цель", result:"КР"}[entry.kind],
                        entry.readiness != null ? entry.readiness.replace(".", ",") + "%" : "", spans.get(entry.key), entry.expandable);
        }
      } else {
        const groups = new Map();
        for (const row of scopeRows) {
          const key = grouping === "none" ? "" : row[grouping] || "";
          if (!groups.has(key)) groups.set(key, []);
          groups.get(key).push(row);
        }
        for (const [key, rows] of groups) {
          if (grouping !== "none") {
            const groupId = grouping + "/" + key;
            const label = rows[0][grouping + "Title"] || (grouping === "project" ? "Без проекта" : "Без направления");
            groupRow(groupId, label, 0, "", String(rows.length));
            if (folded.has(groupId)) continue;
          }
          for (const row of rows) taskRow(row);
        }
      }
      function taskRow(row, entry) {
        const tr = body.insertRow();
        const title = tr.insertCell(); title.className = "gantt-row-name";
        if (row.missing && !closed) {
          title.append(node("span", "gantt-missing", "Карточка недоступна"));
          tr.insertCell().colSpan = mode === "chart" ? 1 : Object.keys(dateLabels).length;
          return;
        }
        if (entry) {
          title.style.paddingInlineStart = Math.min(entry.level, 10) * 12 + "px";
          if (entry.expandable) title.append(toggleButton(entry.key, row.title, "gantt-tree-toggle"));
          if (entry.reference) {
            const ref = button("↗ " + (row.title || row.id), "gantt-tree-reference", () => choose(row.id));
            ref.title = "Открыть общую работу";
            title.append(ref);
            const cell = tr.insertCell();
            cell.colSpan = mode === "chart" ? 1 : Object.keys(dateLabels).length;
            cell.append(node("small", "gantt-context", entry.contextOnly ? "Родительская работа" : "Общая работа"));
            return;
          }
        }
        tr.dataset.task = row.id;
        const task = button(row.title || row.id, "gantt-task", () => choose(row.id));
        if (entry?.resultId && entry.linkedToResult === false) {
          task.append(node("small", "gantt-tree-unlinked", "Без связи с КР"));
        }
        if (row.missing) task.append(node("small", "gantt-missing", "Карточка была недоступна"));
        if (row.kind === "milestone") {
          tr.classList.add("gantt-milestone");
          task.prepend(node("span", "gantt-milestone-label", "Веха"));
        }
        task.addEventListener("keydown", event => {
          const allRows = [...body.querySelectorAll(".gantt-task")], i = allRows.indexOf(task);
          let next;
          if (event.key === "ArrowDown") next = allRows[i + 1];
          else if (event.key === "ArrowUp") next = allRows[i - 1];
          else return;
          event.preventDefault(); next?.focus(); if (next) next.click();
        });
        title.append(task);
        if (mode === "list") {
          for (const field of Object.keys(dateLabels)) tr.insertCell().textContent = row[field] ? dateText(row[field]) : "—";
        } else {
          const lane = track(false);
          const schedule = Object.entries(dateLabels).filter(([field]) => row[field])
            .map(([field, label]) => label + " " + dateText(row[field])).join(" · ");
          task.setAttribute("aria-label", (row.kind === "milestone" ? "Веха. " : "") + (row.title || row.id) + ". " + (schedule || "Без дат"));
          lane.title = schedule || "Без дат";
          const signs = {earlier: "← Раньше", later: "Позже →", outside: "За пределами окна", undated: "Без дат", invalid: "Проверить даты"};
          for (const mark of scheduleMarks(row, period)) {
            const symbol = node("span", "gantt-mark gantt-" + mark.kind, signs[mark.kind]);
            if (mark.at !== undefined) symbol.style.left = mark.at * 100 + "%";
            if (dateLabels[mark.kind]) symbol.title = dateLabels[mark.kind] + " · " + dateText(row[mark.kind]);
            if (mark.kind === "range") {
              symbol.style.width = (mark.end - mark.at) * 100 + "%";
              symbol.classList.toggle("cut-start", mark.before); symbol.classList.toggle("cut-end", mark.after);
            }
            lane.append(symbol);
            if (!closed && (mark.kind === "due" || mark.kind === "forecast")) {
              const grip = node("span", "gantt-grip grip-" + mark.kind);
              grip.title = symbol.title + " · перетащить";
              grip.style.left = symbol.style.left;
              grip.addEventListener("pointerdown", event => dragDate(event, grip, lane, row, mark.kind));
              lane.append(grip);
            }
          }
          tr.insertCell().append(lane);
        }
        tr.addEventListener("click", event => { if (!event.target.closest("button,.gantt-grip")) choose(row.id); });
      }
      function track(group) {
        const lane = node("div", group ? "gantt-lane gantt-group-lane" : "gantt-lane");
        const deadline = dayOf(plan.project?.due);
        if (deadline !== null && deadline >= period.start && deadline < period.start + period.size) {
          const line = node("span", "gantt-project-deadline");
          line.style.left = (deadline - period.start + .5) / period.size * 100 + "%";
          line.title = "Срок проекта · " + dateText(plan.project.due);
          lane.append(line);
        }
        if (scaleTicks.length > 1) lane.style.backgroundSize = (scaleTicks[1].at * 100) + "% 100%";
        if (todayOnScale >= 0 && todayOnScale < 1) {
          const line = node("span", "gantt-now");
          line.style.left = (todayOnScale * 100) + "%"; line.title = cursorLabel + " · " + shortDay(today); lane.append(line);
        }
        return lane;
      }
      function dragDate(event, grip, lane, row, field) {
        if (!event.isPrimary || event.button !== 0) return;
        const initialX = event.clientX, width = lane.getBoundingClientRect().width;
        if (!discard()) return;
        ++refreshVersion; dragging = true;
        grip.setPointerCapture(event.pointerId);
        const tip = node("span", "gantt-drag-tip");
        let moved = false, newDate = null;
        function move(e) {
          if (Math.abs(e.clientX - initialX) < 4 && !moved) return;
          moved = true;
          const offset = Math.round((e.clientX - initialX) / width * period.size);
          const proposed = dayOf(row[field]) + offset;
          if (proposed < dayOf("0001-01-01") || proposed > dayOf("9999-12-31")) return;
          newDate = isoDay(proposed);
          tip.textContent = dateLabels[field] + " · " + dateText(newDate); lane.append(tip);
        }
        function finish(e) {
          dragging = false;
          grip.removeEventListener("pointermove", move);
          grip.removeEventListener("pointerup", finish);
          grip.removeEventListener("pointercancel", finish);
          tip.remove();
          if (e.type === "pointerup") choose(row.id, moved && newDate ? {field, value: newDate} : undefined);
        }
        grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", finish); grip.addEventListener("pointercancel", finish);
      }
      if (mode === "chart") {
        const sheet = node("div", "gantt-chart-sheet");
        sheet.append(table); board.append(sheet);
        sizing.disconnect(); sizing.observe(sheet);
      } else { sizing.disconnect(); board.append(table); }
      if (!scopeRows.length) board.append(node("p", "gantt-empty", scope ? "В выбранной группе пока нет работ." : projectView ? "В проекте пока нет работ." : "В этом плане пока нет задач."));
      const legend = node("div", "gantt-legend");
      if (mode === "chart") {
        const markers = [["range", "Плановый период"], ...Object.entries(dateLabels), ["now", cursorLabel + " · " + shortDay(today)]];
        if (dayOf(plan.project?.due) !== null) markers.push(["project-deadline", "Срок проекта · " + dateText(plan.project.due)]);
        for (const [key, label] of markers) {
          const entry = node("span", "gantt-legend-item");
          entry.append(node("i", "legend-" + key), node("span", "", label)); legend.append(entry);
        }
      }
      stage.replaceChildren(controls, board, legend);
      markSelection();
    }

    function drawInspector() {
      inspector = null;
      if (closed && selected) {
        detailPlace.replaceChildren(snapshotInspector(ui, {plan, row: plan.items.find(r => r.id === selected),
          root, urlFor, deselect: () => choose(null)}));
      } else if (selected && detail) {
        const membership = projectView ? {projectId: id} : {planId};
        const rowUI = {...ui, api: (path, payload) => api(path,
          payload && path.startsWith("plan/") ? {...payload, ...membership} : payload)};
        inspector = taskInspector(rowUI, {task: detail, row: plan.items.find(r => r.id === selected), plan, root,
          refresh, deselect: () => choose(null), selectRelated: async id => {
            const row = plan.items.find(item => item.id === id);
            if (!row) return;
            if (grouping === "tree") folded.clear();
            else folded.delete(grouping + "/" + (row[grouping] || ""));
            scope = "";
            drawChart();
            await choose(id);
            const tr = [...stage.querySelectorAll("tr[data-task]")].find(tr => tr.dataset.task === id);
            if (tr && !window.matchMedia("(max-width: 600px)").matches) tr.scrollIntoView({block: "nearest"});
          }});
        detailPlace.replaceChildren(inspector.element);
        if (!projectView && plan.items.length > 1) {
          const row = plan.items.find(item => item.id === selected);
          const box = node("details", "work-panel gantt-parent-editor");
          const label = plan.items.find(item => item.id === row?.parentId)?.title;
          box.append(node("summary", "text-link", label ? "Подработа · " + label : "Вложенность работы"));
          box.addEventListener("toggle", () => {
            if (!box.open || box.querySelector("form")) return;
            const form = node("form", "work-tools");
            const parents = node("select");
            parents.append(option("", "Верхний уровень"));
            for (const other of plan.items) {
              if (other.id !== row.id && !other.missing) parents.append(option(other.id,
                other.title + (other.projectTitle ? " · " + other.projectTitle : "")));
            }
            parents.value = row.parentId || "";
            const save = node("button", "button button-secondary", "Сохранить");
            save.type = "submit";
            const status = node("span"); status.setAttribute("role", "status");
            form.append(ui.labelled("Родительская работа в этом плане", parents), save, status);
            form.addEventListener("submit", async e => {
              e.preventDefault(); save.disabled = true;
              try {
                await api("plan/parent", {id: planId, taskId: row.id, parentId: parents.value || null, revision: plan.revision});
                await refresh("Вложенность сохранена.");
              } catch (error) { status.replaceChildren(notice(error.message)); save.disabled = false; }
            });
            box.append(form);
          });
          detailPlace.append(box);
        }
      } else {
        const overview = node("section", "gantt-overview");
        const summary = node("div");
        const rows = scopeRows.filter(r => !r.missing);
        const milestones = rows.filter(r => r.kind === "milestone").length;
        const totals = [counted(rows.length - milestones, ["задача", "задачи", "задач"])];
        if (milestones) totals.push(counted(milestones, ["веха", "вехи", "вех"]));
        summary.append(node("h2", "", scope ? "Работы по фильтру" : closed ? "Итог цикла" : projectView ? "Работы проекта" : "Обзор плана"), node("p", "", totals.join(" · ")));
        if (rows.length) summary.append(node("small", "", closed ? "Выберите работу для просмотра сохранённых дат." :
          "Выберите строку, чтобы открыть сводку и события."));
        const nearest = rows.filter(r => !r.completed && r.due && dayOf(r.due) >= today).sort((a, b) => a.due.localeCompare(b.due))[0];
        const next = node("div");
        next.append(node("h3", "", closed ? "Ближайший срок на дату закрытия" : "Ближайший согласованный срок"));
        if (nearest) next.append(button(nearest.title, "gantt-action", () => choose(nearest.id)), node("small", "", dateText(nearest.due)));
        else next.append(node("p", "gantt-empty", "Предстоящие сроки не указаны."));
        const undated = rows.filter(r => Object.keys(dateLabels).every(field => !r[field]));
        const open = node("div"); open.append(node("h3", "", "Без дат"));
        if (undated.length) {
          for (const row of undated) open.append(button(row.title, "gantt-action", () => choose(row.id)));
        } else open.append(node("p", "gantt-empty", "Таких задач нет."));
        overview.append(summary, next, open); detailPlace.replaceChildren(overview);
      }
    }
    if (selected && plan.items.some(r => r.id === selected && (closed || !r.missing))) {
      if (!closed) detail = await readDetail(selected);
    }
    else if (selected) setSelected(null);
    if (view.isConnected) { drawChart(); drawInspector(); }
  };
}
