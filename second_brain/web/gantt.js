import {dateLabels, dateText, dayOf, fitWindow, isoDay, scheduleMarks, shortDay, ticks, todayDay, windowTitle} from "./gantt-axis.js";
import {cardLinks, taskInspector} from "./gantt-detail.js";
import {milestoneForm} from "./gantt-dependencies.js";
import {closedDay, closedStamp, cyclePreview, snapshotInspector} from "./gantt-cycle.js";

import {okrLink, openOKR} from "./gantt-okr.js";

const urlFor = (id, task) => "#plan/" + encodeURIComponent(id) + (task ? "?" + new URLSearchParams({task}) : "");
const endpoint = (path, id) => path + "?id=" + encodeURIComponent(id);

export function planningView(ui) {
  const {node, link, button, option, counted, api, pageHead, notice, go} = ui;
  let dispose = () => {};
  return async function open(view, id, selectedId, page) {
    dispose();
    if (page === "okr") return openOKR(ui, view, id, urlFor);
    const planId = id;
    const life = new AbortController();
    dispose = () => life.abort();
    const [catalog, initial] = await Promise.all([api("plans"), planId ? api(endpoint("plan", planId)) : null]);
    if (!view.isConnected) return;
    const vault = catalog.name || "";
    if (!planId) {
      view.append(pageHead(vault, "Планы", okrLink(ui)));
      const hasClosed = catalog.items.some(p => p.status === "closed");
      for (const closed of [false, true]) {
        const entries = catalog.items.filter(p => (p.status === "closed") === closed);
        if (!entries.length) continue;
        if (hasClosed) view.append(node("h2", "cycle-catalog-title", closed ? "Закрытые циклы" : "Активные планы"));
        const list = node("div", "gantt-plan-list");
        for (const plan of entries) {
          const a = link("", urlFor(plan.id), "gantt-plan-card");
          const copy = node("span");
          copy.append(node("strong", "", plan.title));
          if (closed) copy.append(node("small", "cycle-card-stamp", closedStamp(plan.closedAt)));
          a.append(node("span", "gantt-plan-glyph"), copy, node("span", "gantt-arrow", "→")); list.append(a);
        }
        view.append(list);
      }
      if (!catalog.items.length) view.append(node("p", "gantt-empty", "Пока нет планов. В Codex можно попросить «Создай план недели»."));
      return;
    }
    let plan = initial, selected = selectedId, detail = null, inspector = null;
    const closed = plan.status === "closed";
    const today = closed ? closedDay(plan.closedAt) : todayDay();
    const cursorLabel = closed ? "Закрытие" : "Сегодня";
    let period = fitWindow(plan.items, today), grouping = "direction", mode = "chart", refreshVersion = 0;
    let dragging = false;
    const folded = new Set();
    const stage = node("section", "gantt-stage");
    const sizing = new ResizeObserver(() => drawLinks());
    life.signal.addEventListener("abort", () => sizing.disconnect(), {once: true});
    const createPlace = node("div");
    const detailPlace = node("div", "gantt-detail-place");
    const feedback = node("p", "gantt-status");
    feedback.setAttribute("role", "status");
    const all = link("← Все планы", "#plans", "back-link");
    const chooser = node("select");
    chooser.setAttribute("aria-label", "Выбрать план");
    for (const p of catalog.items) chooser.append(option(p.id, p.status === "closed" ? p.title + " · закрыт" : p.title));
    chooser.value = planId;
    chooser.addEventListener("change", () => {
      if (discard()) go(urlFor(chooser.value));
      else chooser.value = planId;
    });
    const refreshButton = button("Обновить", "button button-secondary", () => {
      if (discard()) refresh("План перечитан.").catch(report);
    });
    const connector = pageHead(vault, plan.title, chooser, refreshButton);
    connector.classList.add("gantt-heading");
    const navigation = node("div", "work-tools");
    navigation.append(all, okrLink(ui));
    view.append(navigation, connector, createPlace, stage, detailPlace, feedback);
    const cycleStatus = closed ? node("div", "cycle-heading-status") : null;
    function showCycleLinks() {
      const openNote = cardLinks(ui, catalog.root);
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
    } else {
      const close = button("Закрыть цикл…", "button button-secondary", () => {
        if (!discard()) return;
        stage.hidden = detailPlace.hidden = close.disabled = true;
        const panel = cyclePreview(ui, planId, catalog.root, () => {
          createPlace.replaceChildren();
          stage.hidden = detailPlace.hidden = close.disabled = false;
          close.focus();
        }, () => go(urlFor(planId, selected)));
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
      history.replaceState(null, "", urlFor(planId, id));
    }
    async function readDetail(id) {
      const [task, relations] = await Promise.all([api(endpoint("plan/task", id)), api(endpoint("plan/dependencies", id))
        .catch(error => ({error: error.message}))]);
      return {...task, relations};
    }
    async function refresh(message = "") {
      const sequence = ++refreshVersion;
      const fresh = await api(endpoint("plan", planId));
      if (!view.isConnected || sequence !== refreshVersion) return;
      if (fresh.status !== plan.status) { go(urlFor(planId, selected)); return; }
      const task = selected && !closed ? await readDetail(selected).catch(error => {
        if (error.status !== 404) throw error;
        return null;
      }) : null;
      if (!view.isConnected || sequence !== refreshVersion || (!message && inspector?.editing())) return;
      if (!message && detail?.revision === task?.revision && JSON.stringify(detail?.relations) === JSON.stringify(task?.relations) && JSON.stringify(plan) === JSON.stringify(fresh)) return;
      plan = fresh;
      if (closed) showCycleLinks();
      if (!period.zoom) period = fitWindow(plan.items, today);
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
      views.setAttribute("role", "group"); views.setAttribute("aria-label", "Представление плана");
      for (const [value, title] of [["chart", "Гант"], ["list", "Таблица"]]) {
        const b = button(title, "", () => updateChart(() => { mode = value; }, value));
        b.dataset.control = value; b.setAttribute("aria-pressed", String(mode === value));
        views.append(b);
      }
      controls.append(views);
      const group = node("select");
      for (const [value, title] of [["direction", "По направлениям"], ["project", "По проектам"], ["none", "Без группировки"]]) group.append(option(value, title));
      group.setAttribute("aria-label", "Группировка задач");
      group.dataset.control = "group"; group.value = grouping;
      group.addEventListener("change", () => updateChart(() => { grouping = group.value; }, "group"));
      controls.append(group);
      const addMilestone = button("+ Веха", "gantt-tool", () => {
        if (!discard()) return;
        createPlace.replaceChildren(milestoneForm(ui, plan, async item => {
          createPlace.replaceChildren();
          if (item) {
            setSelected(item.id);
            await refresh("Веха добавлена в план.");
          } else stage.querySelector(".gantt-add-milestone")?.focus();
        }));
        createPlace.querySelector("input").focus();
      });
      addMilestone.classList.add("gantt-add-milestone");
      addMilestone.setAttribute("aria-label", "Добавить веху");
      if (!closed) controls.append(addMilestone);
      if (mode === "chart") {
        const calendarTools = node("div", "gantt-toolbar-end");
        const zoom = node("select");
        for (const [value, title] of [["fit", "Весь план"], ["14", "Две недели"], ["42", "Шесть недель"], ["91", "Квартал"]]) zoom.append(option(value, title));
        zoom.dataset.control = "window"; zoom.setAttribute("aria-label", "Окно шкалы");
        zoom.value = period.zoom || "fit";
        zoom.addEventListener("change", () => updateChart(() => {
          if (zoom.value === "fit") period = fitWindow(plan.items, today);
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
          // Panning from a fitted plan enters a fixed window, so «Весь план» can reset it.
          const size = period.zoom ? period.size : 14;
          period = {start: period.start + direction * size, size, zoom: String(size)};
        };
        calendarTools.append(zoom,
          travel("‹", "Предыдущее окно", "prev", () => shift(-1)),
          travel(cursorLabel, closed ? "Показать дату закрытия" : "Показать сегодня", "today", () => {
            const size = period.zoom ? period.size : 14;
            period = {start: today - Math.floor(size / 2), size, zoom: String(size)};
          }), travel("›", "Следующее окно", "next", () => shift(1)));
        controls.append(calendarTools);
      }
      const board = node("div", period.zoom ? "gantt-board" : "gantt-board gantt-fit");
      board.tabIndex = 0;
      board.setAttribute("role", "region"); board.setAttribute("aria-label", "Задачи плана");
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
        const scale = node("div", "gantt-ticks");
        for (const tick of scaleTicks) {
          const mark = node("span", "", tick.label); mark.style.left = tick.at * 100 + "%";
          mark.title = dateText(isoDay(tick.day)); scale.append(mark);
        }
        axis.append(scale); head.append(axis);
      } else head.append(...Object.values(dateLabels).map(th));
      const body = table.createTBody();
      const groups = new Map();
      for (const row of plan.items) {
        const key = grouping === "none" ? "" : row[grouping] || "";
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(row);
      }
      for (const [key, rows] of groups) {
        if (grouping !== "none") {
          const groupId = grouping + "/" + key;
          const tr = body.insertRow(); tr.className = "gantt-group";
          const label = rows[0][grouping + "Title"] || (grouping === "project" ? "Без проекта" : "Без направления");
          const fold = button("", "gantt-fold", () => {
            if (folded.has(groupId)) folded.delete(groupId); else folded.add(groupId);
            drawChart();
            [...stage.querySelectorAll(".gantt-fold")].find(b => b.dataset.group === groupId)?.focus();
          });
          fold.dataset.group = groupId;
          fold.setAttribute("aria-expanded", String(!folded.has(groupId)));
          fold.append(node("span", "gantt-disclosure", folded.has(groupId) ? "▸" : "▾"), node("span", "", label), node("small", "", rows.length));
          tr.insertCell().append(fold);
          const rest = tr.insertCell();
          rest.colSpan = mode === "chart" ? 1 : Object.keys(dateLabels).length;
          if (mode === "chart") rest.append(track(true));
          if (folded.has(groupId)) continue;
        }
        for (const row of rows) {
          const tr = body.insertRow();
          const title = tr.insertCell(); title.className = "gantt-row-name";
          if (row.missing && !closed) {
            title.append(node("span", "gantt-missing", "Карточка недоступна"));
            tr.insertCell().colSpan = mode === "chart" ? 1 : Object.keys(dateLabels).length;
            continue;
          }
          tr.dataset.task = row.id;
          const task = button(row.title || row.id, "gantt-task", () => choose(row.id));
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
      }
      function track(group) {
        const lane = node("div", group ? "gantt-lane gantt-group-lane" : "gantt-lane");
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
      if (!plan.items.length) board.append(node("p", "gantt-empty", "В этом плане пока нет задач."));
      const legend = node("div", "gantt-legend");
      if (mode === "chart") {
        for (const [key, label] of [["range", "Плановый период"], ...Object.entries(dateLabels), ["now", cursorLabel + " · " + shortDay(today)]]) {
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
          root: catalog.root, urlFor, deselect: () => choose(null)}));
      } else if (selected && detail) {
        const rowUI = {...ui, api: (path, payload) => api(path,
          payload && path.startsWith("plan/") ? {...payload, planId} : payload)};
        inspector = taskInspector(rowUI, {task: detail, row: plan.items.find(r => r.id === selected), plan, root: catalog.root,
          refresh, deselect: () => choose(null), selectRelated: async id => {
            const row = plan.items.find(item => item.id === id);
            if (folded.delete(grouping + "/" + (row[grouping] || ""))) drawChart();
            await choose(id);
            const tr = [...stage.querySelectorAll("tr[data-task]")].find(tr => tr.dataset.task === id);
            if (tr && !window.matchMedia("(max-width: 600px)").matches) tr.scrollIntoView({block: "nearest"});
          }});
        detailPlace.replaceChildren(inspector.element);
      } else {
        const overview = node("section", "gantt-overview");
        const summary = node("div");
        const rows = plan.items.filter(r => !r.missing);
        const milestones = rows.filter(r => r.kind === "milestone").length;
        const totals = [counted(rows.length - milestones, ["задача", "задачи", "задач"])];
        if (milestones) totals.push(counted(milestones, ["веха", "вехи", "вех"]));
        summary.append(node("h2", "", closed ? "Итог цикла" : "Обзор плана"), node("p", "", totals.join(" · ")));
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
