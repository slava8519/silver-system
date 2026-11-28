let active = null;

export function editorDialogOpen() {
  return active !== null;
}

// The router calls this on Back/Forward as well as on a hash link.
export function leaveEditor(continueNavigation) {
  if (active) active.requestClose(continueNavigation);
  else continueNavigation();
}

function element(tag, className, text) {
  const el = document.createElement(tag);
  el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function action(text, primary, run) {
  const button = element("button", "button button-" + (primary ? "primary" : "secondary"), text);
  button.type = "button";
  button.addEventListener("click", run);
  return button;
}

export function openEditorDialog({title, context, content, trigger = document.activeElement,
  initialFocus, isDirty, isBusy = () => false, onClose}) {
  if (active) throw Error("Редактор уже открыт.");
  const dialog = element("dialog", "editor-dialog");
  dialog.setAttribute("aria-labelledby", "editor-dialog-title");
  const head = element("header", "editor-dialog-head");
  const heading = element("div", "editor-heading");
  const name = element("h2", "", title);
  name.id = "editor-dialog-title";
  name.tabIndex = -1;
  heading.append(name);
  if (context) heading.append(element("p", "", context));
  head.append(heading, action("Закрыть", false, () => requestClose()));
  const scroll = element("div", "editor-dialog-scroll");
  scroll.append(content);
  dialog.append(head, scroll);
  document.body.append(dialog);

  let confirmation = null;
  const tracked = new Map();
  const fieldValue = field => ["checkbox", "radio"].includes(field.type) ? field.checked : field.value;
  const remember = field => { if (!tracked.has(field)) tracked.set(field, fieldValue(field)); };
  for (const field of content.querySelectorAll("input,select,textarea")) remember(field);
  // Some pickers load their options after the dialog opens.
  function onFocus(event) {
    if (event.target.matches("input,select,textarea")) remember(event.target);
  }
  content.addEventListener("focusin", onFocus);
  const dirty = isDirty || (() => [...tracked].some(([field, value]) => content.contains(field) && fieldValue(field) !== value));
  function beforeUnload(event) {
    if (isBusy() || dirty()) {
      event.preventDefault();
      event.returnValue = "";
    }
  }
  window.addEventListener("beforeunload", beforeUnload);

  function close() {
    if (!dialog.open) return;
    window.removeEventListener("beforeunload", beforeUnload);
    content.removeEventListener("focusin", onFocus);
    confirmation?.close();
    dialog.close();
    dialog.remove();
    active = null;
    if (trigger?.isConnected) trigger.focus({preventScroll:true});
    onClose?.();
  }
  function requestClose(after) {
    if (isBusy() || confirmation || !dialog.open) return;
    if (!dirty()) { close(); after?.(); return; }
    const previousFocus = document.activeElement;
    confirmation = element("dialog", "editor-discard");
    confirmation.setAttribute("aria-labelledby", "editor-discard-title");
    const question = element("h3", "", "Закрыть без сохранения?");
    question.id = "editor-discard-title";
    const resume = () => {
      confirmation.close();
      confirmation.remove();
      confirmation = null;
      if (previousFocus?.isConnected) previousFocus.focus({preventScroll:true});
    };
    const keep = action("Вернуться к правке", true, resume);
    const actions = element("div", "work-tools");
    actions.append(keep, action("Закрыть без сохранения", false, () => { close(); after?.(); }));
    confirmation.append(question, actions);
    dialog.append(confirmation);
    confirmation.addEventListener("cancel", event => { event.preventDefault(); resume(); });
    confirmation.showModal();
    keep.focus();
  }
  dialog.addEventListener("cancel", event => {
    if (event.target !== dialog) return;
    event.preventDefault();
    requestClose();
  });
  dialog.addEventListener("keydown", event => {
    if (event.key.toLowerCase() !== "s" || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
    event.preventDefault();
    if (confirmation || isBusy() || event.repeat || event.isComposing) return;
    const submit = content.querySelector('button[type="submit"]');
    if (submit && !submit.disabled) submit.form.requestSubmit(submit);
  });
  active = {close, requestClose};
  dialog.showModal();
  const focus = typeof initialFocus === "function" ? initialFocus() : initialFocus;
  (focus || name).focus({preventScroll:true});
  return active;
}
