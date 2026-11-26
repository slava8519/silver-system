const projectFields = {
  status: "Статус", due: "Срок", next_step: "Следующий шаг",
  event: "События проекта", summary: "Коротко по событиям проекта"
};
const personScopes = {shared: "Для общего поиска", personal: "Личные сведения"};
const titles = {triage: "Вопросы владельцу", rules: "Правила направлений"};
const pageSize = 10;
const queueURL = (mode, offset = 0) => "#inbox?view=" + mode + (offset ? "&offset=" + offset : "");

export function batchViews({node, link, button, section, labelled, notice, api, pageHead, recordURL}) {
  function describeRule(rule) {
    const summary = node("div", "batch-rule-text");
    if (rule.error) {
      summary.append(notice(rule.error), node("p", "muted", "Исправьте файл правила в Obsidian и обновите страницу."), node("code", "record-path", rule.path));
      return summary;
    }
    if (!rule.revision) {
      summary.append(node("p", "muted", "Действуют настройки по умолчанию."));
    }
    const values = node("dl", "batch-policy");
    for (const [label, keys, names, empty] of [
      ["Проект", rule.project_fields, projectFields, "Обновления отключены"],
      ["Карточки людей", rule.person_visibilities, personScopes, "Запись отключена"]
    ]) {
      const row = node("div");
      row.append(node("dt", "", label), node("dd", "", keys.map(key => names[key] || key).join(", ") || empty));
      values.append(row);
    }
    summary.append(node("p", "batch-instructions", rule.instructions), values);
    return summary;
  }

  function group(label, name, labels, selected) {
    const box = node("fieldset", "batch-options");
    box.append(node("legend", "", label));
    for (const [key, caption] of Object.entries(labels)) {
      const check = node("input");
      check.type = "checkbox";
      check.name = name;
      check.value = key;
      check.checked = selected.includes(key);
      const choice = node("label");
      choice.append(check, caption);
      box.append(choice);
    }
    return box;
  }

  function ruleCard(rule, pageOffset, saved = false) {
    const card = node("article", "work-panel batch-card");
    const heading = node("h2", "work-heading");
    heading.append(link(rule.title, recordURL(rule.direction), "batch-heading-link"));
    card.append(heading, describeRule(rule));
    if (rule.error) return card;
    const details = node("details", "batch-editor");
    const toggle = node("summary", "text-link", rule.revision ? "Изменить правило" : "Настроить правило");
    const form = node("form", "fact-correction");
    const instructions = node("textarea");
    instructions.name = "instructions";
    instructions.rows = 3;
    instructions.required = true;
    instructions.value = rule.instructions;
    const fields = group("Что можно записать в проект", "project_fields", projectFields, rule.project_fields);
    const eventCheck = fields.querySelector('input[value="event"]');
    const summaryCheck = fields.querySelector('input[value="summary"]');
    summaryCheck.addEventListener("change", () => {
      if (summaryCheck.checked) eventCheck.checked = true;
    });
    eventCheck.addEventListener("change", () => {
      if (!eventCheck.checked) summaryCheck.checked = false;
    });
    const scopes = group("Что можно сохранить о людях", "person_visibilities", personScopes, rule.person_visibilities);
    const save = node("button", "button button-primary", "Сохранить правило");
    save.type = "submit";
    const feedback = node("div", "editor-feedback");
    feedback.setAttribute("aria-live", "polite");
    form.append(labelled("Как распознать и разобрать материал", instructions), fields, scopes, feedback, save);
    details.append(toggle, form);
    card.append(details);
    if (saved) {
      const message = node("p", "intake-success", "Правило сохранено.");
      message.setAttribute("role", "status");
      card.append(message);
    }
    let revision = rule.revision;
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (save.disabled) return;
      const data = new FormData(form);
      save.disabled = true;
      feedback.replaceChildren();
      try {
        const result = await api("inbox/rule", {direction:rule.direction, instructions:instructions.value.trim(),
          project_fields:data.getAll("project_fields"), person_visibilities:data.getAll("person_visibilities"), expected_revision:revision});
        const updated = ruleCard(result, pageOffset, true);
        card.replaceWith(updated);
        updated.querySelector("summary").focus();
      } catch (error) {
        feedback.append(notice(error.message));
        if (error.status !== 409) { save.disabled = false; return; }
        try {
          const result = await api("inbox/rules?offset=" + pageOffset + "&limit=" + pageSize);
          const latest = result.items.find(row => row.direction === rule.direction);
          if (!latest) {
            feedback.append(node("p", "muted", "Направление больше не находится на этой странице. Откройте список правил заново."));
            return;
          }
          feedback.append(node("strong", "", "Текущие правила в файле"), describeRule(latest),
            button("Сверил с файлом — продолжить", "button button-secondary", () => {
              revision = latest.revision;
              feedback.replaceChildren(node("p", "muted", "Проверьте текст и разрешения перед сохранением."));
              save.disabled = false;
              instructions.focus();
            }));
        } catch (readError) { feedback.append(notice(readError.message)); save.disabled = false; }
      }
    });
    return card;
  }

  function triageCard(item) {
    const card = node("article", "work-panel batch-card");
    const location = node("div", "batch-source");
    location.append(node("span", "record-path", item.path));
    if (item.line > 0) {
      const orig = "/original/" + encodeURIComponent(item.revision) + "?start=" + encodeURIComponent(item.line);
      location.append(link("Оригинал · строка " + item.line, orig, "text-link"));
    } else if (item.source_path && !/\.(?:md|txt)$/i.test(item.source_path)) {
      location.append(link("Скачать исходный файл", "/attachment/" + encodeURIComponent(item.revision), "text-link"));
    } else if (item.source_path) {
      location.append(node("span", "record-path", item.source_path));
    }
    card.append(location, node("h2", "work-heading", item.reason));
    if (item.quote) card.append(node("blockquote", "intake-quote", item.quote));
    if (item.status === "damaged") {
      card.append(node("p", "muted", "Проверьте квитанцию разбора в Obsidian и обновите страницу."), node("code", "record-path", item.receipt_path));
      return card;
    }
    function ready(clarification) {
      const reply = node("div", "batch-reply");
      const state = node("p", "intake-success", "Уточнение для следующего разбора");
      state.setAttribute("role", "status");
      reply.append(state, node("p", "", clarification));
      return reply;
    }
    if (item.status === "ready") {
      card.append(ready(item.clarification));
      return card;
    }
    const form = node("form", "batch-answer");
    const input = node("input");
    input.name = "clarification";
    input.required = true;
    const save = node("button", "button button-secondary", "Передать уточнение");
    save.type = "submit";
    const feedback = node("div", "editor-feedback");
    feedback.setAttribute("aria-live", "polite");
    form.append(labelled("Ваше уточнение", input), feedback, save);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (save.disabled) return;
      const clarification = input.value.trim();
      if (!clarification) { feedback.replaceChildren(notice("Введите уточнение.")); input.focus(); return; }
      save.disabled = true;
      feedback.replaceChildren();
      try {
        const result = await api("inbox/resolve", {id:item.id, clarification});
        const reply = ready(result.clarification);
        form.replaceWith(reply);
        reply.tabIndex = -1;
        reply.focus({preventScroll:true});
      } catch (error) {
        feedback.append(notice(error.message));
        save.disabled = false;
      }
    });
    card.append(form);
    return card;
  }

  async function page(view, vault, mode, params) {
    const raw = Number(params.get("offset"));
    const offset = Number.isSafeInteger(raw) && raw >= 0 ? raw : 0;
    view.append(pageHead(vault + " · Входящие", titles[mode], link("Ко входящим", "#inbox", "button button-secondary")));
    const {items, total, truncated} = await api("inbox/" + mode + "?offset=" + offset + "&limit=" + pageSize);
    if (mode === "triage" && items.length) {
      view.append(node("p", "batch-lede", "Откройте исходную строку и добавьте недостающие сведения. Codex учтёт ваш ответ в следующий раз, когда вы попросите разобрать всё новое."));
    }
    for (const item of items) view.append(mode === "triage" ? triageCard(item) : ruleCard(item, offset));
    if (!items.length) {
      const empty = section(offset ? "Страница пуста" : mode === "triage" ? "Открытых вопросов нет" : "Пока нет направлений");
      if (offset) empty.append(link("К началу списка", queueURL(mode), "text-link"));
      else if (mode === "triage") empty.append(node("p", "muted", "Здесь появятся фрагменты входящих, для которых Codex запросит уточнение."));
      else empty.append(link("Создать направление", "#new/direction", "text-link"));
      view.append(empty);
    }
    if (items.length && (offset || truncated)) {
      const nav = node("nav", "work-tools batch-pager");
      nav.setAttribute("aria-label", "Страницы списка");
      if (offset) nav.append(link("Предыдущая страница", queueURL(mode, Math.max(0, offset - pageSize)), "text-link"));
      nav.append(node("span", "muted", (offset + 1) + "–" + (offset + items.length) + " из " + total));
      if (truncated) nav.append(link("Следующая страница", queueURL(mode, offset + items.length), "text-link"));
      view.append(nav);
    }
  }

  return {page};
}
