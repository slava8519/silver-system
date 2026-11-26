import assert from "node:assert/strict";
import test from "node:test";
import {renderOverviewCommands} from "../second_brain/web/overview-commands.js";

function panel(vault, clipboard, execCommand) {
  const doc = {defaultView: {navigator: {clipboard}}, execCommand};
  function node(tag, className, textContent = "") {
    return {
      tag, className, textContent, ownerDocument: doc, children: [], attrs: {}, dataset: {},
      setAttribute(key, value) { this.attrs[key] = value; },
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = children; },
      focus() { doc.focused = this; },
      select() { doc.selected = this.value; }
    };
  }
  function button(label, className, action) {
    return {...node("button", className, label), click: action};
  }
  const section = renderOverviewCommands({node, button}, vault);
  const [, , list, manual, status] = section.children;
  return {doc, rows: list.children, manual, status, section};
}

test("all Codex requests copy their visible action and the exact selected vault", async () => {
  const vault = "/Volumes/Мои записи/Кедр & Маяк";
  const copied = [];
  const {rows, manual, status, section} = panel(vault, {writeText: async text => copied.push(text)});
  assert.match(section.children[0].textContent, /Управление вторым мозгом/);
  assert.equal(copied.length, 0);
  assert.equal(rows.length, 6);
  assert.equal(rows[0].children[0].textContent, "Обнови все сводки и истории");
  assert.ok(rows.some(row => row.children[0].textContent === "Покажи состояние направлений и последние события"));
  assert.ok(rows.some(row => row.children[0].textContent === "Пересобери поисковый индекс"));
  for (const [index, row] of rows.entries()) {
    const [label, control] = row.children;
    assert.equal(control.tag, "button");
    assert.ok(control.attrs["aria-label"].includes(label.textContent));
    await control.click();
    assert.equal(copied[index], label.textContent + "\n\nПуть к хранилищу\n" + vault);
    assert.equal(control.textContent, "Скопировано");
    assert.equal(status.dataset.state, "copied");
    assert.equal(manual.hidden, true);
    if (index > 0) assert.equal(rows[index - 1].children[1].textContent, "Копировать");
  }
});

test("denied clipboard falls back to copying the selected text", async () => {
  const vault = "D:\\Рабочая папка\\Мои записи";
  let value;
  const ui = panel(vault, {writeText() { throw Error("denied"); }}, function (command) {
    assert.equal(command, "copy");
    value = this.selected;
    return true;
  });
  const [label, button] = ui.rows[1].children;
  await button.click();
  assert.equal(value, label.textContent + "\n\nПуть к хранилищу\n" + vault);
  assert.equal(ui.doc.focused, button);
  assert.equal(ui.manual.hidden, true);
  assert.equal(ui.status.attrs.role, "status");
});

test("a restricted browser offers the exact text for manual copying", async () => {
  const ui = panel("/vault", undefined);
  const [label, button] = ui.rows[0].children;
  await button.click();
  assert.equal(ui.manual.hidden, false);
  const [field] = ui.manual.children;
  assert.equal(field.value, label.textContent + "\n\nПуть к хранилищу\n/vault");
  assert.equal(ui.doc.selected, field.value);
  assert.equal(ui.doc.focused, field);
  assert.equal(field.readOnly, true);
  assert.equal(ui.status.dataset.state, "manual");
  assert.equal(button.textContent, "Копировать");
});

test("a missing vault path does not offer an unscoped command", () => {
  const ui = panel(undefined);
  assert.ok(ui.rows.every(row => row.children[1].disabled));
  assert.match(ui.status.textContent, /путь к хранилищу/);
});

test("a late permission denial cannot copy a selection from a different page", async () => {
  let deny;
  let attempts = 0;
  const ui = panel("/vault", {writeText: () => new Promise((_, reject) => { deny = reject; })},
    () => { attempts += 1; return true; });
  const result = ui.rows[0].children[1].click();
  ui.section.isConnected = false;
  deny(Error("permission denied after navigation"));
  await result;
  assert.equal(attempts, 0);
  assert.equal(ui.manual.hidden, true);
});
