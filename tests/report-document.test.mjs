import assert from "node:assert/strict";
import test from "node:test";
import {recordDocument, recordSection} from "../second_brain/web/record-document.js";
import {reportDocument, reportPlainText} from "../second_brain/web/report-document.js";

// Only the text-node and element operations used by the Markdown reader.
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
  }
  append(...nodes) { this.children.push(...nodes); }
  set textContent(text) { this.children = [text]; }
  get textContent() { return this.children.map(child => child instanceof Element ? child.textContent : child).join(""); }
  get lastElementChild() { return this.children.filter(child => child instanceof Element).at(-1); }
  elements(tag) { return this.children.filter(child => child.tagName === tag.toUpperCase()); }
}

const body = [
  "## Повестка поездки",
  "",
  "- **Выезд**",
  "    - Изменили маршрут.",
  "\t  - Остался один открытый вопрос.",
  "\t\t- [ToDo] Согласовать окно въезда.",
  "    - [Расписание](https://example.org/train)",
  "- <img src=x> — запись автора.",
].join("\n");

test("agenda keeps four list levels and returns to the right parent after dedenting", () => {
  globalThis.document = {createElement: name => new Element(name)};
  const doc = reportDocument(body);
  const top = doc.elements("ul")[0].elements("li");
  const second = top[0].elements("ul")[0].elements("li");
  const third = second[0].elements("ul")[0].elements("li");
  const fourth = third[0].elements("ul")[0].elements("li");
  assert.equal(top[0].elements("strong")[0].textContent, "Выезд");
  assert.equal(top.length, 2);
  assert.equal(second.length, 2);
  assert.equal(second[1].textContent, "Расписание");
  assert.equal(third.length, 1);
  assert.equal(fourth.length, 1);
  assert.equal(fourth[0].textContent, "[ToDo] Согласовать окно въезда.");
  assert.equal(top[1].textContent, "<img src=x> — запись автора.");
  assert.equal(top[1].elements("img").length, 0);
});

test("chat copy keeps the original indentation, task markers, link target and plain headings", () => {
  globalThis.document = {createElement: name => new Element(name)};
  assert.equal(reportPlainText(body), body
    .replace("## Повестка", "Повестка")
    .replace("**Выезд**", "Выезд")
    .replace("[Расписание](https://example.org/train)", "Расписание (https://example.org/train)"));
  assert.equal(reportPlainText("Пример\n\n```\n**данные**\n```"), "Пример\n\n**данные**");
});

test("copying a report omits editor comments and preserves literal Markdown examples", () => {
  globalThis.document = {createElement: name => new Element(name)};
  const body = [
    "## Выпуск", "", "    - Готово. <!-- личная пометка", "ещё один черновик --> Проверено.",
    "<!--", "не отправлять", "-->", "В примере `<!-- текст -->`.",
    "~~~~", "    <!-- в коде -->", "~~~", "**сохранить**", "~~~~", "Итог <!-- скрыт --> виден."
  ].join("\n");
  const copy = reportPlainText(body);
  assert.equal(copy, [
    "Выпуск", "", "    - Готово. ", " Проверено.", "В примере <!-- текст -->.",
    "    <!-- в коде -->", "~~~", "**сохранить**", "Итог  виден."
  ].join("\n"));
  const rendered = reportDocument(body).textContent;
  for (const draft of ["личная пометка", "черновик", "не отправлять", "скрыт"]) assert.ok(!rendered.includes(draft));
  assert.ok(rendered.includes("<!-- текст -->"));
  assert.ok(rendered.includes("    <!-- в коде -->\n~~~\n**сохранить**"));
});

