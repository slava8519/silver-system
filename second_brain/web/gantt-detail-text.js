import {dateText} from "./gantt-axis.js";
import {reportDocument} from "./report-document.js";

// Markdown destinations are relative to the task card, including cards in subfolders.
function localNote(cardPath, href) {
  const hash = href.indexOf("#");
  const relative = hash < 0 ? href : href.slice(0, hash);
  let subpath;
  if (hash >= 0) {
    try { subpath = decodeURIComponent(href.slice(hash)); } catch { return null; }
    if (/[\u0000-\u001f\u007f]/.test(subpath)) return null;
    if (subpath === "#") subpath = undefined;
  }
  if (!relative) return subpath ? {path: cardPath, subpath} : null;
  if (/^(?:\/|[a-z][\w+.-]*:)/i.test(relative) || relative.includes("?")) return null;
  const parts = cardPath.split("/").slice(0, -1);
  for (const raw of relative.split("/")) {
    let part;
    try { part = decodeURIComponent(raw); } catch { return null; }
    if (part === ".") continue;
    if (part === "..") {
      if (raw !== ".." || !parts.length) return null;
      parts.pop();
    } else {
      if (!part || part.startsWith(".") || /[\\/#:\u0000-\u001f\u007f]/.test(part)) return null;
      parts.push(part);
    }
  }
  return /\.md$/i.test(parts.at(-1) || "") ? {path: parts.join("/"), subpath} : null;
}

export function inspectorText(ui, className, value, cardPath, markdownLink) {
  const doc = reportDocument(value, (label, href) => {
    if (/^https?:\/\//i.test(href)) {
      const a = ui.link(label, href, "text-link");
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      return a;
    }
    const target = localNote(cardPath, href);
    if (!target) return label;
    const a = markdownLink(label, target.path, null, null, target.subpath);
    a.title = target.path + (target.subpath || "");
    return a;
  });
  doc.className = className;
  return doc;
}

export function readinessText(state) {
  if (state?.value == null) return "Не указана";
  const basis = {estimate: "Оценка", fact: "Факт"}[state.kind];
  return [String(state.value).replace(".", ",") + "%", basis, state.date && dateText(state.date)].filter(Boolean).join(" · ");
}
