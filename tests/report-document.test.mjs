import assert from "node:assert/strict";
import test from "node:test";
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
