import {dateText, progressText} from "./gantt-axis.js";
import {recordDocument} from "./record-document.js";

// The semicolons in Markdown links, emphasis and code belong to those inline elements.
function fieldValue(ui, content) {
  const value = ui.node("dd");
  const items = [[]];
  for (const part of content) {
    if (typeof part !== "string") {
      items.at(-1).push(part);
      continue;
    }
    const [first, ...rest] = part.split(/;[ \t\u00a0]+/u);
    items.at(-1).push(first);
    for (const text of rest) items.push([text]);
  }
  if (items.length === 1 || items.some(item => item.every(part => typeof part === "string" && !part.trim()))) {
    value.append(...content);
    return value;
  }
  const list = ui.node("ul", "gantt-reading-items");
  for (const parts of items) {
    const item = ui.node("li");
    item.append(...parts);
    list.append(item);
  }
  value.append(list);
  return value;
}

// Older briefs store their lead and named fields in one paragraph. Split only
// plain text after Markdown rendering so a label inside a link or code stays intact.
function readingBlocks(ui, paragraph, journalEntry) {
  const blocks = [];
  let fields = null, content = [], label = null;
  function finish() {
    if (label !== null) {
      if (!fields) { fields = ui.node("dl", "gantt-reading-fields"); blocks.push(fields); }
      const term = ui.node("dt", "", label);
      if (/^(мешает|риски?)$/i.test(label)) term.className = "gantt-reading-risk";
      if (/^решени[ея]$/i.test(label)) term.className = "gantt-reading-decision";
      fields.append(term, fieldValue(ui, content));
    } else if (content.length) {
      const p = ui.node("p");
      p.append(...content);
      blocks.push(p);
      fields = null;
    }
    content = [];
  }
  const markers = /(?:^[ \t]*|\s+[•·]\s+)(Сделано|Дальше|Мешает|Риски?|Решени[ея]|Сроки?|Следующий шаг):[ \t]+|\s+·\s+/gi;
  let split = false, atStart = true;
  for (const child of [...paragraph.childNodes]) {
    if (child.nodeType !== 3) { content.push(child); atStart = false; continue; }
    const text = child.textContent;
    let from = 0;
    for (const marker of text.matchAll(markers)) {
      if (!marker[1] && !journalEntry) continue;
      if (marker[1] && !atStart && marker.index === 0 && !/[•·]/.test(marker[0])) continue;
      if (marker.index > from) content.push(text.slice(from, marker.index));
      finish();
      label = marker[1] || null;
      from = marker.index + marker[0].length;
      split = true;
    }
    if (text.length > from) content.push(text.slice(from));
    if (text.length) atStart = false;
  }
  if (!split) return [paragraph];
  finish();
  return blocks;
}

export function inspectorText(ui, className, value, cardPath, markdownLink, journalEntry = false) {
  const doc = recordDocument({path:cardPath}, value, ui, {nativeLink:markdownLink});
  doc.className = className;
  const blocks = [...doc.children].flatMap(block =>
    block.tagName === "P" ? readingBlocks(ui, block, journalEntry) : [block]);
  doc.replaceChildren(...blocks);
  return doc;
}

export function readinessText(state) {
  if (state?.value == null) return "Не указана";
  const basis = {estimate: "Оценка", fact: "Факт"}[state.kind];
  return [progressText(state.value), basis, state.date && dateText(state.date)].filter(Boolean).join(" · ");
}
