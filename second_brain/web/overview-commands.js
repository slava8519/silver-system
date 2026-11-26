const requests = [
  "Обнови все сводки и истории",
  "Разбери всё новое в Inbox",
  "Покажи вопросы ко мне из Inbox",
  "Покажи состояние направлений и последние события",
  "Покажи ручные правки Ганта",
  "Пересобери поисковый индекс"
];

export function renderOverviewCommands({node, button}, vaultPath) {
  const panel = node("section", "work-panel overview-commands");
  const heading = node("h2", "work-heading", "Управление вторым мозгом");
  heading.id = "overview-commands-heading";
  panel.setAttribute("aria-labelledby", heading.id);
  const intro = node("p", "overview-command-intro", "Вставьте запрос в чат Codex. В него войдёт путь к этому хранилищу.");
  const list = node("ul", "overview-command-list");
  const manual = node("div", "overview-command-manual");
  manual.hidden = true;
  const status = node("p", "overview-command-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-atomic", "true");
  panel.append(heading, intro, list, manual, status);

  let copying = false;
  let lastCopied;
  async function copy(request, control) {
    if (!hasPath || copying) return;
    copying = true;
    const text = request + "\n\nПуть к хранилищу\n" + vaultPath;
    const doc = panel.ownerDocument;
    status.textContent = "";
    status.dataset.state = "";
    manual.hidden = true;
    manual.replaceChildren();
    if (lastCopied) lastCopied.textContent = "Копировать";
    lastCopied = null;
    control.textContent = "Копирование…";
    control.setAttribute("aria-busy", "true");
    try {
      let copied = false;
      try {
        const clipboard = doc.defaultView?.navigator?.clipboard;
        if (clipboard?.writeText) {
          await clipboard.writeText(text);
          copied = true;
        }
      } catch {
        // Embedded views and browser permissions can restrict clipboard access.
      }
      if (!copied) {
        if (panel.isConnected === false) return;
        const field = node("textarea", "overview-command-path");
        field.readOnly = true;
        field.value = text;
        field.rows = 4;
        field.setAttribute("aria-label", "Запрос для ручного копирования");
        manual.append(field);
        manual.hidden = false;
        field.focus();
        field.select();
        try {
          copied = doc.execCommand?.("copy") === true;
        } catch {
          // The selected text remains available through the system copy action.
        }
        if (!copied) {
          status.dataset.state = "manual";
          status.textContent = "Скопируйте выделенный запрос вручную.";
          return;
        }
        manual.hidden = true;
        manual.replaceChildren();
        control.focus();
      }
      lastCopied = control;
      control.textContent = "Скопировано";
      status.dataset.state = "copied";
      status.textContent = "Запрос скопирован. Вставьте его в Codex.";
    } finally {
      control.setAttribute("aria-busy", "false");
      if (!lastCopied) control.textContent = "Копировать";
      copying = false;
    }
  }

  const hasPath = typeof vaultPath === "string" && vaultPath.trim().length > 0;
  for (const request of requests) {
    const row = node("li", "overview-command-row");
    const control = button("Копировать", "overview-command-copy", () => copy(request, control));
    control.setAttribute("aria-label", "Скопировать запрос «" + request + "»");
    control.disabled = !hasPath;
    row.append(node("span", "overview-command-text", request), control);
    list.append(row);
  }
  if (!hasPath) status.textContent = "Обновите страницу, чтобы получить путь к хранилищу.";
  return panel;
}
