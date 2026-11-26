import {dayOf} from "./gantt-axis.js";

// Keep all relationships visible. A shared task gets a single editable bar.
export function treeRows(plan, scope, folded) {
  const tree = plan.tree || [];
  const nodes = new Map(tree.map(entry => [entry.key, entry]));
  const items = new Map(plan.items.map(row => [row.id, row]));
  const unlinked = scope === "unlinked"
    ? new Set(plan.objectives.flatMap(goal => goal.results.flatMap(kr => kr.taskIds))) : null;
  const wanted = new Set();
  function includeAncestors(entry) {
    while (entry && !wanted.has(entry.key)) {
      wanted.add(entry.key);
      entry = nodes.get(entry.parentKey);
    }
  }
  if (!scope) for (const entry of tree) wanted.add(entry.key);
  else if (unlinked) {
    // A supporting subtask can be outside the KR's accounting even when its
    // parent contributes. This filter shows just those tasks, as a flat list.
    return plan.items.filter(row => !unlinked.has(row.id))
      .map(row => ({key: "unlinked/task/" + row.id, kind: "task", id: row.id, row, level: 0}));
  } else {
    const results = new Set([scope]);
    const goal = plan.objectives.find(item => item.id === scope);
    if (goal) for (const kr of goal.results) results.add(kr.id);
    for (const entry of tree) {
      if (results.has(entry.resultId) || entry.id === scope) includeAncestors(entry);
    }
  }
  const parents = new Set(tree.filter(entry => wanted.has(entry.key)).map(entry => entry.parentKey));
  const seen = new Set(), hidden = new Set();
  const display = [];
  for (const entry of tree) {
    if (!wanted.has(entry.key)) continue;
    const row = entry.kind === "task" ? items.get(entry.id) : undefined;
    const entryHidden = hidden.has(entry.parentKey);
    if (entryHidden || folded.has(entry.key)) hidden.add(entry.key);
    if (entryHidden) continue;
    const reference = entry.kind === "task" && (entry.contextOnly || seen.has(entry.id));
    if (entry.kind === "task" && !entry.contextOnly) seen.add(entry.id);
    display.push({...entry, row, reference, expandable: parents.has(entry.key)});
  }
  return display;
}

export function branchDates(entries, fields) {
  const spans = new Map();
  for (const entry of [...entries].reverse()) {
    const dates = entry.contextOnly ? [] : fields.map(field => entry.row?.[field]).filter(date => dayOf(date) !== null);
    const span = spans.get(entry.key);
    if (span) dates.push(span.start, span.end);
    if (!dates.length) continue;
    dates.sort();
    const current = {start: dates[0], end: dates[dates.length - 1]};
    spans.set(entry.key, current);
    if (entry.parentKey) {
      const parent = spans.get(entry.parentKey);
      spans.set(entry.parentKey, {start: parent && parent.start < current.start ? parent.start : current.start,
        end: parent && parent.end > current.end ? parent.end : current.end});
    }
  }
  return spans;
}
