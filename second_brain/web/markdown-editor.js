import {reportDocument} from "./report-document.js";

let sequence = 0;

// Keep the original textarea, its selection, undo stack and form bindings in both modes.
export function markdownEditor(ui, field, {label, render = reportDocument}) {
  const {node, button} = ui;
  const editor = node("div", "markdown-editor");
  field.id ||= "markdown-source-" + ++sequence;
  field.classList.add("markdown-source");
  const caption = node("label", "markdown-label", label);
  caption.htmlFor = field.id;
  const bar = node("div", "markdown-bar");
  const modes = node("div", "markdown-modes");
  modes.setAttribute("role", "group");
  modes.setAttribute("aria-label", "Режим редактирования");
  const source = button("Markdown", "markdown-mode", () => show(false));
  const preview = button("Предпросмотр", "markdown-mode", () => show(true));
  modes.append(source, preview);
  const output = node("div", "markdown-preview");
  output.tabIndex = 0;
  output.setAttribute("role", "region");
  output.setAttribute("aria-label", "Предпросмотр текста");
  const formatting = node("div", "markdown-format");
  formatting.setAttribute("role", "group");
  formatting.setAttribute("aria-label", "Форматирование Markdown");
  function wrap(before, after, placeholder, selection) {
    const start = field.selectionStart, end = field.selectionEnd;
    const value = field.value.slice(start, end) || placeholder;
    field.focus();
    // insertText preserves native Undo on the browsers used by the workspace and Obsidian.
    if (!document.execCommand?.("insertText", false, before + value + after)) {
      field.setRangeText(before + value + after, start, end, "end");
      field.dispatchEvent(new Event("input", {bubbles:true}));
    }
    if (selection === "url") {
      const urlStart = start + before.length + value.length + 2;
      field.setSelectionRange(urlStart, urlStart + "https://".length);
    } else field.setSelectionRange(start + before.length, start + before.length + value.length);
  }
  for (const [symbol, name, before, after, placeholder, selection] of [
    ["B", "Полужирный", "**", "**", "текст"],
    ["I", "Курсив", "*", "*", "текст"],
    ["↗", "Вставить ссылку", "[", "](https://)", "текст ссылки", "url"]
  ]) {
    const control = button(symbol, "markdown-format-button", () => wrap(before, after, placeholder, selection));
    control.setAttribute("aria-label", name);
    control.title = name;
    formatting.append(control);
  }
  function drawPreview() {
    if (!output.hidden) output.replaceChildren(field.value.trim() ? render(field.value) : node("p", "muted", "Текст пока пуст."));
  }
  function show(read) {
    field.hidden = read;
    formatting.hidden = read;
    output.hidden = !read;
    source.setAttribute("aria-pressed", String(!read));
    preview.setAttribute("aria-pressed", String(read));
    drawPreview();
  }
  // A required field can be empty when the form is submitted from Preview.
  field.addEventListener("invalid", () => show(false));
  field.addEventListener("input", drawPreview);
  bar.append(modes, formatting);
  editor.append(caption, bar, field, output);
  show(false);
  return editor;
}
