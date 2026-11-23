import { batchViews } from "./batch.js";
import { capturePanel } from "./capture.js";
import { teamViews } from "./teams.js";

const kinds = {role: "Роль", fact: "Факт", observation: "Наблюдение"};
const access = {personal: "Личное", shared: "Для общего поиска"};
const personURL = id => "#person/" + encodeURIComponent(id);
const paramsFor = data => new URLSearchParams(data).toString();

export function intakeViews(helpers) {
  const {node, link, button, option, section, labelled, notice, api, go, counted, pageHead, recordURL} = helpers;
  const batch = batchViews(helpers);
  const teams = teamViews(helpers);
  function personRow(person, direction) {
    const row = link("", personURL(person.id) + (direction ? "?" + paramsFor({direction}) : ""), "record-row");
    const copy = node("div", "record-main");
    copy.append(node("strong", "row-title", person.name), node("span", "record-path", person.path));
    row.append(node("span", "type-icon", person.name.slice(0, 1)), copy);
    return row;
  }

  function people(view, options) {
    return teams.directory(view, options, personRow);
  }

  async function inbox(view, {params, vault, directions}) {
    const path = params.get("path");
    if (path) {
      await intakeForm(view, vault, directions, path);
      return;
    }
    const mode = params.get("view");
    if (mode === "triage" || mode === "rules") {
      await batch.page(view, vault, mode, params);
      return;
    }
    view.append(pageHead(vault, "Входящие"));
    const panel = section("Материалы во входящих");
    const list = node("div", "inbox-materials");
    panel.append(list);
    const review = section("Разбор в Codex", "work-panel batch-start");
    const hint = node("p", "batch-instruction");
    hint.append("В чате хранилища напишите ", node("strong", "", "«Разбери всё новое»"), ".");
    const controls = node("div", "work-tools");
    const questions = link("Вопросы владельцу", "#inbox?view=triage", "button button-secondary");
    controls.append(questions, link("Правила направлений", "#inbox?view=rules", "text-link"));
    review.append(hint, controls);
    const bottom = node("div", "inbox-bottom");
    bottom.append(panel, review);
    let request = 0;
    async function refresh() {
      const sequence = ++request;
      try {
        const {items, triageOpen} = await api("inbox");
        if (sequence !== request) return;
        questions.textContent = triageOpen ? "Вопросы владельцу · " + triageOpen : "Вопросы владельцу";
        list.replaceChildren();
        for (const item of items) {
          const row = item.attachment ? node("div", "record-row") : link("", "#inbox?" + paramsFor({path: item.path}), "record-row");
          const copy = node("div", "record-main");
          copy.append(node("strong", "row-title", item.path.split("/").pop()), node("span", "record-path", item.path));
          const status = item.processed ? "Оригинал сохранён" : "Ожидает разбора";
          row.append(node("span", "type-icon " + (item.attachment ? "type-attachment" : "type-note"), item.attachment ? "◇" : "↳"), copy,
            node("span", "record-side", item.attachment ? "Вложение · " + status : status));
          list.append(row);
        }
        if (!items.length) list.append(node("p", "muted", "Здесь появятся сохранённые материалы."));
      } catch (error) {
        if (sequence === request) list.replaceChildren(notice(error.message), button("Обновить список", "intake-action", refresh));
      }
    }
    view.append(capturePanel(helpers, refresh), bottom);
    await refresh();
  }

  async function intakeForm(view, vault, directions, path) {
    view.append(pageHead(vault + " · Входящие", path.split("/").pop(), link("Ко входящим", "#inbox", "button button-secondary")));
    const source = section("Текст материала");
    const original = node("div");
    source.append(node("p", "record-path", path), original, node("p", "muted", "Выберите строку, которая подтверждает роль, факт или наблюдение."));
    const rows = node("div", "intake-lines");
    const pages = node("div", "work-tools intake-pager");
    const sourceFeedback = node("div");
    source.append(rows, pages, sourceFeedback);

    const form = node("form", "work-panel intake-form");
    form.append(node("h2", "work-heading", "Разбор материала"));
    const searchName = node("input");
    searchName.type = "search";
    const selector = node("select");
    selector.name = "person";
    selector.required = true;
    const newOption = () => option("+", "Создать отдельную карточку человека");
    selector.append(option("", "Выберите человека"), newOption());
    const name = node("input");
    name.name = "name";
    const newName = node("div", "new-person-fields");
    const key = node("input");
    key.name = "personKey";
    const namesake = node("details", "person-history");
    namesake.append(node("summary", "", "В материале два человека с одинаковым именем"),
      labelled("Ключ этого участника для повторного разбора", key));
    newName.append(labelled("Имя нового человека", name), namesake);
    const candidatesFeedback = node("div");
    const searchRow = node("div", "people-search");
    const searchButton = button("Найти человека", "button button-secondary", () => loadCandidates());
    searchRow.append(labelled("Найти по имени или другому названию", searchName), searchButton);
    newName.hidden = true;
    selector.addEventListener("change", () => { newName.hidden = selector.value !== "+"; name.required = !newName.hidden; });
    searchName.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); loadCandidates(); } });
    let candidateRequest = 0;
    async function loadCandidates(offset = 0, match = searchName.value.trim()) {
      const request = ++candidateRequest;
      try {
        const {items, truncated} = await api("people?" + paramsFor({match, offset}));
        if (request !== candidateRequest) return;
        const previous = selector.value;
        selector.replaceChildren(option("", "Выберите человека"), ...items.map(p => option(p.id, p.name + " · " + p.path)), newOption());
        if (previous === "+" || items.some(p => p.id === previous)) selector.value = previous;
        selector.dispatchEvent(new Event("change"));
        candidatesFeedback.replaceChildren();
        if (offset) candidatesFeedback.append(button("Предыдущие участники", "intake-action", () => loadCandidates(Math.max(0, offset-20), match)));
        if (truncated) candidatesFeedback.append(button("Следующие участники", "intake-action", () => loadCandidates(offset+items.length, match)));
        if (!items.length && searchName.value.trim()) candidatesFeedback.append(node("p", "muted", "Совпадений нет. Можно создать отдельную карточку."));
      } catch (error) {
        if (request === candidateRequest) candidatesFeedback.replaceChildren(notice(error.message));
      }
    }

    const drafts = node("div", "intake-drafts");
    const empty = node("p", "muted", "Пока не выбрано ни одной строки.");
    const feedback = node("div", "editor-feedback");
    feedback.setAttribute("aria-live", "polite");
    const submit = node("button", "button button-primary", "Сохранить разбор");
    submit.type = "submit";
    const actions = node("div", "work-tools");
    actions.append(submit);
    form.append(searchRow, candidatesFeedback, labelled("Карточка человека", selector), newName, drafts, empty, feedback, actions);
    const layout = node("div", "intake-layout");
    layout.append(source, form);
    view.append(layout);
    const entries = [];
    let revision = null, start = 1, sourceRequest = 0, reselect = null, saving = false;

    function updateSubmit() {
      empty.hidden = entries.length > 0;
      submit.disabled = saving || !entries.length || !directions.length || entries.some(entry => !entry.verified);
      const selected = new Set(entries.filter(entry => entry.verified).map(entry => entry.line));
      for (const pick of rows.querySelectorAll("button[data-line]")) {
        pick.classList.toggle("line-in-use", selected.has(Number(pick.dataset.line)));
      }
    }
    function pickLine(line) {
      if (reselect) {
        reselect.line = line.n;
        reselect.quote.textContent = line.text;
        reselect.verified = true;
        reselect.legend.textContent = "Строка " + line.n;
        reselect.relink.textContent = "Сменить строку";
        reselect.text.focus();
        reselect = null;
      } else {
        const fieldset = node("fieldset", "intake-entry");
        const legend = node("legend", "", "Строка " + line.n);
        const quote = node("blockquote", "intake-quote", line.text);
        const kind = node("select");
        kind.required = true;
        kind.append(option("", "Выберите тип"));
        for (const [key, label] of Object.entries(kinds)) kind.append(option(key, label));
        const direction = node("select");
        direction.required = true;
        direction.append(option("", "Выберите направление"), ...directions.map(d => option(d.id, d.title)));
        const visibility = node("select");
        for (const [key, label] of Object.entries(access)) visibility.append(option(key, label));
        const text = node("textarea");
        text.rows = 2;
        text.required = true;
        text.value = line.text.trim();
        const properties = node("div", "intake-properties");
        properties.append(labelled("Тип", kind), labelled("Направление", direction), labelled("Доступ", visibility));
        const relink = button("Сменить строку", "intake-action", () => {
          reselect = entry;
          sourceFeedback.replaceChildren(node("p", "muted", "Выберите строку для этой формулировки."));
          source.scrollIntoView({block:"start"});
          rows.querySelector("button")?.focus({preventScroll:true});
        });
        const entry = {line:line.n, legend, quote, text, kind, direction, visibility, relink, verified:true};
        entries.push(entry);
        const tools = node("div", "work-tools");
        tools.append(relink, button("Убрать", "intake-action", () => {
          entries.splice(entries.indexOf(entry), 1);
          if (reselect === entry) reselect = null;
          fieldset.remove();
          updateSubmit();
        }));
        fieldset.append(legend, quote, properties, labelled("Дословная выдержка из строки", text), tools);
        drafts.append(fieldset);
      }
      sourceFeedback.replaceChildren(node("p", "muted", "Выбрана строка " + line.n + "."));
      updateSubmit();
    }

    async function showSource(next) {
      const request = ++sourceRequest;
      try {
        const preview = await api("inbox/preview?" + paramsFor({path, start:next, limit:30}));
        if (request !== sourceRequest) return;
        if (revision && revision !== preview.revision) {
          for (const entry of entries) {
            entry.verified = false;
            entry.relink.textContent = "Сверить с новой версией";
          }
          reselect = null;
          feedback.replaceChildren(notice("Материал изменился. Ваши формулировки сохранены в форме. Выберите для каждой строку в новой версии."));
        }
        revision = preview.revision;
        original.replaceChildren();
        if (preview.processed) original.append(link("Открыть сохранённый оригинал", "/original/" + encodeURIComponent(revision), "text-link"));
        start = next;
        rows.replaceChildren();
        for (const line of preview.lines) {
          const row = node("div", "intake-line");
          if (line.text.trim()) {
            const pick = button("", "intake-pick", () => pickLine(line));
            pick.dataset.line = line.n;
            pick.title = "Выбрать строку " + line.n;
            pick.setAttribute("aria-label", "Выбрать строку " + line.n + ". " + line.text);
            pick.append(node("span", "intake-number", line.n), node("span", "", line.text));
            row.append(pick);
          } else row.append(node("span", "intake-blank", line.n));
          rows.append(row);
        }
        pages.replaceChildren();
        if (start > 1) pages.append(button("Назад на 30 строк", "intake-action", () => showSource(Math.max(1, start - 30))));
        pages.append(node("span", "muted", preview.lines.length ? "Строки " + start + "–" + preview.lines[preview.lines.length-1].n + " из " + preview.total : "В материале нет строк."));
        if (start + 30 <= preview.total) pages.append(button("Следующие 30 строк", "intake-action", () => showSource(start + 30)));
        sourceFeedback.replaceChildren();
        updateSubmit();
      } catch (error) {
        if (request === sourceRequest) sourceFeedback.replaceChildren(notice(error.message), button("Перечитать начало", "intake-action", () => showSource(1)));
      }
    }
    if (!directions.length) form.append(node("p", "muted", "Для разбора потребуется направление."), link("Создать направление", "#new/direction", "text-link"));
    updateSubmit();
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (submit.disabled) return;
      const invalid = entries.find(e => !e.text.value.trim() || !e.quote.textContent.includes(e.text.value.trim()));
      if (invalid) {
        feedback.replaceChildren(notice("Проверьте выдержку из строки " + invalid.line + ". Для разбора нужен дословный фрагмент."));
        invalid.text.focus();
        return;
      }
      const person = selector.value === "+" ? {name:name.value.trim(), new:true} : {id:selector.value};
      if (person.new && key.value.trim()) person.key = key.value.trim();
      const facts = entries.map(e => ({line:e.line, text:e.text.value.trim(), direction:e.direction.value, visibility:e.visibility.value, kind:e.kind.value}));
      saving = true;
      updateSubmit();
      feedback.replaceChildren();
      try {
        const result = await api("inbox/process", {path, revision, person, facts});
        if (view.isConnected) {
          lastSave = {id:result.personId, personal:facts.some(f => f.visibility === "personal")};
          go(personURL(result.personId));
        }
      } catch (error) {
        feedback.append(notice(error.message));
        if (error.status === 409) await showSource(1);
      } finally {
        saving = false;
        updateSubmit();
      }
    });
    await Promise.all([showSource(1), loadCandidates()]);
  }

  async function person(view, {id, params, vault, directions}) {
    const personal = params.get("personal") === "1";
    const direction = params.get("direction");
    const scope = direction ? {direction} : {};
    const args = {id, ...scope, ...(personal ? {personal:1} : {})};
    const endpoint = "person?" + paramsFor(args);
    let current = await api(endpoint);
    const listURL = "#people" + (direction ? "?" + paramsFor(scope) : "");
    const changeScope = personal ? scope : {...scope, personal:1};
    const toggle = link(personal ? "Скрыть личные сведения" : "Показать личные сведения", personURL(id) + (Object.keys(changeScope).length ? "?" + paramsFor(changeScope) : ""), "button button-secondary");
    const caption = direction ? directions.find(d => d.id === direction)?.title || "Направление" : vault;
    view.append(pageHead(caption + " · Люди", current.name, toggle, link("К списку", listURL, "text-link")));
    const panel = section("Сведения о человеке");
    panel.append(node("p", "record-path", current.path));
    if (personal) panel.append(node("p", "muted", "Открыты общие и личные сведения."));
    view.append(panel);
    const list = node("div", "person-facts");
    panel.append(list);
    if (lastSave && lastSave.id === id) {
      const status = node("p", "intake-success", lastSave.personal && !personal ? "Разбор сохранён. Личные сведения доступны по кнопке выше." : "Разбор сохранён.");
      status.setAttribute("role", "status");
      panel.prepend(status);
      lastSave = null;
    }
    function renderFacts() {
      list.replaceChildren();
      if (!current.facts.length) list.append(node("p", "muted", personal ? "В этой области пока нет подтверждённых сведений." : "В этой области пока нет общих сведений."));
      for (const fact of current.facts) {
        const card = node("article", "person-fact");
        const tags = node("div", "person-scope");
        tags.append(node("strong", "", kinds[fact.kind] || fact.kind));
        const area = directions.find(d => d.id === fact.direction);
        tags.append(area ? link(area.title, recordURL(area.id), "text-link") : node("span","","Направление отсутствует"));
        tags.append(node("span", fact.visibility === "personal" ? "personal-mark" : "", access[fact.visibility]));
        const text = node("blockquote", "intake-quote", fact.text);
        const actions = node("div", "work-tools");
        const source = "/evidence/" + encodeURIComponent(id) + "/" + encodeURIComponent(fact.id) + (personal && fact.visibility === "personal" ? "?personal=1" : "");
        actions.append(link("Оригинал · строка " + fact.line, source, "text-link"));
        const edit = button("Уточнить", "intake-action", () => {
          edit.hidden = true;
          const otherEdits = [...list.querySelectorAll(".edit-person-fact")].filter(b => b !== edit);
          otherEdits.forEach(b => { b.disabled = true; });
          const editor = correctionForm(fact, () => {
            editor.remove();
            edit.hidden = false;
            otherEdits.forEach(b => { b.disabled = false; });
            edit.focus();
          });
          card.append(editor);
          editor.querySelector("textarea").focus();
        });
        edit.classList.add("edit-person-fact");
        actions.append(edit);
        card.append(tags, text, actions);
        if (fact.history?.length) {
          const history = node("details", "person-history");
          history.append(node("summary", "", "История формулировки"));
          const versions = node("ol");
          for (const change of fact.history) {
            const item = node("li");
            const actions = {imported:"При разборе", corrected:"После уточнения", observed:"После правки в файле"};
            item.append(node("small","muted", actions[change.action] || "Формулировка"), node("p","",change.text));
            versions.append(item);
          }
          history.append(versions);
          card.append(history);
        }
        list.append(card);
      }
    }

    function correctionForm(fact, cancel) {
      let revision = current.revision;
      const form = node("form", "fact-correction");
      const input = node("textarea");
      input.rows = 3;
      input.required = true;
      input.value = fact.text;
      const feedback = node("div", "editor-feedback");
      feedback.setAttribute("aria-live", "polite");
      const save = node("button", "button button-primary", "Сохранить уточнение");
      save.type = "submit";
      const controls = node("div", "work-tools");
      controls.append(save, button("Отмена", "intake-action", cancel));
      form.append(labelled("Уточнённая формулировка", input), feedback, controls);
      form.addEventListener("submit", async event => {
        event.preventDefault();
        if (save.disabled) return;
        save.disabled = true;
        feedback.replaceChildren();
        try {
          await api("person/correct", {id, factId:fact.id, revision, text:input.value.trim(), personal});
          current = await api(endpoint);
          renderFacts();
        } catch (error) {
          feedback.append(notice(error.message));
          if (error.status !== 409) { save.disabled = false; return; }
          try {
            const latest = await api(endpoint);
            const updated = latest.facts.find(f => f.id === fact.id);
            if (!updated) { feedback.append(node("p","muted","Сведение больше не доступно в этой области.")); return; }
            feedback.append(node("strong","","Текущая формулировка в файле"), node("blockquote","intake-quote", updated.text),
              button("Сверил с файлом — продолжить", "button button-secondary", () => {
                revision = latest.revision;
                feedback.replaceChildren(node("p","muted","Проверьте свой текст перед сохранением."));
                save.disabled = false;
                input.focus();
              }));
          } catch (latestError) {
            feedback.append(notice(latestError.message));
            save.disabled = false;
          }
        }
      });
      return form;
    }
    renderFacts();
    await teams.personTeams(view, current);
  }

  let lastSave = null;
  return {inbox, people, person};
}
