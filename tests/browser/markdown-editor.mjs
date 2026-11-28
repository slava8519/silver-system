import assert from "node:assert/strict";
import {readFile, writeFile} from "node:fs/promises";
import {setTimeout as delay} from "node:timers/promises";
import {connectPage} from "./chromium.mjs";

const [portFile, base, fixture] = process.argv.slice(2);
const {card, issue, template, plan, task, issuePath} = JSON.parse(fixture);
const {command, diagnostics, close} = await connectPage(portFile);
const dialog = 'document.querySelector("dialog.editor-dialog[open]")';
const textarea = `${dialog}?.querySelector('textarea[name="body"], textarea[name="text"], .markdown-editor textarea')`;
const quote = JSON.stringify;

async function evaluate(expression) {
  const {result, exceptionDetails} = await command("Runtime.evaluate", {expression, returnByValue:true});
  if (exceptionDetails) throw Error(exceptionDetails.exception?.description || exceptionDetails.text);
  return result.value;
}

async function until(expression) {
  const deadline = Date.now() + 7000;
  do {
    const value = await evaluate(expression);
    if (value) return value;
    await delay(40);
  } while (Date.now() < deadline);
  const visible = await evaluate("document.body?.innerText.slice(-2500)");
  throw Error(`Timed out waiting for ${expression}\n${visible}\n${diagnostics.join("\n")}`);
}

async function click(label, scope = "document") {
  const button = `[...(${scope}?.querySelectorAll('button,a,summary') || [])]
    .find(el => el.textContent.trim() === ${quote(label)} && el.checkVisibility() && !el.disabled)`;
  const {x, y} = await until(`(() => {
    const el = ${button}; if (!el) return;
    el.scrollIntoView({block:"center", inline:"nearest"});
    const r = el.getBoundingClientRect();
    return {x:r.left+r.width/2, y:r.top+r.height/2};
  })()`);
  for (const type of ["mousePressed", "mouseReleased"]) {
    await command("Input.dispatchMouseEvent", {type, x, y, button:"left", clickCount:1});
  }
}

async function key(name, code, modifiers = 0) {
  const keyCode = {Escape:27, Tab:9, KeyS:83}[code];
  for (const type of ["rawKeyDown", "keyUp"]) {
    await command("Input.dispatchKeyEvent", {type, key:name, code, modifiers, windowsVirtualKeyCode:keyCode});
  }
}

async function writeText(value) {
  await until(`${textarea}?.checkVisibility()`);
  await evaluate(`${textarea}.focus(); ${textarea}.select()`);
  await command("Input.insertText", {text:value});
  assert.equal(await evaluate(`${textarea}.value`), value);
}

async function viewport(width, height) {
  await command("Emulation.setDeviceMetricsOverride", {width, height, deviceScaleFactor:1, mobile:width < 600});
}

async function checkDialog() {
  await until(`${dialog}?.matches(":modal")`);
  const geometry = await evaluate(`(() => {
    const r = ${dialog}.getBoundingClientRect();
    return [r.left, r.top, innerWidth-r.right, innerHeight-r.bottom];
  })()`);
  assert.ok(geometry.every(edge => Math.abs(edge) <= 2), `Expected full workspace, gaps: ${geometry}`);
  assert.ok(await evaluate(`${dialog}.contains(document.activeElement)`));
}

async function visit(hash) {
  await command("Page.navigate", {url:base + hash});
  await until(`location.hash === ${quote(hash)} && document.querySelector('main#content:not([aria-busy=true])')`);
}

