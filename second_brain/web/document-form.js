import {openEditorDialog} from "./editor-dialog.js";

// A card and a report have different write operations, but share a draft, revision
// check, conflict review and save lifecycle. Values are those of the displayed controls.
export function documentForm(ui, {form, fields, original, read, valuesOf, save, onSaved, dialog, cancel, saveLabel = "Сохранить"}) {
  const {node, button, notice} = ui;
  const entries = Object.entries(fields);
  const values = () => Object.fromEntries(entries.map(([key, field]) => [key, field.value]));
  let base = values(), revision = original?.revision, busy = false, reviewing = false;
  // An older template version can be chosen as the starting draft. Its intentional
  // differences from the current file must survive a later conflict.
  let mergeBase = original ? valuesOf(original) : base;
  const dirty = () => entries.some(([key, field]) => field.value !== base[key]);
  const feedback = node("div", "editor-feedback");
  const submit = node("button", "button button-primary", saveLabel);
  submit.type = "submit";
  const actions = node("div", "work-tools editor-actions");
  actions.append(submit);
  if (cancel) actions.append(cancel);
  form.append(feedback, actions);
  const session = dialog && openEditorDialog({...dialog, content:form, isDirty:dirty, isBusy:() => busy});

  async function compare() {
    const latest = await read();
    if (!form.isConnected) return;
    const current = valuesOf(latest);
    const differences = node("details", "editor-differences");
    differences.open = true;
    differences.append(node("summary", "", "Текущая версия файла"));
    for (const [key, field] of entries) {
      if (current[key] === mergeBase[key]) continue;
      const label = field.labels?.[0]?.textContent || field.name;
      differences.append(node("strong", "", label), node("pre", "record-body", current[key] || "Поле пусто"));
    }
    if (differences.childElementCount === 1) differences.append(node("p", "muted", "Изменились другие данные файла."));
    reviewing = true;
    feedback.append(differences, button("Сверил с файлом — продолжить правку", "button button-secondary", () => {
      for (const [key, field] of entries) {
        if (field.value === mergeBase[key] && field.value !== current[key]) {
          field.value = current[key];
          field.dispatchEvent(new Event("input", {bubbles:true}));
        }
      }
      base = mergeBase = current;
      revision = latest.revision;
      reviewing = false;
      submit.disabled = false;
      feedback.replaceChildren(node("p", "muted", "Проверьте текст и сохраните правку."));
    }));
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || reviewing) return;
    busy = true;
    submit.disabled = true;
    // Freeze both the fields and the formatting toolbar while this draft is being saved.
    form.inert = true;
    form.setAttribute("aria-busy", "true");
    submit.textContent = "Сохранение…";
    feedback.replaceChildren();
    try {
      const result = await save(values(), base, revision);
      if (!form.isConnected) return;
      session?.close();
      onSaved(result);
    } catch (error) {
      if (!form.isConnected) return;
      feedback.replaceChildren(notice(error.message));
      if (read && error.status === 409) {
        try { await compare(); }
        catch (readError) { feedback.append(notice(readError.message)); }
      }
      feedback.scrollIntoView({block:"nearest"});
    } finally {
      busy = false;
      form.inert = false;
      form.removeAttribute("aria-busy");
      submit.textContent = saveLabel;
      submit.disabled = reviewing;
      if (form.isConnected && !form.contains(document.activeElement)) {
        (feedback.querySelector("button") || submit).focus({preventScroll:true});
      }
    }
  });
  return session;
}
