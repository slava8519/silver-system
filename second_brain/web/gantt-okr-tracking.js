import {editor, field} from "./gantt-okr-forms.js";
import {sourcePicker} from "./gantt-okr-source.js";
import {symbols} from "./gantt-okr-values.js";

function choice(ui, form, label, name, entries) {
  const select = ui.node("select"); select.name = name; select.required = true;
  for (const [value, title] of entries) select.append(ui.option(value, title));
  form.append(ui.labelled(label, select));
  return select;
}
function decimal(ui, form, label, name) {
  const input = field(ui, form, label, name);
  input.inputMode = "decimal";
  return input;
}
function tick(ui, form, label, name) {
  const box = ui.node("input"); box.type = "checkbox"; box.name = name;
  const wrap = ui.node("label", "okr-tick");
  wrap.append(box, ui.node("span", "", label)); form.append(wrap);
  return box;
}
const operators = Object.entries(symbols);

export function criterionEditor(ui, result, done) {
  return editor(ui, "Добавить критерий", "Создать критерий", form => {
    const title = field(ui, form, "Что проверяем", "title");
    const kind = choice(ui, form, "Тип критерия", "kind", [["numeric","Числовой порог"],["boolean","Условие да / нет"]]);
    const numeric = ui.node("div", "okr-form-part");
    form.append(numeric);
    const unit = field(ui, numeric, "Единица измерения", "unit");
    const operator = choice(ui, numeric, "Условие", "operator", operators);
    const target = decimal(ui, numeric, "Порог", "target");
    const primary = result.unit && !result.criteria?.some(c => c.primary)
      ? tick(ui, numeric, "Основная метрика этого КР", "primary") : null;
    function fields() {
      const active = kind.value === "numeric";
      numeric.hidden = !active;
      target.required = unit.required = active;
      if (active && primary?.checked) {
        unit.value = result.unit;
        if (target.value === "" && result.target != null) target.value = result.target;
      }
      unit.readOnly = Boolean(active && primary?.checked);
    }
    kind.addEventListener("change", fields); primary?.addEventListener("change", fields); fields();
    const requestId = crypto.randomUUID();
    return async () => {
      const payload = {id: result.id, revision: result.revision, requestId, title: title.value.trim(), kind: kind.value};
      if (kind.value === "numeric") Object.assign(payload, {unit: unit.value.trim(), operator: operator.value, target: target.value.trim(), primary: Boolean(primary?.checked)});
      await ui.api("plan/okr/criterion", payload); await done("Критерий добавлен.", "criteria");
    };
  });
}

export function observationEditor(ui, result, done) {
  const criteria = result.criteria || [];
  const primary = criteria.find(row => row.primary);
  const hasLegacy = Boolean(result.unit && !primary);
  if (!criteria.length && !hasLegacy) return null;
  return editor(ui, "Записать замер или проверку", "Сохранить проверку", form => {
    const entries = criteria.map(row => [row.id, row.title]);
    if (hasLegacy) entries.unshift(["legacy", "Метрика КР · " + result.unit]);
    const criterion = choice(ui, form, "Критерий или метрика", "criterionId", entries);
    const value = decimal(ui, form, "Значение", "value");
    const verdict = choice(ui, form, "Подтверждённое значение", "verdict", [["","Выберите значение"],["true","Да"],["false","Нет"]]);
    const date = field(ui, form, "Дата замера или проверки", "date", "date");
    const baseline = tick(ui, form, "Записать как исходную точку", "baseline");
    function fields() {
      const active = criteria.find(row => row.id === criterion.value);
      const boolean = active?.kind === "boolean";
      const label = value.closest("label");
      label.hidden = value.disabled = boolean;
      label.firstChild.textContent = "Значение" + ((active?.unit || result.unit) ? ", " + (active?.unit || result.unit) : "");
      verdict.closest("label").hidden = verdict.disabled = !boolean;
      baseline.closest("label").hidden = baseline.disabled = !active || boolean;
    }
    criterion.addEventListener("change", fields); fields();
    const selectedSource = sourcePicker(ui, form);
    return async () => {
      const legacy = criterion.value === "legacy";
      const payload = {id: result.id, value: value.disabled ? verdict.value === "true" : value.value.trim(),
        date: date.value, source: selectedSource(), revision: result.revision};
      if (!legacy) Object.assign(payload, {criterionId: criterion.value, baseline: !baseline.disabled && baseline.checked});
      await ui.api(legacy ? "plan/okr/measure" : "plan/okr/observe", payload);
      await done("Проверка сохранена.", "criteria");
    };
  });
}

