const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const packageDir = path.join(__dirname, '../plugins/obsidian-gantt');
const source = readFileSync(path.join(packageDir, 'main.js'), 'utf8');
const origin = 'http://127.0.0.1:8766';
const running = { state: 'running', origin, binding: 'f'.repeat(64), workspaceEmbed: true };
const stopped = { state: 'stopped', origin };

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
  addEventListener(name, callback) { this[name] = callback; }
  createEl(tag, attrs) {
    const child = new Element(tag, attrs);
    this.children.push(child);
    return child;
  }
  createDiv(attrs) { return this.createEl('div', attrs); }
}

async function fixture(initial = stopped) {
  let factory, listener, ribbon, leaf;
  const opened = [], external = [], commands = {};
  let status = initial;
  const server = {
    starts: 0,
    async status() { return status; },
    async start() { this.starts++; return status = running; },
    async stop() { return status = stopped; }
  };
  class TFile { constructor() { this.extension = 'md'; } }
  const app = {
    metadataCache: { getFileCache: () => ({}) },
    vault: {
      adapter: { getBasePath: () => __dirname },
      getAbstractFileByPath: p => p === 'Tasks/one.md' ? new TFile() : null
    },
    workspace: {
      getLeavesOfType: type => type === 'second-brain-gantt' && leaf ? [leaf] : [],
      getLeaf: () => ({
        async setViewState() { leaf = this; this.view = factory(this); await this.view.onOpen(); },
        openFile(file, state) { opened.push(state.eState?.line); }
      }),
      revealLeaf() {},
      setActiveLeaf() {}
    }
  };
  class ItemView {
    constructor() { this.app = app; this.contentEl = new Element('div'); }
    registerDomEvent(target, name, callback) { assert.equal(name, 'message'); listener = callback; }
    addAction(icon, title, callback) { return {icon, title, click: callback}; }
    onPaneMenu() {}
  }
  class Plugin {
    constructor() { this.app = app; this.manifest = { dir: packageDir }; }
    async loadData() { return { port: '8766/path' }; }
    registerView(type, fn) { factory = fn; }
    addRibbonIcon(icon, label, callback) { assert.equal(label, 'Второй мозг'); ribbon = callback; }
    addCommand(command) { commands[command.id] = command.callback; }
    addSettingTab() {}
  }
  const modules = {
    obsidian: { ItemView, Plugin, PluginSettingTab: class {}, TFile, Notice: class {},
      resolveSubpath: (_cache, subpath) => subpath === '#Ход работ' ? {start: {line: 11}} : null },
    electron: { shell: { openExternal: href => external.push(href) } },
    [path.join(packageDir, 'server.js')]: { AdminServer: class {
      constructor(options) {
        assert.equal(options.port, 8766);
        return server;
      }
    } }
  };
  const context = { require: name => modules[name] || require(name), module: {}, URL };
  vm.runInNewContext(source, context);
  const plugin = new context.module.exports();
  await plugin.onload();
  return { plugin, opened, external, commands, ribbon, server,
    view: () => leaf?.view, restore: () => app.workspace.getLeaf().setViewState(),
    send: event => listener(event), state: value => { status = value; } };
}

