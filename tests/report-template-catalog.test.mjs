import assert from "node:assert/strict";
import test from "node:test";
import {reportsView} from "../second_brain/web/reports.js";

class Element {
  constructor(tag, cls, text) { this.tag = tag; this.className = cls; this.children = []; this.text = text || ""; this.isConnected = true; }
  append(...children) { this.children.push(...children.filter(child => child instanceof Element)); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(type, callback) { this[type] = callback; }
  matches(tag) { return this.tag === tag; }
  focus() { this.focused = true; }
  all() { return [this, ...this.children.flatMap(child => child.all())]; }
  querySelector(tag) { return this.all().find(child => child.tag === tag); }
  get childElementCount() { return this.children.length; }
}
const templates = [{id: "base", title: "Коробочный формат", origin: "builtin", version: 2},
  ...Array.from({length: 8}, (_, index) => ({id: "own-" + index, title: "Свой формат " + index, origin: "custom", version: 1}))];
async function render(page, query = "", catalog = templates) {
  const node = (tag, cls, text) => new Element(tag, cls, text);
  const view = node("main");
  const ui = {
    node, link: (text, href, cls) => Object.assign(node("a", cls, text), {href}),
    button: (text, cls, click) => Object.assign(node("button", cls, text), {click}),
    option: (value, text) => Object.assign(node("option", "", text), {value}),
    labelled: (caption, control) => { const label = node("label", "", caption); label.append(control); return label; },
    pageHead: () => node("header"), go: hash => { view.destination = hash; },
    api: url => {
      if (url === "planning-vault") return {root: "/vault", name: "Vault"};
      const [path, search] = url.split("?");
      assert.equal(path, "reports/templates");
      const params = new URLSearchParams(search);
      const rows = catalog.filter(item => (!params.get("origin") || item.origin === params.get("origin"))
        && item.title.includes(params.get("match") || ""));
      const start = Number(params.get("offset")), end = start + Number(params.get("limit"));
      return {items: rows.slice(start, end), total: rows.length, nextOffset: end < rows.length ? end : null};
    }
  };
  await reportsView(ui)(view, page, new URLSearchParams(query));
  return view;
}

test("each origin has its own page, count and links to the matching template and composer", async () => {
  const view = await render("templates");
  const [custom, builtin] = view.all().filter(item => item.className === "report-template-group");
  const cards = section => section.all().filter(item => item.className === "report-template-card");
  const count = section => section.all().find(item => item.className === "report-group-count").textContent;
  assert.deepEqual([count(custom), count(builtin)], ["8", "1"]);
  assert.equal(cards(custom).length, 6);
  assert.equal(cards(builtin)[0].querySelector("span").text, "Своя редакция · версия 2");
  const more = custom.querySelector("button");
  await more.click();
  assert.equal(cards(custom).length, 8);
  assert.ok(more.hidden);
  assert.ok(cards(custom)[6].querySelector("a").focused);
  assert.deepEqual(cards(custom)[7].all().filter(item => item.tag === "a").map(item => item.href),
    ["#reports/template?id=own-7", "#reports/new?template=own-7"]);
  assert.equal(cards(builtin).length, 1);
});

test("filters use a bookmarkable route and an empty search has a single message and a reset", async () => {
  const view = await render("templates", "origin=custom&match=missing");
  const group = view.all().filter(item => item.className === "report-template-group");
  assert.equal(group.length, 1);
  assert.ok(group[0].hidden);
  const empty = view.all().filter(item => item.className === "report-empty" && !item.hidden);
  assert.equal(empty.length, 1);
  assert.equal(empty[0].querySelector("h3").text, "Шаблоны не найдены");
  assert.equal(view.all().find(item => item.text === "Сбросить").href, "#reports/templates");
  view.querySelector("input").value = "  Новый  ";
  view.querySelector("select").value = "builtin";
  view.querySelector("form").submit({preventDefault() {}});
  const params = new URLSearchParams(view.destination.split("?")[1]);
  assert.deepEqual(Object.fromEntries(params), {match: "Новый", origin: "builtin"});
});

test("series template options group both origins and preserve the catalog default or explicit choice", async () => {
  for (const [query, expected] of [["", "base"], ["template=own-4", "own-4"]]) {
    const form = await render("new-series", query);
    const select = form.all().find(item => item.name === "templateId");
    assert.deepEqual(select.children.map(group => group.label), ["Пользовательские", "Коробочные"]);
    assert.equal(select.value, expected);
    assert.ok(select.all().some(option => option.tag === "option" && option.value === expected));
  }
});

test("an empty vault shows its builtins and a place for the first custom template", async () => {
  const builtins = templates.filter(item => item.origin === "builtin");
  const view = await render("templates", "", builtins);
  const [custom, box] = view.all().filter(item => item.className === "report-template-group");
  assert.equal(custom.querySelector("article"), undefined);
  assert.match(custom.querySelector("p").text, /созданные вами шаблоны/);
  assert.equal(box.querySelector("article").querySelector("a").href, "#reports/template?id=base");
  const form = await render("new-series", "", builtins);
  const select = form.all().find(item => item.name === "templateId");
  assert.deepEqual(select.children.map(group => group.label), ["Коробочные"]);
});
