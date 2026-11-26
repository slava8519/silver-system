import assert from "node:assert/strict";
import test from "node:test";
import {showVaultCheck} from "../second_brain/web/vault-check.js";

function node(tag, className, textContent) {
  return {tag, className, textContent, children: [], append(...items) { this.children.push(...items); }, setAttribute() {}};
}

test("check navigation and browser history retain the scan until an explicit rerun", async () => {
  const id = "12".repeat(16);
  const time = "2026-11-27T10:35:00+00:00";
  const requests = [], links = [], pinned = [];
  let reruns = 0;
  const ui = {
    node,
    section: () => node("section"),
    pageHead: (...items) => items,
    button: (label, style, action) => ({label, action}),
    link: (label, href) => { links.push({label, href}); return node("a"); },
    counted: n => String(n),
    rerun: () => { reruns++; },
    pin: url => pinned.push(url),
    api: async url => {
      requests.push(url);
      return {status: "issues", checkedAt: time, snapshot: id, checked: {cards: 1, receipts: 0, originals: 0, rules: 0},
        issues: {items: Array.from({length: 10}, () => ({path: "Notes/a.md", line: 1, message: "Проверить"})),
          total: 23, truncated: true}};
    }
  };
  const view = node("main");
  await showVaultCheck(view, new URLSearchParams(), ui);
  assert.match(requests[0], /snapshot=new/);
  assert.deepEqual(pinned, ["#check?snapshot=" + id]);
  assert.equal(view.children[2].children[0].dateTime, time);
  const next = links.find(row => row.label === "Дальше");
  assert.equal(next.href, "#check?snapshot=" + id + "&offset=10");
  links.length = 0;
  await showVaultCheck(node("main"), new URLSearchParams(next.href.split("?")[1]), ui);
  assert.match(requests[1], new RegExp("snapshot=" + id + "&limit=10&offset=10"));
  assert.equal(links.find(row => row.label === "К началу").href, pinned[0]);
  assert.equal(pinned.length, 1);
  view.children[0].find(item => item.label === "Проверить снова").action();
  assert.equal(reruns, 1);

  const fallback = node("main");
  links.length = 0;
  const warning = "Кеш недоступен.";
  await showVaultCheck(fallback, new URLSearchParams(), {...ui, api: async url => ({
    ...await ui.api(url), snapshot: null, snapshotWarning: warning
  })});
  assert.equal(pinned.length, 1);
  assert.ok(fallback.children[2].children.some(item => item.textContent === warning));
  assert.equal(links.find(row => row.label === "Дальше").href, "#check?offset=10");
});
