import {sourcePicker} from "./gantt-okr-source.js";

const drafts = new Map();
const secondary = "button button-secondary";

function editor(ui, view, vault, heading, key) {
  const {node, button, labelled, notice, go, api} = ui;
  const form = node("form", "report-form");
  form.append(node("h2", "report-form-title", heading));
  const feedback = node("div", "report-feedback");
  view.append(form);
  const cacheKey = JSON.stringify([vault.root, key]);
  const draft = drafts.get(cacheKey);
  const fields = {};
  function field(name, label, tag = "input", value = "", required = false) {
    const input = node(tag);
    input.name = name; input.required = required;
    if (tag === "textarea") input.rows = 4;
    input.value = draft?.fields[name] ?? value;
    fields[name] = input;
    return {input, label: labelled(label, input)};
  }
  let requestId = draft?.requestId || crypto.randomUUID();
  let sentSignature = draft?.sentSignature;
  function finish(verb, endpoint, payload, target, settings = {}) {
    const submit = node("button", "button button-primary", verb);
    submit.type = "submit";
    const actions = node("div", "work-tools");
    const status = node("p", "report-status");
    status.setAttribute("role", "status");
    if (draft) status.textContent = "Черновик восстановлен в этой вкладке.";
    actions.append(submit);
    if (settings.alternateSubmit) actions.append(settings.alternateSubmit);
    actions.append(status); form.append(feedback, actions);
    function remember() {
      drafts.set(cacheKey, {fields: Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, v.value])), requestId, sentSignature, ...settings.extra?.()});
    }
    form.addEventListener("input", remember);
    form.addEventListener("change", remember);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (submit.disabled) return;
      submit.disabled = true;
      if (settings.alternateSubmit) settings.alternateSubmit.disabled = true;
      try {
        feedback.replaceChildren();
        const body = payload(event.submitter);
        const signature = JSON.stringify(body);
        if (sentSignature && sentSignature !== signature) requestId = crypto.randomUUID();
        sentSignature = signature;
        remember();
        const result = await api("reports/" + endpoint, body.id ? body : {...body, requestId});
        drafts.delete(cacheKey);
        if (form.isConnected) go(target(result));
      } catch (error) {
        if (!form.isConnected) return;
        feedback.replaceChildren(notice(error.message));
        if (error.status === 409 && settings.conflict) {
          try { await settings.conflict(feedback); } catch (readError) { feedback.append(notice(readError.message)); }
        }
        feedback.scrollIntoView({block: "nearest"});
      } finally {
        submit.disabled = false;
        if (settings.alternateSubmit) settings.alternateSubmit.disabled = false;
      }
    });
    return remember;
  }
  return {field, form, draft, finish, button};
}

function preview(ui, vault, field) {
  const {node, prose} = ui;
  const details = node("details", "report-preview");
  details.append(node("summary", "", "Предпросмотр текста"));
  const output = node("div");
  details.append(output);
  function refresh() {
    if (details.open) output.replaceChildren(prose(vault, field.value));
  }
  details.addEventListener("toggle", refresh);
  field.addEventListener("input", refresh);
  return details;
}

