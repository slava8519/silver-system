const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync, realpathSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const packageDir = path.join(__dirname, '../plugins/obsidian-gantt');
const source = readFileSync(path.join(packageDir, 'main.js'), 'utf8');
const root = realpathSync(__dirname);
const binding = 'f'.repeat(64);

class Element {
  constructor(tag, attrs = {}) {
    this.tag = tag;
    this.attrs = attrs;
    this.children = [];
    this.ownerDocument = { defaultView: {} };
    this.contentWindow = {};
  }
  addClass() {}
  empty() { this.children = []; }
  remove() {}
  setText(text) { this.attrs.text = text; }
  addEventListener() {}
  createEl(tag, attrs) {
    const child = new Element(tag, attrs);
    this.children.push(child);
    return child;
  }
  createDiv(attrs) { return this.createEl('div', attrs); }
}

async function fixture(requestUrl, port = 8766) {
  let viewFactory;
  let onMessage;
  const opened = [];
  class TFile { constructor() { this.extension = 'md'; } }
  const app = {
    vault: {
      adapter: { getBasePath: () => root },
      getAbstractFileByPath: path => path === 'Tasks/one.md' ? new TFile() : null
    },
    workspace: {
      getLeavesOfType: () => [],
      getLeaf: () => ({ openFile: (file, state) => opened.push(state.eState?.line) }),
      revealLeaf() {},
      setActiveLeaf() {}
    }
  };
  class ItemView {
    constructor() { this.app = app; this.contentEl = new Element('div'); }
    registerDomEvent(target, name, fn) { assert.equal(name, 'message'); onMessage = fn; }
  }
  class Plugin {
    async loadData() { return { port }; }
    registerView(type, fn) { viewFactory = fn; }
    addRibbonIcon() {}
    addCommand() {}
    addSettingTab() {}
  }
  const host = { ItemView, Plugin, PluginSettingTab: class {}, TFile, Notice: class {}, requestUrl };
  const context = { require: name => name === 'obsidian' ? host : require(name), module: {}, process };
  vm.runInNewContext(source, context);
  const plugin = new context.module.exports();
  plugin.app = app;
  await plugin.onload();
  const view = viewFactory({});
  return { view, opened, message: event => onMessage(event) };
}

test('a matching vault opens a bound loopback frame; a second vault is rejected', async () => {
  let serverRoot = path.dirname(root);
  const { view } = await fixture(async ({ url }) => {
    assert.equal(url, 'http://127.0.0.1:8766/api/planning-vault');
    return { status: 200, json: { root: serverRoot, binding } };
  }, '8766/path');
  await view.onOpen();
  assert.equal(view.frame, null);
  assert.match(view.contentEl.children[0].children[0].attrs.text, /другое хранилище/);
  serverRoot = root;
  await view.connect();
  assert.equal(view.frame.attrs.attr.src, 'http://127.0.0.1:8766/?embed=1&binding=' + binding + '#plans');
});

test('only a source message from the bound frame opens a file in the current vault', async () => {
  const { view, opened, message } = await fixture(async () => ({ status: 200, json: { root, binding } }));
  await view.onOpen();
  const event = { source: view.frame.contentWindow, origin: 'http://127.0.0.1:8766',
    data: { type: 'second-brain-open-source', path: 'Tasks/one.md', line: 6 } };
  message({ ...event, origin: 'http://localhost:9000' });
  message({ ...event, source: {} });
  message({ ...event, data: { ...event.data, path: '../outside.md' } });
  assert.deepEqual(opened, []);
  message(event);
  assert.deepEqual(opened, [5]);
});

test('a delayed verification cannot reopen a closed view', async () => {
  let resolve;
  const { view } = await fixture(() => new Promise(done => { resolve = done; }));
  const pending = view.onOpen();
  await view.onClose();
  resolve({ status: 200, json: { root, binding } });
  await pending;
  assert.equal(view.frame, null);
  assert.equal(view.contentEl.children.length, 0);
});
