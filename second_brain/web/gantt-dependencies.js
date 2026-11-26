import {dateText} from "./gantt-axis.js";

export function newWorkForm(ui, plan, finish) {
  const {node, option, labelled, button, notice, api} = ui;
  const project = plan.scope === "project";
  const form = node("form", "gantt-form gantt-new-milestone");
  const requestId = crypto.randomUUID();
  const title = node("input"); title.name = project ? "taskTitle" : "milestoneTitle"; title.required = true;
  const due = node("input"); due.type = "date"; due.name = "due";
  const kind = node("select"), scope = node("select");
  form.append(node("h2", "", project ? "Новая работа" : "Новая веха"),
    labelled(project ? "Название работы" : "Название вехи", title));
  if (project) {
    kind.append(option("task", "Задача"), option("milestone", "Веха"));
    form.append(labelled("Тип работы", kind));
  } else {
    const scopes = new Map();
    for (const row of plan.items) {
      for (const [field, label] of [["direction", "Направление"], ["project", "Проект"]]) {
        if (row[field]) scopes.set(field + "/" + row[field], label + " · " + row[field + "Title"]);
      }
    }
    scope.append(option("", "Без привязки"));
    for (const [key, name] of scopes) scope.append(option(key, name));
    if (scopes.size) form.append(labelled("Связать с направлением или проектом из плана", scope));
  }
  form.append(labelled("Согласованный срок (необязательно)", due));
  const tools = node("div", "work-tools");
  const submit = node("button", "button button-primary", project ? "Добавить в проект" : "Добавить в план"); submit.type = "submit";
  tools.append(submit, button("Отмена", "button button-secondary", () => finish()));
  const output = node("div");
  form.append(tools, output);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!title.value.trim() || submit.disabled) return;
    const payload = {title: title.value.trim(), requestId, ...(project ? {projectId: plan.id, kind: kind.value} : {planId: plan.id})};
    if (due.value) payload.due = due.value;
    if (scope.value) {
      const [field, id] = scope.value.split("/");
      payload[field] = id;
    }
    for (const control of form.elements) control.disabled = true;
    try { await finish(await api(project ? "plan/project-work" : "plan/milestone", payload)); }
    catch (error) { output.replaceChildren(notice(error.message)); }
    finally { for (const control of form.elements) control.disabled = false; }
  });
  return form;
}

export function dependencySection(ui, settings) {
  const {node, button, option, labelled, notice, api} = ui;
  const {task, plan, openForm, actions, refresh, markdownLink, selectRelated} = settings;
  const group = node("section", "gantt-dependencies");
  const header = node("div", "gantt-pane-heading");
  header.append(node("h3", "", "Зависимости"));
  group.append(header);
  const relations = task.relations;
  if (relations.error) {
    group.append(notice(relations.error));
    return group;
  }
  const {predecessors, successors} = relations;
  const add = button("Добавить предшественника", "gantt-action", () => editRelation("add", add));
  header.append(add);
  for (const [name, rows] of [["Сначала завершить", predecessors], ["Затем можно начать", successors]]) {
    if (!rows.length) continue;
    const line = node("div", "gantt-relation");
    line.append(node("span", "gantt-byline", name));
    const list = node("ul");
    for (const related of rows) {
      const li = node("li");
      if (!related.path || related.missing) {
        li.append(node("span", "gantt-context", "Недоступная карточка · " + related.id));
      } else if (plan.items.some(row => row.id === related.id && !row.missing)) {
        li.append(button(related.title, "gantt-action", () => selectRelated(related.id)));
      } else li.append(markdownLink(related.title + " ↗", related.path));
      if (related.kind === "milestone") li.append(node("small", "gantt-byline", "Веха"));
      if (related.linkPath && Number.isInteger(related.linkLine)) {
        const source = markdownLink("Строка " + related.linkLine + " ↗", related.linkPath, null, related.linkLine);
        source.classList.add("gantt-link-source");
        source.title = related.linkPath + " · строка " + related.linkLine;
        source.setAttribute("aria-label", "Запись зависимости · " + source.title);
        li.append(source);
      }
      list.append(li);
    }
    line.append(list);
    group.append(line);
  }
  if (!predecessors.length && !successors.length) group.append(node("p", "gantt-context", "Связи с другими работами пока не заданы."));
  if (predecessors.length) {
    const remove = button("Убрать связь", "gantt-action", () => editRelation("remove", remove));
    group.append(remove);
  }
  function editRelation(action, trigger) {
    const adding = action === "add";
    const form = openForm(adding ? "Что должно завершиться до начала этой работы?" : "Какую зависимость убрать?");
    if (!form) return;
    const select = node("select"); select.name = "predecessor"; select.required = true;
    const query = node("input"); query.type = "search"; query.name = "taskSearch";
    const hint = node("small", "gantt-byline"); hint.setAttribute("role", "status");
    const excluded = new Set([task.id, ...predecessors.map(item => item.id)]);
    function options(list) {
      const items = adding ? list.filter(item => !excluded.has(item.id) && !item.missing) : list;
      const counts = new Map();
      for (const item of items) counts.set(item.title, (counts.get(item.title) || 0) + 1);
      select.replaceChildren(option("", items.length ? "Выберите работу" : "Нет подходящих работ"));
      for (const item of items) {
        const label = item.missing ? "Недоступная карточка · " + item.id : (item.kind === "milestone" ? "Веха · " : "") + item.title;
        select.append(option(item.id, item.path && counts.get(item.title) > 1 ? label + " · " + item.path : label));
      }
    }
    options(adding ? plan.items : predecessors);
    let pending = 0, timer;
    if (adding) {
      form.append(labelled("Поиск по всем планам и задачам", query));
      query.addEventListener("input", () => {
        const version = ++pending;
        clearTimeout(timer);
        if (!query.value.trim()) { hint.textContent = ""; options(plan.items); return; }
        options([]); hint.textContent = "Поиск…";
        timer = setTimeout(async () => {
          try {
            const page = await api("plan/tasks?" + new URLSearchParams({match: query.value.trim(), limit: 20}));
            if (version !== pending || !form.isConnected) return;
            options(page.items);
            hint.textContent = page.total > page.items.length ? "Показаны первые 20 результатов. Уточните название." : "";
          } catch (error) {
            if (version === pending && form.isConnected) hint.replaceChildren(notice(error.message));
          }
        }, 180);
      });
    }
    form.append(labelled("Предшественник", select), hint);
    const {submit, feedback} = actions(form, adding ? "Добавить связь" : "Убрать связь", trigger);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (submit.disabled || !select.value) return;
      for (const control of form.elements) control.disabled = true;
      try {
        await api("plan/dependency", {id: task.id, predecessor: select.value, action});
        await refresh(adding ? "Зависимость добавлена." : "Зависимость убрана.");
      } catch (error) { feedback.replaceChildren(notice(error.message)); }
      finally { for (const control of form.elements) control.disabled = false; }
    });
    (adding ? query : select).focus();
  }
  return group;
}

