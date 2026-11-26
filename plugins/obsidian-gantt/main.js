const { ItemView, Notice, Plugin, PluginSettingTab, Setting, TFile, resolveSubpath } = require('obsidian');
const { shell } = require('electron');
const path = require('path');

const VIEW = 'second-brain-gantt'; // Preserve existing workspace layouts and hotkeys.
const DEFAULT_PORT = 8766;
const OVERVIEW = '/#overview';

function validPort(value) {
  return /^\d{1,5}$/.test(String(value)) && Number(value) >= 1 && Number(value) <= 65535;
}

function browserRoute(route, origin) {
  if (typeof route !== 'string' || !route.startsWith('/') || route.startsWith('//')) return null;
  try {
    const url = new URL(route, origin);
    if (url.origin !== origin || !/^\/(?:about\.html|(?:source|report-source)\/[^/]+|evidence\/[^/]+\/[^/]+|original\/[a-f0-9]{64})?$/.test(url.pathname)) return null;
    url.searchParams.delete('embed');
    url.searchParams.delete('binding');
    return url.pathname + url.search + url.hash;
  } catch {
    return null;
  }
}

class AdminView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.generation = 0;
    this.frame = null;
    this.route = OVERVIEW;
  }

  getViewType() { return VIEW; }
  getDisplayText() { return 'Второй мозг'; }
  getIcon() { return 'brain'; }

  async onOpen() {
    this.contentEl.addClass('second-brain-gantt');
    this.registerDomEvent(this.contentEl.ownerDocument.defaultView, 'message', event => {
      if (!this.frame || event.source !== this.frame.contentWindow || event.origin !== this.origin) return;
      if (event.data?.type === 'second-brain-location') {
        const route = browserRoute(event.data.route, this.origin);
        if (route) this.route = route;
      } else if (event.data?.type === 'second-brain-open-source') {
        this.openSource(event.data).catch(() => new Notice('Не удалось открыть запись.'));
      }
    });
    // Restoring an Obsidian layout should leave a stopped server idle.
    await this.connect();
  }

  async openSource({ path, line, subpath }) {
    if (typeof path !== 'string' || path.includes('\\') ||
        path.split('/').some(part => !part || part.startsWith('.'))) return;
    const file = this.app.vault.getAbstractFileByPath(path);
    if (!(file instanceof TFile) || file.extension.toLowerCase() !== 'md') {
      new Notice('Исходная запись больше не доступна по этому пути.');
      return;
    }
    let target = Number.isSafeInteger(line) && line >= 1 ? line - 1 : null;
    if (subpath) {
      if (typeof subpath !== 'string' || !/^#[^\u0000-\u001f\u007f]+$/.test(subpath)) return;
      const cache = this.app.metadataCache.getFileCache(file);
      const section = cache && resolveSubpath(cache, subpath);
      if (section) target = section.start.line;
      else new Notice('Раздел больше не доступен. Открыта карточка.');
    }
    const state = target == null ? {} : { eState: { line: target } };
    const { workspace } = this.app;
    const existing = workspace.getLeavesOfType('markdown').find(leaf => leaf.getViewState().state?.file === path);
    const leaf = existing || workspace.getLeaf('tab');
    await leaf.openFile(file, state);
    await workspace.revealLeaf(leaf);
    workspace.setActiveLeaf(leaf, { focus: true });
  }

  status(text, action) {
    this.contentEl.empty();
    this.frame = null;
    const panel = this.contentEl.createDiv({ cls: 'second-brain-gantt-status' });
    panel.createEl('p', { text, attr: { role: 'status' } });
    if (action) panel.createEl('button', { text: action, cls: 'mod-cta' })
      .addEventListener('click', () => this.connect(true));
  }

  show(result, route) {
    if (result.state !== 'running' && result.state !== 'external') {
      const stopped = result.state === 'stopped';
      this.status(result.message || (stopped ? 'Админка выключена.' : 'Проверьте порт в настройках плагина.'),
        stopped ? 'Запустить админку' : 'Проверить подключение');
      return;
    }
    if (!result.workspaceEmbed) {
      const stop = result.state === 'running'
        ? 'Выберите команду «Остановить админку» в палитре Obsidian.'
        : 'Остановите её в терминале, где запустили.';
      this.status('На этом порту работает прежняя версия админки. ' + stop +
        ' Укажите обновлённую папку программы в настройках плагина и запустите админку.', 'Запустить админку');
      return;
    }
    const origin = new URL(result.origin).origin;
    const reuse = this.frame && this.origin === origin && this.binding === result.binding &&
      this.instance === result.workControl;
    if (reuse && (!route || route === this.route)) return;
    this.origin = origin;
    this.instance = result.workControl;
    this.binding = result.binding;
    this.route = route || this.route;
    const url = new URL(this.route, this.origin);
    url.searchParams.set('embed', 'workspace');
    url.searchParams.set('binding', this.binding);
    if (reuse) {
      this.frame.src = url.href;
      return;
    }
    this.contentEl.empty();
    const bar = this.contentEl.createDiv({ cls: 'second-brain-admin-controls' });
    bar.createEl('button', { text: 'Открыть в браузере' })
      .addEventListener('click', () => this.openBrowser());
    bar.createEl('button', { text: 'Остановить админку' })
      .addEventListener('click', () => this.plugin.stop());
    this.frame = this.contentEl.createEl('iframe', {
      cls: 'second-brain-gantt-frame',
      attr: {
        title: 'Второй мозг', sandbox: 'allow-scripts allow-same-origin allow-forms allow-modals allow-downloads',
        referrerpolicy: 'no-referrer', src: url.href
      }
    });
  }

  async connect(start = false, route) {
    const request = ++this.generation;
    if (route && !this.frame) this.route = route;
    if (!this.frame) this.status(start ? 'Запускаем админку…' : 'Проверяем подключение…');
    try {
      const server = this.plugin.server;
      const result = start ? await server.start() : await server.status();
      if (request === this.generation) this.show(result, route);
    } catch (error) {
      if (request === this.generation) this.status(error.message, 'Повторить');
    }
  }

  async openBrowser() {
    const generation = this.generation;
    try {
      const result = await this.plugin.server.status();
      if (!this.frame || generation !== this.generation) return;
      if (new URL(result.origin).origin !== this.origin || result.binding !== this.binding ||
          (result.state !== 'running' && result.state !== 'external')) {
        this.show(result);
        return;
      }
      await shell.openExternal(new URL(this.route, this.origin).href);
    } catch (error) {
      new Notice(error.message);
    }
  }

  async onClose() {
    this.generation++;
    this.frame = null;
    this.contentEl.empty();
  }
}

