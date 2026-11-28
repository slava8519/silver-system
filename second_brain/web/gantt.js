import {calendarYear, dateText, dayOf, fitWindow, intervalEnd, isoDay, monthBands, progressText, progressValue, scheduleMarks, scheduleState, shortDay, tickAlignments, ticks, timelineDates, todayDay, visibleFill, windowTitle} from "./gantt-axis.js";
import {cardLinks, rowInspector} from "./gantt-detail.js";
import {newWorkForm} from "./gantt-dependencies.js";
import {archiveStamp, closedDay, closedStamp, cyclePreview, snapshotInspector} from "./gantt-cycle.js";

import {branchDates, initialFolds, kindPrefix, revealTask, treeRows} from "./gantt-tree.js";
import {okrLink, openOKR} from "./gantt-okr.js";
import {layerPicker, positionPicker, readLayers} from "./gantt-layers.js";
import {describeRow, recordedSpan, selectionParams} from "./gantt-rows.js";
import {activityCatalog} from "./project-activity.js";
import {planProjects} from "./project-participation.js";
import {projectUpdatesPanel} from "./project-updates.js";

function urlFor(id, task, scope, group = false) {
  const params = selectionParams(task, scope, group);
  return "#plan/" + encodeURIComponent(id) + (params.size ? "?" + params : "");
}
const endpoint = (path, id) => path + "?id=" + encodeURIComponent(id);

