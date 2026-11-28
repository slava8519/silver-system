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
  get childNodes() { return this.children.map(c => typeof c === "string" ? {nodeType: 3, textContent: c} : c); }
  get tagName() { return this.tag.toUpperCase(); }
  get lastElementChild() { return this.children.filter(c => c instanceof Element).at(-1); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
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

test("named fields and journal operations remain readable with Markdown and evidence links", () => {
  const text = "Готов черновик. • Сделано: проверен [образец · серия A](../../Notes/Test.md) " +
    "• Дальше: сверить `значение • Срок: по договору` • Срок: пятница";
  const history = "сводка · " + text + " · [Запись](../../Notes/Report.md)";
  const panel = render({brief: {text}, latest: [{text: history}, {text: "Скорость · точность."}], latestTotal: 2});
  const byClass = (root, name) => [...root.all()].find(e => e.className === name);
  for (const name of ["gantt-summary-text", "gantt-event-text"]) {
    const prose = byClass(panel, name);
    const fields = byClass(prose, "gantt-reading-fields");
    assert.deepEqual(fields.children.filter(n => n.tag === "dt").map(n => n.textContent), ["Сделано", "Дальше", "Срок"]);
    const values = fields.children.filter(n => n.tag === "dd");
    assert.equal(values[0].children.find(n => n.tag === "a").textContent, "образец · серия A");
    assert.equal(values[1].children.find(n => n.tag === "code").textContent, "значение • Срок: по договору");
    assert.equal(values[2].textContent, "пятница");
  }
  assert.equal(byClass(panel, "gantt-event-kind").textContent, "сводка");
  const journal = byClass(panel, "gantt-event-text");
  assert.equal(journal.lastElementChild.textContent, "Запись");
  assert.equal(journal.lastElementChild.lastElementChild.tag, "a");
  assert.ok(panel.textContent.includes("Скорость · точность."));
});

test("compact sections start on the brief and the selected work can switch to history or dates", () => {
  const panel = render({}, {button: (label, cls, click) => Object.assign(node("button", cls, label), {click})});
  const tabs = [...panel.all()].filter(e => e.className === "gantt-inspector-tab");
  assert.equal(panel["data-pane"], "brief");
  for (const tab of tabs) {
    tab.click();
    assert.equal(panel["data-pane"], tab.value);
    assert.deepEqual(tabs.filter(t => t["aria-pressed"] === "true"), [tab]);
    assert.ok([...panel.all()].some(e => e.id === tab["aria-controls"]));
  }
});

test("task summary and events show labels with safe targets and preserve the Obsidian heading", async () => {
  const href = "../../Notes/%D0%9F%D0%BB%D0%B0%D0%BD%20A.md#%D0%AD%D1%82%D0%B0%D0%BF";
  const brief = {text: "См. [План A](" + href + ") и [место](#^update). [[Notes/План A#Этап|Тот же этап]]."};
  const invalid = ["../../../outside.md", "%2e%2e/out.md", ".hidden/doc.md", "%2Foutside.md",
    "data:text/html,bad", "//example.com/a.md", "file:///private.md", "a%5Cb.md", "%zz.md"];
  const events = [{text: invalid.map((s,i) => "[запись " + i + "](" + s + ")").join(" ") + " <img src=x> [сайт](https://example.org/x)", date:"2026-11-01"}];
  const panel = render({brief, latest:events, latestTotal:events.length});
  const links = [...panel.all()].filter(n => n.tag === "a");
  const entry = links.find(n => n.textContent === "План A");
  const params = new URLSearchParams(entry.href.split("?")[1]);
  assert.match(entry.href, /^#record\?/);
  assert.equal(params.get("path"), "Notes/План A.md");
  assert.equal(params.get("section"), "Этап");
  assert.equal(links.find(n => n.textContent === "Тот же этап").href, entry.href);
  assert.equal(new URL(links.find(n => n.textContent === "место").href).searchParams.get("path"), root + "/" + path + "#^update");
  assert.equal(links.find(n => n.textContent === "сайт").rel, "noopener noreferrer");
  assert.ok(!links.some(n => n.textContent.startsWith("запись")));
  assert.ok(!panel.textContent.includes("%D0"));
  assert.ok(panel.textContent.includes("<img src=x>"));
  assert.ok(![...panel.all()].some(n => n.tag === "img"));
  const sent = [];
  globalThis.window = {parent:{postMessage:(message, origin) => sent.push({message, origin})}};
  try {
    const full = render({brief}, {embedded:true});
    assert.equal([...full.all()].find(n => n.textContent === "План A" && n.tag === "a").href, entry.href);
    const embedded = render({brief}, {embedded:true, compact:true});
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
    .includes("Готовность при закрытии33% · Факт · " + dateText(readiness.date)));
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

test("aggregate rows edit their own journal and frozen rows expose only the recorded slice", () => {
  globalThis.document = {createElement:node};
  const group = {kind:"key_result", label:"КР", id:"quality", progress:{value:44,hasEstimates:true}, rows:[row]};
  const task = {id:"quality", title:"Стабильность", path:"Plans/Result.md", brief:{text:"Результат сверили."},
    latest:[{text:"Проверен график",date:"2026-11-20"}],latestTotal:1};
  for (const readOnly of [false,true]) {
    const panel = taskInspector(ui, {group,task:{...task,readOnly}, plan:{id:"period"},root}).element;
    const buttons = [...panel.all()].filter(item => item.tag === "button").map(item => item.textContent);
    for (const action of ["Изменить","Добавить","Исправить"]) assert.equal(buttons.includes(action), !readOnly);
    assert.ok(!buttons.includes("Изменить даты"));
    assert.match(panel.textContent,/Готовность работ≈44%/);
    assert.match(panel.textContent,/Результат сверили/);
    assert.match(panel.textContent,/Проверен график/);
    const kr = [...panel.all()].find(item => item.textContent === "Критерии и замеры");
    assert.equal(kr.href,"#okr/quality?plan=period");
  }
});

test("brief fields split plain semicolon-delimited items and preserve inline Markdown", () => {
  const text = [
    "Итог принят; сроки уточняются.",
    "Сделано: согласовали окно; измерили `p50; p99`; сохранили [замер; сводку](../../Notes/Measure.md)",
    "Мешает: ожидание ответа",
    "Риск: брака много; тестов мало",
    "Срок: **пятница; до встречи**",
    "Решение: оставили исходный план"
  ].join(" • ");
  const panel = render({brief:{text}, latest:[{text}], latestTotal:1});
  for (const name of ["gantt-summary-text", "gantt-event-text"]) {
    const prose = [...panel.all()].find(item => item.className === name);
    assert.equal(prose.children[0].textContent, "Итог принят; сроки уточняются.");
    const fields = [...prose.all()].find(item => item.tag === "dl");
    const [done, blocker, risk, due, decision] = fields.children.filter(item => item.tag === "dd");
    assert.deepEqual(done.children[0].children.map(item => item.textContent),
      ["согласовали окно", "измерили p50; p99", "сохранили замер; сводку"]);
    assert.equal([...done.all()].find(item => item.tag === "code").textContent, "p50; p99");
    const link = [...done.all()].find(item => item.tag === "a");
    assert.ok(link.href.includes("Measure.md")); assert.equal(link.textContent, "замер; сводку");
    assert.deepEqual(risk.children[0].children.map(item => item.textContent), ["брака много", "тестов мало"]);
    assert.equal([...due.all()].find(item => item.tag === "strong").textContent, "пятница; до встречи");
    for (const single of [blocker, due, decision]) assert.ok(![...single.all()].some(item => item.tag === "ul"));
    assert.deepEqual(fields.children.filter(item => item.className === "gantt-reading-risk").map(item => item.textContent),
      ["Мешает","Риск"]);
    assert.equal(fields.children.find(item => item.className === "gantt-reading-decision").textContent, "Решение");
  }
});

test("nested work events load only when expanded and keep task links and evidence separate from the row journal", async () => {
  globalThis.document = {createElement:node};
  const calls = [], chosen = [];
  const group = {kind:"plan", label:"План", id:"period", rows:[row]};
  const controls = {...ui, button:(label, cls, click) => Object.assign(node("button",cls,label), {click})};
  const journal = {workEvents: async offset => {
    calls.push(offset);
    const entry = {taskId:row.id,taskTitle:row.title,taskPath:row.path,id:String(offset),date:"2026-11-25",
      author:"manual", text:"См. [протокол](../../Notes/Check.md)", sources:[{path:"Notes/Check.md",line:3,quote:"Цитата из проверки",unavailable:true}]};
    return {items:[entry],total:2,truncated:offset === 0};
  }};
  const base = {task:{id:"period",title:"Период",path:"Plans/Period.md",latest:[{text:"Собственное событие"}],latestTotal:1},group,plan,root,journal,
    selectMember:id => chosen.push(id)};
  const panel = taskInspector(controls,base).element;
  const nested = [...panel.all()].find(n => n.className === "gantt-work-events");
  assert.equal(nested.open,false);
  assert.deepEqual(calls,[]);
  nested.isConnected = nested.open = true;
  nested.toggle();
  await new Promise(resolve => setImmediate(resolve));
  const find = (tag, text) => [...nested.all()].find(n => n.tag === tag && n.textContent === text);
  assert.deepEqual(calls,[0]);
  assert.ok(nested.textContent.includes("Цитата из проверки"));
  assert.equal(new URLSearchParams(find("a","протокол").href.split("?")[1]).get("path"), "Notes/Check.md");
  find("button",row.title).click();
  assert.deepEqual(chosen,[row.id]);
  await find("button","Показать ещё").click();
  assert.deepEqual(calls,[0,1]);
  assert.ok(find("button","Показать ещё").hidden);
  nested.open = false; nested.toggle(); nested.open = true; nested.toggle();
  assert.deepEqual(calls,[0,1]);
  const frozen = taskInspector(controls,{...base,task:{...base.task,readOnly:true}}).element;
  assert.ok(![...frozen.all()].some(n => n.className === "gantt-work-events"));
  const empty = taskInspector(controls,{...base,task:{...base.task,latest:[],latestTotal:0}}).element;
  assert.ok([...empty.all()].find(n => n.className === "gantt-work-events").open);
  const noWorks = taskInspector(controls,{...base,group:{...group,rows:[]}}).element;
  assert.ok(![...noWorks.all()].some(n => n.className === "gantt-work-events"));
});
