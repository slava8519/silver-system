import { openObsidianSource, workspaceLink } from "./obsidian-workspace.js";
import { cleanupNotice, mountCleanup } from "./demo-cleanup.js";
import { intakeViews } from "./intake.js";
import { planningView } from "./gantt.js";
import { activityHelp } from "./activity-help.js";
import { mountRelationMap } from "./relation-map.js";
import { relationMapHelp } from "./relation-map-help.js";
import { documentForm } from "./document-form.js";
import { markdownEditor } from "./markdown-editor.js";
import { editorDialogOpen, leaveEditor } from "./editor-dialog.js";
import { recordDocument, recordSection } from "./record-document.js";
import { projectArchiveControl, projectEvents, projectUpdatesPanel } from "./project-updates.js";
import { mountDirectionStates } from "./direction-states.js";
import { mountInboxActivity } from "./inbox-activity.js";
import { renderOverviewCommands } from "./overview-commands.js";
import { renderOverviewFolders } from "./overview-folders.js";
import { reportsView } from "./reports.js";
import { showVaultCheck } from "./vault-check.js";

(() => {
  const main = document.getElementById("content");
  const sectionPicker = document.querySelector(".section-picker");
  const navigation = document.getElementById("workspace-nav");
  const closeNavigation = () => sectionPicker.setAttribute("aria-expanded", "false");
  sectionPicker.addEventListener("click", () => {
    const open = sectionPicker.getAttribute("aria-expanded") === "true";
    sectionPicker.setAttribute("aria-expanded", String(!open));
  });
  document.addEventListener("click", event => {
    if (!sectionPicker.contains(event.target)) closeNavigation();
  });
  sectionPicker.parentElement.addEventListener("focusout", event => {
    if (!event.currentTarget.contains(event.relatedTarget)) closeNavigation();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && sectionPicker.getAttribute("aria-expanded") === "true") {
      closeNavigation();
      sectionPicker.focus();
    }
  });
  const query = new URLSearchParams(location.search);
  const compact = query.get("embed") === "1";
  const fullWorkspace = query.get("embed") === "workspace";
  const embedded = compact || fullWorkspace;
  document.body.classList.toggle("gantt-embed", compact);
  document.body.classList.toggle("workspace-embed", fullWorkspace);
  const typeNames = { direction: "Направление", project: "Проект", note: "Заметка" };
  const sections = { overview: "Обзор хранилища", records: "Карточки" };
  const fields = { status: "Статус", projectStart: "Согласованное начало", due: "Срок", nextStep: "Следующий шаг" };
  let data, byId, directions, pageVersion = 0;

  function node(tag, className = "", text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function link(text, href, className = "") {
    const a = node("a", className, text);
    a.href = fullWorkspace ? workspaceLink(href, location.href) : href;
    return a;
  }

  function button(text, className, action) {
    const control = node("button", className, text);
    control.type = "button";
    control.addEventListener("click", action);
    return control;
  }

  function option(value, text) {
    const o = node("option", "", text);
    o.value = value;
    return o;
  }

  function section(title, className = "work-panel") {
    const box = node("section", className);
    box.append(node("h2", "work-heading", title));
    return box;
  }

  function labelled(caption, input, className = "work-field") {
    const label = node("label", className);
    label.append(node("span", "", caption), input);
    return label;
  }

  function notice(text) {
    const p = node("p", "field-error", text);
    p.setAttribute("role", "alert");
    return p;
  }

  function go(hash) {
    if (location.hash === hash) render();
    else location.hash = hash;
  }

  function recordURL(id, params) {
    return "#record/" + encodeURIComponent(id) + (params ? "?" + new URLSearchParams(params) : "");
  }

  async function api(path, payload) {
    let response;
    try {
      const file = payload instanceof Blob;
      const headers = {};
      if (embedded) headers["X-Brain-Vault"] = query.get("binding") || "";
      response = await fetch("/api/" + path, payload === undefined ? {cache: "no-store", headers} : {
        method: "POST", headers: {...headers, "Content-Type": file ? "application/octet-stream" : "application/json"},
        body: file ? payload : JSON.stringify(payload)
      });
    } catch {
      throw new Error("Не удалось связаться с локальным сервером.");
    }
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || "Не удалось выполнить запрос.");
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function counted(number, forms) {
    const n = number % 100;
    const form = n >= 11 && n <= 19 ? 2 : n % 10 === 1 ? 0 : n % 10 >= 2 && n % 10 <= 4 ? 1 : 2;
    return number + " " + forms[form];
  }

  function scopeName(record) {
    if (!record.direction) return "Без направления";
    return byId.get(record.direction)?.title || "Направление не найдено";
  }

  function pageHead(caption, title, ...actions) {
    const header = node("header", "work-head");
    const copy = node("div");
    copy.append(node("p", "work-vault", caption), node("h1", "work-title", title));
    const toolbar = node("div", "work-tools");
    toolbar.append(...actions);
    header.append(copy, toolbar);
    return header;
  }

  function newLink(kind = "note", direction = "") {
    const href = "#new/" + kind + (direction ? "?" + new URLSearchParams({direction}) : "");
    return link("Создать карточку", href, "button button-primary");
  }

  const intake = intakeViews({node, link, button, option, section, labelled, notice, api, go, counted, pageHead, recordURL});
  const planning = planningView({node, link, button, option, labelled, notice, api, go, counted, pageHead, embedded, compact});
  const reports = reportsView({node, link, button, option, labelled, notice, api, go, counted, pageHead, embedded, refreshAfterEdit});

  async function refreshAfterEdit() {
    const current = location.hash;
    await render();
    if (current !== location.hash) return;
    const trigger = main.querySelector("[data-edit-document]");
    if (!trigger) return;
    const status = node("span", "report-status");
    status.setAttribute("role", "status");
    trigger.after(status);
    status.textContent = "Изменения сохранены.";
    trigger.focus({preventScroll:true});
  }

  function fieldValue(record, key) {
    const value = record[key];
    if (!value) return node("span", "unknown-value", key === "projectStart" ? "Не указано" : key === "nextStep" ? "Не задан" : "Не указан");
    if (!record.fieldSources?.[key]) return node("span", "", value);
    const a = link(value, recordURL(record.id, {field: key}), "field-source");
    a.title = record.fieldSources[key].path + " · строка " + record.fieldSources[key].line;
    return a;
  }

  async function overview() {
    main.append(pageHead(data.name, sections.overview, link("Проверить хранилище", "#check", "button button-secondary"),
      button("Обновить обзор", "button button-secondary", render), newLink()));
    const receipt = cleanupNotice(node);
    if (receipt) main.append(receipt);
    const stats = node("nav", "work-stats");
    stats.setAttribute("aria-label", "Состав хранилища");
    for (const [type, caption] of [["direction","Направления"],["project","Проекты"],["note","Заметки"],["person","Люди"]]) {
      const href = type === "person" ? "#people" : "#records?type=" + type;
      const count = type === "person" ? data.peopleCount : data.records.filter(r => r.type === type).length;
      const metric = link("", href, "work-stat");
      metric.append(node("span", "metric-name", caption), node("strong", "metric-count", count));
      stats.append(metric);
    }

    const layout = node("div", "overview-layout");
    const primary = node("div", "overview-primary");
    const arrivals = section("Последние входящие", "work-panel inbox-activity");
    const states = node("details", "work-panel overview-states");
    const stateSummary = node("summary", "work-heading", "Состояние направлений");
    stateSummary.append(node("span", "overview-total", directions.length));
    states.append(stateSummary);
    states.addEventListener("toggle", () => {
      if (states.open && states.childElementCount === 1) {
        mountDirectionStates(states, {node, link, button, notice, api, recordURL, counted});
      }
    });
    const sidebar = node("div", "overview-sidebar");
    const cleanup = node("div", "overview-maintenance");
    cleanup.hidden = true;
    sidebar.append(renderOverviewCommands({node, button}, data.root), renderOverviewFolders({node, counted}, data.folders), cleanup);
    primary.append(arrivals, states);
    layout.append(primary, sidebar);
    main.append(stats, layout);
    await Promise.all([
      mountInboxActivity(arrivals, {node, link, button, option, notice, api}),
      mountCleanup(cleanup, api, node)
    ]);
  }

  function recordRow(record) {
    const a = link("", recordURL(record.id), "record-row");
    const label = node("div", "record-main");
    label.append(node("span", "row-title", record.title), node("span", "record-path", record.path));
    a.append(node("span", "type-icon type-" + record.type, typeNames[record.type]?.slice(0,1) || "•"), label,
      node("span", "record-side", record.type === "direction" ? typeNames.direction : scopeName(record)));
    return a;
  }

  function recordsPage(params) {
    main.append(pageHead(data.name, sections.records, newLink()));
    const sectionNode = node("section", "work-panel");
    const filters = node("div", "filter-row");
    const keyword = node("input");
    keyword.type = "search";
    keyword.placeholder = "Название или другое имя";
    const type = node("select"), area = node("select");
    type.append(option("", "Все типы"), ...Object.entries(typeNames).map(([k,v]) => option(k,v)));
    type.value = params.get("type") || "";
    area.append(option("", "Все направления"), option("none", "Без направления"), ...directions.map(d => option(d.id,d.title)));
    area.value = params.get("direction") || "";
    filters.append(labelled("Найти карточку", keyword), labelled("Тип", type), labelled("Направление", area));
    const list = node("div", "record-list");
    function filter() {
      const term = keyword.value.trim().toLocaleLowerCase();
      const matches = data.records.filter(r => (!type.value || r.type === type.value) &&
        (!area.value || (area.value === "none" ? !r.direction && r.type !== "direction" : r.direction === area.value || r.id === area.value)) &&
        [r.title, ...(r.aliases || [])].some(t => t.toLocaleLowerCase().includes(term)));
      list.replaceChildren(...matches.map(recordRow));
      if (!matches.length) list.append(node("p", "muted", data.records.length ? "По этому фильтру карточек не найдено." : "В хранилище пока нет карточек."));
    }
    [type, area].forEach(control => control.addEventListener("change", filter));
    keyword.addEventListener("input", filter);
    filter();
    sectionNode.append(filters, list);
    main.append(sectionNode);
  }

  function recordFacts(record) {
    const facts = node("dl", "record-facts");
    for (const [key, caption] of Object.entries(fields)) {
      const wrap = node("div");
      const dd = node("dd");
      dd.append(fieldValue(record, key));
      wrap.append(node("dt", "", caption), dd);
      facts.append(wrap);
    }
    return facts;
  }

  async function recordPage(recordId, params, version, openEditor = false) {
    const selector = recordId ? {id:recordId} : {path:params.get("path"), short:params.get("short") || ""};
    const full = params.get("view") === "full" || params.get("source") === "1" ||
      ["line", "field", "section", "revision"].some(key => params.has(key));
    if (!full) selector.view = "current";
    if (recordId && params.has("revision") && /^[1-9]\d*$/.test(params.get("line") || "")) {
      selector.revision = params.get("revision");
      selector.line = params.get("line");
    }
    const record = await api("record?" + new URLSearchParams(selector));
    const id = record.id;
    if (version !== pageVersion) return;
    const actions = [];
    const edit = button("Редактировать", "button button-secondary", () => openCardEditor());
    edit.dataset.editDocument = "";
    async function openCardEditor() {
      if (edit.disabled || editorDialogOpen()) return;
      edit.disabled = true;
      try { await editPage(id, null, params, version, edit); }
      catch (error) { if (edit.isConnected) edit.after(notice(error.message)); }
      finally { edit.disabled = false; }
    }
    if (record.type !== "markdown") actions.push(link("Показать связи", "#map?" + new URLSearchParams({id}), "text-link"), edit);
    if (record.type === "markdown" && record.project_id) actions.push(link("К проекту", recordURL(record.project_id), "text-link"));
    if (record.type === "project") actions.unshift(link("Гант проекта", "#project-gantt/" + encodeURIComponent(id), "button button-primary"));
    const header = pageHead((typeNames[record.type] || "Markdown") + " · " + record.vaultName, record.title, ...actions);
    header.classList.add("record-head");
    main.replaceChildren(header);
    const panel = node("section","work-panel record-panel");
    const meta = node("div", "record-meta");
    panel.append(meta);
    const areaLine = node("p", "muted");
    if (record.type !== "direction" && record.type !== "markdown") {
      areaLine.textContent = record.direction ? "Направление · " : "Без направления";
      meta.append(areaLine);
    }
    if (record.aliases?.length) meta.append(node("p","muted","Другие названия · " + record.aliases.join(", ")));
    if (record.type === "project") {
      meta.append(full ? link("К сводке проекта", recordURL(id), "text-link") :
        link("Полный текст и история", recordURL(id, {view:"full"}), "text-link"));
      const ui = {node, button, link, notice, api, labelled, counted, embedded};
      if (!full) {
        panel.append(projectUpdatesPanel(ui, record, () => go(recordURL(id))));
      } else {
        const history = node("details", "project-full-history");
        history.append(node("summary", "text-link", "Хронология событий · " + record.projectUpdates.total),
          projectEvents(ui, record, true));
        panel.append(history, projectArchiveControl(ui, record, render));
      }
      panel.append(recordFacts(record));
    }
    main.append(panel);
    if (record.type === "project" && !full) {
      if (fullWorkspace) meta.append(button("Открыть в Obsidian ↗", "intake-action", () => openObsidianSource(record.path)));
    } else {
      showRecordText(record, params, panel, meta);
    }
    if (openEditor && edit.isConnected) await openCardEditor();
    if (!record.direction && record.type !== "direction") return;
    const mapURL = "#map?" + new URLSearchParams({id});
    if (record.type !== "direction") {
      const area = link("Открыть направление", recordURL(record.direction), "text-link");
      areaLine.append(area);
      const context = await api("workspace/context?directions=1").catch(() => null);
      if (version !== pageVersion || !context) return;
      const direction = context.directions.find(item => item.id === record.direction);
      if (direction) area.textContent = direction.title;
      else area.replaceWith("Направление не найдено");
      return;
    }
    const related = section("Связанные карточки");
    const shortcuts = node("div", "work-tools");
    shortcuts.append(newLink("project", id), link("Люди направления", "#people?" + new URLSearchParams({direction:id}), "text-link"));
    related.append(shortcuts);
    main.append(related);
    const context = await api("map?" + new URLSearchParams({action:"focus", id})).catch(() => null);
    if (version !== pageVersion) return;
    if (!context) { related.prepend(link("Открыть карту связей", mapURL, "text-link")); return; }
    const links = node("div", "record-list");
    for (const group of context.groups) {
      for (const child of group.items) {
        if (!child.missing && (child.type === "project" || child.type === "note")) {
          links.append(link(child.title, recordURL(child.id), "record-row text-link"));
        }
      }
    }
    const count = context.groups.reduce((sum, group) => sum + group.total, 0);
    if (count > links.childElementCount) links.append(link("Все связи · " + count, mapURL, "record-row text-link"));
    if (!count) links.append(node("p", "muted", "Связанных карточек пока нет."));
    shortcuts.before(links);
  }

  function showRecordText(record, params, panel, meta) {
    const selectedField = params.get("field");
    const requestedLine = record.resolvedLine || Number(params.get("line"));
    const outdated = !record.resolvedLine && params.has("revision") && params.get("revision") !== record.revision;
    const selectedLine = record.fieldSources?.[selectedField]?.line ||
      (!outdated && Number.isSafeInteger(requestedLine) && requestedLine > 0 && requestedLine <= record.raw.split(/\r?\n/).length
        ? requestedLine : null);
    if (outdated) panel.append(notice("Карточка изменилась после открытия обзора. Сверьте новую версию."),
      link("Вернуться к обзору", "#overview", "text-link"));
    if (fullWorkspace) meta.append(button("Открыть в Obsidian ↗", "intake-action",
      () => openObsidianSource(record.path, selectedLine)));
    if (selectedField && !selectedLine) panel.append(node("p","muted","Поле сейчас не задано."));
    const sourceBox = node("details","record-source");
    sourceBox.open = Boolean(selectedLine) || params.get("source") === "1";
    const sourceSummary = node("summary", "text-link", "Исходник" + (selectedLine ? " · строка " + selectedLine : ""));
    sourceSummary.title = record.path;
    sourceBox.append(sourceSummary, node("p", "record-path", record.path));
    const lines = node("div", "source-lines");
    function showSource() {
      if (!sourceBox.open || lines.childElementCount) return;
      record.raw.split(/\r?\n/).forEach((line,index) => {
        const n = index + 1;
        const row = node("div","source-line" + (n === selectedLine ? " highlight" : ""));
        row.append(node("span","line-number",n),node("code","line-text",line || " "));
        lines.append(row);
      });
    }
    showSource();
    sourceBox.addEventListener("toggle", showSource);
    sourceBox.append(lines);
    meta.append(sourceBox);
    if (record.body?.trim()) {
      const body = recordDocument(record, record.body, {link, embedded}, {omitTitle:record.title});
      body.hidden = sourceBox.open;
      sourceBox.addEventListener("toggle", () => { body.hidden = sourceBox.open; });
      panel.append(body);
    }
    if (!record.body && !record.aliases?.length && record.type !== "project") panel.append(node("p","muted","Текст карточки пока пуст."));
    if (selectedLine && requestedLine) lines.querySelector(".highlight")?.scrollIntoView({block: "center"});
    else if (params.get("section")) {
      const heading = recordSection(main, params.get("section"));
      if (heading) heading.scrollIntoView({block:"start"});
      else meta.append(node("p", "muted", "Раздел «" + params.get("section") + "» не найден."));
    }
  }

  async function editPage(id, kind, params, version, trigger) {
    const read = () => api("record?" + new URLSearchParams({id}));
    const [context, record] = await Promise.all([api("workspace/context?directions=1"), id ? read() : null]);
    if (version !== pageVersion || (trigger && !trigger.isConnected)) return;
    const form = node("form","work-panel work-editor");
    const properties = node("div", "editor-property-fields");
    if (record) {
      const details = node("details", "editor-properties");
      details.append(node("summary", "", "Свойства карточки"), properties);
      form.append(details);
      properties.addEventListener("invalid", () => { details.open = true; }, true);
    } else {
      main.replaceChildren(pageHead(context.name, compact ? "Создать проект" : "Новая карточка"), form);
      form.append(properties);
    }
    const type = node("select");
    for (const [k,v] of Object.entries(typeNames)) type.append(option(k,v));
    type.value = record?.type || kind || "note";
    if (!type.value) type.value = "note";
    type.disabled = Boolean(id) || compact;
    properties.append(labelled("Тип карточки", type));
    const controls = {};
    function input(key, caption, tag = "input", parent = properties) {
      const field = node(tag);
      field.name = key;
      controls[key] = field;
      parent.append(labelled(caption, field));
      return field;
    }
    const title = input("title", "Название");
    title.required = true;
    const area = input("direction", "Направление", "select");
    area.append(option("", "Без направления"), ...context.directions.map(r => option(r.id,r.title)));
    const initialDir = record ? record.direction : params.get("direction");
    if (initialDir && !context.directions.some(r => r.id === initialDir)) area.append(option(initialDir, "Направление не найдено · " + initialDir));
    const aliases = input("aliases", "Другие названия (по одному в строке)", "textarea");
    aliases.rows = 2;
    const meta = node("div", "editor-metadata");
    for (const [key,caption] of Object.entries(fields)) {
      const field = input(key, caption, "input", meta);
      if (key === "projectStart") field.type = "date";
    }
    properties.append(meta);
    function syncType() {
      area.parentElement.hidden = type.value === "direction";
      meta.hidden = type.value !== "project";
    }
    type.addEventListener("change",syncType);
    syncType();
    const body = node("textarea");
    body.name = "body";
    controls.body = body;
    form.append(markdownEditor({node, button}, body, {label:"Текст карточки", render:text =>
      recordDocument(record || {}, text, {link, embedded})}));
    function valuesOf(item) {
      return Object.fromEntries(Object.keys(controls).map(key => {
        const value = key === "aliases" ? item.aliases?.join("\n") : item[key];
        return [key, String(value ?? "").replace(/\r\n?/g, "\n")];
      }));
    }
    const initial = valuesOf(record || {direction:initialDir});
    for (const [key, field] of Object.entries(controls)) field.value = initial[key];
    let requestId = crypto.randomUUID(), sentSignature;
    documentForm({node, button, notice}, {form, fields:controls, original:record, valuesOf,
      read:record && read,
      dialog:record && {title:"Редактировать карточку", context:record.title, initialFocus:body, trigger},
      cancel:!record && link("Отмена", compact ? "#activity" : "#records", "button button-secondary"),
      save(values, baseline, revision) {
        const payload = {title:values.title.trim(), body:values.body, aliases:values.aliases.split("\n").map(v => v.trim()).filter(Boolean),
          direction:type.value === "direction" ? null : values.direction || null};
        if (type.value === "project") for (const key of Object.keys(fields)) payload[key] = values[key].trim() || null;
        if (record) {
          for (const key of Object.keys(payload)) if (values[key] === baseline[key]) delete payload[key];
          return api("records/edit", {...payload, id, revision});
        }
        payload.type = type.value;
        const signature = JSON.stringify(payload);
        if (sentSignature && sentSignature !== signature) requestId = crypto.randomUUID();
        sentSignature = signature;
        return api("records/create", {...payload, requestId});
      },
      onSaved:saved => record ? refreshAfterEdit() : go(compact ? "#project-gantt/" + encodeURIComponent(saved.id) : recordURL(saved.id))
    });
  }

  async function render() {
    const version = ++pageVersion;
    if (/^#plans(?:$|\?)/.test(location.hash)) {
      const filters = new URLSearchParams(location.hash.split("?")[1]);
      if (!filters.has("view")) filters.set("view", "plans");
      history.replaceState(null, "", "#activity?" + filters);
    }
    if (compact && !/^#(?:plan\/|project-gantt\/|activity(?:-help)?(?:$|\?)|okr(?:$|[/?])|new\/project(?:$|\?))/.test(location.hash)) history.replaceState(null, "", "#activity?view=plans");
    if (location.hash.split("?")[0] === "#ask") history.replaceState(null, "", "#overview");
    const raw = location.hash.slice(1) || "overview";
    const split = raw.indexOf("?");
    const route = split < 0 ? raw : raw.slice(0,split);
    const params = new URLSearchParams(split < 0 ? "" : raw.slice(split + 1));
    const [page, recordId] = route.split("/");
    const isPlan = ["activity", "activity-help", "okr", "project-gantt", "plan"].includes(page);
    document.body.classList.toggle("gantt-screen", Boolean(recordId) && (page === "plan" || page === "project-gantt"));
    const nav = page === "check" ? "overview" : page === "map-help" ? "map" : isPlan ? "activity" : page === "person" ? "people" : ["new","edit","record"].includes(page) ? "records" : page;
    closeNavigation();
    const navLinks = navigation.querySelectorAll("[data-page]");
    const currentLink = Array.from(navLinks).find(a => a.dataset.page === nav) || navLinks[0];
    for (const a of navLinks) {
      if (a === currentLink) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    sectionPicker.querySelector(".section-name").textContent = currentLink.textContent;
    main.classList.toggle("planning-main", isPlan);
    main.setAttribute("aria-busy","true");
    try {
      if (page === "check") {
        const view = node("div");
        main.replaceChildren(view);
        await showVaultCheck(view, params, {node, link, button, section, api, pageHead, counted,
          openSource: fullWorkspace ? openObsidianSource : null, rerun: () => go("#check"),
          pin: url => { if (version === pageVersion && location.hash === "#" + raw) history.replaceState(null, "", url); }});
      } else if (page === "reports") {
        const view = node("div", "reports-view");
        main.replaceChildren(view);
        await reports(view, recordId || "issues", params);
      } else if (page === "map-help") {
        main.replaceChildren(relationMapHelp(params));
      } else if (page === "map") {
        const view = node("div", "relation-map");
        main.replaceChildren(view);
        await mountRelationMap(view, {api, params, node, link, button, pageHead, counted, embedded});
      } else if (page === "activity-help") {
        main.replaceChildren(activityHelp(params));
      } else if (page === "record" && (recordId || params.has("path"))) {
        main.replaceChildren();
        await recordPage(decodeURIComponent(recordId || ""), params, version);
      } else if (page === "edit" && recordId) {
        const id = decodeURIComponent(recordId);
        history.replaceState(null, "", recordURL(id));
        await recordPage(id, params, version, true);
      } else if (page === "new") {
        await editPage(null, recordId, params, version);
      } else if (isPlan) {
        const view = node("div", "planning-view");
        main.replaceChildren(view);
        await planning(view, recordId && decodeURIComponent(recordId), params.get("task"), page);
      } else if (["inbox", "people", "person"].includes(page)) {
        const withDirections = page === "person" || (page === "inbox" && params.has("path"))
          || (page === "people" && params.has("direction") && !params.has("team"));
        const context = await api("workspace/context" + (withDirections ? "?directions=1" : ""));
        if (version !== pageVersion) return;
        const view = node("div", "intake-view");
        main.replaceChildren(view);
        await intake[page](view, {params, id: decodeURIComponent(recordId || ""), vault: context.name,
          directions: context.directions || []});
      } else {
        const loaded = await api("workspace");
        if (version !== pageVersion) return;
        data = loaded;
        byId = new Map(data.records.map(r => [r.id,r]));
        directions = data.records.filter(r => r.type === "direction");
        main.replaceChildren();
        if (page === "records") recordsPage(params);
        else await overview();
      }
      if (version !== pageVersion) return;
      document.title = (main.querySelector("h1")?.textContent || "Хранилище") + " · Второй мозг";
    } catch (error) {
      if (version === pageVersion) {
        main.replaceChildren(notice(error.message),button("Повторить", "button button-secondary", render));
        if (!compact && page !== "check") main.append(link("Проверить хранилище", "#check", "button button-secondary"));
      }
    } finally {
      if (version === pageVersion) main.removeAttribute("aria-busy");
    }
  }
  document.querySelector(".skip-link").addEventListener("click",event => { event.preventDefault(); main.focus(); });
  window.navigation?.addEventListener("navigate", event => {
    if (!editorDialogOpen() || !event.hashChange || !event.cancelable) return;
    event.preventDefault();
    const destination = event.destination;
    leaveEditor(() => {
      if (event.navigationType === "traverse") window.navigation.traverseTo(destination.key);
      else window.navigation.navigate(destination.url, {history:event.navigationType === "replace" ? "replace" : "push"});
    });
  });
  window.addEventListener("hashchange", event => {
    const navigate = () => { main.focus({preventScroll:true}); window.scrollTo(0,0); render(); };
    if (!editorDialogOpen()) { navigate(); return; }
    // Older browsers deliver Back only after committing it. Restore the draft
    // above the destination so continuing the edit preserves a working Back link.
    history.pushState(null, "", event.oldURL);
    leaveEditor(() => history.back());
  });
  render();
})();