export function planningView(ui) {
  const {node, link, button, option, counted, api, notice, go} = ui;
  const layers = readLayers();
  let dispose = () => {};
  return async function open(view, id, selectedId, page) {
    dispose();
    if (page === "okr" || page === "activity" || (page === "project-gantt" && !id)) return openOKR(ui, view, id, urlFor);
    if (!id) return activityCatalog(ui, view, "plans");
    const projectView = page === "project-gantt";
    const planId = projectView ? null : id;
    const source = endpoint(projectView ? "plan/project-gantt" : "plan", id);
    const chartURL = (key, scope) => {
      const group = !!describeRow(plan, key);
      return projectView ? "#project-gantt/" + encodeURIComponent(id)
        + (key ? "?" + selectionParams(key, null, group) : "") : urlFor(planId, key, scope, group);
    };
    const life = new AbortController();
    dispose = () => life.abort();
    const [catalog, initial] = await Promise.all([projectView ? null : api("plans"), api(source)]);
    if (!view.isConnected) return;
    view.classList.add("gantt-workspace");
    const {name: vault, root} = catalog || initial;
    const query = new URLSearchParams(location.hash.split("?")[1]);
    let plan = initial, selected = selectedId || query.get("row"), detail = null, inspector = null;
    const rootKey = (projectView ? "project/" : "plan/") + id;
    const closed = !projectView && plan.status === "closed";
    const archive = closed ? archiveStamp(plan) : null;
    const today = archive?.date ? dayOf(archive.date) : closed ? closedDay(plan.closedAt) : todayDay();
    const statusDay = closed && !archive?.date && (archive || !plan.closedAt) ? null : today;
    const cursorLabel = archive?.label || (closed ? "Закрытие" : "Сегодня");
    let grouping = projectView ? "none" : (plan.objectives?.length || plan.items.some(row => row.parentId)) ? "tree" : "direction";
    if (describeRow(plan, selected)?.kind === "group" && selected !== "group/outside") grouping = selected.split("/")[1];
    const fit = () => fitWindow(plan.items, today, plan.project);
    let period = fit(), mode = "chart", refreshVersion = 0;
    let scope = new URLSearchParams(location.hash.split("?")[1]).get("okr") || "", scopeRows = plan.items;
    let dragging = false, overviewOpen = false;
    const folded = initialFolds(plan, scope, selected);
    const stage = node("section", "gantt-stage");
    const [layerButton, layerPanel] = layerPicker(node, layers, cursorLabel, projectView, applyLayers);
    const narrowToolbar = window.matchMedia("(max-width: 1024px), (max-height: 590px)");
    window.addEventListener("resize", arrangePicker, {signal: life.signal});
    let fitTickLabels = () => {};
    const sizing = new ResizeObserver(() => { fitTickLabels(); drawLinks(); });
    life.signal.addEventListener("abort", () => sizing.disconnect(), {once: true});
    const createPlace = node("div", "gantt-create-place");
    const detailPlace = node("div", "gantt-detail-place");
    detailPlace.setAttribute("role", "region");
    detailPlace.setAttribute("aria-label", "Подробности Ганта");
    detailPlace.tabIndex = 0;
    const feedback = node("p", "gantt-status");
    feedback.setAttribute("role", "status");
    const actions = node("div", "work-tools gantt-plan-actions");
    if (!projectView) {
      const chooser = node("select");
      chooser.setAttribute("aria-label", "Выбрать план");
      for (const p of catalog.items) chooser.append(option(p.id, p.status === "closed" ? p.title + " · закрыт" : p.title));
      chooser.value = planId;
      chooser.addEventListener("change", () => {
        if (discard()) go(urlFor(chooser.value));
        else chooser.value = planId;
      });
      actions.append(chooser);
    }
    const refreshButton = button("Обновить", "button button-secondary", () => {
      if (discard()) refresh("Данные обновлены.").catch(report);
    });
    actions.append(refreshButton);
    const intro = node("header", "gantt-intro");
    const connector = node("div", "gantt-heading");
    const titleLine = node("div", "gantt-title-line");
    const heading = node("h1", "work-title", plan.title);
    const total = node("span", "gantt-total");
    titleLine.append(heading, total);
    const context = node("div", "gantt-plan-context");
    context.id = "gantt-plan-context";
    context.hidden = query.get("projects") !== "1" || projectView;
    const disclosure = button(projectView ? "О проекте" : "О плане", "gantt-tool gantt-context-toggle", () => {
      context.hidden = !context.hidden;
      disclosure.setAttribute("aria-expanded", String(!context.hidden));
    });
    disclosure.setAttribute("aria-controls", context.id);
    disclosure.setAttribute("aria-expanded", String(!context.hidden));
    connector.append(titleLine, disclosure);
    const projectFacts = node("p", "gantt-project-facts");
    const projectUpdates = node("details", "gantt-project-updates");
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
        const project = {...plan.project, vaultName:vault, projectUpdates:{...updates, events:updates.events.slice(0,1)}};
        projectUpdates.append(node("summary", "text-link", "Коротко о проекте"), projectUpdatesPanel(ui, project));
      }
      projectUpdates.hidden = !projectUpdates.childElementCount;
    }
    const navigation = node("div", "work-tools gantt-navigation");
    if (projectView) {
      showProjectFacts();
      const card = ui.compact ? cardLinks(ui, root)("Карточка проекта ↗", plan.path)
        : link("Карточка проекта", "#record/" + encodeURIComponent(id), "text-link");
      navigation.append(link("← Проекты и Гант", "#activity", "back-link"), card);
    } else navigation.append(link("← Все планы", "#activity?view=plans", "back-link"), okrLink(ui, planId));
    if (!ui.compact) navigation.append(link("Показать связи", "#map?" + new URLSearchParams({id}), "text-link"));
    context.append(node("span", "gantt-vault-name", vault), actions, navigation);
    if (projectView) context.append(projectFacts, projectUpdates);
    if (!projectView) {
      context.append(planProjects(ui, planId, {open:query.get("projects") === "1",
        currentPlan:() => plan, canEdit:discard, refreshChart:() => refresh()}));
    }
    intro.append(connector, context);
    view.append(intro, createPlace, stage, detailPlace, feedback);
    const cycleStatus = closed ? node("div", "cycle-heading-status") : null;
    const cycleDate = closed ? node("time", "cycle-stamp") : null;
    function showCycleLinks() {
      const openNote = cardLinks(ui, root);
      cycleDate.textContent = archive?.text || "Цикл закрыт · " + closedStamp(plan.closedAt);
      cycleDate.dateTime = archive?.date || plan.closedAt;
      cycleStatus.replaceChildren(openNote("Снимок в Obsidian ↗", plan.path));
      for (const issue of plan.weeklyReviews || []) {
        cycleStatus.append(openNote(issue.title + " ↗", issue.path));
      }
      if (plan.weeklyReviewError) cycleStatus.append(node("small", "", plan.weeklyReviewError));
    }
    if (closed) {
      titleLine.append(cycleDate);
      showCycleLinks();
      context.append(cycleStatus);
    } else if (!projectView) {
      const close = button("Закрыть цикл…", "button button-secondary", () => {
        if (!discard()) return;
        stage.hidden = detailPlace.hidden = close.disabled = true;
        const panel = cyclePreview(ui, planId, root, () => {
          createPlace.replaceChildren();
          stage.hidden = detailPlace.hidden = close.disabled = false;
          (context.hidden ? disclosure : close).focus();
        }, () => go(chartURL(selected)));
        createPlace.replaceChildren(panel); panel.querySelector("h2").focus();
      });
      actions.append(close);
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
    function journalFor(group) {
      const address = {planId, kind:group.kind, id:group.id};
      return {
        current: () => api("plan/row?" + new URLSearchParams({...address, limit:5})),
        workEvents: offset => api("plan/row/work-events?" + new URLSearchParams({...address, offset, limit:5})),
        events: async offset => {
          const page = await api("plan/row/events?" + new URLSearchParams({...address, offset, limit:5}));
          return {items:page.latest, revision:page.revision, truncated:page.truncated};
        },
        edit: payload => api("plan/row/edit", {...payload, ...address})
      };
    }
    function hasSelection(data, key) {
      return !!describeRow(data, key) || data.items.some(row => row.id === key && (closed || !row.missing));
    }
    async function readDetail(id, data = plan) {
      const group = describeRow(data, id);
      if (group?.kind === "project") return data.project;
      if (group) return journalFor(group).current();
      if (closed) return null;
      const [task, relations] = await Promise.all([api(endpoint("plan/task", id)), api(endpoint("plan/dependencies", id))
        .catch(error => ({error: error.message}))]);
      return {...task, relations};
    }
    async function refresh(message = "") {
      const sequence = ++refreshVersion;
      const fresh = await api(source);
      if (!view.isConnected || sequence !== refreshVersion) return;
      if (!projectView && fresh.status !== plan.status) { go(chartURL(selected)); return; }
      const task = selected && hasSelection(fresh, selected) ? await readDetail(selected, fresh).catch(error => {
        if (error.status !== 404) throw error;
        return null;
      }) : null;
      if (!view.isConnected || sequence !== refreshVersion || (!message && inspector?.editing())) return;
      if (!message && detail?.revision === task?.revision && JSON.stringify(detail?.relations) === JSON.stringify(task?.relations) && JSON.stringify(plan) === JSON.stringify(fresh)) return;
      plan = fresh;
      if (closed) showCycleLinks();
      if (projectView) showProjectFacts();
      if (!period.zoom) period = fit();
      heading.textContent = plan.title;
      if (selected && ((!closed && !task) || !hasSelection(plan, selected))) setSelected(null);
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
    async function choose(id, proposal, keepFocus = false) {
      if (createPlace.firstChild) { discard(); return; }
      const wasEditing = inspector?.editing();
      if (wasEditing && (id !== selected || proposal) && !discard()) return;
      if (id === selected && (detail || closed) && !proposal) return;
      const previous = selected;
      const sequence = ++refreshVersion;
      try {
        const next = id ? await readDetail(id) : null;
        if (!view.isConnected || sequence !== refreshVersion) return;
        setSelected(id);
        detail = next;
        feedback.textContent = "";
        const row = plan.items.find(item => item.id === id);
        if (row && ![...stage.querySelectorAll("tr[data-task]")].some(tr => tr.dataset.task === id)) {
          folded.delete(rootKey);
          if (grouping === "tree") revealTask(treeRows(plan, scope, new Set()), id, folded);
          else folded.delete(grouping + "/" + (row[grouping] || ""));
          drawChart();
        } else markSelection();
        drawInspector();
        if (row) stage.querySelector(".is-selected")?.scrollIntoView({block:"nearest"});
        detailPlace.scrollTop = 0;
        if (proposal) inspector.dateForm(proposal);
        else if (!id && previous) {
          [...stage.querySelectorAll("[data-selection]")].find(control => control.dataset.selection === previous)?.focus({preventScroll:true});
        } else if (id && !keepFocus && window.matchMedia("(max-width: 600px)").matches) {
          detailPlace.querySelector("h2")?.focus({preventScroll: true});
        }
      } catch (error) { report(error); }
    }
    function markSelection() {
      const predecessors = new Set(detail?.relations?.predecessors?.map(item => item.id));
      const successors = new Set(detail?.relations?.successors?.map(item => item.id));
      for (const control of stage.querySelectorAll(".gantt-group-select")) {
        const active = control.dataset.selection === selected;
        control.setAttribute("aria-pressed", String(active));
        control.closest("tr").classList.toggle("is-selected", active);
      }
      for (const tr of stage.querySelectorAll("[data-task]")) {
        const active = tr.dataset.task === selected;
        tr.classList.toggle("is-selected", active);
        const task = tr.querySelector(".gantt-task");
        task.setAttribute("aria-pressed", String(active));
        task.querySelector(".gantt-related")?.remove();
        const relation = predecessors.has(tr.dataset.task) ? "Предшественник" : successors.has(tr.dataset.task) ? "Следом" : "";
        if (relation && (mode === "list" || layers.has("relations"))) task.append(node("small", "gantt-related", relation));
      }
      drawLinks();
    }
    function drawLinks() {
      const sheet = stage.querySelector(".gantt-chart-sheet");
      if (!sheet) return;
      sheet.querySelector("svg")?.remove();
      if (!layers.has("relations") || !detail?.relations || detail.relations.error) return;
      // Anchor only to recorded dates on rows visible in the current grouping and window.
      const rows = new Map([...sheet.querySelectorAll("tr[data-task]")].map(tr => [tr.dataset.task, tr]));
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("aria-hidden", "true");
      const bounds = sheet.getBoundingClientRect();
      function point(id, finish) {
        const tr = rows.get(id);
        if (!tr) return null;
        const fields = finish || tr.classList.contains("gantt-milestone") ? ["completed", "forecast", "due", "baseline"] : ["start", "range"];
        for (const field of fields) {
          const mark = tr.querySelector(".gantt-" + field);
          if (!mark || mark.classList.contains("cut-start")) continue;
          // A dependency can be visible while its date symbol is hidden.
          const rect = mark.parentElement.getBoundingClientRect();
          return {x: rect.left - bounds.left + rect.width * parseFloat(mark.style.left) / 100, y: rect.top - bounds.top + rect.height / 2};
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
    function applyLayers() {
      for (const item of stage.querySelectorAll("[data-layer]")) {
        item.hidden = !layers.has(item.dataset.layer);
      }
      const legend = stage.querySelector(".gantt-legend");
      if (legend && !legend.querySelector(".gantt-legend-item:not([hidden])")) legend.hidden = true;
      stage.classList.toggle("gantt-show-notes", layers.has("notes"));
      fitTickLabels();
      markSelection();
    }
    function arrangePicker() {
      const toolbar = layerButton.parentElement;
      if (!toolbar?.isConnected) return;
      const after = toolbar.querySelector(narrowToolbar.matches ? ".gantt-mode-switch" : ".gantt-toolbar-end");
      if (after.nextElementSibling !== layerButton) {
        const focused = document.activeElement, open = layerPanel.matches(":popover-open");
        after.after(layerButton);
        if (open || focused === layerButton) {
          const rect = layerButton.getBoundingClientRect(), bounds = toolbar.getBoundingClientRect();
          if (rect.left < bounds.left) toolbar.scrollLeft += rect.left - bounds.left;
          else if (rect.right > bounds.right) toolbar.scrollLeft += rect.right - bounds.right;
          if (open && !layerPanel.matches(":popover-open")) layerPanel.showPopover();
          focused.focus({preventScroll: true});
        }
      }
      if (layerPanel.matches(":popover-open")) {
        const trigger = layerButton.getBoundingClientRect(), bounds = toolbar.getBoundingClientRect();
        if (trigger.right <= bounds.left || trigger.left >= bounds.right) layerPanel.hidePopover();
        else positionPicker(layerButton, layerPanel);
      }
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
      const lastBoard = stage.querySelector(".gantt-board");
      const scrollTop = lastBoard?.scrollTop || 0, scrollLeft = lastBoard?.scrollLeft || 0;
      const toolbarLeft = stage.querySelector(".gantt-toolbar")?.scrollLeft || 0;
      const pickerOpen = layerPanel.matches(":popover-open");
      const pickerFocus = layerPanel.contains(document.activeElement) || document.activeElement === layerButton ? document.activeElement : null;
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
      group.addEventListener("change", () => {
        if (!discard()) { group.value = grouping; return; }
        updateChart(() => { grouping = group.value; }, "group");
        if (describeRow(plan, selected) && !hasVisibleSelection()) choose(null);
      });
      function hasVisibleSelection() {
        return [...stage.querySelectorAll("[data-selection]")].some(control => control.dataset.selection === selected);
      }
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
        filter.append(option("", "Все цели и работы"));
        const linked = new Set();
        let included;
        for (const goal of plan.objectives) {
          const group = node("optgroup");
          group.label = goal.title;
          const scopeName = (kind, title) => [kindPrefix(kind, title), title].filter(Boolean).join(" · ");
          group.append(option(goal.id, scopeName("Цель", goal.title)));
          const goalTasks = new Set();
          for (const kr of goal.results) {
            group.append(option(kr.id, scopeName("КР", kr.title)));
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
          if (selected && !(describeRow(plan, selected) ? hasVisibleSelection() : scopeRows.some(row => row.id === selected))) choose(null);
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
          travel(cursorLabel, archive ? "Показать дату архивной записи" : closed ? "Показать дату закрытия" : "Показать сегодня", "today", () => {
            if (period.zoom === "year") { period = calendarYear(today); return; }
            const size = period.zoom ? period.size : 14;
            period = {start: today - Math.floor(size / 2), size, zoom: String(size)};
          }), travel("›", "Следующее окно", "next", () => shift(1)));
        controls.append(calendarTools, layerButton, layerPanel);
        controls.addEventListener("scroll", arrangePicker);
      }
      const board = node("div", period.zoom ? "gantt-board" : "gantt-board gantt-fit");
      board.tabIndex = 0;
      board.setAttribute("role", "region"); board.setAttribute("aria-label", projectView ? "Работы проекта" : "Задачи плана");
      const table = node("table", mode === "chart" ? "gantt-grid" : "gantt-list-grid");
      const caption = node("caption", "okr-sr", "Готовность работ и календарные даты. Оценочные значения отмечены знаком приблизительного равенства.");
      table.append(caption);
      const head = table.createTHead().insertRow();
      const th = title => { const cell = node("th", "", title); cell.scope = "col"; return cell; };
      const percentHead = th("Готовность");
      percentHead.className = "gantt-percent";
      head.append(th("Работа"), percentHead);
      fitTickLabels = () => {};
      const scaleTicks = ticks(period);
      const todayOnScale = statusDay === null ? -1 : (today - period.start + .5) / period.size;
      const cursorVisible = todayOnScale >= 0 && todayOnScale < 1;
      if (mode === "chart") {
        const axis = th("");
        axis.className = "gantt-axis";
        const title = windowTitle(period), months = monthBands(period);
        if (months.length) {
          const band = node("div", "gantt-axis-months");
          axis.title = title;
          axis.setAttribute("aria-label", title);
          for (const [index, month] of months.entries()) {
            const cell = node("span", "gantt-axis-month");
            cell.style.left = month.at * 100 + "%";
            cell.style.width = month.width * 100 + "%";
            cell.title = month.label + " " + month.year;
            const label = node("b", "", month.label);
            if (!index || month.year !== months[index - 1].year || (index === 1 && months[0].width * period.size < 7)) {
              label.append(node("small", "", " " + month.year));
            }
            cell.append(label); band.append(cell);
          }
          axis.append(band);
        } else axis.append(node("div", "gantt-axis-title", title));
        // One shared gradient gives every lane the exact tick positions,
        // including the unequal spacing of calendar months in the annual view.
        const grid = new Map(scaleTicks.map(tick => [tick.at, "var(--gantt-grid-line)"]));
        for (const month of months) grid.set(month.at, "var(--gantt-month-line)");
        const stops = [...grid].sort(([a], [b]) => a - b).map(([at, color]) => {
          const left = at * 100 + "%", right = "calc(" + left + " + 1px)";
          return `transparent ${left}, ${color} ${left} ${right}, transparent ${right}`;
        });
        table.style.setProperty("--gantt-grid-lines", "linear-gradient(90deg, " + stops.join(", ") + ")");
        if (projectView) {
          const first = dayOf(plan.project.projectStart), last = dayOf(plan.project.due);
          if (first !== null || last !== null) {
            const bounds = node("div", "gantt-project-range");
            bounds.dataset.layer = "project";
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
        const scale = node("div", months.length ? "gantt-ticks gantt-week-ticks" : "gantt-ticks");
        let stamp;
        if (cursorVisible) {
          stamp = node("time", "gantt-cursor-stamp", cursorLabel + " " + shortDay(today));
          stamp.dateTime = isoDay(today);
          stamp.dataset.layer = "now";
          stamp.style.left = (todayOnScale * 100) + "%";
          stamp.classList.toggle("align-end", todayOnScale > .5);
          scale.append(stamp);
        }
        const labels = scaleTicks.map(tick => {
          const mark = node("span");
          mark.style.left = tick.at * 100 + "%";
          const label = node("b", "", tick.label);
          mark.append(label);
          mark.title = dateText(isoDay(tick.day));
          scale.append(mark);
          return label;
        });
        fitTickLabels = () => {
          const widths = labels.map(label => label.getBoundingClientRect().width);
          const stampRect = stamp && !stamp.hidden && stamp.getBoundingClientRect();
          const offset = scale.getBoundingClientRect().left;
          const occupied = stampRect && [stampRect.left - offset, stampRect.right - offset];
          const sides = tickAlignments(scaleTicks, months, scale.clientWidth, widths, occupied);
          labels.forEach((label, i) => { label.dataset.side = sides[i]; });
        };
        axis.append(scale); head.append(axis);
      } else head.append(...Object.values(timelineDates).map(th));
      const body = table.createTBody();
      body.addEventListener("keydown", event => {
        const current = event.target.closest("[data-selection]");
        if (!current || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
        const controls = [...body.querySelectorAll("[data-selection]")];
        const next = controls[controls.indexOf(current) + (event.key === "ArrowDown" ? 1 : -1)];
        event.preventDefault();
        if (next) { next.focus(); choose(next.dataset.selection, undefined, true); }
      });
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
      function percentage(tr, value, estimated, context = "") {
        const cell = tr.insertCell();
        cell.className = "gantt-percent";
        const known = progressValue(value) !== null;
        const text = known ? (estimated ? "≈" : "") + progressText(value) : "—";
        const descriptor = known ? (estimated ? "Оценка готовности" : "Готовность") + " " + progressText(value) : "Готовность не записана";
        cell.textContent = text;
        cell.title = descriptor + (context ? " · " + context : "");
        cell.setAttribute("aria-label", cell.title);
        return cell;
      }
      function progressBand(className, left, right, start, end, value) {
        const band = node("span", "gantt-mark " + className);
        band.dataset.layer = "range";
        band.style.left = left * 100 + "%";
        band.style.width = (right - left) * 100 + "%";
        const filled = visibleFill(start, end, period, value);
        if (filled === null) band.classList.add("is-unknown");
        else {
          const fill = node("span", "gantt-fill");
          fill.style.width = filled + "%";
          band.append(fill);
        }
        return band;
      }
      function groupRow(key, label, depth, caption, meta = "", span = null, expandable = true, value, estimated, selection = key) {
        const tr = body.insertRow();
        tr.className = caption === "КР" ? "gantt-group gantt-result" : "gantt-group gantt-section";
        if (selection === rootKey) tr.classList.add("gantt-root");
        const fold = node("div", "gantt-fold gantt-group-controls");
        fold.style.setProperty("--depth", selection === rootKey ? 0 : Math.min(depth + 1, 6));
        if (expandable) fold.append(toggleButton(key, label, "gantt-group-toggle"));
        else fold.append(node("span", "gantt-fold-space"));
        const pick = button("", "gantt-group-select", () => choose(selection));
        pick.dataset.selection = selection;
        const prefix = kindPrefix(caption, label);
        if (prefix) pick.append(node("span", "gantt-tree-kind", prefix));
        pick.append(node("span", "gantt-group-label", label));
        if (meta) pick.append(node("small", "", meta));
        fold.append(pick);
        tr.insertCell().append(fold);
        tr.addEventListener("click", event => { if (!event.target.closest("button")) choose(selection); });
        percentage(tr, value, estimated);
        const rest = tr.insertCell();
        rest.className = "gantt-cell-track";
        rest.colSpan = mode === "chart" ? 1 : Object.keys(timelineDates).length;
        if (mode === "chart") {
          const lane = track(true);
          if (span) {
            const start = Math.max(period.start, dayOf(span.start)), end = Math.min(period.start + period.size, dayOf(span.end) + 1);
            lane.title = "Диапазон известных дат работ · " + dateText(span.start) + " — " + dateText(span.end);
            if (end > start) {
              lane.append(progressBand("gantt-tree-span", (start - period.start) / period.size,
                (end - period.start) / period.size, span.start, span.end, value));
            }
          }
          if (!span && caption) {
            const note = node("span", "gantt-mark gantt-undated", "Без дат");
            note.dataset.layer = "notes";
            lane.append(note);
          }
          rest.append(lane);
        } else if (span) {
          rest.textContent = dateText(span.start) + " — " + dateText(span.end);
          rest.title = "Диапазон известных дат работ";
        }
      }
      groupRow(rootKey, plan.title, 0, "", "", recordedSpan(plan.items), !!plan.items.length || !!plan.tree?.length,
        plan.progress?.value, plan.progress?.hasEstimates);
      if (grouping === "tree") {
        const entries = treeRows(plan, scope, folded);
        const all = treeRows(plan, scope, new Set());
        const spans = branchDates(all, Object.keys(timelineDates));
        scopeRows = [...new Map(all.filter(e => e.row && !e.contextOnly).map(e => [e.id, e.row])).values()];
        for (const entry of folded.has(rootKey) ? [] : entries) {
          if (entry.row) taskRow(entry.row, entry);
          else groupRow(entry.key, entry.title, entry.level, {objective:"Цель", result:"КР"}[entry.kind],
                        "", spans.get(entry.key), entry.expandable, entry.readiness, entry.hasEstimates, entry.kind === "outside" ? "group/outside" : entry.key);
        }
      } else {
        const groups = new Map();
        for (const row of scopeRows) {
          const key = grouping === "none" ? "" : row[grouping] || "";
          if (!groups.has(key)) groups.set(key, []);
          groups.get(key).push(row);
        }
        for (const [key, rows] of folded.has(rootKey) ? [] : groups) {
          if (grouping !== "none") {
            const groupId = grouping + "/" + key;
            const label = rows[0][grouping + "Title"] || (grouping === "project" ? "Без проекта" : "Без направления");
            groupRow(groupId, label, 0, "", String(rows.length), recordedSpan(rows), true, null, false, "group/" + groupId);
            if (folded.has(groupId)) continue;
          }
          for (const row of rows) taskRow(row);
        }
      }
      function taskRow(row, entry) {
        const tr = body.insertRow();
        const title = tr.insertCell(); title.className = "gantt-row-name";
        title.style.setProperty("--depth", Math.min((entry?.level ?? (grouping === "none" ? 0 : 1)) + 1, 6));
        if (row.missing && !closed) {
          title.append(node("span", "gantt-missing", "Карточка недоступна"));
          percentage(tr, null);
          const cell = tr.insertCell();
          if (mode === "chart") {
            cell.className = "gantt-cell-track";
            cell.append(track(false));
          } else cell.colSpan = Object.keys(timelineDates).length;
          return;
        }
        if (entry) {
          if (entry.expandable) title.append(toggleButton(entry.key, row.title, "gantt-tree-toggle"));
          if (entry.reference) {
            const ref = button("↗ " + (row.title || row.id), "gantt-tree-reference", () => choose(row.id));
            const context = entry.contextOnly ? "Родительская работа" : "Общая работа";
            ref.title = context + " · открыть карточку";
            title.append(ref);
            percentage(tr, row.readiness?.value, row.readiness?.kind === "estimate");
            const cell = tr.insertCell();
            if (mode === "chart") {
              cell.className = "gantt-cell-track";
              const lane = track(false), note = node("span", "gantt-mark gantt-undated", context);
              note.dataset.layer = "notes";
              lane.append(note); cell.append(lane);
            } else {
              cell.colSpan = Object.keys(timelineDates).length;
              cell.append(node("small", "gantt-context", context));
            }
            return;
          }
        }
        tr.dataset.task = row.id;
        const task = button(row.title || row.id, "gantt-task", () => choose(row.id));
        task.dataset.selection = row.id;
        const dependent = entry?.resultId && entry.linkedToResult === false;
        if (dependent) task.title = "Подработа в этой ветви. Её готовность не входит в расчёт КР.";
        if (row.missing) task.append(node("small", "gantt-missing", "Карточка была недоступна"));
        if (row.kind === "milestone") {
          tr.classList.add("gantt-milestone");
          task.prepend(node("span", "gantt-milestone-label", "Веха"));
        }
        title.append(task);
        const measured = row.readiness?.value;
        const state = scheduleState(row, statusDay);
        if (state.kind) tr.classList.add("has-" + state.kind);
        const progress = percentage(tr, measured, row.readiness?.kind === "estimate", state.text);
        if (mode === "list") {
          for (const field of Object.keys(timelineDates)) tr.insertCell().textContent = row[field] ? dateText(row[field]) : "—";
        } else {
          const lane = track(false);
          const schedule = Object.entries(timelineDates).filter(([field]) => row[field])
            .map(([field, label]) => label + " " + dateText(row[field])).join(" · ");
          task.setAttribute("aria-label", (row.kind === "milestone" ? "Веха. " : "") + (row.title || row.id) + ". " + progress.title + ". " + (schedule || "Без дат"));
          lane.title = [progress.title, schedule || "Без дат"].join(" · ");
          const signs = {earlier: "← Раньше", later: "Позже →", outside: "За пределами окна", undated: "Без дат", invalid: "Проверить даты"};
          const marks = scheduleMarks(row, period);
          const range = marks.find(mark => mark.kind === "range");
          if (range && !range.before) marks.push({kind: "start", at: range.at + .5 / period.size});
          for (const mark of marks) {
            const symbol = mark.kind === "range"
              ? progressBand("gantt-range", mark.at, mark.end, row.start, intervalEnd(row), measured)
              : node("span", "gantt-mark gantt-" + mark.kind, signs[mark.kind]);
            symbol.dataset.layer = signs[mark.kind] ? "notes" : mark.kind;
            if (mark.at !== undefined) symbol.style.left = mark.at * 100 + "%";
            if (timelineDates[mark.kind]) symbol.title = timelineDates[mark.kind] + " · " + dateText(row[mark.kind]);
            if (mark.kind === "range") {
              symbol.classList.toggle("cut-start", mark.before); symbol.classList.toggle("cut-end", mark.after);
            }
            lane.append(symbol);
            if (!closed && (mark.kind === "due" || mark.kind === "forecast")) {
              const grip = node("span", "gantt-grip grip-" + mark.kind);
              grip.dataset.layer = mark.kind;
              grip.title = symbol.title + " · перетащить";
              grip.style.left = symbol.style.left;
              grip.addEventListener("pointerdown", event => dragDate(event, grip, lane, row, mark.kind));
              lane.append(grip);
            }
          }
          const cell = tr.insertCell();
          cell.className = "gantt-cell-track";
          cell.append(lane);
        }
        tr.addEventListener("click", event => { if (!event.target.closest("button,.gantt-grip")) choose(row.id); });
      }
      function track(group) {
        const lane = node("div", group ? "gantt-lane gantt-group-lane" : "gantt-lane");
        const deadline = dayOf(plan.project?.due);
        if (deadline !== null && deadline >= period.start && deadline < period.start + period.size) {
          const line = node("span", "gantt-project-deadline");
          line.dataset.layer = "project";
          line.style.left = (deadline - period.start + .5) / period.size * 100 + "%";
          line.title = "Срок проекта · " + dateText(plan.project.due);
          lane.append(line);
        }
        if (cursorVisible) {
          const line = node("span", "gantt-now");
          line.dataset.layer = "now";
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
          tip.textContent = timelineDates[field] + " · " + dateText(newDate); lane.append(tip);
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
      legend.dataset.layer = "legend";
      if (mode === "chart") {
        const markers = [];
        if (body.querySelector('[data-layer="range"]')) markers.push(["range", "Интервал и готовность"]);
        if (body.querySelector(".is-unknown")) markers.push(["unknown", "Готовность не указана", "range"]);
        if (body.querySelector(":is(.has-completion,.has-finished) .gantt-range")) markers.push(["finished", "Завершено", "range"]);
        if (body.querySelector(":is(.has-closed,.has-cancelled) .gantt-range")) markers.push(["terminal", "Закрыто или отменено", "range"]);
        if (body.querySelector(".has-overdue .gantt-range")) markers.push(["overdue", "Дата прошла, завершение не записано", "range"]);
        if (body.querySelector(".has-delay .gantt-range")) markers.push(["delay", "Прогноз позже срока или базы", "range"]);
        for (const [key, label] of Object.entries(timelineDates)) {
          if (body.querySelector(".gantt-mark.gantt-" + key)) markers.push([key, label]);
        }
        if (cursorVisible) markers.push(["now", cursorLabel + " · " + shortDay(today)]);
        if (body.querySelector(".gantt-project-deadline")) markers.push(["project-deadline", "Срок проекта · " + dateText(plan.project.due), "project"]);
        for (const [key, label, layer = key] of markers) {
          const entry = node("span", "gantt-legend-item");
          entry.dataset.layer = layer;
          entry.append(node("i", "legend-" + key), node("span", "", label)); legend.append(entry);
        }
      }
      const overall = progressValue(plan.progress?.value);
      total.textContent = overall === null ? "" : "Готовность плана · " + (plan.progress.hasEstimates ? "≈" : "") + progressText(overall);
      stage.replaceChildren(controls, board, legend);
      arrangePicker();
      controls.scrollLeft = toolbarLeft;
      if (mode === "chart" && pickerOpen && !layerPanel.matches(":popover-open")) layerPanel.showPopover();
      if (pickerFocus?.isConnected) pickerFocus.focus({preventScroll: true});
      applyLayers();
      board.scrollTop = scrollTop;
      board.scrollLeft = scrollLeft;
    }

    function drawInspector() {
      inspector = null;
      const group = describeRow(plan, selected);
      if (group?.kind === "project" && detail) {
        const project = node("section", "gantt-project-inspector");
        const header = node("div", "gantt-pane-heading");
        const close = button("×", "gantt-close", () => choose(null));
        close.setAttribute("aria-label", "Снять выделение");
        const title = node("h2", "", detail.title); title.tabIndex = -1;
        header.append(title, close);
        project.append(header, projectUpdatesPanel(ui, {...detail, vaultName:vault},
          () => refresh("Сводка проекта сохранена.").catch(report)));
        const editing = () => !!project.querySelector("form");
        inspector = {editing, canLeave: () => {
          if (!editing()) return true;
          feedback.textContent = "Сохраните правку или нажмите «Отмена».";
          project.querySelector("textarea")?.focus();
          return false;
        }};
        detailPlace.replaceChildren(project);
      } else if (group && detail) {
        inspector = rowInspector(ui, {task:{...detail, readOnly:closed || detail.readOnly}, group, plan, root,
          refresh, deselect: () => choose(null), selectMember: member => {
            if (!discard()) return;
            if (scope && !scopeRows.some(row => row.id === member)) {
              scope = "";
              drawChart();
            }
            choose(member);
          }, journal:journalFor(group)});
        detailPlace.replaceChildren(inspector.element);
      } else if (closed && selected) {
        detailPlace.replaceChildren(snapshotInspector(ui, {plan, row: plan.items.find(r => r.id === selected),
          root, urlFor, deselect: () => choose(null)}));
      } else if (selected && detail) {
        const membership = projectView ? {projectId: id} : {planId};
        const rowUI = {...ui, api: (path, payload) => api(path,
          payload && path.startsWith("plan/") ? {...payload, ...membership} : payload)};
        inspector = rowInspector(rowUI, {task: detail, row: plan.items.find(r => r.id === selected), plan, root,
          refresh, deselect: () => choose(null), selectRelated: async id => {
            const row = plan.items.find(item => item.id === id);
            if (!row) return;
            if (grouping === "tree") revealTask(plan.tree, id, folded);
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
        const fold = node("details", "gantt-overview-fold");
        const overview = node("section", "gantt-overview");
        const rows = scopeRows.filter(r => !r.missing);
        const milestones = rows.filter(r => r.kind === "milestone").length;
        const totals = [counted(rows.length - milestones, ["задача", "задачи", "задач"])];
        if (milestones) totals.push(counted(milestones, ["веха", "вехи", "вех"]));
        const title = scope ? "Работы по фильтру" : closed ? "Итог цикла" : projectView ? "Работы проекта" : "Обзор плана";
        const summary = node("summary", "", title);
        summary.append(node("span", "gantt-overview-count", totals.join(" · ")));
        const nearest = rows.filter(r => !r.completed && r.due && dayOf(r.due) >= today).sort((a, b) => a.due.localeCompare(b.due))[0];
        const next = node("div");
        next.append(node("h3", "", archive ? "Ближайший срок на дату архивной записи" : closed ? "Ближайший срок на дату закрытия" : "Ближайший согласованный срок"));
        if (nearest) next.append(button(nearest.title, "gantt-action", () => choose(nearest.id)), node("small", "", dateText(nearest.due)));
        else next.append(node("p", "gantt-empty", "Предстоящие сроки не указаны."));
        const undated = rows.filter(r => Object.keys(timelineDates).every(field => !r[field]));
        const open = node("div"); open.append(node("h3", "", "Без дат"));
        if (undated.length) {
          for (const row of undated) open.append(button(row.title, "gantt-action", () => choose(row.id)));
        } else open.append(node("p", "gantt-empty", "Таких задач нет."));
        overview.append(next, open);
        fold.append(summary, overview);
        fold.open = overviewOpen;
        fold.addEventListener("toggle", () => { if (fold.isConnected) overviewOpen = fold.open; });
        detailPlace.replaceChildren(fold);
      }
    }
    if (selected && hasSelection(plan, selected)) {
      detail = await readDetail(selected);
    }
    else if (selected) setSelected(null);
    if (view.isConnected) {
      drawChart();
      drawInspector();
      if (selected) stage.querySelector(".is-selected")?.scrollIntoView({block:"center"});
    }
  };
}
