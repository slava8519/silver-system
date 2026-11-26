import assert from "node:assert/strict";
import test from "node:test";
import {dateText} from "../second_brain/web/gantt-axis.js";
import {taskInspector} from "../second_brain/web/gantt-detail.js";
import {snapshotInspector} from "../second_brain/web/gantt-cycle.js";

class Element {
  constructor(tag, cls, text) { this.tag = tag; this.className = cls; this.children = text ? [text] : []; }
  set textContent(text) { this.children = [text]; }
  get textContent() { return this.children.map(c => typeof c === "string" ? c : c.textContent).join(""); }
  append(...children) { this.children.push(...children); }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(event, fn) { this[event] = fn; }
  *all() {
    yield this;
    for (const child of this.children) if (child instanceof Element) yield* child.all();
  }
}
const node = (tag, cls, text) => new Element(tag, cls, text);
const ui = {node, button: label => node("button", "", label), link: (label, href) => Object.assign(node("a", "", label), {href})};
const path = "Tasks/Operations/Launch.md";
const row = {id: "launch", title: "Запуск", path, kind: "task"};
const plan = {items: [row]};
const root = "/vaults/Example";
const readiness = {value:"0", date:"2026-11-03", kind:"estimate", reason:"Проверка ещё впереди."};
function render(extra = {}, options = {}) {
  globalThis.document = {createElement: node};
  const task = {...row, latest:[], latestTotal:0, relations: {predecessors:[], successors:[]}, ...extra};
  return taskInspector({...ui, ...options}, {row, root, plan, task}).element;
}

test("task summary and events show labels with safe targets and preserve the Obsidian heading", async () => {
  const href = "../../Notes/%D0%9F%D0%BB%D0%B0%D0%BD%20A.md#%D0%AD%D1%82%D0%B0%D0%BF";
  const brief = {text: "См. [План A](" + href + ") и [место](#^update)."};
  const invalid = ["../../../outside.md", "%2e%2e/out.md", ".hidden/doc.md", "%2Foutside.md",
    "data:text/html,bad", "//example.com/a.md", "file:///private.md", "a%5Cb.md", "%zz.md"];
  const events = [{text: invalid.map((s,i) => "[запись " + i + "](" + s + ")").join(" ") + " <img src=x> [сайт](https://example.org/x)", date:"2026-11-01"}];
  const panel = render({brief, latest:events, latestTotal:events.length});
  const links = [...panel.all()].filter(n => n.tag === "a");
  const entry = links.find(n => n.textContent === "План A");
  assert.equal(new URL(entry.href).searchParams.get("path"), root + "/Notes/План A.md#Этап");
  assert.equal(new URL(links.find(n => n.textContent === "место").href).searchParams.get("path"), root + "/" + path + "#^update");
  assert.equal(links.find(n => n.textContent === "сайт").rel, "noopener noreferrer");
  assert.ok(!links.some(n => n.textContent.startsWith("запись")));
  assert.ok(!panel.textContent.includes("%D0"));
  assert.ok(panel.textContent.includes("<img src=x>"));
  assert.ok(![...panel.all()].some(n => n.tag === "img"));
  const sent = [];
  globalThis.window = {parent:{postMessage:(message, origin) => sent.push({message, origin})}};
  try {
    const embedded = render({brief}, {embedded:true});
    await [...embedded.all()].find(n => n.textContent === "План A" && n.tag === "a").click({preventDefault() {}});
    assert.deepEqual(sent, [{origin:"app://obsidian.md", message:{
      type:"second-brain-open-source", path:"Notes/План A.md", line:null, subpath:"#Этап"
    }}]);
  } finally { delete globalThis.window; }
});

test("selected task separates readiness from completion and an old snapshot never reads live readiness", () => {
  const live = render({readiness, completed: "2026-11-07"});
  assert.ok(live.textContent.includes("Готовность0% · Оценка · " + dateText(readiness.date)));
  assert.ok(live.textContent.includes("Факт завершения" + dateText("2026-11-07")));
  assert.ok(live.textContent.includes(readiness.reason));
  assert.ok(render().textContent.includes("ГотовностьНе указана"));
  const snapshot = data => snapshotInspector(ui, {plan, row:{...row, ...data}, root});
  assert.ok(!snapshot({}).textContent.includes("Готовность при закрытии"));
  assert.ok(snapshot({readiness:{...readiness, value:"32.5", kind:"fact"}}).textContent
    .includes("Готовность при закрытии32,5% · Факт · " + dateText(readiness.date)));
});