export async function templateForm(view, ui, vault, params) {
  const {node, button, route, get} = ui;
  const id = params.get("id"), version = params.get("version");
  const current = id ? await get("template", {id}) : null;
  const selected = version && Number(version) !== current?.version ? await get("template", {id, version}) : current;
  if (!view.isConnected) return;
  const e = editor(ui, view, vault, current ? "Новая версия шаблона" : "Новый шаблон", ["template", id, version]);
  let revision = e.draft?.revision || current?.revision;
  if (current) e.form.append(node("p", "report-meta", "Основа — версия " + selected.version + ". Текущая версия — " + current.version + "."));
  const title = e.field("title", "Название шаблона", "input", selected?.title, true);
  const audience = e.field("audience", "Для кого предназначен отчёт", "input", selected?.audience);
  const guidance = e.field("guidance", "Правила отбора и подачи", "textarea", selected?.guidance);
  guidance.input.rows = guidance.input.value.length > 900 ? 12 : 5;
  const body = e.field("body", "Структура текста в Markdown", "textarea", selected?.body, true);
  body.input.rows = 10;
  e.form.append(title.label, audience.label, guidance.label, body.label, preview(ui, vault, body.input));
  e.finish(current ? "Сохранить новую версию" : "Создать шаблон", "template", () => ({
    title: title.input.value, audience: audience.input.value, guidance: guidance.input.value, body: body.input.value,
    ...(id ? {id, revision} : {})
  }), saved => route("template", {id: saved.id, version: saved.version}), {
    extra: () => ({revision}),
    conflict: async feedback => {
      const latest = await get("template", {id});
      const details = node("details", "report-preview");
      details.open = true;
      details.append(node("summary", "", "Текущая версия " + latest.version + " · " + latest.title),
        node("p", "report-value", latest.audience), node("p", "report-value", latest.guidance), node("pre", "report-raw", latest.body));
      feedback.append(details, button("Сверил с текущей версией", secondary, () => {
        revision = latest.revision;
        e.form.dispatchEvent(new Event("change"));
        feedback.replaceChildren(node("p", "report-status", "Проверьте текст и нажмите «Сохранить новую версию»."));
      }));
    }
  });
}

function sourceList(ui, form, initial, changed) {
  const {node, button, labelled, notice} = ui;
  const sources = [...initial];
  const section = node("section", "report-source-editor");
  const header = node("div", "report-source-head"), rows = node("ul"), holder = node("div");
  const add = button("Добавить источник", secondary, () => open(false));
  header.append(node("h3", "report-subhead", "Источники"), add);
  section.append(header, rows, holder); form.append(section);
  function draw() {
    rows.replaceChildren();
    for (const [index, source] of sources.entries()) {
      const item = node("li");
      const copy = node("div");
      if (source.claim) copy.append(node("strong", "", source.claim));
      copy.append(node("p", "", source.path + " · строка " + source.line));
      if (source.quote) copy.append(node("blockquote", "", source.quote));
      const remove = button("Убрать", "intake-action", () => { sources.splice(index, 1); draw(); changed(); add.focus(); });
      remove.setAttribute("aria-label", "Убрать источник " + source.path + ", строка " + source.line);
      item.append(copy, remove); rows.append(item);
    }
    rows.hidden = !sources.length;
  }
  function close() { add.hidden = false; holder.replaceChildren(); add.focus(); }
  function open(manual) {
    add.hidden = true; holder.replaceChildren();
    const fields = node("div", "report-source-fields");
    const feedback = node("div");
    holder.append(fields);
    let selected;
    if (manual) {
      const path = node("input"), line = node("input"), quote = node("input");
      line.type = "number"; line.min = 1;
      fields.append(labelled("Путь к файлу от корня хранилища", path), labelled("Номер строки", line), labelled("Точная цитата (по желанию)", quote));
      selected = () => {
        if (!path.value.trim() || !(Number(line.value) >= 1)) throw new Error("Укажите путь к файлу и номер строки.");
        return {path: path.value.trim(), line: Number(line.value), ...(quote.value ? {quote: quote.value} : {})};
      };
    } else selected = sourcePicker(ui, fields);
    const claim = node("input");
    fields.append(labelled("Какой тезис подтверждает источник (по желанию)", claim));
    const tools = node("div", "work-tools");
    tools.append(button("Приложить источник", secondary, () => {
      try {
        const src = selected();
        if (claim.value.trim()) src.claim = claim.value.trim();
        sources.push(src); draw(); close(); changed();
      } catch (error) { feedback.replaceChildren(notice(error.message)); }
    }), button("Отмена", "intake-action", close));
    holder.append(button(manual ? "Выбрать строку в заметке или проекте" : "Указать путь и строку вручную", "intake-action", () => open(!manual)), feedback, tools);
    fields.querySelector("input")?.focus();
  }
  draw();
  return (validate = true) => {
    if (validate && holder.childElementCount) throw new Error("Приложите выбранный источник или закройте его форму.");
    return sources;
  };
}

