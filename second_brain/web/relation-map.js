import {openObsidianSource} from "./obsidian-workspace.js";
const pageSize = 5;
const typeNames = {direction: "Направление", project: "Проект", note: "Заметка", task: "Задача", plan: "План"};
const countForms = {
  project: ["проект", "проекта", "проектов"], note: ["заметка", "заметки", "заметок"],
  task: ["задача", "задачи", "задач"]
};
const number = new Intl.NumberFormat("ru");
const pathWith = (path, params) => params.size ? path + "?" + params : path;
const mapURL = fields => pathWith("#map", new URLSearchParams(fields));
const cardType = card => card.kind === "milestone" ? "Веха" : typeNames[card.type] || card.type;

// The vault writer escapes Markdown punctuation and HTML entities in link labels.
// Keep both the human-readable label and the original evidence as plain DOM text.
export function relationSourceLabel(line) {
  const match = /^\s*-\s+\[((?:\\.|[^\]\\])*)\]\(/.exec(line);
  if (!match) return "";
  const entities = {amp: "&", gt: ">", lt: "<"};
  return match[1].replace(/\\([\\\[\]*_`])/g, "$1").replace(/&(amp|gt|lt);/g, (_, name) => entities[name]);
}

export function relationCardURL(card, root) {
  const id = encodeURIComponent(card.id);
  const markdown = () => "obsidian://open?" + new URLSearchParams({path: root + "/" + card.path});
  if (card.type === "plan") return card.status === "unavailable" ? markdown() : "#plan/" + id;
  if (card.type !== "task") return "#record/" + id;
  const query = new URLSearchParams({task: card.id});
  if (card.projectId) return "#project-gantt/" + encodeURIComponent(card.projectId) + "?" + query;
  if (card.planId) return "#plan/" + encodeURIComponent(card.planId) + "?" + query;
  return markdown();
}

export async function mountRelationMap(view, {api, params, node, link, button, pageHead, counted, embedded}) {
  let state = new URLSearchParams(params);
  let serial = 0;
  let timer;
  const content = node("section", "rm-content");
  const panel = node("div", "work-panel rm-panel");
  const warnings = node("aside", "rm-warnings");
  warnings.hidden = true;
  const help = link("О разделе", "", "text-link activity-help-link");
  const helpIcon = node("span", "about-icon", "i");
  helpIcon.setAttribute("aria-hidden", "true");
  help.prepend(helpIcon);
  const header = pageHead("", "Карта связей", help);
  const search = searchField("Найти карточку по названию или другому имени", state.get("q"), true);
  view.classList.add("relation-map");
  panel.append(search.label, warnings, content);
  view.append(header, panel);

  function searchField(caption, value, global = false) {
    const label = node("label", global ? "rm-search rm-global" : "rm-search rm-filter");
    const field = node("input");
    field.type = "search";
    field.autocomplete = "off";
    field.placeholder = global ? "Название или другое имя" : "Поиск в этой группе";
    field.setAttribute("aria-label", caption);
    field.value = value || "";
    label.append(field);
    return {label, field};
  }

  function save(next, push = false) {
    const href = pathWith("#map", next);
    if (location.hash !== href) {
      if (push) history.pushState({mapParent: location.hash}, "", href);
      else history.replaceState(history.state, "", href);
    }
    state = next;
    help.href = pathWith("#map-help", state);
  }
  help.href = pathWith("#map-help", state);

  // Native Back/Forward restores the previous query, open group and page.
  // Modified clicks keep the link's normal open-in-new-tab behavior.
  view.addEventListener("click", event => {
    const anchor = event.target.closest("a");
    if (!anchor || event.defaultPrevented || event.button !== 0 || event.metaKey ||
        event.ctrlKey || event.shiftKey || event.altKey) return;
    const target = anchor.getAttribute("href");
    if (!/^#map(?:-help)?(?:\?|$)/.test(target) || target === location.hash) return;
    event.preventDefault();
    if (anchor.dataset.back) {
      history.back();
      return;
    }
    const oldURL = location.href;
    history.pushState({mapParent: location.hash}, "", target);
    window.dispatchEvent(new HashChangeEvent("hashchange", {oldURL, newURL: location.href}));
  });

  function nav(parent) {
    if (!parent && !state.size) return;
    const bar = node("nav", "rm-nav");
    bar.setAttribute("aria-label", "Навигация по карте");
    if (/^#map(?:\?|$)/.test(history.state?.mapParent || "")) {
      const back = link("← Назад", history.state.mapParent, "back-link");
      back.dataset.back = "true";
      bar.append(back);
    }
    if (parent) bar.append(link("Все направления", "#map", "back-link"));
    if (bar.childElementCount) content.append(bar);
  }

  function apiPage(action, fields = {}) {
    const query = new URLSearchParams({action, limit: String(pageSize), ...fields});
    return api("map?" + query);
  }

  function problem(parent, error, retry) {
    const msg = node("p", "rm-error", error.message);
    msg.setAttribute("role", "alert");
    parent.replaceChildren(msg, button("Повторить", "rm-action", retry));
  }

  function showWarnings(result) {
    warnings.replaceChildren();
    warnings.hidden = !result.warnings?.length;
    if (warnings.hidden) return;
    warnings.append(node("strong", "", "Часть связей недоступна"));
    const list = node("ul");
    for (const item of result.warnings) {
      const li = node("li");
      li.append(node("code", "", item.path), " · ", item.message);
      list.append(li);
    }
    warnings.append(list);
    if (result.warningTotal > result.warnings.length)
      warnings.append(node("small", "", "Показано " + result.warnings.length + " из " + result.warningTotal + "."));
  }

  function sourceDetails(ref) {
    const details = node("details", "rm-source");
    const summary = node("summary", "", "Основание");
    const body = node("div", "rm-evidence");
    details.append(summary, body);
    let version = 0;
    details.addEventListener("toggle", async () => {
      const token = ++version;
      if (!details.open) return;
      body.removeAttribute("role");
      body.textContent = "Открываем строку…";
      const query = new URLSearchParams({id: ref.id, line: ref.line});
      if (ref.revision) query.set("revision", ref.revision);
      try {
        const result = await api("map/source?" + query);
        if (token !== version || !details.isConnected || !details.open) return;
        body.replaceChildren(node("small", "record-path", result.path + " · строка " + result.line));
        const label = relationSourceLabel(result.text);
        const clipped = result.truncatedFields?.includes("text");
        const raw = node("code", "", result.text);
        if (label) {
          const markdown = node("details", "rm-original");
          markdown.append(node("summary", "", clipped ? "Фрагмент строки Markdown" : "Строка Markdown"), raw);
          body.append(node("p", "rm-source-title", label), markdown);
        } else {
          body.append(raw);
          if (clipped) body.append(node("small", "", "Показан фрагмент строки."));
        }
      } catch (error) {
        if (token === version && details.isConnected && details.open) {
          body.textContent = error.message;
          body.setAttribute("role", "alert");
          body.append(button("Обновить карту", "rm-action", draw));
        }
      }
    });
    return details;
  }

  function row(card, caption, evidence = false, showPath = false) {
    const li = node("li", "rm-row");
    const item = card.missing ? node("span", "rm-item rm-missing") : link("", mapURL({id: card.id}), "rm-item");
    item.append(node("strong", "", card.title || card.id));
    if (caption) item.append(node("small", "rm-caption", caption));
    if (showPath) item.append(node("small", "record-path", card.path));
    li.append(item);
    if (evidence && card.source) li.append(sourceDetails(card.source));
    else if (!card.missing) {
      const arrow = node("span", "rm-next", "›");
      arrow.setAttribute("aria-hidden", "true");
      li.append(arrow);
    }
    return li;
  }

  function annotation(card) {
    const aliases = new Set([card.matchedAlias, ...(card.aliases || [])].filter(Boolean));
    const values = [cardType(card), ...aliases];
    if (card.type === "plan" && card.status === "closed") values.push("Закрыт");
    if (card.type === "plan" && card.status === "unavailable") values.push("Состав недоступен");
    if (card.missing) values.push("Карточка недоступна");
    return values.filter(Boolean).join(" · ");
  }

  function relatedRow(card) {
    const caption = [card.relation, annotation(card)].filter(Boolean).join(" · ");
    return row(card, caption, Boolean(card.relation));
  }

  function page(parent, result, make, change, emptyText) {
    parent.replaceChildren();
    const {items, total, offset, limit} = result;
    if (items.length) {
      const list = node("ul", "rm-list");
      list.append(...items.map(make));
      parent.append(list);
    } else parent.append(node("p", "muted rm-empty", total ? "На этой странице больше нет карточек." : emptyText));
    if (offset === 0 && total <= limit) return;
    const pager = node("nav", "rm-pager");
    pager.setAttribute("aria-label", "Страницы списка");
    if (items.length) pager.append(node("span", "rm-range", number.format(offset + 1) + "–" +
      number.format(offset + items.length) + " из " + number.format(total)));
    if (offset > 0) {
      const last = Math.max(0, Math.floor((total - 1) / limit) * limit);
      pager.append(button("← Предыдущие", "rm-action", () => change(Math.min(last, Math.max(0, offset - limit)))));
    }
    if (offset + items.length < total) pager.append(button("Следующие →", "rm-action", () => change(offset + limit)));
    parent.append(pager);
  }

  function setOffset(next, offset) {
    if (offset > 0) next.set("offset", String(offset));
    else next.delete("offset");
    return next;
  }

  function offset() {
    const value = Number(state.get("offset"));
    return Number.isSafeInteger(value) && value >= 0 ? value : 0;
  }

  function groupList(holder, key, id, total, initial) {
    const entries = node("div", "rm-entries");
    const filter = searchField("Поиск в группе «" + holder.getAttribute("aria-label") + "»", state.get("filter"));
    let version = 0;
    let delay;
    if (total > pageSize || state.has("filter")) holder.append(filter.label);
    holder.append(entries);
    const active = token => token === version && entries.isConnected && !holder.hidden && state.get("group") === key;
    function display(result) {
      page(entries, result, relatedRow, value => {
        save(setOffset(new URLSearchParams(state), value));
        load();
      }, filter.field.value ? "Совпадений в группе нет." : "В группе пока нет карточек.");
    }
    async function load() {
      const token = ++version;
      entries.setAttribute("aria-busy", "true");
      const fields = {group: key, query: filter.field.value.trim(), offset: offset()};
      if (id) fields.id = id;
      try {
        const result = await apiPage("group", fields);
        if (!active(token)) return;
        showWarnings(result);
        display(result);
      } catch (error) {
        if (active(token)) problem(entries, error, load);
      } finally {
        if (active(token)) entries.removeAttribute("aria-busy");
      }
    }
    filter.field.addEventListener("input", () => {
      ++version;
      clearTimeout(delay);
      const next = setOffset(new URLSearchParams(state), 0);
      if (filter.field.value) next.set("filter", filter.field.value);
      else next.delete("filter");
      save(next);
      delay = setTimeout(() => { if (entries.isConnected && !holder.hidden) load(); }, 200);
    });
    if (initial) display(initial);
    else load();
  }

  function focusPage(result) {
    const card = result.focus;
    const heading = node("header", "rm-focus");
    const text = node("div");
    text.append(node("small", "rm-kind", annotation(card)), node("h2", "work-heading", card.title));
    const href = relationCardURL(card, result.root);
    const markdown = href.startsWith("obsidian:");
    const open = link(markdown ? "Открыть карточку в Obsidian ↗" : "Открыть карточку", href, "text-link rm-open");
    if (embedded && markdown) open.addEventListener("click", event => {
      event.preventDefault();
      openObsidianSource(card.path);
    });
    heading.append(text, open);
    content.append(heading);
    if (!result.groups.length && card.status !== "unavailable") content.append(node("p", "muted",
      result.warningTotal ? "В прочитанных записях прямых связей этой карточки не найдено."
        : "У этой карточки пока нет прямых связей на карте."));
    const large = new Map();
    for (const group of result.groups) {
      const small = group.total <= 3;
      const box = node("section", "rm-group");
      const trigger = button("", "rm-toggle", toggle);
      const title = node("h3");
      trigger.append(node("span", "", group.label), node("span", "rm-total", number.format(group.total)));
      title.append(trigger);
      const body = node("div", "rm-children");
      body.id = "rm-group-" + group.key;
      body.setAttribute("aria-label", group.label);
      trigger.setAttribute("aria-controls", body.id);
      box.append(title, body);
      content.append(box);
      function expand(open) {
        trigger.setAttribute("aria-expanded", String(open));
        body.hidden = !open;
        body.replaceChildren();
        if (!open) return;
        if (small) {
          const list = node("ul", "rm-list");
          list.append(...group.items.map(relatedRow));
          body.append(list);
        } else groupList(body, group.key, card.id, group.total);
      }
      function toggle() {
        const open = body.hidden;
        const next = new URLSearchParams(state);
        if (small) {
          const hidden = new Set(next.getAll("hide"));
          if (open) hidden.delete(group.key);
          else hidden.add(group.key);
          next.delete("hide");
          hidden.forEach(key => next.append("hide", key));
        } else {
          for (const [key, close] of large) if (key !== group.key) close(false);
          next.delete("filter");
          next.delete("offset");
          if (open) next.set("group", group.key);
          else next.delete("group");
        }
        save(next);
        expand(open);
      }
      if (!small) large.set(group.key, expand);
      expand(small ? !state.getAll("hide").includes(group.key) : state.get("group") === group.key);
    }
  }

  function directory(result) {
    content.append(node("h2", "work-heading", "Направления"));
    const entries = node("div");
    content.append(entries);
    page(entries, result, card => {
      const counts = Object.entries(card.counts).filter(([key, n]) => countForms[key] && n)
        .map(([key, n]) => counted(n, countForms[key]));
      return row(card, counts.join(" · ") || "Связанных карточек пока нет");
    }, changePage, "Направления пока не созданы.");
    const tools = node("nav", "rm-extra");
    tools.setAttribute("aria-label", "Другие карточки");
    for (const [key, title] of [["unassigned", "Без направления"], ["broken", "Направление не найдено"]]) {
      if (result[key]) tools.append(link(title + " · " + number.format(result[key]), mapURL({group: key}), "text-link"));
    }
    if (tools.childElementCount) content.append(tools);
  }

  function changePage(value) {
    save(setOffset(new URLSearchParams(state), value));
    draw();
  }

  async function draw() {
    const token = ++serial;
    const active = () => token === serial && view.isConnected;
    const query = state.get("q")?.trim(), id = state.get("id"), group = state.get("group");
    content.setAttribute("aria-busy", "true");
    try {
      const action = query ? "search" : id ? "focus" : group ? "group" : "home";
      const fields = action === "focus" ? {id} : {offset: offset()};
      if (query) fields.query = query;
      if (action === "group") Object.assign(fields, {group, query: state.get("filter") || ""});
      const result = await apiPage(action, fields);
      if (!active()) return;
      showWarnings(result);
      header.querySelector(".work-vault").textContent = result.name;
      content.replaceChildren();
      nav(action !== "home");
      if (action === "focus") focusPage(result);
      else if (action === "home") directory(result);
      else if (action === "group") {
        content.append(node("h2", "work-heading", result.label));
        const holder = node("div");
        holder.setAttribute("aria-label", result.label);
        content.append(holder);
        groupList(holder, group, null, result.total, result);
      } else {
        content.append(node("h2", "work-heading", "Результаты поиска · " + number.format(result.total)));
        const entries = node("div");
        content.append(entries);
        page(entries, result, card => row(card, annotation(card), false, true), changePage, "По этому запросу карточек не найдено.");
      }
    } catch (error) {
      if (active()) {
        problem(content, error, draw);
        content.append(link("Все направления", "#map", "back-link"));
      }
    } finally {
      if (active()) content.removeAttribute("aria-busy");
    }
  }

  search.field.addEventListener("input", () => {
    ++serial;
    clearTimeout(timer);
    const next = new URLSearchParams();
    if (search.field.value.trim()) next.set("q", search.field.value);
    save(next, next.has("q") && !state.has("q"));
    content.replaceChildren();
    content.setAttribute("aria-busy", "true");
    timer = setTimeout(() => { if (view.isConnected) draw(); }, 200);
  });
  await draw();
}
