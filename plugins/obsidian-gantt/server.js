const { spawn } = require('child_process');
const { createHmac } = require('crypto');
const { realpathSync, statSync } = require('fs');
const { readFile } = require('fs').promises;
const http = require('http');
const { homedir } = require('os');
const path = require('path');

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function request(port, pathname, options = {}, body) {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: '127.0.0.1', port, path: pathname, agent: false, ...options }, response => {
      let text = '';
      response.setEncoding('utf8');
      response.on('error', reject);
      response.on('data', chunk => {
        text += chunk;
        if (text.length > 16384) req.destroy(new Error('Слишком длинный ответ службы на выбранном порту.'));
      });
      response.on('end', () => {
        try {
          resolve({ status: response.statusCode, data: JSON.parse(text) });
        } catch {
          reject(new Error('На выбранном порту ответила другая служба.'));
        }
      });
    });
    const timer = setTimeout(() => req.destroy(new Error('Служба на выбранном порту не отвечает.')), 1500);
    req.on('error', reject);
    req.on('close', () => clearTimeout(timer));
    req.end(body);
  });
}

class AdminServer {
  constructor({ root, port, programDir, python }) {
    if (!/^\d{1,5}$/.test(String(port)) || Number(port) < 1 || Number(port) > 65535) {
      throw new Error('Введите порт от 1 до 65535.');
    }
    this.root = realpathSync(root);
    this.port = Number(port);
    this.origin = 'http://127.0.0.1:' + this.port;
    this.programDir = programDir;
    this.python = python || (process.platform === 'win32' ? 'python' : 'python3');
    this.child = null;
    this.pending = Promise.resolve();
    this.disposed = false;
  }

  async status() {
    const { origin } = this;
    try {
      const { status, data } = await request(this.port, '/api/planning-vault');
      if (status !== 200 || typeof data?.root !== 'string' || !path.isAbsolute(data.root) ||
          !/^[a-f0-9]{64}$/.test(data.binding)) {
        throw new Error('На выбранном порту работает другая служба или деморежим.');
      }
      if (path.relative(this.root, realpathSync(data.root)) !== '') {
        throw new Error('На выбранном порту открыто другое хранилище.');
      }
      const workControl = typeof data.workControl === 'string' && /^[a-f0-9]{32}$/.test(data.workControl)
        ? data.workControl : null;
      const owned = this.child?.ready && (!this.child.instance || this.child.instance === workControl);
      return { state: owned ? 'running' : 'external', origin, binding: data.binding,
        workspaceEmbed: data.workspaceEmbed === true, workControl };
    } catch (error) {
      if (error.code === 'ECONNREFUSED') return { state: 'stopped', origin };
      return { state: 'conflict', origin, message: error.message };
    }
  }

  enqueue(action) {
    const next = this.pending.then(() => {
      if (this.disposed) throw new Error('Плагин отключён.');
      return action.call(this);
    });
    this.pending = next.catch(() => {});
    return next;
  }

  start() { return this.enqueue(this.startServer); }
  stop() { return this.enqueue(this.stopServer); }

