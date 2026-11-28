import {evidenceLink} from "./gantt-detail.js";

export function evidence(ui, root, source) {
  const {node} = ui;
  const box = node("details", "okr-proof");
  const label = source.path + " · строка " + source.line;
  box.append(node("summary", "text-link", source.unavailable ? "Сохранённая цитата · строка недоступна" : "Источник · строка " + source.line));
  box.append(node("blockquote", "", source.quote));
  if (source.unavailable) box.append(node("small", "record-path", label));
  const a = evidenceLink(ui, root, source, label);
  if (a) box.append(a);
  return box;
}

// Select the exact line from the same stored note that the write will verify.
export function sourcePicker(ui, form) {
  const {node, labelled, option, button, api, notice} = ui;
  const group = node("fieldset", "okr-picker okr-proof-picker");
  group.append(node("legend", "", "Источник"));
  form.append(group);
  let currentFile = null, currentLine = null;
  function selector(title, searchLabel, getPath, labelOf, onChange) {
    const section = node("div", "okr-proof-step");
    const query = node("input"); query.type = "search";
    const select = node("select"); select.required = true;
    const pages = node("div", "okr-pager"), errors = node("div");
    const placeholder = option("", title);
    select.append(placeholder);
    section.append(labelled(searchLabel, query), labelled(title, select), pages, errors);
    group.append(section);
    let rows = [], offset = 0, request = 0, timer;
    const limit = 10;
    async function load() {
      const token = ++request, path = getPath();
      rows = [];
      select.replaceChildren(placeholder);
      select.disabled = true;
      pages.replaceChildren();
      errors.replaceChildren();
      onChange(null);
      if (!path) return;
      try {
        const page = await api(path + (path.includes("?") ? "&" : "?") + new URLSearchParams({match: query.value.trim(), offset, limit}));
        if (token !== request || !section.isConnected) return;
        rows = page.items;
        rows.forEach((item, index) => select.append(option(String(index), labelOf(item))));
        select.disabled = false;
        if (page.total > limit) {
          const prev = button("Назад", "text-link", () => { offset -= limit; load(); });
          const next = button("Дальше", "text-link", () => { offset += limit; load(); });
          prev.disabled = offset === 0;
          next.disabled = !page.truncated;
          pages.append(prev,node("small", "", (offset + 1) + "–" + (offset + rows.length) + " из " + page.total),next);
        } else if (!rows.length) pages.append(node("small", "", "Совпадений нет."));
      } catch (error) {
        if (token === request) errors.replaceChildren(notice(error.message),button("Повторить поиск", "text-link", load));
      }
    }
    function reset() { offset = 0; query.value = ""; clearTimeout(timer); load(); }
    query.addEventListener("input", () => {
      ++request; offset = 0; select.disabled = true; onChange(null);
      clearTimeout(timer); timer = setTimeout(load, 160);
    });
    query.addEventListener("keydown", event => { if (event.key === "Enter") event.preventDefault(); });
    select.addEventListener("change", () => onChange(select.value === "" ? null : rows[Number(select.value)]));
    return {section, reset};
  }
  const files = selector("Запись-источник", "Найти заметку или проект", () => "plan/okr-sources", row => row.title + " · " + row.path, row => {
    currentFile = row; currentLine = null; lines.section.hidden = !row; lines.reset();
  });
  const lines = selector("Строка источника", "Найти текст в записи", () => currentFile && "plan/okr-source-lines?id=" + encodeURIComponent(currentFile.id),
    row => row.line + " · " + row.quote.slice(0, 160), row => { currentLine = row; preview.textContent = row?.quote || ""; });
  const preview = node("blockquote");
  group.append(preview);
  files.reset();
  return () => {
    if (!currentFile || !currentLine) throw new Error("Выберите запись и точную строку источника.");
    return {id: currentFile.id, path: currentFile.path, ...currentLine};
  };
}
