// A paste on the open Inbox can start at the page, a field, or the file picker.
document.addEventListener("paste", event => {
  const composer = document.querySelector(".inbox-capture");
  if (!composer || !event.clipboardData) return;
  const data = event.clipboardData;
  const plain = data.getData("text/plain");
  const editable = event.target.closest?.("input:not([type=file]),textarea,[contenteditable=true]");
  let files = Array.from(data.files);
  if (!files.length) files = Array.from(data.items).filter(item => item.kind === "file").map(item => item.getAsFile()).filter(Boolean);
  if (files.length) composer.dispatchEvent(new CustomEvent("inbox-files", {detail: files}));
  if (editable && plain) return; // Keep native insertion, selection replacement and undo.
  if (!files.length && !plain) return;
  const draft = composer.querySelector("textarea");
  if (plain && draft.readOnly) return;
  event.preventDefault();
  if (plain) {
    const end = draft.value.length;
    draft.setRangeText((end ? "\n" : "") + plain, end, end, "end");
    draft.focus();
  }
});

export function capturePanel({node, button, section, labelled, notice, api}, onSaved) {
  const grid = node("div", "inbox-capture");
  const textPanel = section("Записать текст");
  const form = node("form", "capture-form");
  const title = node("input");
  title.name = "title";
  title.placeholder = "Например, встреча с командой";
  const text = node("textarea");
  text.name = "text";
  text.rows = 4;
  text.required = true;
  text.placeholder = "Вставьте заметку или фрагмент переписки…";
  const feedback = node("div", "capture-feedback");
  feedback.setAttribute("aria-live", "polite");
  const save = node("button", "button button-primary", "Сохранить текст");
  save.type = "submit";
  form.append(labelled("Название (необязательно)", title), labelled("Текст", text), save, feedback);
  textPanel.append(form);

  function receipt(path) {
    const line = node("p", "capture-receipt");
    line.append("Сохранено: ", node("code", "", path));
    return line;
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (save.disabled) return;
    feedback.replaceChildren();
    if (!text.value.trim() || new Blob([text.value]).size > 1024 * 1024) {
      feedback.append(notice(text.value.trim() ? "Текст больше 1 МиБ. Выберите его как файл." : "Введите текст для сохранения."));
      text.focus();
      return;
    }
    save.disabled = text.readOnly = title.readOnly = true;
    save.textContent = "Сохраняем…";
    try {
      const saved = await api("inbox/text", {text: text.value, ...(title.value.trim() ? {title: title.value.trim()} : {})});
      form.reset();
      feedback.append(receipt(saved.path));
      if (grid.isConnected) await onSaved();
    } catch (error) {
      feedback.append(notice(error.message));
    } finally {
      save.disabled = text.readOnly = title.readOnly = false;
      save.textContent = "Сохранить текст";
    }
  });

  const filesPanel = section("Файлы и скриншоты", "work-panel capture-files");
  const picker = node("input");
  picker.type = "file";
  picker.multiple = true;
  picker.hidden = true;
  const zone = button("", "capture-drop", () => picker.click());
  const icon = node("span", "capture-icon", "↑");
  icon.setAttribute("aria-hidden", "true");
  zone.append(icon, node("strong", "", "Выбрать файлы"), node("span", "capture-drop-hint", "или перетащите их сюда"));
  const hint = node("p", "capture-help", "Скриншот из буфера можно вставить прямо на этой вкладке. До 32 МиБ на файл.");
  const uploads = node("ul", "capture-uploads");
  uploads.setAttribute("aria-live", "polite");
  filesPanel.append(zone, picker, hint, uploads);

  async function upload(file, row) {
    row.replaceChildren(node("span", "capture-help", "Сохраняем " + file.name + "…"));
    if (file.size > 32 * 1024 * 1024) {
      row.replaceChildren(notice(file.name + " — файл больше 32 МиБ."));
      return;
    }
    try {
      const result = await api("inbox/file?" + new URLSearchParams({name: file.name}), file);
      row.replaceChildren(receipt(result.path));
    } catch (error) {
      row.replaceChildren(notice(file.name + " — " + error.message),
        button("Повторить загрузку", "intake-action", async () => {
          if (await upload(file, row) && grid.isConnected) await onSaved();
        }));
      return;
    }
    return true;
  }

  async function addFiles(files) {
    let saved = false;
    for (const file of files) {
      const row = node("li");
      uploads.append(row);
      if (await upload(file, row)) saved = true;
    }
    if (saved && grid.isConnected) await onSaved();
  }

  picker.addEventListener("change", () => {
    const files = Array.from(picker.files);
    picker.value = "";
    addFiles(files);
  });
  grid.addEventListener("inbox-files", event => addFiles(event.detail));
  grid.addEventListener("dragover", event => {
    if (!event.dataTransfer?.types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    zone.classList.add("drag-over");
  });
  grid.addEventListener("dragleave", event => {
    if (!grid.contains(event.relatedTarget)) zone.classList.remove("drag-over");
  });
  grid.addEventListener("drop", event => {
    zone.classList.remove("drag-over");
    const files = Array.from(event.dataTransfer?.files || []);
    if (!files.length) return;
    event.preventDefault();
    addFiles(files);
  });
  grid.append(textPanel, filesPanel);
  return grid;
}
