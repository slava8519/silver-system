// Vault Markdown is rendered with text nodes and a fixed set of elements.
function appendInline(parent, value, linkFor, wikiLink) {
  const pattern = /\\([\\\[\]*_`|])|`+([^`]+?)`+|\[\[([^\]\n]+?)\]\]|\[((?:\\.|[^\]\\])+)\]\((<[^>]+>|(?:[^()\s]|\([^()\s]*\))+)\)|\*\*(.+?)\*\*|__(.+?)__|\*([^*\s][^*]*?)\*|(?<![\p{L}\d_])_([^_\s][^_]*?)_(?![\p{L}\d_])|~~(.+?)~~/gu;
  let end = 0;
  for (const match of value.matchAll(pattern)) {
    parent.append(decodeEntities(value.slice(end, match.index)));
    const [, escaped, code, wiki, label, target, bold, boldAlt, italic, italicAlt, strike] = match;
    if (escaped) parent.append(escaped);
    else if (wiki) {
      const [dest, ...alias] = wiki.replace(/\\\|/g, "|").split("|");
      const [path, ...heading] = dest.split("#");
      const filename = path.split("/").pop().replace(/\.md$/i, "");
      const name = alias.join("|").trim() || [filename, heading.join("#")].filter(Boolean).join(" › ") || dest;
      parent.append(wikiLink ? wikiLink(name, dest.trim()) : match[0]);
    } else if (label) {
      const name = decodeEntities(label.replace(/\\([\\\[\]*_`])/g, "$1"));
      parent.append(linkFor?.(name, target.replace(/^<|>$/g, "")) || name);
    } else {
      const element = document.createElement(code ? "code" : strike ? "del" : italic || italicAlt ? "em" : "strong");
      if (code) element.textContent = code;
      else appendInline(element, bold || boldAlt || italic || italicAlt || strike, linkFor, wikiLink);
      parent.append(element);
    }
    end = match.index + match[0].length;
  }
  const tail = value.slice(end);
  const block = wikiLink && /(?:^|\s)\^([A-Za-z0-9-]+)\s*$/.exec(tail);
  if (block) parent.id = "md-block-" + block[1];
  parent.append(decodeEntities(block ? tail.slice(0, block.index) : tail));
}

function decodeEntities(value) {
  return value.replace(/&(amp|lt|gt|quot);/g, (_, name) => ({amp: "&", lt: "<", gt: ">", quot: '"'}[name]));
}

function tableCells(line) {
  const cells = [""];
  for (const [token] of line.trim().replace(/^\||(?<!\\)\|$/g, "").matchAll(/\\.|\[\[[^\]]*\]\]|`[^`]*`|[^|`\\[]+|./g)) {
    if (token === "|") cells.push("");
    else cells[cells.length - 1] += token;
  }
  return cells.map(text => text.trim());
}

function markdownTable(header, rows, inline) {
  const wrap = document.createElement("div");
  wrap.className = "markdown-table";
  const table = document.createElement("table");
  function group(tag, cellTag, lines) {
    const section = document.createElement(tag);
    for (const values of lines) {
      const row = document.createElement("tr");
      header.forEach((_, index) => {
        const cell = document.createElement(cellTag);
        if (cellTag === "th") cell.scope = "col";
        inline(cell, values[index] || "");
        row.append(cell);
      });
      section.append(row);
    }
    table.append(section);
  }
  group("thead", "th", [header]);
  group("tbody", "td", rows);
  wrap.append(table);
  return wrap;
}

// Keep comment and fence handling identical in the reader and in plain-text copies.
function markdownLines(markdown) {
  const lines = [];
  let fence = "", comment = false;
  for (let line of markdown.replace(/\r\n?/g, "\n").split("\n")) {
    const delimiter = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (fence) {
      const closing = delimiter?.[1].startsWith(fence) && !delimiter[2].trim();
      lines.push({kind:closing ? "close" : "code", text:line});
      if (closing) fence = "";
      continue;
    }
    if (delimiter && !comment) {
      fence = delimiter[1];
      lines.push({kind:"open", text:line});
      continue;
    }
    let visible = "";
    if (!comment && !line.includes("<!--")) {
      lines.push({kind:"text", text:line});
      continue;
    }
    while (line) {
      if (comment) {
        const end = line.indexOf("-->");
        if (end < 0) break;
        line = line.slice(end + 3);
        comment = false;
        continue;
      }
      // A comment delimiter between matching inline backticks is literal code.
      const token = /<!--|`+/.exec(line);
      if (!token) { visible += line; break; }
      visible += line.slice(0, token.index);
      line = line.slice(token.index + token[0].length);
      if (token[0] === "<!--") {
        comment = true;
      } else {
        const end = [...line.matchAll(/`+/g)].find(run => run[0] === token[0]);
        const count = end ? end.index + end[0].length : 0;
        visible += token[0] + line.slice(0, count);
        line = line.slice(count);
      }
    }
    if (visible.trim()) lines.push({kind:"text", text:visible});
  }
  return lines;
}

export function reportDocument(markdown, linkFor, {wikiLink, omitTitle} = {}) {
  const doc = document.createElement("div");
  doc.className = "report-prose";
  const inline = (el, value) => appendInline(el, value, linkFor, wikiLink);
  const lists = [];
  let paragraph = null, codeBlock = null;
  const lines = markdownLines(markdown);
  for (let index = 0; index < lines.length; index++) {
    const {kind, text:line} = lines[index];
    if (kind === "close") { codeBlock = null; continue; }
    if (kind === "code") { codeBlock.append(line + "\n"); continue; }
    if (kind === "open") {
      codeBlock = document.createElement("pre");
      doc.append(codeBlock);
      paragraph = null;
      lists.length = 0;
      continue;
    }
    const block = wikiLink && /^\^([A-Za-z0-9-]+)\s*$/.exec(line);
    if (block && doc.lastElementChild) {
      doc.lastElementChild.id = "md-block-" + block[1];
      continue;
    }
    if (line.includes("|") && /^\s*\|?\s*:?-+:?\s*\|/.test(lines[index + 1]?.text || "")) {
      const header = tableCells(line), separators = tableCells(lines[index + 1].text);
      if (header.length === separators.length && separators.every(cell => /^:?-+:?$/.test(cell))) {
        const rows = [];
        index++;
        while (lines[index + 1]?.kind === "text" && lines[index + 1].text.includes("|")) rows.push(tableCells(lines[++index].text));
        doc.append(markdownTable(header, rows, inline));
        lists.length = 0;
        paragraph = null;
        continue;
      }
    }
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
      const task = /^\[([ xX])\]\s+(.*)/.exec(item[3]);
      if (task) {
        const check = document.createElement("input");
        check.type = "checkbox";
        check.checked = task[1] !== " ";
        check.disabled = true;
        const label = document.createElement("label");
        label.append(check, " ");
        inline(label, task[2]);
        paragraph.className = "markdown-task";
        paragraph.append(label);
      } else inline(paragraph, item[3]);
      lists[lists.length - 1].node.append(paragraph);
      continue;
    }
    if (line.trim() && /^\s/.test(line) && paragraph?.tagName === "LI") {
      paragraph.append("\n");
      inline(paragraph, line.trimStart());
      continue;
    }
    paragraph = paragraph?.tagName === "P" ? paragraph : null;
    if (!line.trim()) { paragraph = null; continue; }
    lists.length = 0;
    const heading = /^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$/.exec(line);
    if (heading || /^>\s?/.test(line) || /^(---+|\*\*\*)\s*$/.test(line)) {
      const element = document.createElement(heading ? "h" + Math.max(2, heading[1].length) : line.startsWith(">") ? "blockquote" : "hr");
      if (element.tagName !== "HR") inline(element, heading ? heading[2] : line.replace(/^>\s?/, ""));
      const sameTitle = heading?.[1] === "#" && !doc.children.length && element.textContent.trim() === omitTitle;
      if (!sameTitle) doc.append(element);
      paragraph = null;
    } else {
      if (!paragraph) { paragraph = document.createElement("p"); doc.append(paragraph); }
      else if (/(?: {2,}|\\)$/.test(lines[index - 1]?.text)) paragraph.append(document.createElement("br"));
      else paragraph.append("\n");
      inline(paragraph, line.replace(/\\$/, ""));
    }
  }
  return doc;
}

// Preserve indents and the author's list markers when pasting into plain-text chat.
export function reportPlainText(markdown) {
  const lines = [];
  for (const {kind, text:line} of markdownLines(markdown)) {
    if (kind === "open" || kind === "close") continue;
    if (kind === "code") { lines.push(line); continue; }
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
