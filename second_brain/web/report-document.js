// Build the report's reading view from text nodes. Vault Markdown may contain arbitrary HTML.
function appendInline(parent, value, linkFor) {
  const pattern = /\\([\\\[\]*_`])|\*\*(.+?)\*\*|__(.+?)__|`([^`]+)`|\[((?:\\.|[^\]\\])+)\]\((<[^>]+>|[^\s)]+)\)|\*([^*\s][^*]*?)\*/g;
  let end = 0;
  for (const match of value.matchAll(pattern)) {
    parent.append(decodeEntities(value.slice(end, match.index)));
    const [, escaped, bold, boldAlt, code, label, target, italic] = match;
    if (escaped) parent.append(escaped);
    else if (label) {
      const name = decodeEntities(label.replace(/\\([\\\[\]*_`])/g, "$1"));
      parent.append(linkFor?.(name, target.replace(/^<|>$/g, "")) || name);
    } else {
      const element = document.createElement(code ? "code" : italic ? "em" : "strong");
      element.textContent = decodeEntities(code || italic || bold || boldAlt);
      parent.append(element);
    }
    end = match.index + match[0].length;
  }
  parent.append(decodeEntities(value.slice(end)));
}

function decodeEntities(value) {
  return value.replace(/&(amp|lt|gt|quot);/g, (_, name) => ({amp: "&", lt: "<", gt: ">", quot: '"'}[name]));
}

export function reportDocument(markdown, linkFor) {
  const doc = document.createElement("div");
  doc.className = "report-prose";
  const lists = [];
  let paragraph = null, codeBlock = null;
  for (const line of markdown.replace(/\r\n?/g, "\n").split("\n")) {
    if (/^\s*```/.test(line)) {
      if (codeBlock) codeBlock = null;
      else {
        codeBlock = document.createElement("pre");
        doc.append(codeBlock);
      }
      paragraph = null;
      lists.length = 0;
      continue;
    }
    if (codeBlock) { codeBlock.append(line + "\n"); continue; }
    const item = /^(\s*)([-+*]|\d+[.)])\s+(.*)$/.exec(line);
    if (item) {
      const indent = item[1].replace(/\t/g, "    ").length;
      const tag = /\d/.test(item[2]) ? "ol" : "ul";
      while (lists.length && lists[lists.length - 1].indent > indent) lists.pop();
      if (lists.length && lists[lists.length - 1].indent === indent && lists[lists.length - 1].tag !== tag) lists.pop();
      if (!lists.length || lists[lists.length - 1].indent < indent) {
        const list = document.createElement(tag);
        if (tag === "ol") list.start = parseInt(item[2], 10);
        (lists.length ? lists[lists.length - 1].node.lastElementChild : doc).append(list);
        lists.push({indent, tag, node: list});
      }
      paragraph = document.createElement("li");
      appendInline(paragraph, item[3], linkFor);
      lists[lists.length - 1].node.append(paragraph);
      continue;
    }
    if (line.trim() && /^\s/.test(line) && paragraph?.tagName === "LI") {
      paragraph.append("\n");
      appendInline(paragraph, line.trimStart(), linkFor);
      continue;
    }
    paragraph = paragraph?.tagName === "P" ? paragraph : null;
    if (!line.trim()) { paragraph = null; continue; }
    lists.length = 0;
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading || /^>\s?/.test(line) || /^(---+|\*\*\*)\s*$/.test(line)) {
      const element = document.createElement(heading ? "h" + Math.max(2, heading[1].length) : line.startsWith(">") ? "blockquote" : "hr");
      if (element.tagName !== "HR") appendInline(element, heading ? heading[2] : line.replace(/^>\s?/, ""), linkFor);
      doc.append(element);
      paragraph = null;
    } else {
      if (!paragraph) { paragraph = document.createElement("p"); doc.append(paragraph); }
      else paragraph.append("\n");
      appendInline(paragraph, line, linkFor);
    }
  }
  return doc;
}

// Preserve indents and the author's list markers when pasting into plain-text chat.
export function reportPlainText(markdown) {
  let fenced = false;
  const lines = [];
  for (const line of markdown.replace(/\r\n?/g, "\n").split("\n")) {
    if (/^\s*```/.test(line)) { fenced = !fenced; continue; }
    if (fenced) { lines.push(line); continue; }
    const span = document.createElement("span");
    appendInline(span, line.replace(/^#{1,6}\s+/, ""), (label, target) =>
      /^https?:\/\//i.test(target) && label !== target ? label + " (" + target + ")" : label);
    lines.push(span.textContent);
  }
  return lines.join("\n");
}

export function copyControls(ui, options) {
  const {node, button, labelled} = ui;
  const group = node("div", "report-copy");
  const actions = node("div", "work-tools");
  const feedback = node("div", "report-status");
  feedback.setAttribute("role", "status");
  const manual = node("div");
  for (const {label, ariaLabel, text, primary, success} of options) {
    const control = button(label, "button button-" + (primary ? "primary" : "secondary"), async () => {
      const value = text();
      feedback.textContent = "";
      manual.replaceChildren();
      try {
        await navigator.clipboard.writeText(value);
      } catch {
        if (!group.isConnected) return;
        const field = node("textarea");
        field.readOnly = true;
        field.rows = 4;
        field.value = value;
        manual.append(labelled("Текст для ручного копирования", field));
        field.focus();
        field.select();
        let copied = false;
        try { copied = document.execCommand("copy"); } catch { /* The selection stays available. */ }
        if (!copied) { feedback.textContent = "Скопируйте выделенный текст."; return; }
        manual.replaceChildren();
        control.focus();
      }
      feedback.textContent = success;
    });
    if (ariaLabel) control.setAttribute("aria-label", ariaLabel);
    actions.append(control);
  }
  group.append(actions, feedback, manual);
  return group;
}