export function thresholdEditor(ui, result, done) {
  const numbers = (result.criteria || []).filter(row => row.kind === "numeric");
  if (!numbers.length) return null;
  return editor(ui, "Изменить порог", "Сохранить порог", form => {
    const criterion = choice(ui, form, "Критерий", "criterionId", numbers.map(row => [row.id, row.title]));
    const operator = choice(ui, form, "Условие", "operator", operators);
    const target = decimal(ui, form, "Порог", "target");
    function fill() {
      const row = numbers.find(c => c.id === criterion.value);
      operator.value = row.operator; target.value = row.target;
    }
    criterion.addEventListener("change", fill); fill();
    return async () => {
      await ui.api("plan/okr/criterion-target", {id: result.id, criterionId: criterion.value, revision: result.revision, operator: operator.value, target: target.value.trim()});
      await done("Порог сохранён.", "criteria");
    };
  });
}

export function weightEditor(ui, result, rows, done) {
  return editor(ui, "Изменить вес", "Сохранить вес", form => {
    const options = rows.filter(row => !row.missing).map(row => [row.id, row.title]);
    if (result.objectiveId) options.unshift(["kr", "Ключевой результат в цели"]);
    const item = choice(ui, form, "Чей вес изменить", "taskId", options);
    const weight = decimal(ui, form, "Доля от 0 до 1", "weight");
    const remove = tick(ui, form, "Снять вес", "clear");
    function fill() { weight.value = (item.value === "kr" ? result.weight : result.taskWeights?.[item.value]) ?? ""; }
    item.addEventListener("change", fill); fill();
    remove.addEventListener("change", () => { weight.disabled = remove.checked; });
    return async () => {
      const data = {id: result.id, weight: remove.checked ? null : weight.value.trim(), revision: result.revision};
      if (item.value !== "kr") data.taskId = item.value;
      await ui.api("plan/okr/weight", data); await done("Вес сохранён.", "initiatives");
    };
  });
}

export function readinessEditor(ui, rows, done) {
  const available = rows.filter(row => !row.missing && row.revision);
  if (!available.length) return null;
  return editor(ui, "Обновить готовность", "Сохранить готовность", form => {
    const task = choice(ui, form, "Инициатива", "taskId", available.map(row => [row.id, row.title]));
    const kind = choice(ui, form, "Основание готовности", "kind", [["fact","Факт"],["estimate","Оценка"]]);
    const value = decimal(ui, form, "Готовность, %", "value");
    const date = field(ui, form, "Дата готовности", "date", "date");
    const reason = field(ui, form, "Основание готовности", "reason");
    const confirm = tick(ui, form, "Подтверждаю готовность вручную", "ownerConfirmed");
    const withSource = tick(ui, form, "Приложить источник", "withSource");
    const holder = ui.node("div"); holder.hidden = true; form.append(holder);
    let selectedSource;
    withSource.addEventListener("change", () => {
      holder.hidden = !withSource.checked; reason.required = !withSource.checked;
      if (withSource.checked && !selectedSource) selectedSource = sourcePicker(ui, holder);
      else if (!withSource.checked) { holder.replaceChildren(); selectedSource = null; }
    });
    return async () => {
      const selected = available.find(row => row.id === task.value);
      const data = {id: selected.id, revision: selected.revision, value: value.value.trim(), date: date.value, kind: kind.value, ownerConfirmed: confirm.checked};
      if (reason.value.trim()) data.reason = reason.value.trim();
      if (withSource.checked) data.source = selectedSource();
      await ui.api("plan/okr/readiness", data); await done("Готовность обновлена.", "initiatives");
    };
  });
}

export function relationsEditor(ui, result, rows, options, done) {
  const current = result.contextProjects || [];
  const linked = new Set(current.map(p => p.id));
  const projects = (options.projects || []).filter(p => !linked.has(p.id));
  const groups = [];
  if (projects.length) groups.push(["add","Связать контекстный проект",projects]);
  if (current.length) groups.push(["remove","Убрать связь с проектом",current]);
  if (rows.length) groups.push(["unlink","Отвязать инициативу",rows]);
  if (!groups.length) return null;
  return editor(ui, "Настроить связи КР", "Сохранить изменение", form => {
    const action = choice(ui,form,"Действие","action",groups.map(([key,label])=>[key,label]));
    const record = choice(ui,form,"Проект или инициатива","record",[]);
    const hint = ui.node("p","gantt-context"); form.append(hint);
    function fill() {
      const records = groups.find(([key])=>key===action.value)[2];
      record.replaceChildren(ui.option("", "Выберите запись"));
      for (const row of records) record.append(ui.option(row.id,(row.title || "Недоступная карточка · " + row.id) +
        (row.projectTitle ? " · " + row.projectTitle : "")));
      hint.textContent = action.value === "unlink" ? "Снимаются связь с этим результатом и её вес. Карточка задачи сохраняется." : "";
    }
    action.addEventListener("change",fill); fill();
    return async () => {
      if (action.value === "unlink") await ui.api("plan/okr/unlink", {id:result.id,taskId:record.value,revision:result.revision});
      else await ui.api("plan/okr/context", {id:result.id,projectId:record.value,present:action.value === "add",revision:result.revision});
      await done("Связи обновлены.","initiatives");
    };
  });
}
