import {workspaceLink} from "./obsidian-workspace.js";

const endpoint = "demo-cleanup";

function openWorkspace(result) {
  // A hash-only navigation would leave the old demo document and its scripts running.
  const receipt = {archive:result.archive, removeCount:result.removeCount, keepCount:result.keepCount};
  history.replaceState({...history.state, cleanupReceipt:receipt}, "", workspaceLink("/#overview", location.href));
  location.reload();
}

export function cleanupNotice(node) {
  const receipt = history.state?.cleanupReceipt;
  if (!receipt) return null;
  const state = {...history.state};
  delete state.cleanupReceipt;
  history.replaceState(state, "", location.href);
  const notice = node("div", "cleanup-receipt");
  notice.setAttribute("role", "status");
  notice.append(node("strong", "", "Открыто рабочее хранилище."));
  if (Number.isSafeInteger(receipt.removeCount) && Number.isSafeInteger(receipt.keepCount)) {
    notice.append(node("p", "", "Учебных файлов в архиве — " + receipt.removeCount
      + ". Осталось в хранилище — " + receipt.keepCount + "."));
  }
  if (receipt.archive && receipt.removeCount !== 0) {
    const path = node("p", "", "ZIP-архив в хранилище — ");
    path.append(node("code", "", receipt.archive));
    notice.append(path);
  }
  return notice;
}

export async function mountCleanup(host, api, node, {guide=false}={}) {
  host.hidden = true;
  try {
    const state = await api(endpoint);
    if (!host.isConnected) return;
    if (guide && state.cleaned) { openWorkspace(state); return; }
    if (!state.available || state.cleaned) return;
  } catch (error) {
    // A normal vault has no cleanup control. The guide can retry a failed preview.
    if (!guide || error.status === 404 || !host.isConnected) return;
  }
  function button(label, style, action) {
    const el = node("button", "button " + style, label);
    el.type = "button";
    el.addEventListener("click", action);
    return el;
  }
  const trigger = button("Убрать демоданные", "button-secondary", open);
  trigger.setAttribute("aria-haspopup", "dialog");
  host.append(trigger);
  host.hidden = false;

  function open() {
    const dialog = node("dialog", "cleanup-dialog");
    const title = node("h2", "", "Убрать демоданные?");
    title.id = "cleanup-title";
    dialog.setAttribute("aria-labelledby", title.id);
    const details = node("div", "cleanup-details");
    details.setAttribute("aria-live", "polite");
    const status = node("p", "cleanup-status");
    status.setAttribute("role", "status");
    const tools = node("div", "cleanup-actions");
    const cancel = button("Отмена", "button-secondary", () => dialog.close());
    const retry = button("Обновить предпросмотр", "button-secondary", preview);
    const apply = button("Перенести в архив и продолжить", "button-primary", submit);
    cancel.autofocus = true;
    tools.append(cancel, retry, apply);
    dialog.append(title, details, status, tools);
    host.append(dialog);
    let revision, saving = false, unavailable = false;
    dialog.addEventListener("cancel", e => { if (saving) e.preventDefault(); });
    dialog.addEventListener("close", () => {
      dialog.remove();
      if (unavailable) host.hidden = true;
    }, {once:true});
    dialog.showModal();
    preview();

    function failure(error) {
      revision = null;
      apply.hidden = true;
      retry.hidden = false;
      status.classList.add("field-error");
      details.replaceChildren();
      status.textContent = error.status === 409
        ? error.message + " Обновите предпросмотр перед подтверждением."
        : error.message;
    }
    async function preview() {
      revision = null;
      details.replaceChildren();
      retry.hidden = apply.hidden = true;
      status.classList.remove("field-error");
      status.textContent = "Проверяем учебные файлы…";
      try {
        const state = await api(endpoint);
        if (!dialog.open || !dialog.isConnected) return;
        if (state.cleaned) { openWorkspace(state); return; }
        if (!state.available) {
          unavailable = true;
          cancel.textContent = "Закрыть";
          status.textContent = "В этом хранилище нет учебных файлов для архива.";
          return;
        }
        if (typeof state.revision !== "string" || !state.revision ||
            ![state.removeCount, state.keepCount].every(n => Number.isSafeInteger(n) && n >= 0)) {
          throw new Error("Не удалось прочитать результат проверки учебных файлов.");
        }
        const counts = node("dl", "cleanup-counts");
        for (const [label, value] of [["Учебных файлов в ZIP-архив",state.removeCount],["Учебных файлов останется",state.keepCount]]) {
          const row = node("div");
          row.append(node("dt", "", label), node("dd", "", value));
          counts.append(row);
        }
        details.append(node("p", "", "После подтверждения откроется рабочее хранилище. Ваши записи сохранятся вместе с учебными файлами, которые вы изменили или связали со своими материалами."), counts);
        revision = state.revision;
        status.textContent = "";
        apply.textContent = state.removeCount ? "Перенести в архив и продолжить" : "Перейти к работе";
        apply.hidden = false;
      } catch (error) {
        if (dialog.open && dialog.isConnected) failure(error);
      }
    }
    async function submit() {
      if (saving || !revision) return;
      saving = true;
      cancel.disabled = apply.disabled = true;
      status.textContent = "Готовим рабочее хранилище…";
      try {
        const result = await api(endpoint, {revision});
        if (!result.cleaned) throw new Error("Проверьте состояние хранилища в новом предпросмотре.");
        openWorkspace(result);
      } catch (error) {
        saving = false;
        cancel.disabled = apply.disabled = false;
        failure(error);
        retry.focus();
      }
    }
  }
}
