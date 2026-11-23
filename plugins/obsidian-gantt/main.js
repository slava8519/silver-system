const { ItemView, Notice, Plugin, PluginSettingTab, Setting, TFile, requestUrl } = require('obsidian');
const { realpathSync } = require('fs');
const { isAbsolute } = require('path');

const VIEW = 'second-brain-gantt';
const DEFAULT_PORT = 8766;

function validPort(value) {
  return /^\d{1,5}$/.test(String(value)) && Number(value) >= 1 && Number(value) <= 65535;
}

function quoteArgument(value) {
  if (process.platform === 'win32') return "'" + value.replace(/'/g, "''") + "'";
  return "'" + value.replace(/'/g, "'\\''") + "'";
}

class GanttView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.generation = 0;
    this.frame = null;
  }

  getViewType() { return VIEW; }
  getDisplayText() { return 'Гант'; }
  getIcon() { return 'gantt-chart'; }

  async onOpen() {
    this.contentEl.addClass('second-brain-gantt');
    this.registerDomEvent(this.contentEl.ownerDocument.defaultView, 'message', event => {
      if (!this.frame || event.source !== this.frame.contentWindow || event.origin !== this.origin) return;
      if (event.data?.type === 'second-brain-open-source') {
        this.openSource(event.data).catch(() => new Notice('Не удалось открыть запись.'));
      }
    });
    await this.connect();
  }

  async openSource({ path, line }) {
    if (typeof path !== 'string' || path.includes('\\') ||
        path.split('/').some(part => !part || part.startsWith('.'))) return;
    const file = this.app.vault.getAbstractFileByPath(path);
    if (!(file instanceof TFile) || file.extension.toLowerCase() !== 'md') {
      new Notice('Исходная запись больше не доступна по этому пути.');
      return;
    }
    const state = Number.isSafeInteger(line) && line >= 1 ? { eState: { line: line - 1 } } : {};
    const { workspace } = this.app;
    const existing = workspace.getLeavesOfType('markdown').find(leaf => leaf.getViewState().state?.file === path);
    const leaf = existing || workspace.getLeaf('tab');
    await leaf.openFile(file, state);
    await workspace.revealLeaf(leaf);
    workspace.setActiveLeaf(leaf, { focus: true });
  }

  async connect() {
    const request = ++this.generation;
    const { contentEl } = this;
    contentEl.empty();
    this.frame = null;
    const root = this.app.vault.adapter.getBasePath();
    const origin = 'http://127.0.0.1:' + this.plugin.port;
    const panel = contentEl.createDiv({ cls: 'second-brain-gantt-status' });
    const message = panel.createEl('p', { text: 'Открываем планы хранилища…', attr: { role: 'status' } });
    const showSetup = problem => {
      message.setText(problem);
      panel.createEl('p', { text: 'Из папки программы запустите оболочку для этого хранилища.' });
      panel.createEl('pre').createEl('code', {
        text: 'python3 -m second_brain --vault ' + quoteArgument(root) + ' work --port ' + this.plugin.port
      });
      panel.createEl('button', { text: 'Подключиться повторно', cls: 'mod-cta' })
        .addEventListener('click', () => this.connect());
    };
    try {
      const response = await requestUrl({ url: origin + '/api/planning-vault', throw: false });
      if (request !== this.generation) return;
      if (response.status !== 200) {
        showSetup('Проверьте версию рабочей оболочки и порт в настройках плагина.');
        return;
      }
      const { root: serverRoot, binding } = response.json;
      if (typeof serverRoot !== 'string' || !isAbsolute(serverRoot) || !/^[a-f0-9]{64}$/.test(binding)) {
        showSetup('Оболочка не передала сведения о хранилище. Проверьте её версию.');
        return;
      }
      if (realpathSync(serverRoot) !== realpathSync(root)) {
        showSetup('На этом порту открыто другое хранилище. Выберите порт оболочки текущего хранилища.');
        return;
      }
      panel.remove();
      this.origin = origin;
      this.frame = contentEl.createEl('iframe', {
        cls: 'second-brain-gantt-frame',
        attr: {
          title: 'Планы и диаграмма Ганта',
          sandbox: 'allow-scripts allow-same-origin allow-forms allow-modals',
          referrerpolicy: 'no-referrer',
          src: origin + '/?embed=1&binding=' + binding + '#plans'
        }
      });
    } catch {
      if (request === this.generation) showSetup('Не удалось подключиться к рабочей оболочке. Проверьте порт и запущенную версию программы.');
    }
  }

  async onClose() {
    this.generation++;
    this.frame = null;
    this.contentEl.empty();
  }
}

class GanttSettings extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    this.containerEl.empty();
    let port = String(this.plugin.port);
    new Setting(this.containerEl)
      .setName('Порт рабочей оболочки')
      .setDesc('Порт на 127.0.0.1, указанный при запуске оболочки для этого хранилища.')
      .addText(input => input.setValue(port).onChange(value => { port = value.trim(); }))
      .addButton(button => button.setButtonText('Применить').onClick(async () => {
        if (!validPort(port)) {
          new Notice('Введите номер порта от 1 до 65535.');
          return;
        }
        this.plugin.port = Number(port);
        await this.plugin.saveData({ port: this.plugin.port });
        for (const leaf of this.app.workspace.getLeavesOfType(VIEW)) {
          if (leaf.view instanceof GanttView) await leaf.view.connect();
        }
        new Notice('Порт оболочки сохранён.');
      }));
  }
}

module.exports = class SecondBrainGanttPlugin extends Plugin {
  async onload() {
    const saved = await this.loadData();
    this.port = validPort(saved?.port) ? Number(saved.port) : DEFAULT_PORT;
    this.registerView(VIEW, leaf => new GanttView(leaf, this));
    this.addRibbonIcon('gantt-chart', 'Открыть Гант', () => this.activate());
    this.addCommand({ id: 'open', name: 'Открыть Гант', callback: () => this.activate() });
    this.addSettingTab(new GanttSettings(this.app, this));
  }

  async activate() {
    const { workspace } = this.app;
    let [leaf] = workspace.getLeavesOfType(VIEW);
    if (!leaf) {
      leaf = workspace.getLeaf('tab');
      await leaf.setViewState({ type: VIEW, active: true });
    }
    await workspace.revealLeaf(leaf);
    workspace.setActiveLeaf(leaf, { focus: true });
  }
};