try {
  await viewport(1366, 650);
  await visit("#records");
  await visit("#record/" + card);
  await click("Редактировать");
  await checkDialog();
  await key("Tab", "Tab");
  assert.ok(await evaluate(`${dialog}.contains(document.activeElement)`));
  await key("Escape", "Escape");
  await until(`!${dialog}`);
  assert.equal(await evaluate("document.activeElement.textContent.trim()"), "Редактировать");

  await click("Редактировать");
  const cardText = "## Стенд\n\n**Диски заменены.**\n\n| Узел | Статус |\n| --- | --- |\n| Система | Готова |";
  await writeText(cardText);
  await click("Предпросмотр", dialog);
  await until(`${dialog}?.querySelector("table")?.innerText.includes("Готова")`);
  await click("Markdown", dialog);
  assert.equal(await evaluate(`${textarea}.value`), cardText);
  await key("Escape", "Escape");
  await click("Вернуться к правке", dialog);
  assert.equal(await evaluate(`${textarea}.value`), cardText);
  await evaluate("history.back()");
  await click("Вернуться к правке", dialog);
  assert.equal(await evaluate(`${textarea}.value`), cardText);
  await key("s", "KeyS", 2);
  await until(`!${dialog} && document.querySelector(".record-prose strong")?.textContent === "Диски заменены."`);

  await evaluate("history.back()");
  await until('location.hash === "#records"');

  await viewport(390, 844);
  await visit("#reports/issue?id=" + issue);
  await click("Редактировать текст");
  await checkDialog();
  const issueText = "## Маршрут\n\nВокзал подтверждён.";
  await writeText(issueText);
  await click("Предпросмотр", dialog);
  await until(`${dialog}.innerText.includes("Вокзал подтверждён.")`);
  await click("Сохранить", dialog);
  await until(`!${dialog} && document.querySelector(".report-paper .report-prose")?.innerText.includes("Вокзал подтверждён.")`);

  await click("Редактировать текст");
  await writeText("Устаревший черновик.");
  // Reproduce a concurrent Obsidian edit of the same isolated release.
  const oldFile = await readFile(issuePath, "utf8");
  assert.ok(oldFile.includes(issueText));
  await writeFile(issuePath, oldFile.replace("Вокзал подтверждён.", "Внешняя правка."));
  await key("s", "KeyS", 4);
  await until(`/изменил|изменён|верси[яию]/i.test(${dialog}?.innerText || "")`);
  assert.equal(await evaluate(`${textarea}.value`), "Устаревший черновик.");
  await click("Закрыть", dialog);
  await click("Закрыть без сохранения", dialog);
  await until(`!${dialog}`);

  await visit("#reports/template?id=" + template);
  await click("Редактировать шаблон");
  await writeText("## Проверка маршрута\n\n**Место уточнено.**");
  await click("Предпросмотр", dialog);
  await until(`${dialog}?.querySelector(".markdown-preview strong")?.textContent === "Место уточнено."`);
  // A second editor changes only the audience while the first is revising the structure.
  const endpoint = base + "api/reports/template";
  const original = await (await fetch(endpoint + "?id=" + template)).json();
  const other = await fetch(endpoint, {method:"POST", headers:{"Content-Type":"application/json", Origin:new URL(base).origin},
    body:JSON.stringify({id:template, revision:original.revision, title:original.title, audience:"Участники поездки",
      guidance:original.guidance, body:original.body})});
  assert.equal(other.status, 200);
  await click("Сохранить новую версию", dialog);
  await click("Сверил с файлом — продолжить правку", dialog);
  await click("Сохранить новую версию", dialog);
  await until(`!${dialog} && document.querySelector('.report-paper .report-prose strong')?.textContent === "Место уточнено."`);
  await click("Версия 1");
  await until('document.querySelector(".report-paper .report-prose")?.innerText.includes("Встреча")');

  await visit("#plan/" + plan + "?task=" + task);
  await click("Коротко", 'document.querySelector(".gantt-inspector-switch")');
  await click("Изменить", 'document.querySelector(".gantt-brief")');
  await checkDialog();
  await writeText("Кедр выбран для второй пробы.");
  await click("Сохранить", dialog);
  await until(`!${dialog} && document.querySelector(".gantt-summary-text")?.textContent.includes("Кедр выбран")`);
  assert.ok(await evaluate(`location.hash.includes(${quote(task)})`));

  await click("История", 'document.querySelector(".gantt-inspector-switch")');
  await click("Добавить", 'document.querySelector(".gantt-history")');
  await writeText("Сравнение согласовано.");
  await click("Сохранить", dialog);
  await until(`!${dialog} && document.querySelector('.gantt-inspector[data-pane="history"] .gantt-history')?.innerText.includes("Сравнение согласовано.")`);

  await viewport(1366, 650);
  await click("Изменить даты");
  await checkDialog();
  await evaluate(`(() => {
    const field = ${dialog}.querySelector("select"), date = ${dialog}.querySelector('input[type="date"]');
    field.value = "forecast";
    field.dispatchEvent(new Event("change", {bubbles:true}));
    date.value = "2026-11-29";
    date.dispatchEvent(new Event("input", {bubbles:true}));
  })()`);
  await click("Проверить прогноз", dialog);
  await until(`${dialog}?.querySelector(".gantt-date-preview")?.innerText.includes("Зимние пробы")`);
  await click("Сохранить прогноз", dialog);
  await until(`!${dialog} && document.querySelector(".gantt-inspector")?.innerText.includes("Кедр выбран")`);
  assert.ok(await evaluate(`location.hash.includes(${quote(task)})`));
} finally {
  await close();
}