class AdminSettings extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    this.containerEl.empty();
    const options = { ...this.plugin.options };
    const field = (key, name, description, placeholder) => new Setting(this.containerEl)
      .setName(name).setDesc(description)
      .addText(input => input.setPlaceholder(placeholder).setValue(String(options[key]))
        .onChange(value => { options[key] = value.trim(); }));
    field('programDir', 'Папка программы', 'Для запуска укажите полный путь к папке, в которой лежит каталог second_brain.', '/…/second-brain');
    field('python', 'Python', 'Для своего интерпретатора укажите путь к исполняемому файлу Python 3.9 или новее.', 'Автоматически');
    field('port', 'Порт админки', 'Для одновременно открытых хранилищ выберите разные порты.', '8766');
    new Setting(this.containerEl).addButton(button => button.setButtonText('Применить').onClick(async () => {
      if (!validPort(options.port)) return new Notice('Введите номер порта от 1 до 65535.');
      if (options.programDir && !path.isAbsolute(options.programDir)) return new Notice('Укажите полный путь к папке программы.');
      options.port = Number(options.port);
      try {
        await this.plugin.configure(options);
        new Notice('Настройки админки сохранены.');
      } catch (error) {
        new Notice(error.message);
      }
    }));
  }
}

module.exports = class SecondBrainPlugin extends Plugin {
  async onload() {
    const saved = await this.loadData();
    this.options = {
      port: validPort(saved?.port) ? Number(saved.port) : DEFAULT_PORT,
      programDir: saved?.programDir || '',
      python: saved?.python || ''
    };
    this.intent = 0;
    this.root = this.app.vault.adapter.getBasePath();
    const pluginDir = path.resolve(this.root, this.manifest.dir);
    this.AdminServer = require(path.join(pluginDir, 'server.js')).AdminServer;
    this.server = new this.AdminServer({ ...this.options, root: this.root });
    this.registerView(VIEW, leaf => new AdminView(leaf, this));
    this.addRibbonIcon('brain', 'Второй мозг', () => this.activate());
    this.addCommand({ id: 'open-admin', name: 'Открыть админку', callback: () => this.activate() });
    this.addCommand({ id: 'open', name: 'Открыть Гант', callback: () => this.activate('/#plans') });
    this.addCommand({ id: 'stop-admin', name: 'Остановить админку', callback: () => this.stop() });
    this.addSettingTab(new AdminSettings(this.app, this));
  }

  views() {
    return this.app.workspace.getLeavesOfType(VIEW).map(leaf => leaf.view).filter(view => view instanceof AdminView);
  }

  async configure(options) {
    if (Object.keys(options).every(key => options[key] === this.options[key])) return;
    const old = await this.server.status();
    if (old.state === 'running') await this.server.stop();
    await this.saveData(options);
    this.server.dispose();
    this.options = { ...options };
    this.server = new this.AdminServer({ ...this.options, root: this.root });
    await Promise.all(this.views().map(view => view.connect()));
  }

  async activate(route) {
    const intent = ++this.intent;
    try {
      const { workspace } = this.app;
      let [leaf] = workspace.getLeavesOfType(VIEW);
      if (!leaf) {
        leaf = workspace.getLeaf('tab');
        await leaf.setViewState({ type: VIEW, active: true });
      }
      await workspace.revealLeaf(leaf);
      workspace.setActiveLeaf(leaf, { focus: true });
      if (intent === this.intent && leaf.view instanceof AdminView) await leaf.view.connect(true, route);
    } catch (error) {
      new Notice(error.message);
    }
  }

  async stop() {
    const intent = ++this.intent;
    for (const view of this.views()) {
      view.generation++;
      view.status('Останавливаем админку…');
    }
    try {
      const result = await this.server.stop();
      if (intent !== this.intent) return;
      for (const view of this.views()) view.show(result);
      new Notice(result.message || (result.state === 'stopped' ? 'Админка выключена.' : 'Проверьте подключение к админке.'));
    } catch (error) {
      if (intent !== this.intent) return;
      await Promise.all(this.views().map(view => view.connect()));
      new Notice(error.message);
    }
  }

  onunload() {
    this.intent++;
    this.server?.dispose();
  }
};
