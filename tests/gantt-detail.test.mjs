import assert from "node:assert/strict";
import test from "node:test";
import {dateText} from "../second_brain/web/gantt-axis.js";
import {taskInspector} from "../second_brain/web/gantt-detail.js";
import {snapshotInspector} from "../second_brain/web/gantt-cycle.js";
import {evidence} from "../second_brain/web/gantt-okr-source.js";
import {workspaceLink} from "../second_brain/web/obsidian-workspace.js";

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

test("task, KR and closed plan open the cited Inbox original in the browser and the bound Obsidian frame", () => {
  const id = "af".repeat(32), base = "http://127.0.0.1:8766";
  const quote = "Первый комплект проверен.";
  globalThis.window = {parent: {}};
  try {
    for (const ext of ["txt", "md"]) for (const embedded of [false, true]) {
      const page = base + (embedded ? "/?embed=workspace&binding=test" : "/");
      const options = {...ui, embedded, link:(label, href) => ui.link(label, workspaceLink(href, page))};
      const source = {path:`Sources/Originals/${id}.${ext}`, id, line:4, quote, ref:"dd".repeat(32)};
      const state = {...readiness, source};
      const panels = [render({readiness:state}, options), evidence(options,root,source),
        snapshotInspector(options,{plan, row:{...row, readiness:state}, root})];
      for (const panel of panels) {
        assert.ok(panel.textContent.includes(quote));
        const a = [...panel.all()].find(n => n.tag === "a" && n.textContent === "Открыть оригинал");
        const url = new URL(a.href, page);
        assert.equal(url.origin, base);
        assert.equal(url.pathname, "/original/" + id);
        assert.equal(url.searchParams.get("start"), "4");
        assert.equal(url.searchParams.get("binding"), embedded ? "test" : null);
        assert.equal(a.target, embedded ? undefined : "_blank");
        assert.equal(a.click, undefined);
      }
      for (const broken of [{unavailable:true}, {id:"another-id"}, {line:0}]) {
        const panel = evidence(options, root, {...source, ...broken, href:"https://other.example/"});
        assert.ok(panel.textContent.includes(quote));
        assert.ok(![...panel.all()].some(n => n.tag === "a"));
      }
    }
  } finally { delete globalThis.window; }
});

test("native Markdown evidence still opens its verified line in Obsidian and its document URL in the browser", async () => {
  const source = {path:"Notes/Report.md", id:"note-1", line:8, ref:"dd".repeat(32), quote:"Проверено.",
    href:"/source/note-1?line=8#cited-line"};
  const messages = [];
  globalThis.window = {parent:{postMessage:(message, origin) => messages.push({message, origin})}};
  globalThis.location = new URL("http://127.0.0.1:8766/");
  try {
    const panel = evidence({...ui, embedded:true, api:async (action,args) => {
      assert.equal(action,"citation");
      assert.deepEqual(args,{id:source.id, line:source.line, ref:source.ref});
      return {path:source.path, start:11};
    }}, root,source);
    await [...panel.all()].find(n => n.tag === "a").click({preventDefault() {}});
    assert.deepEqual(messages,[{origin:"app://obsidian.md", message:{type:"second-brain-open-source",path:source.path,line:11}}]);
    const a = [...evidence(ui,root,source).all()].find(n => n.tag === "a");
    assert.equal(a.href,location.origin + source.href);
    assert.equal(a.target,"_blank");
    const unsafe = evidence(ui,root,{...source, href:"https://other.example/source/note-1"});
    assert.ok(![...unsafe.all()].some(n => n.tag === "a"));
  } finally { delete globalThis.window; delete globalThis.location; }
});
