import { cleanupNotice, mountCleanup } from "./demo-cleanup.js";
import { intakeViews } from "./intake.js";
import { planningView } from "./gantt.js";
import { activityHelp } from "./activity-help.js";

(() => {
  const main = document.getElementById("content");
  const query = new URLSearchParams(location.search);
  const embedded = query.get("embed") === "1";
  document.body.classList.toggle("gantt-embed", embedded);
  const typeNames = { direction: "Направление", project: "Проект", note: "Заметка" };
  const sections = { overview: "Обзор хранилища", records: "Карточки", map: "Карта связей" };
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
    a.href = href;
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
      if (embedded && query.get("binding")) headers["X-Brain-Vault"] = query.get("binding");
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
  const planning = planningView({node, link, button, option, labelled, notice, api, go, counted, pageHead, embedded});

  function fieldValue(record, key) {
    const value = record[key];
    if (!value) return node("span", "unknown-value", key === "projectStart" ? "Не указано" : key === "nextStep" ? "Не задан" : "Не указан");
    if (!record.fieldSources?.[key]) return node("span", "", value);
    const a = link(value, recordURL(record.id, {field: key}), "field-source");
    a.title = record.fieldSources[key].path + " · строка " + record.fieldSources[key].line;
    return a;
  }

  function projectTable(projects) {
    const panel = section("Состояние проектов");
    if (!projects.length) {
      panel.append(node("p", "muted", "В хранилище пока нет проектов."), link("Создать проект", "#new/project", "text-link"));
      return panel;
    }
    const table = node("table", "projects-table");
    const tr = table.createTHead().insertRow();
    for (const name of ["Проект", ...Object.values(fields)]) {
      const th = node("th", "", name);
      th.scope = "col";
      tr.append(th);
    }
    const body = table.createTBody();
    for (const record of projects) {
      const row = body.insertRow();
      const first = row.insertCell();
      first.append(link(record.title, recordURL(record.id), "table-title"));
      if (record.aliases?.length) first.append(node("span", "table-scope", record.aliases.join(" · ")));
      first.append(node("span", "table-scope", scopeName(record)));
      for (const key of Object.keys(fields)) {
        const td = row.insertCell();
        td.dataset.label = fields[key];
        td.append(fieldValue(record, key));
      }
    }
    panel.append(table);
    return panel;
  }

  async function overview() {
    const cleanup = node("div");
    cleanup.hidden = true;
    main.append(pageHead(data.name, sections.overview, button("Обновить", "button button-secondary", render), newLink(), cleanup));
    const receipt = cleanupNotice(node);
    if (receipt) main.append(receipt);
    const stats = node("div", "work-stats");
    for (const [type, caption] of [["direction","Направления"],["project","Проекты"],["note","Заметки"]]) {
      const metric = link("", "#records?type=" + type, "work-stat metric-" + type);
      metric.append(node("span", "metric-name", caption), node("strong", "metric-count", data.records.filter(r => r.type === type).length));
      stats.append(metric);
    }
    if (typeof data.peopleCount === "number") {
      const people = link("", "#people", "work-stat metric-people");
      people.append(node("span", "metric-name", "Люди"), node("strong", "metric-count", data.peopleCount));
      stats.append(people);
      stats.classList.add("with-people");
    }
    const row = node("div", "overview-row");
    const areas = section("Направления", "work-panel directions-panel");
    if (!directions.length) areas.append(node("p", "muted", "Создайте направление для постоянной области вашей жизни или работы."), link("Создать направление", "#new/direction", "text-link"));
    else {
      const list = node("ul", "area-list");
      for (const area of directions) {
        const records = data.records.filter(r => r.direction === area.id);
        const l = link("", recordURL(area.id), "area-link");
        l.append(node("span","area-tab",""), node("span","area-title",area.title), node("span","area-count",counted(records.length,["карточка","карточки","карточек"])));
        const li = node("li");
        li.append(l);
        list.append(li);
      }
      areas.append(list);
    }
    const capture = section("Входящие", "work-panel inbox-shortcut");
    capture.append(node("p", "muted", "Сохраните текст, файл или скриншот переписки для следующего разбора в Codex."),
      link("Добавить материал", "#inbox", "button button-primary"));
    row.append(areas, capture);
    main.append(stats, row, projectTable(data.records.filter(r => r.type === "project")));
    await mountCleanup(cleanup, api, node);
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

  function mapPage() {
    main.append(pageHead(data.name, sections.map, button("Обновить", "button button-secondary", render)));
    const panel = section("Связи направлений");
    const tree = node("div", "map-tree");
    for (const area of directions) {
      const branch = node("div", "map-branch");
      const root = link("", recordURL(area.id), "map-root");
      root.append(node("small", "", typeNames.direction), node("strong", "", area.title));
      const leaves = node("div", "map-leaves");
      const children = data.records.filter(r => r.direction === area.id);
      for (const record of children) {
        const leaf = link("", recordURL(record.id), "map-leaf");
        leaf.append(node("small", "", typeNames[record.type]), node("span", "", record.title));
        leaves.append(leaf);
      }
      branch.append(root);
      if (children.length) branch.append(leaves);
      else branch.append(node("p", "map-empty", "Связанных карточек пока нет."));
      tree.append(branch);
    }
    if (!directions.length) tree.append(node("p","muted","Направления пока не созданы."));
    panel.append(tree);
    main.append(panel);
    const loose = data.records.filter(r => r.type !== "direction" && !directions.some(d => d.id === r.direction));
    if (loose.length) {
      const unlinked = section("Карточки без доступного направления");
      unlinked.append(...loose.map(recordRow));
      main.append(unlinked);
    }
    const folders = section("Папки хранилища");
    folders.append(node("p", "muted", "Счётчик включает карточки во вложенных папках."));
    const chips = node("ul","folder-set");
    for (const folder of data.folders) {
      const li = node("li");
      const count = counted(folder.count, ["карточка", "карточки", "карточек"]);
      li.setAttribute("aria-label", folder.path + " — " + count + ", включая вложенные папки");
      li.append(node("code","",folder.path),node("span","",count));
      chips.append(li);
    }
    folders.append(chips);
    main.append(folders);
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

  function recordBody(record) {
    const body = node("pre", "record-body");
    if (record.type !== "project") {
      body.textContent = record.body;
      return body;
    }
    const toRoot = "../".repeat(record.path.split("/").length - 1);
    const archiveLink = /\[Источник, строка (\d+)\]\(((?:\.\.\/)+)Sources\/Originals\/([a-f0-9]{64})\.(?:md|txt)\)/g;
    let start = 0;
    for (const match of record.body.matchAll(archiveLink)) {
      const [, line, folders, digest] = match;
      if (folders !== toRoot || !Number.isSafeInteger(Number(line)) || Number(line) < 1) continue;
      body.append(record.body.slice(start, match.index), link("Оригинал · строка " + line,
        "/original/" + digest + "?start=" + line, "text-link"));
      start = match.index + match[0].length;
    }
    body.append(record.body.slice(start));
    return body;
  }

  async function recordPage(id, params, version) {
    const record = await api("record?" + new URLSearchParams({id}));
    if (version !== pageVersion) return;
    const actions = [link("Редактировать", "#edit/" + encodeURIComponent(id), "button button-secondary")];
    if (record.type === "project") actions.unshift(link("Гант проекта", "#project-gantt/" + encodeURIComponent(id), "button button-primary"));
    main.append(pageHead(typeNames[record.type] + " · " + data.name, record.title, ...actions));
    const panel = node("section","work-panel");
    if (record.type !== "direction") {
      const area = byId.get(record.direction);
      const p = node("p","muted", "Направление · ");
      if (area) p.append(link(area.title, recordURL(area.id), "text-link"));
      else p.append(scopeName(record));
      panel.append(p);
    }
    if (record.aliases?.length) panel.append(node("p","muted","Другие названия · " + record.aliases.join(", ")));
    if (record.type === "project") panel.append(recordFacts(record));
    const selectedField = params.get("field");
    const selectedLine = record.fieldSources?.[selectedField]?.line;
    if (selectedField && !selectedLine) panel.append(node("p","muted","Поле сейчас не задано."));
    const sourceBox = node("details","record-source");
    sourceBox.open = Boolean(selectedLine);
    sourceBox.append(node("summary", "text-link", record.path + (selectedLine ? " · строка " + selectedLine : "")));
    const lines = node("div", "source-lines");
    record.raw.split(/\r?\n/).forEach((line,index) => {
      const n = index + 1;
      const row = node("div","source-line" + (n === selectedLine ? " highlight" : ""));
      row.append(node("span","line-number",n),node("code","line-text",line || " "));
      lines.append(row);
    });
    sourceBox.append(lines);
    panel.append(sourceBox);
    if (record.body) {
      const body = recordBody(record);
      body.hidden = sourceBox.open;
      sourceBox.addEventListener("toggle", () => { body.hidden = sourceBox.open; });
      panel.append(body);
    }
    if (!record.body && !record.aliases?.length && record.type !== "project") panel.append(node("p","muted","Текст карточки пока пуст."));
    main.append(panel);
    if (record.type === "direction") {
      const children = data.records.filter(r => r.direction === id);
      const related = section("Связанные карточки");
      if (children.length) related.append(...children.map(recordRow));
      else related.append(node("p", "muted", "Связанных карточек пока нет."));
      const actions = node("div", "work-tools");
      actions.append(newLink("project", id), link("Люди направления", "#people?" + new URLSearchParams({direction:id}), "text-link"));
      related.append(actions);
      main.append(related);
    }
  }

  async function editPage(id, kind, params, version) {
    let record = id ? await api("record?" + new URLSearchParams({id})) : null;
    if (version !== pageVersion) return;
    main.append(pageHead(data.name, record ? "Редактировать карточку" : embedded ? "Создать проект" : "Новая карточка"));
    const form = node("form","work-panel work-editor");
    const controls = {};
    const type = node("select");
    for (const [k,v] of Object.entries(typeNames)) type.append(option(k,v));
    type.value = record?.type || kind || "note";
    if (!type.value) type.value = "note";
    type.disabled = Boolean(id) || embedded;
    form.append(labelled("Тип карточки", type));
    function input(key, name, value, tag = "input") {
      const field = node(tag);
      field.name = key;
      field.value = value || "";
      controls[key] = field;
      form.append(labelled(name, field));
      return field;
    }
    const title = input("title","Название",record?.title);
    title.required = true;
    const area = node("select");
    area.name = "direction";
    area.append(option("", "Без направления"), ...directions.map(r => option(r.id,r.title)));
    const initialDir = record ? record.direction : params.get("direction");
    if (initialDir && !directions.some(r => r.id === initialDir)) area.append(option(initialDir, "Направление не найдено · " + initialDir));
    area.value = initialDir || "";
    controls.direction = area;
    const dirGroup = labelled("Направление",area);
    form.append(dirGroup);
    const aliases = input("aliases","Другие названия (по одному в строке)",record?.aliases?.join("\n"),"textarea");
    aliases.rows = 2;
    const body = input("body","Текст",record?.body,"textarea");
    body.rows = 6;
    const meta = node("div","editor-metadata");
    for (const [key,caption] of Object.entries(fields)) {
      const field = input(key,caption,record?.[key]);
      if (key === "projectStart") field.type = "date";
      meta.append(field.parentElement);
    }
    form.append(meta);
    function syncType() {
      dirGroup.hidden = type.value === "direction";
      meta.hidden = type.value !== "project";
    }
    type.addEventListener("change",syncType);
    syncType();
    const feedback = node("div", "editor-feedback");
    const submit = node("button","button button-primary","Сохранить");
    submit.type = "submit";
    const actions = node("div","work-tools");
    actions.append(submit, link("Отмена",id ? recordURL(id) : embedded ? "#activity" : "#records","button button-secondary"));
    form.append(feedback, actions);
    main.append(form);
    const initial = () => Object.fromEntries(Object.entries(controls).map(([k,f]) => [k,f.value]));
    let baseline = initial();
    let requestId = crypto.randomUUID();
    let sentSignature;
    async function resolveConflict() {
      const latest = await api("record?" + new URLSearchParams({id}));
      if (!form.isConnected) return;
      const compare = node("details","record-source");
      compare.open = true;
      compare.append(node("summary", "", "Текущая версия файла"), node("pre","record-body",latest.raw));
      const merge = button("Сверил с файлом — продолжить правку","button button-secondary",() => {
        const values = {...latest, aliases: latest.aliases.join("\n")};
        for (const [key,field] of Object.entries(controls)) {
          const current = typeof values[key] === "string" ? values[key].replace(/\r\n?/g, "\n") : "";
          if (field.value === baseline[key]) field.value = current;
          baseline[key] = current;
        }
        record = latest;
        feedback.replaceChildren(node("p","muted","Проверьте поля перед сохранением."));
        submit.disabled = false;
      });
      feedback.append(compare, merge);
    }
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (submit.disabled) return;
      const values = initial();
      const payload = {type: type.value, title: values.title.trim(), body: values.body,
        aliases: values.aliases.split("\n").map(s => s.trim()).filter(Boolean),
        direction: type.value === "direction" ? null : values.direction || null};
      if (!payload.title) { title.focus(); return; }
      if (type.value === "project") for (const key of Object.keys(fields)) payload[key] = values[key].trim() || null;
      if (record) {
        delete payload.type;
        for (const key of Object.keys(payload)) {
          if (values[key] === baseline[key]) delete payload[key];
        }
        payload.id = id;
        payload.revision = record.revision;
      } else {
        const signature = JSON.stringify(payload);
        if (sentSignature && sentSignature !== signature) requestId = crypto.randomUUID();
        sentSignature = signature;
        payload.requestId = requestId;
      }
      feedback.replaceChildren();
      submit.disabled = true;
      try {
        const saved = await api("records/" + (record ? "edit" : "create"), payload);
        if (form.isConnected) go(embedded ? "#project-gantt/" + encodeURIComponent(saved.id) : recordURL(saved.id));
      } catch (error) {
        if (!form.isConnected) return;
        feedback.append(notice(error.message));
        if (record && error.status === 409) {
          try { await resolveConflict(); }
          catch (readError) { feedback.append(notice(readError.message)); submit.disabled = false; }
        } else submit.disabled = false;
      }
    });
  }

  async function render() {
    const version = ++pageVersion;
    if (embedded && !/^#(?:plans$|plan\/|project-gantt\/|activity(?:-help)?(?:$|\?)|okr(?:$|[/?])|new\/project(?:$|\?))/.test(location.hash)) history.replaceState(null, "", "#plans");
    if (location.hash.split("?")[0] === "#ask") history.replaceState(null, "", "#overview");
    const raw = location.hash.slice(1) || "overview";
    const split = raw.indexOf("?");
    const route = split < 0 ? raw : raw.slice(0,split);
    const params = new URLSearchParams(split < 0 ? "" : raw.slice(split + 1));
    const [page, recordId] = route.split("/");
    const isActivity = ["activity", "activity-help", "okr", "project-gantt"].includes(page);
    const isPlan = page === "plans" || page === "plan" || isActivity;
    const nav = isActivity ? "activity" : isPlan ? "plans" : page === "person" ? "people" : ["new","edit","record"].includes(page) ? "records" : page;
    main.classList.toggle("planning-main", isPlan);
    main.setAttribute("aria-busy","true");
    try {
      if (page === "activity-help") {
        main.replaceChildren(activityHelp(params));
      } else if (isPlan) {
        const view = node("div", "planning-view");
        main.replaceChildren(view);
        await planning(view, recordId && decodeURIComponent(recordId), params.get("task"), page);
      } else {
        const loaded = await api("workspace");
        if (version !== pageVersion) return;
        data = loaded;
        byId = new Map(data.records.map(r => [r.id,r]));
        directions = data.records.filter(r => r.type === "direction");
        main.replaceChildren();
        if (page === "records") recordsPage(params);
        else if (page === "map") mapPage();
        else if (["inbox","people","person"].includes(page)) {
          const view = node("div", "intake-view");
          main.append(view);
          await intake[page](view, {params, id: decodeURIComponent(recordId || ""), vault: data.name, directions});
        }
        else if (page === "record" && recordId) await recordPage(decodeURIComponent(recordId),params,version);
        else if (page === "edit" && recordId) await editPage(decodeURIComponent(recordId),null,params,version);
        else if (page === "new") await editPage(null,recordId,params,version);
        else await overview();
      }
      if (version !== pageVersion) return;
      document.querySelectorAll("[data-page]").forEach(a => {
        if (a.dataset.page === nav || (nav === "" && a.dataset.page === "overview")) a.setAttribute("aria-current","page");
        else a.removeAttribute("aria-current");
      });
      document.title = (main.querySelector("h1")?.textContent || "Хранилище") + " · Второй мозг";
    } catch (error) {
      if (version === pageVersion) main.replaceChildren(notice(error.message),button("Повторить", "button button-secondary", render));
    } finally {
      if (version === pageVersion) main.removeAttribute("aria-busy");
    }
  }
  document.querySelector(".skip-link").addEventListener("click",event => { event.preventDefault(); main.focus(); });
  window.addEventListener("hashchange", () => { main.focus({preventScroll: true}); window.scrollTo(0,0); render(); });
  render();
})();
