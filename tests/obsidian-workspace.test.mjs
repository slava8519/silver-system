import assert from "node:assert/strict";
import test from "node:test";
import {browserRoute, connectObsidianWorkspace, workspaceLink} from "../second_brain/web/obsidian-workspace.js";

const origin = "http://127.0.0.1:8766";
const binding = "a".repeat(64);
const query = "?embed=workspace&binding=" + binding;

test("bound links retain the current vault across sources, help, pagination and downloads", () => {
  for (const href of ["/about.html#flow", "/#inbox", "/source/n1?line=2&ref=r#cited-line",
    "/evidence/p1/f1?personal=1", "/original/file?start=31", "/attachment/file", "/report-source/issue?index=0#cited-line"]) {
    const dest = new URL(workspaceLink(href, origin + "/" + query + "#people"), origin);
    assert.equal(dest.searchParams.get("embed"), "workspace");
    assert.equal(dest.searchParams.get("binding"), binding);
    assert.equal(browserRoute(dest), href);
  }
  const following = workspaceLink("?start=61", origin + "/original/file" + query + "&start=31");
  assert.equal(browserRoute(origin + following), "/original/file?start=61");
});

test("map and Gantt deep links travel intact and ordinary links keep their destination", () => {
  const focus = "#map?id=item&group=plans&filter=a%2Fb";
  assert.equal(workspaceLink(focus, origin + "/" + query), focus);
  assert.equal(browserRoute(origin + "/" + query + focus), "/" + focus);
  for (const href of ["https://example.org/about.html", "//other.test/", "obsidian://open?path=a", "/api/workspace"]) {
    assert.equal(workspaceLink(href, origin + "/" + query), href);
  }
  for (const current of [origin + "/", origin + "/?embed=1&binding=" + binding]) {
    assert.equal(workspaceLink("/about.html", current), "/about.html");
  }
});

test("the frame reports the initial section and replacements of the active history entry to Obsidian", () => {
  const frame = new EventTarget();
  frame.navigation = new EventTarget();
  frame.document = {querySelectorAll: () => []};
  frame.location = new URL(origin + "/" + query + "#activity");
  const messages = [];
  frame.parent = {postMessage: (message, target) => messages.push([message, target])};
  connectObsidianWorkspace(frame);
  frame.location.hash = "#activity?q=one";
  frame.navigation.dispatchEvent(new Event("currententrychange"));
  assert.deepEqual(messages, ["#activity", "#activity?q=one"].map(hash =>
    [{type: "second-brain-location", route: "/" + hash}, "app://obsidian.md"]));
});

test("sandboxed new-tab links open a web page via Obsidian or a bound source inside the frame", () => {
  const listeners = new Map(), messages = [], followed = [];
  const frame = {
    location: {origin, href:origin + "/" + query, search:query, assign: url => followed.push(url)},
    document: {querySelectorAll: () => []},
    parent: {postMessage: (msg, target) => messages.push([msg, target])},
    addEventListener: (name, fn) => listeners.set(name, fn)
  };
  connectObsidianWorkspace(frame);
  messages.length = 0;
  function click(href, {download = false, type = "click", button = 0} = {}) {
    const link = {href, hasAttribute: name => name === "download" && download};
    let handled = false;
    listeners.get(type)({type, button, target:{closest: () => link}, preventDefault: () => { handled = true; }});
    return handled;
  }
  assert.ok(click("https://example.org/notes#sources", {type:"auxclick", button:1}));
  assert.ok(click(origin + "/report-source/issue?index=0#cited-line"));
  assert.deepEqual(messages, [[{type:"second-brain-open-web-link", href:"https://example.org/notes#sources"}, "app://obsidian.md"]]);
  assert.equal(followed.length, 1);
  assert.equal(new URL(followed[0], origin).searchParams.get("binding"), binding);
  assert.equal(browserRoute(new URL(followed[0], origin)), "/report-source/issue?index=0#cited-line");
  assert.equal(click("file:///test"), false);
  assert.equal(click(origin + "/api/workspace"), false);
  assert.equal(click(origin + "/attachment/file", {download:true}), false);
});