  async startServer() {
    const before = await this.status();
    if (before.state === 'conflict') throw new Error(before.message);
    if (before.state !== 'stopped') return before;
    if (!this.programDir || !path.isAbsolute(this.programDir) ||
        !statSync(path.join(this.programDir, 'second_brain', '__main__.py'), { throwIfNoEntry: false })?.isFile()) {
      throw new Error('В настройках плагина укажите полный путь к папке программы, где находится second_brain.');
    }
    if (this.disposed) throw new Error('Плагин отключён.');
    const args = ['-m', 'second_brain', '--vault', this.root, 'work', '--port', String(this.port)];
    const child = spawn(this.python, args, { cwd: this.programDir, windowsHide: true,
      env: { ...process.env, SECOND_BRAIN_PARENT_STDIN: '1' }, shell: false, stdio: ['pipe', 'pipe', 'pipe'] });
    const run = { child, ready: false, errors: '', ended: false };
    this.child = run;
    let output = '';
    child.stdout.setEncoding('utf8').on('data', chunk => {
      output = (output + chunk).slice(-4096);
      if (output.split(/\r?\n/).includes(this.origin + '/')) run.ready = true;
    });
    child.stderr.setEncoding('utf8').on('data', chunk => { run.errors = (run.errors + chunk).slice(-2000); });
    child.on('error', error => {
      run.errors = 'Не удалось запустить Python. Проверьте путь к интерпретатору в настройках. ' + error.message;
    });
    run.closed = new Promise(resolve => child.once('close', () => {
      run.ended = true;
      if (this.child === run) this.child = null;
      resolve();
    }));
    try {
      const deadline = Date.now() + 15000;
      while (!this.disposed && !run.ended && !run.ready && Date.now() < deadline) await pause(100);
      if (this.disposed) throw new Error('Плагин отключён.');
      if (run.ready || run.ended) {
        const current = await this.status();
        if (current.state === 'running' || current.state === 'external') {
          run.instance = current.workControl;
          run.parentPipe = current.workspaceEmbed;
          return current;
        }
        if (current.state === 'conflict') throw new Error(current.message);
      }
      throw new Error(run.errors.trim() || 'Админка не подтвердила запуск. Проверьте папку программы и Python.');
    } catch (error) {
      await this.terminateChild(run);
      throw error;
    }
  }

  async terminateChild(run) {
    if (run.ended) return;
    // A closed parent pipe also reaches Python if Obsidian exits before an HTTP request can finish.
    run.child.stdin.end();
    if (run.parentPipe) return run.closed;
    run.child.kill(process.platform === 'win32' ? 'SIGTERM' : 'SIGINT');
    const end = Date.now() + 2500;
    while (!run.ended && Date.now() < end) await pause(80);
    if (!run.ended) {
      run.child.kill('SIGKILL');
      await run.closed;
    }
  }

  async stopServer() {
    const current = await this.status();
    if (current.state === 'conflict') throw new Error(current.message);
    if (current.state === 'stopped') return current;
    const { workControl, binding } = current;
    if (!workControl) {
      if (current.state === 'external') {
        return { ...current, message: 'Эту версию админки можно остановить в терминале, где её запустили, сочетанием Ctrl+C.' };
      }
      await this.terminateChild(this.child);
      return this.status();
    }
    let key;
    try {
      key = (await readFile(path.join(homedir(), '.second-brain', 'run', workControl + '.key'), 'utf8')).trim();
      if (!/^[a-f0-9]{64}$/.test(key)) throw new Error();
    } catch {
      if (current.state === 'running' && this.child?.instance === workControl) {
        await this.terminateChild(this.child);
        return this.status();
      }
      throw new Error('Не удалось прочитать ключ этого экземпляра админки. Остановите её в исходном терминале сочетанием Ctrl+C.');
    }
    const proof = createHmac('sha256', Buffer.from(key, 'hex'))
      .update('stop:' + workControl + ':' + this.port + ':' + binding).digest('hex');
    const response = await request(this.port, '/api/work/stop', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Brain-Vault': binding }
    }, JSON.stringify({ instance: workControl, proof }));
    if (response.status !== 200 || response.data?.stopping !== true) {
      throw new Error('Админка отклонила остановку. Обновите состояние подключения.');
    }
    const end = Date.now() + 10000;
    while (Date.now() < end) {
      await pause(100);
      const after = await this.status();
      if (after.state === 'stopped') return after;
      if (after.workControl && after.workControl !== workControl) return after;
    }
    throw new Error('Админка ещё завершает работу. Обновите состояние подключения.');
  }

  dispose() {
    this.disposed = true;
    if (this.child) return this.terminateChild(this.child).catch(() => {});
  }
}

module.exports = { AdminServer };