test('restoring a stopped tab stays idle; ribbon starts full workspace, stop releases it and ribbon restarts', async () => {
  const f = await fixture();
  await f.restore();
  const v = f.view();
  assert.equal(f.server.starts, 0);
  assert.match(v.contentEl.children[0].children[0].attrs.text, /выключена/);
  await f.ribbon();
  const frame = v.frame;
  assert.equal(frame.attrs.attr.src, origin + '/?embed=workspace&binding=' + running.binding + '#overview');
  assert.match(frame.attrs.attr.sandbox, /allow-downloads/);
  await f.ribbon();
  assert.equal(v.frame, frame); // Do not lose a form in the current section.
  assert.deepEqual(v.contentEl.children, [frame]);
  assert.equal(v.browserAction.hidden, false);
  const menu = new Map();
  v.onPaneMenu({ addItem(build) {
    const item = {
      setTitle(title) { this.title = title; return this; },
      setIcon() { return this; },
      onClick(callback) { menu.set(this.title, callback); return this; }
    };
    build(item);
  } }, 'tab-header');
  assert.equal(menu.has('Открыть в браузере'), true);
  await menu.get('Остановить админку')();
  assert.equal(v.frame, null);
  assert.equal(v.browserAction.hidden, true);
  await f.ribbon();
  assert.ok(v.frame);
  await f.commands.open();
  assert.match(v.frame.src, /#activity\?view=plans$/);
});

test('browser action passes through the current section or source, stripping iframe parameters', async () => {
  const f = await fixture();
  await f.ribbon();
  const v = f.view();
  function route(value, overrides = {}) {
    f.send({ origin, source: v.frame.contentWindow, data: { type: 'second-brain-location', route: value }, ...overrides });
  }
  route('/#map?id=123&group=plans');
  await v.browserAction.click();
  route('/source/note?ref=a&embed=workspace&binding=secret&line=17');
  route('/#inbox', { source: {} });
  route('/#overview', { origin: 'http://127.0.0.1:8770' });
  for (const invalid of ['//example.org/', 'https://example.org/', '/api/work/stop', '/\\evil.test/']) route(invalid);
  await v.openBrowser();
  route('/report-source/issue?index=0&embed=workspace&binding=secret#cited-line');
  await v.openBrowser();
  route('/about.html#system-map');
  await v.openBrowser();
  assert.deepEqual(f.external, [
    origin + '/#map?id=123&group=plans', origin + '/source/note?ref=a&line=17',
    origin + '/report-source/issue?index=0#cited-line', origin + '/about.html#system-map'
  ]);
  f.state({ state: 'conflict', origin, message: 'На порту другое хранилище.' });
  await v.openBrowser();
  assert.equal(v.frame, null);
  assert.equal(f.external.length, 4);
});

test('only a source message from the bound frame opens a file in the current vault', async () => {
  const f = await fixture(running);
  await f.restore();
  const event = { source: f.view().frame.contentWindow, origin,
    data: { type: 'second-brain-open-source', path: 'Tasks/one.md', line: 6 } };
  f.send({ ...event, origin: 'http://localhost:9000' });
  f.send({ ...event, source: {} });
  f.send({ ...event, data: { ...event.data, path: '../outside.md' } });
  assert.deepEqual(f.opened, []);
  f.send(event);
  assert.deepEqual(f.opened, [5]);
  f.send({ ...event, data: { ...event.data, line: undefined, subpath: '#Ход работ' } });
  assert.deepEqual(f.opened, [5, 11]);
});

test('the bound workspace opens external web links in the system browser', async () => {
  const f = await fixture(running);
  await f.restore();
  const event = {source:f.view().frame.contentWindow, origin,
    data:{type:'second-brain-open-web-link', href:'https://example.org/research?q=test#section'}};
  f.send({...event, source:{}});
  f.send({...event, origin:'https://example.org'});
  for (const href of ['file:///note', 'obsidian://open', 'javascript:void(0)', 'data:text/plain,hi', '/relative', origin + '/api/workspace']) {
    f.send({...event, data:{...event.data, href}});
  }
  assert.deepEqual(f.external, []);
  f.send(event);
  assert.deepEqual(f.external, [event.data.href]);
});

test('a delayed verification cannot reopen a closed view; a legacy server shows upgrade guidance', async () => {
  const f = await fixture({ ...running, workspaceEmbed: false });
  await f.restore();
  const v = f.view();
  assert.equal(v.frame, null);
  assert.match(v.contentEl.children[0].children[0].attrs.text, /прежняя версия/);
  let resolve;
  f.server.status = () => new Promise(done => { resolve = done; });
  const pending = v.connect();
  await v.onClose();
  resolve(running);
  await pending;
  assert.equal(v.frame, null);
  assert.equal(v.contentEl.children.length, 0);
});

test('a restarted server for the same vault replaces a stale frame at the last selected section', async () => {
  const f = await fixture({ ...running, workControl: 'a'.repeat(32) });
  await f.restore();
  const v = f.view();
  const previous = v.frame;
  f.send({ origin, source: previous.contentWindow, data: { type: 'second-brain-location', route: '/#inbox' } });
  f.state({ ...running, workControl: 'b'.repeat(32) });
  await v.connect();
  assert.notEqual(v.frame, previous);
  assert.match(v.frame.attrs.attr.src, /#inbox$/);
});

test('default HTTP port accepts the canonical Chromium message origin', async () => {
  const f = await fixture({ ...running, origin: 'http://127.0.0.1:80' });
  await f.restore();
  const v = f.view();
  f.send({ origin: 'http://127.0.0.1', source: v.frame.contentWindow,
    data: { type: 'second-brain-location', route: '/#records' } });
  await v.openBrowser();
  assert.deepEqual(f.external, ['http://127.0.0.1/#records']);
});

test('stop while a new tab is being restored supersedes the ribbon activation', async () => {
  const f = await fixture(stopped);
  let resolveProbe;
  f.server.status = () => new Promise(done => { resolveProbe = done; });
  const opening = f.ribbon();
  await f.commands['stop-admin']();
  resolveProbe(stopped);
  await opening;
  assert.equal(f.server.starts, 0);
  assert.equal(f.view().frame, null);
});
