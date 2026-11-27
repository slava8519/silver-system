import {dayOf, timelineDates} from "./gantt-axis.js";

export function selectionParams(key, scope, group = false) {
  const params = new URLSearchParams();
  if (key) params.set(group ? "row" : "task", key);
  if (scope) params.set("okr", scope);
  return params;
}

export function recordedSpan(rows) {
  let start, end;
  for (const row of rows) {
    for (const field of Object.keys(timelineDates)) {
      const date = row[field];
      if ((row.kind === "milestone" && field === "start") || dayOf(date) === null) continue;
      if (!start || date < start) start = date;
      if (!end || date > end) end = date;
    }
  }
  return start ? {start, end} : null;
}

// Group journals use stable plan-local keys, including buckets without an assigned project or direction.
export function describeRow(plan, key) {
  if (!key?.includes("/")) return null;
  if (key === (plan.scope === "project" ? "project/" : "plan/") + plan.id) {
    const project = plan.scope === "project";
    return {kind:project ? "project" : "plan", id:plan.id, label:project ? "Проект" : "План", title:plan.title,
      rows:plan.items, progress:plan.progress};
  }
  const tree = plan.tree || [];
  const entry = tree.find(node => node.key === key && ["objective", "result"].includes(node.kind));
  if (entry) {
    const branch = new Set([key]), members = new Set();
    for (const child of tree) {
      if (!branch.has(child.parentKey)) continue;
      branch.add(child.key);
      if (child.kind === "task" && !child.contextOnly) members.add(child.id);
    }
    const goal = entry.kind === "objective";
    return {kind:goal ? "objective" : "key_result", id:entry.id, label:goal ? "Цель" : "КР",
      title:entry.title, progress:{value:entry.readiness, hasEstimates:entry.hasEstimates},
      rows:plan.items.filter(row => members.has(row.id))};
  }
  if (key === "group/outside") {
    if (!tree.some(node => node.key === "outside")) return null;
    const ids = new Set(tree.filter(node => node.kind === "task" && !node.resultId && !node.contextOnly).map(node => node.id));
    return {kind:"group", id:"outside", label:"Группа плана", title:"Другие работы плана", rows:plan.items.filter(row => ids.has(row.id))};
  }
  const match = /^group\/(direction|project)\/(.*)$/.exec(key);
  if (!match || plan.scope === "project") return null;
  const [, field, id] = match;
  const rows = plan.items.filter(row => (row[field] || "") === id);
  if (!rows.length) return null;
  return {kind:"group", id:field + "/" + id, label:"Группа плана",
    title:rows[0][field + "Title"] || (field === "project" ? "Без проекта" : "Без направления"), rows};
}