export function scenarioForm(ui, settings) {
  const {node, labelled, counted, api, notice} = ui;
  const {task, form, actions, trigger, markdownLink, refresh} = settings;
  const forecast = node("input"); forecast.type = "date"; forecast.name = "scenarioForecast"; forecast.required = true;
  forecast.value = task.forecast || "";
  const result = node("section", "gantt-scenario-result");
  result.setAttribute("aria-live", "polite");
  form.append(labelled("Предполагаемая дата завершения", forecast), result);
  const {submit, feedback} = actions(form, "Рассчитать", trigger);
  let revision = null;
  function reset() {
    revision = null;
    result.replaceChildren();
    feedback.replaceChildren();
    submit.textContent = "Рассчитать";
  }
  forecast.addEventListener("input", reset);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (submit.disabled || !forecast.value) return;
    for (const control of form.elements) control.disabled = true;
    const payload = {id: task.id, forecast: forecast.value};
    try {
      if (revision) {
        await api("plan/scenario/apply", {...payload, revision});
        await refresh("Прогнозы сохранены в карточках.");
        return;
      }
      const preview = await api("plan/scenario/preview", payload);
      const total = counted(preview.changed, ["изменение", "изменения", "изменений"]);
      result.replaceChildren(node("h4", "", "Предпросмотр · " + total));
      const list = node("ol");
      const labels = {changed: "Сдвиг прогноза", unchanged: "Без изменения", blocked: "Нужны данные", completed: "Завершено"};
      for (const row of preview.rows) {
        const item = node("li", "gantt-impact");
        const head = node("div", "gantt-impact-heading");
        head.append(node("strong", "", row.title), node("span", "gantt-impact-state is-" + row.status, labels[row.status]));
        item.append(head);
        if (row.status === "changed") {
          item.append(node("p", "gantt-impact-date", dateText(row.before) + " → " + dateText(row.after)));
        } else if (row.status === "completed") {
          item.append(node("p", "gantt-impact-date", "Факт · " + dateText(row.completed)));
        } else if (row.status === "unchanged" && row.after) {
          item.append(node("p", "gantt-impact-date", "Прогноз · " + dateText(row.after)));
        }
        const deadline = row.due ? ["Согласованный срок " + dateText(row.due)] : ["Срок не согласован"];
        if (Number.isInteger(row.slackDays)) {
          deadline.push(row.slackDays === 0 ? "точно в срок" : (row.slackDays < 0 ? "позже на " : "запас ") + Math.abs(row.slackDays) + " дн.");
        }
        item.append(node("p", "gantt-byline", deadline.join(" · ")));
        if (row.reason) item.append(node("p", "", row.reason));
        const foot = node("div", "gantt-impact-foot");
        if (row.plans.length) foot.append(node("span", "", row.plans.map(p => p.title).join(" · ")));
        foot.append(markdownLink("Карточка ↗", row.path));
        item.append(foot); list.append(item);
      }
      result.append(list);
      if (preview.changed) {
        revision = preview.revision;
        submit.textContent = "Применить прогнозы";
      } else feedback.textContent = "Записывать изменения не требуется.";
    } catch (error) {
      reset();
      feedback.replaceChildren(notice(error.status === 409 ? error.message + " Рассчитайте сценарий ещё раз." : error.message));
    } finally { for (const control of form.elements) control.disabled = false; }
  });
  forecast.focus();
}
