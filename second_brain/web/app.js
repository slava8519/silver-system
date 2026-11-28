import { mountCleanup } from "./demo-cleanup.js";

(() => {
  const home = document.getElementById("home");
  const detail = document.getElementById("detail");
  const guideError = document.getElementById("guide-error");
  const homeTitle = document.title;
  let guide;
  let seen = new Set();
  let progressKey;
  let justSaved;
  let draft = { title: "", noteTitle: "", body: "" };
  let pendingCreate;

  function el(tag, className, content) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined) element.textContent = content;
    return element;
  }

  function link(text, href, className = "button button-secondary") {
    const a = el("a", className, text);
    a.href = href;
    return a;
  }

  function button(text, className) {
    const b = el("button", className, text);
    b.type = "button";
    return b;
  }

  async function api(path, payload) {
    let response;
    try {
      response = await fetch("/api/" + path, payload === undefined
        ? { cache: "no-store" }
        : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    } catch {
      throw new Error("Не удалось связаться с локальным хранилищем. Проверьте, что деморежим запущен.");
    }
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("Хранилище вернуло неожиданный ответ. Попробуйте ещё раз.");
    }
    if (!response.ok) {
      const error = new Error(data.error || "Не удалось выполнить запрос.");
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function errorMessage(error) {
    const message = el("p", "field-error", error.message);
    message.setAttribute("role", "alert");
    return message;
  }

  function complete(scenario) {
    return scenario.questions.length > 0 && scenario.questions.every(q => seen.has(q.id));
  }

  function updateProgress() {
    const count = guide.scenarios.filter(complete).length;
    const total = guide.scenarios.length;
    const startLabel = document.querySelector(".hero-start")?.firstChild;
    if (startLabel?.nodeType === Node.TEXT_NODE) {
      let action = "Начать знакомство";
      if (count === total && total) action = "Добавить своё направление";
      else if (seen.size) action = "Продолжить знакомство";
      startLabel.textContent = action + " ";
    }
    document.querySelectorAll("[data-progress]").forEach(node => {
      node.textContent = count + " из " + total + " пройдено";
    });
    document.querySelectorAll("[data-progress-bar]").forEach(node => {
      node.style.width = (total ? count / total * 100 : 0) + "%";
      node.setAttribute("aria-valuenow", String(count));
      node.setAttribute("aria-valuemax", String(total));
    });
    document.querySelectorAll("[data-completed]").forEach(node => {
      const scenario = guide.scenarios.find(item => String(item.id) === node.dataset.completed);
      node.hidden = !scenario || !complete(scenario);
    });
  }

  function markSeen(id) {
    if (!seen.has(id)) {
      seen.add(id);
      try { localStorage.setItem(progressKey, JSON.stringify([...seen])); } catch { /* Optional browser storage. */ }
      updateProgress();
    }
  }

  function go(hash) {
    if (location.hash === hash) renderRoute();
    else location.hash = hash;
  }

  function header(backHref, backText, title, intro, eyebrow) {
    const h = el("header", "detail-header");
    h.append(link("← " + backText, backHref, "back-link"));
    if (eyebrow) h.append(el("p", "eyebrow", eyebrow));
    const heading = el("h1", "detail-title", title);
    heading.tabIndex = -1;
    h.append(heading);
    if (intro) h.append(el("p", "detail-intro", intro));
    return h;
  }

  function showSource(source) {
    const section = el("section", "source-view");
    section.setAttribute("aria-label", "Фрагмент исходного Markdown-файла");
    section.tabIndex = -1;
    const range = source.start === source.end
      ? "строка " + source.start : "строки " + source.start + "–" + source.end;
    section.append(el("h3", "source-path", source.path), el("p", "source-range", range));
    const lines = el("div", "source-lines");
    for (const item of source.lines) {
      const matched = item.n >= source.start && item.n <= source.end;
      const row = el("div", "source-line" + (matched ? " highlight" : ""));
      row.append(el("span", "line-number", item.n), el("code", "line-text", item.text || " "));
      lines.append(row);
    }
    section.append(lines);
    return section;
  }

  function questionHash(query) {
    return "#ask?" + new URLSearchParams({ q: query });
  }

  function freeQuestionForm(value = "") {
    const form = document.getElementById("question-form").content.firstElementChild.cloneNode(true);
    const input = form.elements.query;
    input.value = value;
    input.addEventListener("input", () => input.setCustomValidity(""));
    form.addEventListener("submit", event => {
      event.preventDefault();
      const query = input.value.trim();
      if (query) go(questionHash(query));
      else {
        input.setCustomValidity("Введите вопрос или слова из записи.");
        input.reportValidity();
      }
    });
    const suggestion = form.querySelector(".suggested-query");
    suggestion.addEventListener("click", () => {
      input.value = suggestion.textContent;
      input.setCustomValidity("");
      form.requestSubmit();
    });
    return form;
  }

  function citationLink(query, item, position) {
    const wrapper = el("div", "citation");
    const fragment = el("div", "citation-fragment");
    fragment.id = "citation-" + position;
    fragment.hidden = true;
    const anchor = link("", "", "citation-link");
    anchor.setAttribute("aria-expanded", "false");
    anchor.setAttribute("aria-controls", fragment.id);
    wrapper.append(anchor, fragment);
    let source = item.source;

    function label() {
      anchor.textContent = source.path + " · строка " + source.start;
      anchor.href = source.href;
    }

    async function open() {
      fragment.setAttribute("aria-busy", "true");
      fragment.hidden = false;
      fragment.textContent = "Открываем запись…";
      anchor.setAttribute("aria-expanded", "true");
      try {
        const fresh = await api("citation", {
          id: source.id || item.id, line: source.start, ref: source.ref, archivePlan: source.archivePlan
        });
        if (!wrapper.isConnected) return;
        source = fresh;
        label();
        fragment.replaceChildren(showSource(source));
      } catch (error) {
        const retry = button("Повторить вопрос", "button button-secondary");
        retry.addEventListener("click", () => go(questionHash(query)));
        fragment.replaceChildren(errorMessage(error), retry);
      } finally {
        fragment.removeAttribute("aria-busy");
      }
    }
    anchor.addEventListener("click", event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (fragment.hasAttribute("aria-busy")) return;
      if (fragment.hidden) open();
      else {
        fragment.hidden = true;
        anchor.setAttribute("aria-expanded", "false");
      }
    });
    label();
    return wrapper;
  }

  async function queryResults(results, query) {
    const status = el("p", "query-status", "Ищем в трёх направлениях…");
    status.setAttribute("role", "status");
    results.append(status);
    try {
      const response = await api("ask", { query });
      if (!results.isConnected) return;
      if (response.items.length && !response.message) {
        status.className = "sr-only";
        status.textContent = "Записей в ответе — " + response.items.length + ".";
      } else {
        status.textContent = response.message || "В записях нет ответа на этот вопрос.";
      }
      if (response.items.length) {
        results.prepend(el("h2", "panel-heading", "Ответ по записям"));
        const list = el("div", "answer-records");
        response.items.forEach((item, index) => {
          const card = el("article", "query-record");
          card.append(el("h3", "record-heading", item.label || item.title));
          card.append(el("p", item.supported ? "record-answer" : "record-missing", item.answer));
          if (item.source) card.append(citationLink(query, item, index));
          list.append(card);
        });
        results.append(list);
      }
    } catch (error) {
      if (!results.isConnected) return;
      const retry = button("Повторить поиск", "button button-secondary");
      retry.addEventListener("click", () => go(questionHash(query)));
      results.replaceChildren(errorMessage(error), retry);
    }
  }

  function askPage(params) {
    const query = (params.get("q") || "").trim();
    detail.append(header("#home", "Все примеры", "Спросить по записям",
      "Задайте вопрос по одному или нескольким из трёх направлений."));
    const box = el("div", "panel query-form-panel");
    box.append(freeQuestionForm(query));
    detail.append(box);
    if (query) {
      const results = el("section", "query-results");
      results.setAttribute("aria-label", "Ответ на вопрос");
      detail.append(results);
      queryResults(results, query);
    }
  }

  // Re-read the answer and its source together when the reader opens the file.
  function answerPanel(placeholder) {
    const panel = el("section", "panel answer-panel");
    const heading = el("h2", "panel-heading", "Ответ");
    const content = el("div", "answer-content");
    const status = el("span", "sr-only");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    panel.append(heading, content, status);
    let revision = 0;

    function reset() {
      revision++;
      content.replaceChildren(el("p", "answer-placeholder", placeholder));
      panel.removeAttribute("aria-busy");
      status.textContent = "";
    }

    async function ask(payload, onSource, opened = false) {
      const version = ++revision;
      panel.setAttribute("aria-busy", "true");
      const loading = opened ? "Открываем файл…" : "Ищем в записях…";
      if (!opened) content.replaceChildren(el("p", "answer-placeholder", loading));
      status.textContent = loading;
      try {
        const result = await api("ask", payload);
        if (version !== revision || !panel.isConnected) return;
        content.replaceChildren(el("p", "answer-text", result.answer));
        panel.removeAttribute("aria-busy");
        status.textContent = result.answer;
        if (!result.source) return;

        const disclosure = button(opened ? "Скрыть источник" : "Открыть источник", "source-button");
        disclosure.setAttribute("aria-expanded", String(opened));
        const sourceId = "active-source";
        const source = showSource(result.source);
        source.id = sourceId;
        source.hidden = !opened;
        disclosure.setAttribute("aria-controls", sourceId);
        disclosure.addEventListener("click", () => {
          if (source.hidden) {
            disclosure.disabled = true;
            ask(payload, onSource, true);
          } else {
            source.hidden = true;
            disclosure.textContent = "Открыть источник";
            disclosure.setAttribute("aria-expanded", "false");
          }
        });
        content.append(disclosure, source);
        if (opened) {
          if (onSource) onSource();
          source.focus({ preventScroll: true });
          if (window.matchMedia("(max-width: 720px)").matches) source.scrollIntoView({ block: "nearest" });
        } else if (window.matchMedia("(max-width: 720px)").matches) {
          panel.scrollIntoView({ block: "nearest" });
        }
      } catch (error) {
        if (version !== revision || !panel.isConnected) return;
        panel.removeAttribute("aria-busy");
        status.textContent = "";
        const retry = button(opened ? "Открыть ещё раз" : "Повторить поиск", "button button-secondary");
        retry.addEventListener("click", () => ask(payload, onSource, opened));
        content.replaceChildren(errorMessage(error), retry);
      }
    }
    reset();
    return { panel, ask, reset };
  }

  function scenarioPage(scenario) {
    const index = guide.scenarios.indexOf(scenario);
    const chapter = header("#home", "Все примеры", scenario.title, scenario.description,
      "Пример " + (index + 1) + " из " + guide.scenarios.length);
    chapter.classList.add("chapter-header", "chapter-" + scenario.id);
    detail.append(chapter);
    const row = el("div", "detail-layout");
    const controls = el("section", "panel questions-panel");
    controls.append(el("h2", "panel-heading", "Выберите вопрос"));
    const questions = el("div", "questions");
    const answers = answerPanel("Задайте вопрос и откройте строку, на которую опирается ответ.");
    const actions = el("nav", "detail-actions");
    actions.setAttribute("aria-label", "Продолжить знакомство");
    const buttons = new Map();
    let selected;

    function syncQuestions() {
      for (const q of scenario.questions) {
        const b = buttons.get(q.id);
        b.setAttribute("aria-pressed", String(q.id === selected));
        const checked = seen.has(q.id);
        b.classList.toggle("was-read", checked);
        b.querySelector(".question-mark").textContent = checked ? "✓" : "↗";
        const description = [checked && "Источник просмотрен.", q.goal && q.recordTitle, q.goal, q.text];
        b.setAttribute("aria-label", description.filter(Boolean).join(" "));
      }
    }

    function nextAction() {
      actions.replaceChildren();
      const nextQuestion = scenario.questions.find(q => !seen.has(q.id));
      if (nextQuestion && nextQuestion.id !== selected && selected && seen.has(selected)) {
        const next = button("Следующий вопрос →", "button button-primary");
        next.addEventListener("click", () => {
          const target = buttons.get(nextQuestion.id);
          target.focus();
          target.click();
        });
        actions.append(next);
      }
      const later = [...guide.scenarios.slice(index + 1), ...guide.scenarios.slice(0, index)];
      const nextExample = later.find(s => !complete(s));
      const primary = complete(scenario) ? "button button-primary" : "button button-secondary";
      if (nextExample) actions.append(link("Следующий пример →", "#example/" + nextExample.id, primary));
      else actions.append(link("Добавить своё направление →", "#new", primary));
    }

    for (const question of scenario.questions) {
      const b = button("", "question-button");
      const mark = el("span", "question-mark");
      mark.setAttribute("aria-hidden", "true");
      const copy = el("span", "question-copy");
      if (question.goal) {
        copy.append(el("strong", "question-project", question.recordTitle), el("small", "question-goal", question.goal));
      }
      copy.append(el("span", "question-label", question.text));
      b.append(copy, mark);
      b.addEventListener("click", () => {
        selected = question.id;
        syncQuestions();
        nextAction();
        answers.ask({ questionId: question.id }, () => {
          markSeen(question.id);
          syncQuestions();
          nextAction();
        });
      });
      questions.append(b);
      buttons.set(question.id, b);
    }
    controls.append(questions, link("Задать свой вопрос по трём направлениям →", "#ask", "ask-other"));
    row.append(controls, answers.panel);
    detail.append(row, actions);
    syncQuestions();
    nextAction();
  }

  function minePage() {
    detail.append(header("#home", "Все примеры", "Мои направления",
      "Здесь собраны ваши направления и связанные с ними записи."));
    if (guide.custom.length) {
      const list = el("div", "direction-list");
      for (const direction of guide.custom) {
        const a = link("", "#direction/" + encodeURIComponent(direction.id), "panel direction-link");
        a.append(el("h2", "panel-heading", direction.title));
        if (direction.notes.length) a.append(el("p", "direction-note", direction.notes.map(n => n.title).join(" · ")));
        a.append(el("span", "back-link", "Открыть направление →"));
        list.append(a);
      }
      detail.append(list);
    }
    const cta = el("div", guide.custom.length ? "detail-actions" : "panel empty-state");
    if (!guide.custom.length) cta.append(el("h2", "panel-heading", "Какую тему хотите запомнить?"),
      el("p", "detail-intro", "Добавьте свою тему и сохраните в ней первый факт."));
    cta.append(link("Добавить направление", "#new", "button button-primary"));
    detail.append(cta);
  }

  function field(form, name, labelText, placeholder, multiline) {
    const group = el("div", "form-field");
    const label = el("label", "", labelText);
    const input = el(multiline ? "textarea" : "input");
    input.name = name;
    input.id = "field-" + name;
    input.placeholder = placeholder;
    input.required = true;
    input.maxLength = multiline ? 6000 : 140;
    input.value = draft[name];
    if (multiline) input.rows = 4;
    label.htmlFor = input.id;
    group.append(label, input);
    form.append(group);
    input.addEventListener("input", () => { draft[name] = input.value; input.setCustomValidity(""); });
    return input;
  }

  function newPage() {
    detail.append(header("#mine", "Мои направления", "Своё направление",
      "Выберите тему и запишите одну подробность, к которой захотите вернуться."));
    const form = el("form", "panel direction-form");
    const title = field(form, "title", "Название направления", "Например, «Сад»", false);
    const note = field(form, "noteTitle", "Название первой заметки", "Например, «Рассада на окне»", false);
    const body = field(form, "body", "Что хотите запомнить?", "Запишите наблюдение своими словами", true);
    const errors = el("div", "form-feedback");
    const submit = el("button", "button button-primary", "Сохранить направление");
    submit.type = "submit";
    form.append(errors, submit);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (submit.disabled) return;
      for (const input of [title, note, body]) {
        if (!input.value.trim()) {
          input.setCustomValidity("Заполните это поле.");
          input.reportValidity();
          return;
        }
      }
      const values = { title: title.value.trim(), noteTitle: note.value.trim(), body: body.value.trim() };
      const signature = JSON.stringify(values);
      if (pendingCreate?.signature !== signature)
        pendingCreate = { signature, id: crypto.randomUUID() };
      const requestId = pendingCreate.id;
      errors.replaceChildren();
      submit.disabled = true;
      submit.textContent = "Сохраняем…";
      form.setAttribute("aria-busy", "true");
      try {
        const created = await api("directions", { ...values, requestId });
        const existing = guide.custom.findIndex(d => d.id === created.id);
        if (existing < 0) guide.custom.push(created);
        else guide.custom[existing] = created;
        if (pendingCreate?.id === requestId) pendingCreate = undefined;
        if (JSON.stringify({ title: draft.title.trim(), noteTitle: draft.noteTitle.trim(), body: draft.body.trim() }) === signature)
          draft = { title: "", noteTitle: "", body: "" };
        if (form.isConnected) {
          justSaved = created.id;
          go("#direction/" + encodeURIComponent(created.id));
        }
      } catch (error) {
        if (form.isConnected) errors.replaceChildren(errorMessage(error));
      } finally {
        form.removeAttribute("aria-busy");
        submit.disabled = false;
        submit.textContent = "Сохранить направление";
      }
    });
    detail.append(form);
  }

  function directionPage(direction) {
    const intro = justSaved === direction.id
      ? "Направление и первая заметка сохранены. Теперь найдите свой факт."
      : "Найдите факт в заметке и проверьте строку источника.";
    justSaved = undefined;
    detail.append(header("#mine", "Мои направления", direction.title, intro));
    if (!direction.notes.length) {
      detail.append(el("p", "panel empty-state", "В направлении пока нет заметок."));
      return;
    }
    const row = el("div", "detail-layout");
    const form = el("form", "panel custom-search");
    form.append(el("h2", "panel-heading", "Поиск по заметке"));
    const note = el("select");
    for (const item of direction.notes) {
      const option = el("option", "", item.title);
      option.value = item.id;
      note.append(option);
    }
    const noteField = el("div", "form-field");
    if (direction.notes.length === 1) {
      noteField.append(el("p", "direction-note", direction.notes[0].title));
    } else {
      const noteLabel = el("label", "", "Заметка");
      note.id = "note-choice";
      noteLabel.htmlFor = note.id;
      noteField.append(noteLabel, note);
    }
    const queryField = el("div", "form-field");
    const label = el("label", "", "Ваш вопрос");
    const query = el("input");
    query.type = "search";
    query.required = true;
    query.maxLength = 1000;
    query.id = "custom-query";
    label.htmlFor = query.id;
    query.placeholder = "Что вы хотите вспомнить?";
    queryField.append(label, query);
    const search = el("button", "button button-primary", "Найти ответ");
    search.type = "submit";
    form.append(noteField, queryField, search);
    const answers = answerPanel("Здесь появится найденный в заметке фрагмент.");
    note.addEventListener("change", () => answers.reset());
    query.addEventListener("input", () => query.setCustomValidity(""));
    form.addEventListener("submit", event => {
      event.preventDefault();
      const text = query.value.trim();
      if (!text) {
        query.setCustomValidity("Введите вопрос или слова из заметки.");
        query.reportValidity();
        return;
      }
      answers.ask({ noteId: note.value, query: text });
    });
    row.append(form, answers.panel);
    detail.append(row);
  }

  function renderRoute() {
    if (!guide) return;
    const hash = location.hash.slice(1);
    const [locationPath] = hash.split("?");
    const [route = "home", id] = locationPath.split("/");
    if (route === "start") {
      const next = guide.scenarios.find(s => !complete(s));
      const to = next ? "#example/" + next.id : "#new";
      history.replaceState(null, "", to);
      return renderRoute();
    }
    const selected = guide.scenarios.find(s => String(s.id) === id);
    const custom = guide.custom.find(d => encodeURIComponent(d.id) === id);
    const showHome = !(route === "ask" || route === "mine" || route === "new" || (route === "example" && selected) || (route === "direction" && custom));
    home.hidden = !showHome;
    detail.hidden = showHome;
    detail.replaceChildren();
    const activeNav = showHome || route === "example" || route === "ask" ? "home" : "mine";
    document.querySelectorAll("[data-nav]").forEach(node => {
      if (node.dataset.nav === activeNav) node.setAttribute("aria-current", "page");
      else node.removeAttribute("aria-current");
    });
    if (showHome) document.title = homeTitle;
    else {
      if (route === "ask") askPage(new URLSearchParams(hash.slice(locationPath.length + 1)));
      if (route === "example") scenarioPage(selected);
      if (route === "mine") minePage();
      if (route === "new") newPage();
      if (route === "direction") directionPage(custom);
      const heading = detail.querySelector("h1");
      document.title = heading.textContent + " · Второй мозг";
      heading.focus({ preventScroll: true });
    }
    window.scrollTo(0, 0);
  }

  async function loadGuide() {
    if (guideError) guideError.hidden = true;
    try {
      guide = await api("guide");
      progressKey = "second-brain.demo.progress.v1." + (guide.vaultId || location.host);
      try {
        const saved = JSON.parse(localStorage.getItem(progressKey));
        if (Array.isArray(saved)) seen = new Set(saved);
      } catch { /* The walkthrough also works without browser storage. */ }
      updateProgress();
      renderRoute();
    } catch (error) {
      if (guideError) {
        const retry = button("Повторить", "button button-secondary");
        retry.addEventListener("click", loadGuide);
        guideError.replaceChildren(errorMessage(error), retry);
        guideError.hidden = false;
      }
    }
  }

  document.querySelector(".skip-link")?.addEventListener("click", event => {
    event.preventDefault();
    document.getElementById("content").focus();
  });
  document.getElementById("home-question").append(freeQuestionForm());
  mountCleanup(document.getElementById("guide-cleanup"), api, el, {guide:true});
  window.addEventListener("hashchange", renderRoute);
  loadGuide();
})();
