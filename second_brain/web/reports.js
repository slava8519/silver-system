import {cardLinks} from "./gantt-detail.js";
import {copyControls, reportPlainText} from "./report-document.js";
import {recordDocument} from "./record-document.js";
import {issueForm, seriesForm, templateForm} from "./report-forms.js";
import {templateChanges} from "./report-template-changes.js";

const cadenceNames = {daily: "Ежедневно", weekly: "Еженедельно", biweekly: "Раз в две недели", monthly: "Ежемесячно", quarterly: "Ежеквартально", yearly: "Ежегодно"};
const sections = {issues: "Выпуски", series: "Серии", templates: "Шаблоны"};
export const issueStatusNames = {draft: "Черновик", final: "Готовый выпуск", posted: "Опубликован"};
const templateOrigins = {custom: "Пользовательские", builtin: "Коробочные"};
const issueFilters = {draft: "Черновики", final: "Готовые", posted: "Опубликованные"};
const dates = new Intl.DateTimeFormat("ru", {day: "numeric", month: "short", year: "numeric", timeZone: "UTC"});
function period(start, end) {
  if (!start) return "";
  const a = new Date(start + "T00:00:00Z"), b = new Date((end || start) + "T00:00:00Z");
  return dates.formatRange ? dates.formatRange(a, b) : (start === end ? dates.format(a) : dates.format(a) + " — " + dates.format(b));
}
function route(name, args = {}) {
  const query = new URLSearchParams(Object.entries(args).filter(([,v]) => v !== null && v !== undefined && v !== ""));
  return "#reports" + (name === "issues" ? "" : "/" + name) + (query.size ? "?" + query : "");
}

export function reportRequestText(vault, {template, series, start, end, title, asOf}, scope = "") {
  const lines = ["Подготовь черновик отчёта по записям этого хранилища."];
  if (title) lines.push("Название выпуска: " + title);
  if (template) lines.push("Шаблон «" + template.title + "», ID " + template.id + ", версия " + template.version + ".");
  if (series) lines.push("Серия «" + series.title + "», ID " + series.id + ".");
  if (scope.trim()) lines.push("Тема, направление или участник: " + scope.trim());
  if (start || end) lines.push("Период: " + [start, end].filter(Boolean).join(" — ") + ".");
  if (asOf) lines.push("Данные на " + asOf + ".");
  lines.push("Сверь факты и даты с источниками. Сохрани текст и основания в хранилище командой report-save с флагом --draft. Дай ссылку на черновик, чтобы я мог проверить и финализировать его в разделе «Отчёты».", "", "Путь к хранилищу", vault.root);
  return lines.join("\n");
}

