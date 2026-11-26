// Each editor is built on demand so a board with many results keeps its reading view light.
export function editor(ui, caption, submitText, build) {
  const {node, button, notice} = ui;
  const box = node("details", "okr-editor");
  const summary = node("summary", "text-link", caption);
  box.append(summary);
  box.addEventListener("toggle", () => {
    if (!box.open || box.querySelector("form")) return;
    const form = node("form", "okr-form");
    const save = build(form);
    const status = node("div");
    const submit = node("button", "button button-primary", submitText);
    submit.type = "submit";
    const cancel = button("Отмена", "button button-secondary", () => {
      box.open = false;
      form.remove();
      summary.focus();
    });
    const controls = node("div", "work-tools");
    controls.append(submit, cancel);
    form.append(controls, status);
    box.append(form);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (submit.disabled) return;
      submit.disabled = cancel.disabled = true;
      status.replaceChildren();
      try {
        await save();
      } catch (error) {
        status.replaceChildren(notice(error.message));
        if (error.status === 409) {
          status.append(button("Перечитать данные", "button button-secondary", () => ui.go(location.hash)));
        }
      } finally {
        submit.disabled = cancel.disabled = false;
      }
    });
  });
  return box;
}

export function field(ui, form, title, name, type = "text") {
  const input = ui.node("input");
  input.name = name;
  input.type = type;
  input.required = true;
  form.append(ui.labelled(title, input));
  return input;
}

export function planEditor(ui, done) {
  return editor(ui, "Создать план периода", "Создать план", form => {
    const title = field(ui, form, "Название плана или периода", "title");
    const requestId = crypto.randomUUID();
    return async () => done(await ui.api("plan/create", {title: title.value.trim(), requestId}));
  });
}

export function objectiveEditor(ui, planId, directions, done) {
  return editor(ui, "Добавить цель", "Создать цель", form => {
    const title = field(ui, form, "Формулировка цели", "title");
    const direction = ui.node("select");
    direction.name = "direction";
    direction.required = true;
    direction.append(ui.option("", "Выберите направление"));
    for (const row of directions) direction.append(ui.option(row.id, row.title));
    if (directions.length === 1) direction.value = directions[0].id;
    form.append(ui.labelled("Направление цели", direction));
    const requestId = crypto.randomUUID();
    return async () => done(await ui.api("plan/objective/create", {
      planId, title: title.value.trim(), direction: direction.value, requestId
    }));
  });
}

export const taskScope = row => row.projectTitle || row.directionTitle || row.project || row.direction || "";

function taskPicker(ui, form, rows, multiple) {
  const {node, labelled, button, counted} = ui;
  const group = node("fieldset", "okr-picker");
  group.append(node("legend", "", multiple ? "Связанные задачи" : "Задача"));
  const search = node("input");
  search.type = "search";
  search.addEventListener("keydown", event => { if (event.key === "Enter") event.preventDefault(); });
  group.append(labelled("Поиск по названию, проекту или направлению", search));
  const list = node("div", "okr-choices");
  const pages = node("div", "okr-pager");
  const picked = node("p", "gantt-context");
  picked.setAttribute("aria-live", "polite");
  group.append(list, pages, picked);
  form.append(group);
  const selected = new Set();
  let offset = 0;
  const size = 15;
  function tally() {
    picked.textContent = selected.size ? "Выбрано: " + counted(selected.size, ["задача","задачи","задач"]) : "";
  }
  function draw() {
    const q = search.value.trim().toLocaleLowerCase("ru");
    const found = rows.filter(row => [row.title, row.projectTitle, row.directionTitle, row.id]
      .some(value => value?.toLocaleLowerCase("ru").includes(q)));
    list.replaceChildren();
    for (const row of found.slice(offset, offset + size)) {
      const label = node("label", "okr-choice");
      const box = node("input");
      box.type = multiple ? "checkbox" : "radio";
      box.name = "taskId";
      box.value = row.id;
      box.checked = selected.has(row.id);
      box.addEventListener("change", () => {
        if (!multiple) selected.clear();
        if (box.checked) selected.add(row.id);
        else selected.delete(row.id);
        tally();
      });
      const copy = node("span");
      copy.append(node("span", "", row.title), node("small", "gantt-context", taskScope(row)));
      label.append(box, copy);
      list.append(label);
    }
    if (!found.length) list.append(node("p", "gantt-context", rows.length ? "Совпадений нет." : "Доступных задач пока нет."));
    pages.replaceChildren();
    if (found.length > size) {
      const prev = button("Назад", "text-link", () => { offset -= size; draw(); });
      const next = button("Дальше", "text-link", () => { offset += size; draw(); });
      prev.disabled = offset === 0;
      next.disabled = offset + size >= found.length;
      pages.append(prev, node("span", "gantt-context", (offset + 1) + "–" + Math.min(offset + size, found.length) + " из " + found.length), next);
    }
  }
  search.addEventListener("input", () => { offset = 0; draw(); });
  draw();
  return selected;
}