export async function issueForm(view, ui, vault, params) {
  const {node, labelled, option, get, all, route, period, prompt, link} = ui;
  const [templates, seriesList] = await Promise.all([all("templates"), all("series")]);
  if (!view.isConnected) return;
  const e = editor(ui, view, vault, "Новый отчёт", ["issue", params.toString()]);
  const seriesField = e.field("seriesId", "Серия", "select");
  seriesField.input.append(option("", "Разовый отчёт"));
  seriesList.forEach(item => seriesField.input.append(option(item.id, item.title)));
  seriesField.input.value = e.draft?.fields.seriesId ?? params.get("series") ?? "";
  const templateField = e.field("templateId", "Шаблон", "select", "", true);
  ui.fillTemplateOptions(templateField.input, templates);
  templateField.input.value = e.draft?.fields.templateId || params.get("template") || templates[0]?.id || "";
  const head = node("div", "report-fields-row"); head.append(seriesField.label, templateField.label);
  const title = e.field("title", "Название выпуска", "input", "", true);
  const body = e.field("body", "Текст выпуска в Markdown", "textarea", "", true);
  body.input.rows = 12;
  const dates = node("details", "report-period-form");
  const dateSummary = node("summary", "", "Период отчёта (по желанию)");
  const dateRow = node("div", "report-fields-row");
  const start = e.field("start", "С", "input"), end = e.field("end", "По", "input");
  const asOf = e.field("asOf", "Данные на (по желанию)", "input");
  asOf.input.type = "date";
  start.input.type = "date"; end.input.type = "date";
  dateRow.append(start.label, end.label, asOf.label); dates.append(dateSummary, dateRow);
  const instructions = node("details", "report-instructions");
  const ruleHeading = node("summary", "", "Правила шаблона");
  const rules = node("div"); instructions.append(ruleHeading, rules);
  const periodNote = node("p", "report-status");
  dates.append(periodNote);
  e.form.append(head, title.label, dates, instructions, body.label, preview(ui, vault, body.input));
  const getSources = sourceList(ui, e.form, e.draft?.sources || [], () => e.form.dispatchEvent(new Event("change")));
  let template = null, series = null, token = 0, loading = false, revision;
  const initialVersion = e.draft?.version || params.get("version");
  async function select(first = false) {
    const seq = ++token, previous = template;
    loading = true;
    series = seriesList.find(row => row.id === seriesField.input.value) || null;
    templateField.input.disabled = Boolean(series);
    if (series) templateField.input.value = series.templateId;
    if (!(first && e.draft) && (series || !first)) {
      start.input.value = series?.nextStart || (previous && !start.input.readOnly ? start.input.value : "");
      end.input.value = series?.nextEnd || (previous && !end.input.readOnly ? end.input.value : "");
    }
    dates.open = Boolean(series || start.input.value || end.input.value || asOf.input.value);
    dateSummary.textContent = series ? "Период серии · " + period(start.input.value, end.input.value) : "Период отчёта и дата проверки данных";
    periodNote.replaceChildren();
    if (series && (start.input.value !== series.nextStart || end.input.value !== series.nextEnd)) {
      const browse = link("Посмотреть выпуски серии", route("series-detail", {id: series.id}), "text-link");
      browse.target = "_blank"; browse.rel = "noopener";
      periodNote.append("У серии уже другой очередной период. В черновике сохранены прежние даты. ", browse);
    }
    start.input.readOnly = end.input.readOnly = Boolean(series);
    try {
      const query = {id: templateField.input.value};
      if (first && initialVersion) query.version = initialVersion;
      const next = await get("template", query);
      if (seq !== token || !view.isConnected) return;
      template = next;
      revision = first && e.draft?.revision ? e.draft.revision : next.revision;
      if (!body.input.value || (previous && body.input.value === previous.body)) {
        body.input.value = next.body; body.input.dispatchEvent(new Event("input"));
      }
      rules.replaceChildren(node("p", "report-value", next.audience), node("p", "report-value", next.guidance));
      if (revision !== next.revision) {
        instructions.open = true;
        rules.append(ui.notice("Файл шаблона изменился после начала работы. Сверьте текущие правила с текстом черновика."),
          ui.button("Использовать проверенные правила", secondary, () => {
            revision = next.revision;
            rules.replaceChildren(node("p", "report-value", next.audience), node("p", "report-value", next.guidance));
            e.form.dispatchEvent(new Event("change"));
          }));
      }
      const open = link("Открыть шаблон в новой вкладке", route("template", {id: next.id, version: next.version}), "text-link");
      open.target = "_blank"; open.rel = "noopener"; rules.append(open);
      ruleHeading.textContent = "Правила шаблона · версия " + next.version;
      if (!first) e.form.dispatchEvent(new Event("change"));
    } catch (error) {
      if (seq === token) { template = null; rules.replaceChildren(ui.notice(error.message), ui.button("Загрузить снова", secondary, () => select(first))); instructions.open = true; }
    } finally { if (seq === token) loading = false; }
  }
  seriesField.input.addEventListener("change", () => select());
  templateField.input.addEventListener("change", () => select());
  await select(true);
  if (!view.isConnected) return;
  const ready = node("button", secondary, "Сохранить готовый выпуск");
  ready.type = "submit";
  e.finish("Сохранить черновик", "issue", submitter => {
    if (loading || !template) throw new Error("Дождитесь загрузки шаблона.");
    if (!series && Boolean(start.input.value) !== Boolean(end.input.value)) throw new Error("Для периода укажите обе даты.");
    if (body.input.value.trim() === template.body.trim()) throw new Error("Заполните текст выпуска по выбранному шаблону.");
    return {title: title.input.value, body: body.input.value, templateId: template.id, templateVersion: template.version, templateRevision: revision, sources: getSources(),
      ...(submitter === ready ? {} : {draft: true}),
      ...(series ? {seriesId: series.id} : {}), ...(asOf.input.value ? {asOf: asOf.input.value} : {}),
      ...(start.input.value ? {start: start.input.value, end: end.input.value} : {})};
  }, result => route("issue", {id: result.id}), {
    alternateSubmit: ready,
    extra: () => ({version: template?.version, revision, sources: getSources(false)}),
    conflict: async feedback => {
      const open = link("Сверить шаблон", route("template", {id: template.id, version: template.version}), "text-link");
      open.target = "_blank"; open.rel = "noopener";
      feedback.append(open, node("p", "report-meta", "Черновик сохранён в этой вкладке. Обновите форму, чтобы сверить изменения."));
      feedback.append(ui.button("Перечитать форму", secondary, () => ui.go(location.hash)));
    }
  });
  view.append(prompt(vault, () => ({template, series, title: title.input.value, start: start.input.value, end: end.input.value, asOf: asOf.input.value})));
}