export function reportsView(ui) {
  const {node, link, button, api, pageHead, notice} = ui;
  const primary = "button button-primary", secondary = "button button-secondary";
  function get(name, args = {}) { return api("reports/" + name + "?" + new URLSearchParams(args)); }
  async function all(name) {
    const items = [];
    let offset = 0;
    do {
      const page = await get(name, {offset, limit: 20});
      items.push(...page.items);
      offset = page.nextOffset;
    } while (offset);
    return items;
  }
  const actions = {
    issues: () => link("Создать отчёт", route("new"), primary),
    series: () => link("Создать серию", route("new-series"), primary),
    templates: () => link("Создать шаблон", route("edit-template"), primary)
  };
  function bar(view, active, vault, action, reader = false) {
    if (reader) view.append(node("p", "work-vault report-reader-vault", vault.name));
    else view.append(pageHead(vault.name, "Отчёты", ...(action ? [action] : [])));
    const nav = node("nav", "report-nav");
    nav.setAttribute("aria-label", "Разделы отчётов");
    for (const [key, title] of Object.entries(sections)) {
      const a = link(reader && key === "issues" ? "Все выпуски" : title, route(key));
      if (key === active) a.setAttribute("aria-current","page");
      nav.append(a);
    }
    view.append(nav);
  }
  function crumb(view, section, label) {
    const back = link("← " + label, route(section), "report-back");
    view.append(back);
  }
  function titled(title, text) {
    const section = node("section", "report-empty");
    section.append(node("h3", "", title), node("p", "", text));
    return section;
  }
  function fillTemplateOptions(select, templates) {
    for (const [origin, label] of Object.entries(templateOrigins)) {
      const group = node("optgroup");
      group.label = label;
      for (const item of templates.filter(template => template.origin === origin)) {
        group.append(ui.option(item.id, item.title));
      }
      if (group.childElementCount) select.append(group);
    }
  }
  function templateCard(item) {
    const card = node("article", "report-template-card");
    const a = link(item.title, route("template", {id: item.id}), "report-item-name");
    const heading = node("h3");
    heading.append(a);
    const version = (item.origin === "builtin" && item.version > 1 ? "Своя редакция · версия " : "Версия ") + item.version;
    card.append(node("span", "report-version", version), heading);
    if (item.audience) card.append(node("p", "", item.audience));
    const compose = link("Подготовить отчёт →", route("new", {template: item.id}), "text-link");
    compose.setAttribute("aria-label", "Подготовить отчёт по шаблону «" + item.title + "»");
    card.append(compose);
    return card;
  }
  function issueRow(item) {
    const row = link("", route("issue", {id: item.id}), "report-row");
    let kind = item.seriesTitle || (item.seriesId ? "Выпуск серии" : "Разовый отчёт");
    if (item.kind === "weekly_review") kind = "Недельный обзор плана";
    const details = node("div");
    const status = issueStatusNames[item.editorialStatus];
    details.append(node("h3", "", item.title), node("small", "", [status, kind].filter(Boolean).join(" · ")));
    row.append(details, node("span", "report-period", period(item.start, item.end)), node("span", "report-arrow", "→"));
    row.lastElementChild.setAttribute("aria-hidden", "true");
    return row;
  }
  function seriesRow(item) {
    const row = link("", route("series-detail", {id: item.id}), "report-row");
    const title = node("div");
    title.append(node("h3", "", item.title), node("small", "", cadenceNames[item.cadence]));
    row.append(title, node("span", "report-period", "Следующий период · " + period(item.nextStart, item.nextEnd)), node("span", "report-arrow", "→"));
    row.lastElementChild.setAttribute("aria-hidden", "true");
    return row;
  }
  async function list(box, name, args = {}, onTotal) {
    const renderRow = {issues: issueRow, series: seriesRow, templates: templateCard}[name];
    const rows = node("div", name === "templates" ? "report-template-grid" : "report-list");
    const status = node("div");
    const more = button("Показать ещё", secondary, () => load(true));
    if (name === "templates") more.setAttribute("aria-label", "Показать ещё шаблоны · " + templateOrigins[args.origin]);
    more.hidden = true;
    box.append(rows, status, more);
    let offset = 0;
    async function load(append) {
      if (more.disabled) return;
      more.disabled = true;
      try {
        const page = await get(name, {...args, offset, limit: name === "templates" ? 6 : 12});
        if (!box.isConnected) return;
        const added = page.items.map(renderRow);
        rows.append(...added);
        if (append && added.length) (added[0].matches("a") ? added[0] : added[0].querySelector("a")).focus();
        status.replaceChildren();
        more.hidden = !page.nextOffset;
        offset = page.nextOffset;
        onTotal?.(page.total);
        if (!page.total) {
          if (name === "issues" && (args.match || args.status)) {
            status.append(titled("Выпуски не найдены", "Измените название или сбросьте фильтры."));
          } else if (name === "issues") {
            status.append(titled("Пока нет выпусков", args.seriesId ? "Подготовьте первый отчёт за указанный период." : "Выберите шаблон для первого отчёта."));
            if (!args.seriesId) await templateGroups(status);
          } else if (name === "series") status.append(titled("Пока нет серий", "Для повторяющегося отчёта задайте шаблон, периодичность и дату начала. У каждого периода будет свой выпуск."));
          else if (!args.match) status.append(node("p", "report-meta", "Здесь появятся созданные вами шаблоны."));
        }
      } catch (error) {
        status.replaceChildren(notice(error.message), button("Повторить загрузку", secondary, () => load(append)));
      } finally { more.disabled = false; }
    }
    await load(false);
  }
  async function templateGroups(box, {origin, match} = {}) {
    const groups = Object.entries(templateOrigins).filter(([key]) => !origin || key === origin);
    const panels = groups.map(([key, label]) => {
      const section = node("section", "report-template-group");
      const heading = node("h2", "work-heading", label);
      const count = node("span", "report-group-count");
      heading.append(" ", count);
      section.append(heading);
      box.append(section);
      return {key, section, count};
    });
    const empty = titled("Шаблоны не найдены", "Измените поисковый запрос или сбросьте фильтры.");
    empty.hidden = true;
    if (match) box.append(empty);
    await Promise.all(panels.map(({key, section, count}) =>
      list(section, "templates", {origin: key, ...(match ? {match} : {})}, total => {
        count.textContent = String(total);
        section.hidden = Boolean(match) && total === 0;
        empty.hidden = panels.some(panel => !panel.section.hidden);
      })));
  }
  function prose(vault, body, path) {
    const doc = recordDocument({path, vaultName:vault.name}, body, ui, {nativeLink:cardLinks(ui, vault.root), reportLinks:true});
    doc.className = "report-prose";
    return doc;
  }
  function prompt(vault, values = () => ({})) {
    const container = node("aside", "report-prompt");
    container.append(node("h3", "", "Подготовить в Codex"), node("p", "", "Скопируйте запрос в чат. Codex сохранит черновик с источниками. Вы сможете проверить и финализировать его в разделе «Выпуски»."));
    const scope = node("input");
    container.append(ui.labelled("Тема, направление или участник (по желанию)", scope));
    const make = () => reportRequestText(vault, values(), scope.value);
    container.append(copyControls(ui, [{label: "Скопировать запрос для Codex", text: make, success: "Запрос скопирован."}]));
    return container;
  }
  async function catalog(view, screen, vault, params) {
    bar(view, screen, vault, actions[screen]());
    const box = node("section", "report-catalog");
    box.append(node("h2", "sr-only", sections[screen]));
    view.append(box);
    const match = params?.get("match")?.trim() || "";
    if (screen === "series") return list(box, screen);
    const isTemplate = screen === "templates";
    const filterName = isTemplate ? "origin" : "status";
    const choices = isTemplate ? templateOrigins : issueFilters;
    const selection = params?.get(filterName);
    const selected = Object.hasOwn(choices, selection) ? selection : "";
    const search = node("form", "report-search");
    search.setAttribute("role", "search");
    const input = node("input");
    input.type = "search"; input.name = "match"; input.value = match; input.maxLength = 180;
    search.append(ui.labelled(screen === "issues" ? "Поиск по названию выпуска" : "Поиск по названию или адресату", input));
    const filter = node("select");
    filter.name = filterName;
    filter.append(ui.option("", isTemplate ? "Все шаблоны" : "Все выпуски"));
    for (const [key, label] of Object.entries(choices)) filter.append(ui.option(key, label));
    filter.value = selected;
    search.append(ui.labelled(isTemplate ? "Происхождение" : "Статус", filter, "work-field report-catalog-filter"));
    filter.addEventListener("change", () => search.requestSubmit());
    const submit = node("button", secondary, "Найти"); submit.type = "submit";
    search.append(submit);
    if (match || selected) search.append(link("Сбросить", route(screen), "text-link"));
    search.addEventListener("submit", event => {
      event.preventDefault();
      ui.go(route(screen, {match: input.value.trim(), [filterName]: filter.value}));
    });
    box.append(search);
    if (isTemplate) return templateGroups(box, {origin: selected, match});
    await list(box, screen, {...(match ? {match} : {}), ...(selected ? {status: selected} : {})});
    view.append(prompt(vault));
  }

  async function readIssue(view, id, vault) {
    const issue = await get("issue", {id});
    view.classList.add("report-issue-detail");
    bar(view, "issues", vault, null, true);
    const layout = node("div", "report-reading");
    const paper = node("article", "report-paper");
    const aside = node("aside", "report-side");
    const heading = node("h1", "report-paper-title", issue.title);
    const stamp = period(issue.start, issue.end);
    paper.append(heading);
    const status = node("p", "report-version", issueStatusNames[issue.editorialStatus]);
    if (issue.editorialStatus) paper.append(status);
    if (issue.releaseDate) paper.append(node("p", "report-meta", "Дата выпуска " + period(issue.releaseDate, issue.releaseDate)));
    if (stamp) paper.append(node("p", "report-meta", stamp));
    if (issue.asOf) paper.append(node("p", "report-meta", "Данные на " + period(issue.asOf, issue.asOf)));
    const documentView = prose(vault, issue.body, issue.path);
    const first = documentView.firstElementChild;
    if (first?.tagName === "H2" && first.textContent === issue.title) first.remove();
    paper.append(copyControls(ui, [
      {label: "Скопировать текст", primary: true, text: () => reportPlainText(issue.body), success: "Текст выпуска скопирован."},
      {label: "Markdown", ariaLabel: "Скопировать Markdown", text: () => issue.body, success: "Markdown скопирован."}
    ]), documentView);
    aside.append(node("h3", "", "Сведения о выпуске"));
    if (issue.templateId || issue.seriesId) {
      const info = node("div", "report-side-note");
      if (issue.seriesId) info.append(link(issue.seriesTitle || "Открыть серию", route("series-detail", {id: issue.seriesId}), "text-link"));
      if (issue.templateId) info.append(link("Шаблон · версия " + issue.templateVersion, route("template", {id: issue.templateId, version: issue.templateVersion}), "text-link"));
      if (issue.templateChanged) info.append(node("small", "muted", "Файл этой версии шаблона изменился после сохранения выпуска."));
      aside.append(info);
    }
    const sources = issue.sources || [];
    if (sources.length) {
      const group = node("details", "report-sources");
      group.append(node("summary", "", "Источники · " + sources.length));
      sources.forEach(source => {
        const entry = node("div", "report-source");
        if (source.claim) entry.append(node("strong", "", source.claim));
        if (source.quote) entry.append(node("blockquote", "", source.quote));
        const label = source.path + " · строка " + source.line;
        if (source.available !== false && source.href) entry.append(link(label, source.href, "text-link"));
        else entry.append(node("small", "", label + " · строка недоступна"));
        group.append(entry);
      });
      paper.append(group);
    }
    if (issue.path) aside.append(cardLinks(ui, vault.root)("Открыть файл в Obsidian ↗", issue.path));
    if (issue.editorialStatus === "draft") {
      const feedback = node("div");
      const finalize = button("Финализировать выпуск", primary, async () => {
        if (finalize.disabled) return;
        finalize.disabled = true;
        feedback.replaceChildren();
        try {
          const result = await api("reports/finalize", {id, revision: issue.revision});
          status.textContent = issueStatusNames[result.editorialStatus];
          const done = node("p", "report-status", "Выпуск финализирован.");
          done.setAttribute("role", "status");
          done.tabIndex = -1;
          finalize.replaceWith(done);
          done.focus({preventScroll: true});
        } catch (error) {
          if (error.status === 409) {
            // The first request may have succeeded even if its response never reached this tab.
            try {
              const current = await get("issue", {id});
              if (!view.isConnected) return;
              if (current.editorialStatus === "final" || current.editorialStatus === "posted") {
                ui.go(route("issue", {id}));
                return;
              }
            } catch { /* The owner can retry the read using the action below. */ }
            feedback.append(notice(error.message));
            feedback.append(button("Перечитать выпуск", secondary, () => ui.go(route("issue", {id}))));
          } else {
            feedback.append(notice(error.message));
            finalize.disabled = false;
          }
        }
      });
      aside.append(finalize, feedback);
    }
    layout.append(paper, aside); view.append(layout);
  }

  async function readTemplate(view, id, version, vault) {
    const item = await get("template", {id, ...(version ? {version} : {})});
    bar(view, "templates", vault, link("Создать отчёт по шаблону", route("new", {template: id, version: item.version}), primary));
    crumb(view, "templates", "Все шаблоны");
    const layout = node("div", "report-reading");
    const paper = node("article", "report-paper");
    paper.append(node("span", "report-version", "Версия " + item.version), node("h2", "report-paper-title", item.title));
    if (item.audience) paper.append(node("h3", "report-caption", "Для кого"), node("p", "report-value", item.audience));
    if (item.guidance?.length > 1400) {
      const guidance = node("details", "report-instructions report-long-guidance");
      guidance.append(node("summary", "", "Правила выпуска"), node("p", "report-value", item.guidance));
      paper.append(guidance);
    } else if (item.guidance) paper.append(node("h3", "report-caption", "Правила выпуска"), node("p", "report-value", item.guidance));
    paper.append(node("h3", "report-caption", "Структура текста"), prose(vault, item.body, item.path));
    const aside = node("aside", "report-side");
    const versions = [...item.history].sort((a,b) => b.version - a.version);
    const current = versions[0]?.version || item.version;
    aside.append(node("h3", "", "Версии шаблона"));
    const history = node("ul", "report-history");
    for (const rev of versions) {
      const row = node("li");
      const a = link("Версия " + rev.version + (rev.version === current ? " · текущая" : ""), route("template", {id, version: rev.version}), "text-link");
      if (rev.version === item.version) a.setAttribute("aria-current", "true");
      row.append(a); history.append(row);
    }
    aside.append(history);
    const changes = templateChanges(ui, get, item);
    if (changes) paper.append(changes);
    aside.append(link(current === item.version ? "Редактировать шаблон" : "Взять за основу новой версии", route("edit-template", {id, version: item.version}), secondary));
    layout.append(paper, aside); view.append(layout, prompt(vault, () => ({template: item})));
  }

  async function readSeries(view, id, vault) {
    const series = await get("series", {id});
    const template = await get("template", {id: series.templateId});
    bar(view, "series", vault, link("Подготовить выпуск", route("new", {series: id}), primary));
    crumb(view, "series", "Все серии");
    const box = node("section", "report-series");
    box.append(node("h2", "report-paper-title", series.title));
    const meta = node("p", "report-meta", cadenceNames[series.cadence] + " · ");
    meta.append(link(template.title, route("template", {id: template.id}), "text-link")); box.append(meta);
    box.append(node("p", "report-next", "Следующий период · " + period(series.nextStart, series.nextEnd)));
    if (series.nextStart && series.nextStart > new Intl.DateTimeFormat("sv-SE").format(new Date())) {
      box.append(node("small", "muted", "Период ещё не начался."));
    }
    box.append(node("h3", "report-subhead", "Выпуски серии"));
    view.append(box);
    await list(box, "issues", {seriesId: id});
    const request = prompt(vault, () => ({template, series, start: series.nextStart, end: series.nextEnd}));
    view.append(request);
    const schedule = node("details", "report-schedule");
    schedule.append(node("summary", "", "Настроить расписание в Codex"), node("p", "", "Скопируйте запрос в чат Codex, чтобы выбрать время подготовки выпусков."));
    schedule.append(copyControls(ui, [{
      label: "Скопировать запрос для расписания",
      text: () => ["Настрой периодическую подготовку отчётов в Codex. Серия «" + series.title + "», ID " + id + ".",
        "Периодичность: " + cadenceNames[series.cadence] + ". Начало первого периода: " + series.start + ".",
        "Уточни удобное время и часовой пояс. При каждом запуске проверяй, какой период завершился и ждёт выпуска. Сверяй факты с источниками и используй актуальную версию шаблона. Сохраняй готовый выпуск в этом хранилище.", "", "Путь к хранилищу", vault.root].join("\n"),
      success: "Запрос для настройки расписания скопирован."
    }]));
    view.append(schedule);
  }

  return async function show(view, page, params) {
    const vault = await api("planning-vault");
    if (!view.isConnected) return;
    if (sections[page]) return catalog(view, page, vault, params);
    const id = params.get("id");
    if (page === "issue") return readIssue(view, id, vault);
    if (page === "template") return readTemplate(view, id, params.get("version"), vault);
    if (page === "series-detail") return readSeries(view, id, vault);
    const forms = {new: [issueForm, "issues"], "edit-template": [templateForm, "templates"], "new-series": [seriesForm, "series"]};
    if (!forms[page]) return catalog(view, "issues", vault);
    const [form, tab] = forms[page];
    bar(view, tab, vault); crumb(view, tab, sections[tab]);
    await form(view, {...ui, route, get, all, prose, prompt, period, cadenceNames, fillTemplateOptions}, vault, params);
  };
}