export function resultEditor(ui, objectives, tasks, done) {
  return editor(ui, "Добавить ключевой результат", "Создать результат", form => {
    const objective = ui.node("select");
    objective.name = "objectiveId";
    objective.required = true;
    objective.append(ui.option("", "Выберите цель"));
    for (const row of objectives) objective.append(ui.option(row.id, row.title));
    if (objectives.length === 1) objective.value = objectives[0].id;
    form.append(ui.labelled("Цель", objective));
    const title = field(ui, form, "Название ключевого результата", "title");
    const metrics = ui.node("div", "okr-metric-fields");
    const unit = field(ui, metrics, "Единица измерения (для числового КР)", "unit");
    unit.required = false;
    const target = field(ui, metrics, "Ориентир (необязательно)", "target");
    target.inputMode = "decimal";
    target.required = false;
    form.append(metrics);
    const selected = taskPicker(ui, form, tasks, true);
    const requestId = crypto.randomUUID();
    return async () => done(await ui.api("plan/okr/create", {
      objectiveId: objective.value, title: title.value.trim(), ...(unit.value.trim() ? {unit: unit.value.trim()} : {}), ...(target.value.trim() ? {target: target.value.trim()} : {}),
      taskIds: [...selected], requestId
    }));
  });
}

export function linkEditor(ui, result, tasks, done) {
  return editor(ui, "Привязать задачу", "Привязать", form => {
    const selected = taskPicker(ui, form, tasks.filter(row => !result.taskIds.includes(row.id)), false);
    return async () => {
      const [taskId] = selected;
      if (!taskId) throw new Error("Выберите задачу для этого результата.");
      await done(await ui.api("plan/okr/link", {id: result.id, taskId, revision: result.revision}));
    };
  });
}

export function assignmentEditor(ui, card, options) {
  const objectives = options.objectives.filter(row => row.direction === card.direction);
  const plans = new Map(options.plans.map(row => [row.id, row.title]));
  if (!objectives.length) {
    const empty = ui.node("p", "gantt-context", "Чтобы включить результат в план, создайте цель в том же направлении. ");
    empty.append(ui.link("Проектная деятельность", "#activity", "text-link"));
    return empty;
  }
  return editor(ui, "Включить в цель плана", "Включить в цель", form => {
    const select = ui.node("select");
    select.name = "objectiveId";
    select.required = true;
    select.append(ui.option("", "Выберите цель"));
    for (const row of objectives) select.append(ui.option(row.id, (plans.get(row.planId) || row.planId) + " · " + row.title));
    form.append(ui.labelled("План и цель", select));
    return async () => {
      const objective = objectives.find(row => row.id === select.value);
      await ui.api("plan/okr/assign", {id: card.id, objectiveId: objective.id, revision: card.revision});
      ui.go("#okr?" + new URLSearchParams({plan: objective.planId}));
    };
  });
}