export async function seriesForm(view, ui, vault, params) {
  const {option, all, route, node, cadenceNames} = ui;
  const templates = await all("templates");
  if (!view.isConnected) return;
  const e = editor(ui, view, vault, "Новая серия", ["series", params.toString()]);
  const title = e.field("title", "Название серии", "input", "", true);
  const template = e.field("templateId", "Шаблон выпусков", "select", "", true);
  ui.fillTemplateOptions(template.input, templates);
  template.input.value = e.draft?.fields.templateId || params.get("template") || templates[0]?.id || "";
  const cadence = e.field("cadence", "Периодичность", "select", "", true);
  Object.entries(cadenceNames).forEach(([key, name]) => cadence.input.append(option(key, name)));
  cadence.input.value = e.draft?.fields.cadence || "weekly";
  const start = e.field("start", "Начало первого периода", "input", "", true);
  start.input.type = "date";
  const row = node("div", "report-fields-row"); row.append(cadence.label, start.label);
  e.form.append(title.label, template.label, row, node("p", "report-meta", "После сохранения серии можно подготовить первый выпуск или скопировать запрос для настройки расписания в Codex."));
  e.finish("Создать серию", "series", () => ({title: title.input.value, templateId: template.input.value, cadence: cadence.input.value, start: start.input.value}),
    saved => route("series-detail", {id: saved.id}));
}