test("card reader formats a note and opens headings, named wiki links and original evidence in the workspace", () => {
  globalThis.document = {createElement: name => new Element(name)};
  const record = {id:"kit", path:"Directions/Workshop/Набор.md", title:"Набор", vaultName:"Демо"};
  const link = (label, href) => {
    const a = document.createElement("a");
    a.textContent = label;
    a.href = href;
    return a;
  };
  const digest = "af".repeat(32);
  const markdown = [
    "# Набор", "", "## Окна проверки", "Окно выбрано. ^window", "", "**Ход** и _срок_. Читайте [[Projects/Замена#Порядок|план]].",
    "[Сверка](<../../Notes/Сверка дат.md#Итоги>) [в начало](#Окна%20проверки) [[Проверка]]", "",
    "| Окно | В работе |", "| --- | --- |", "| [[Projects/План\\|Май]] | `A|B` |", "",
    "- [x] Собрано", "- [ ] <img src=x onerror=alert(1)>", "",
    "[Квартал](../../Archive/ProjectHistory/kit/2026-Q2-" + digest + ".md#Май)", "",
    "[Источник, строка 17](../../Sources/Originals/" + digest + ".txt) [сайт](https://example.org/)", "",
    "~~~", "[[Совпадение|не ссылка]]", "~~~", "<!-- внутренний маркер -->",
    "[плохая ссылка](javascript:alert(1)) [наружу](../../../file.md) [[.hidden/Secret|скрытая]]",
  ].join("\n");
  const doc = recordDocument(record, markdown, {link}, {omitTitle:record.title});
  function all(node, tag) {
    return node.children.filter(child => child instanceof Element).flatMap(child =>
      [...(child.tagName === tag ? [child] : []), ...all(child, tag)]);
  }
  assert.deepEqual(all(doc, "H2").map(el => el.textContent), ["Окна проверки"]);
  assert.equal(recordSection({querySelectorAll:() => all(doc,"H2")}, "окна-проверки").textContent, "Окна проверки");
  const block = recordSection({querySelectorAll:() => all(doc, "P")}, "^window");
  assert.equal(block.textContent, "Окно выбрано.");
  assert.equal(all(doc, "STRONG")[0].textContent, "Ход");
  assert.equal(all(doc, "EM")[0].textContent, "срок");
  const byName = new Map(all(doc, "A").map(a => [a.textContent, a]));
  function route(label) { return new URLSearchParams(byName.get(label).href.split("?")[1]); }
  assert.equal(route("план").get("path"), "Projects/Замена.md");
  assert.equal(route("план").get("section"), "Порядок");
  assert.equal(route("Сверка").get("path"), "Notes/Сверка дат.md");
  assert.equal(route("Проверка").get("short"), "1");
  assert.equal(route("Май").get("path"), "Projects/План.md");
  assert.equal(route("Квартал").get("path"), "Archive/ProjectHistory/kit/2026-Q2-" + digest + ".md");
  assert.equal(route("Квартал").get("section"), "Май");
  assert.match(byName.get("в начало").href, /^#record\/kit\?/);
  assert.equal(byName.get("Оригинал · строка 17").href, "/original/" + digest + "?start=17");
  assert.equal(byName.get("сайт").rel, "noopener noreferrer");
  for (const label of ["плохая ссылка", "наружу", "скрытая", "не ссылка"]) assert.equal(byName.has(label), false);
  assert.equal(all(doc, "TABLE").length, 1);
  assert.equal(all(doc, "TD").length, 2);
  assert.equal(all(doc, "CODE")[0].textContent, "A|B");
  assert.deepEqual(all(doc, "INPUT").map(c => [c.checked, c.disabled]), [[true,true],[false,true]]);
  assert.equal(all(doc, "IMG").length, 0);
  assert.equal(all(doc, "PRE")[0].textContent.trim(), "[[Совпадение|не ссылка]]");
  assert.equal(doc.textContent.includes("внутренний маркер"), false);
});


test("reports preserve source routes in the bound iframe and open Markdown references in the reader", () => {
  globalThis.document = {createElement: name => new Element(name)};
  const issue = {path:"Reports/Weekly/Issue.md", vaultName:"Демо"};
  const link = (label, href) => Object.assign(new Element("a"), {textContent:label, href});
  const body = "[цитата](/report-source/123?index=1) [Гант](/#plan/period) [заметка](../../Notes/Proof.md#Итог) [[Proof]]";
  for (const embedded of [false, true]) {
    const doc = recordDocument(issue, body, {link, embedded}, {reportLinks:true});
    const [source, gantt, note, short] = doc.elements("p")[0].elements("a");
    assert.equal(source.href, "/report-source/123?index=1");
    assert.equal(gantt.href, "/#plan/period");
    assert.equal(source.target, embedded ? undefined : "_blank");
    assert.equal(gantt.target, source.target);
    assert.equal(new URLSearchParams(note.href.split("?")[1]).get("section"), "Итог");
    assert.equal(new URLSearchParams(short.href.split("?")[1]).get("path"), "Proof.md");
  }
});
