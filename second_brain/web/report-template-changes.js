const fields = [
  ["title", "Название шаблона"], ["audience", "Для кого предназначен отчёт"],
  ["guidance", "Правила отбора и подачи"], ["body", "Структура текста"]
];

// Keep separated edits readable, omitting matching Markdown lines from the comparison.
export function changedLines(before, after) {
  const old = before ? before.replace(/\r\n?/g, "\n").split("\n") : [];
  const next = after ? after.replace(/\r\n?/g, "\n").split("\n") : [];
  let start = 0, oldEnd = old.length, newEnd = next.length;
  while (start < oldEnd && start < newEnd && old[start] === next[start]) start++;
  while (oldEnd > start && newEnd > start && old[oldEnd - 1] === next[newEnd - 1]) { oldEnd--; newEnd--; }
  const a = old.slice(start, oldEnd), b = next.slice(start, newEnd);
  if (!a.length && !b.length) return [];
  // A long wholesale rewrite or insertion is clearer as a single replacement.
  if (!a.length || !b.length || (a.length + 1) * (b.length + 1) > 150000) return [{before: a, after: b}];
  const width = b.length + 1, common = new Uint32Array((a.length + 1) * width);
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      const at = i * width + j;
      common[at] = a[i] === b[j] ? 1 + common[at + width + 1] : Math.max(common[at + width], common[at + 1]);
    }
  }
  const changes = [];
  let group, i = 0, j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { i++; j++; group = null; continue; }
    if (!group) { group = {before: [], after: []}; changes.push(group); }
    const at = i * width + j;
    if (i < a.length && (j === b.length || common[at + width] >= common[at + 1])) group.before.push(a[i++]);
    else group.after.push(b[j++]);
  }
  return changes;
}

export function templateChanges(ui, get, item) {
  const previous = Math.max(0, ...item.history.map(row => row.version).filter(version => version < item.version));
  if (!previous) return null;
  const {node, notice} = ui;
  const details = node("details", "report-preview report-changes");
  const result = node("div");
  details.append(node("summary", "", "Изменения в версии " + item.version), result);
  let loaded = false, pending = false;
  details.addEventListener("toggle", async () => {
    if (!details.open || loaded || pending) return;
    pending = true;
    result.setAttribute("aria-busy", "true");
    try {
      const older = await get("template", {id: item.id, version: previous});
      result.replaceChildren();
      for (const [field, title] of fields) {
        const changes = changedLines(older[field] || "", item[field] || "");
        if (!changes.length) continue;
        result.append(node("h4", "", title));
        for (const change of changes) {
          for (const [lines, label] of [[change.before, "Было · версия " + previous], [change.after, "Стало · версия " + item.version]]) {
            if (!lines.length) continue;
            result.append(node("small", "", label), node("p", "report-value", lines.join("\n") || "(пустая строка)"));
          }
        }
      }
      if (!result.children.length) result.append(node("p", "", "Текст совпадает с предыдущей версией."));
      loaded = true;
    } catch (error) { result.replaceChildren(notice(error.message)); }
    finally {
      pending = false;
      result.removeAttribute("aria-busy");
    }
  });
  return details;
}
