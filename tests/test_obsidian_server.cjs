const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mkdtemp, mkdir, rm } = require('node:fs/promises');
const { createServer } = require('node:http');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { AdminServer } = require('../plugins/obsidian-gantt/server');

const programDir = path.resolve(__dirname, '..');

async function localPort() {
  const server = createServer();
  const port = await listen(server);
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  return server.address().port;
}

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), "Хранилище с пробелом ' "));
  await mkdir(path.join(root, 'Projects'));
  const options = { root, port: await localPort(), programDir };
  const server = new AdminServer(options);
  t.after(async () => {
    try { await server.stop(); } finally {
      server.dispose();
      await rm(root, { force: true, recursive: true });
    }
  });
  return { server, options };
}

test('duplicate starts reuse one server and stopping releases the port for a restart', async t => {
  const { server, options } = await fixture(t);
  assert.equal((await server.status()).state, 'stopped');
  const [first, duplicate] = await Promise.all([server.start(), server.start()]);
  assert.equal(first.state, 'running');
  assert.equal(first.origin, 'http://127.0.0.1:' + options.port);
  assert.deepEqual(duplicate, first);
  assert.equal((await server.stop()).state, 'stopped');
  const restarted = await server.start();
  assert.equal(restarted.state, 'running');
  if (first.workControl) assert.notEqual(restarted.workControl, first.workControl);
});

test('a matching external server can be reused without local program settings', async t => {
  const { server, options } = await fixture(t);
  const original = await server.start();
  const visitor = new AdminServer({ ...options, programDir: '' });
  const reused = await visitor.start();
  assert.equal(reused.state, 'external');
  assert.equal(reused.binding, original.binding);
  visitor.dispose();
  assert.equal((await server.status()).state, 'running');
  const stopper = new AdminServer(options);
  assert.equal((await stopper.stop()).state, 'stopped');
  assert.equal((await server.status()).state, 'stopped');
});

test('unloading the owner closes the child process and releasing a pending launch leaves it stopped', async t => {
  const { options, server: observer } = await fixture(t);
  const owner = new AdminServer(options);
  await owner.start();
  await owner.dispose();
  assert.equal((await observer.status()).state, 'stopped');
  const opening = new AdminServer(options);
  const pending = opening.start();
  opening.dispose();
  await assert.rejects(pending, /отключён/);
  assert.equal((await observer.status()).state, 'stopped');
});

test('validating the vault refuses both open and stop on another vault', async t => {
  const { server, options } = await fixture(t);
  await server.start();
  const other = new AdminServer({ ...options, root: programDir });
  assert.equal((await other.status()).state, 'conflict');
  await assert.rejects(other.start(), /другое хранилище/);
  await assert.rejects(other.stop(), /другое хранилище/);
  assert.equal((await server.status()).state, 'running');
});

test('other services receive no stop requests and legacy external servers stay accessible', async t => {
  const { options } = await fixture(t);
  const stops = [];
  let data = { service: 'different' };
  const other = createServer((req, res) => {
    if (req.method === 'POST') stops.push(req.url);
    res.end(JSON.stringify(data));
  });
  const port = await listen(other);
  t.after(() => new Promise(resolve => other.close(resolve)));
  const viewer = new AdminServer({ ...options, port });
  assert.equal((await viewer.status()).state, 'conflict');
  await assert.rejects(viewer.stop(), /другая служба/);
  data = { root: options.root, binding: 'a'.repeat(64) };
  const result = await viewer.stop();
  assert.equal(result.state, 'external');
  assert.match(result.message, /Ctrl\+C/);
  assert.deepEqual(stops, []);
});

test('missing program and interpreter report a recoverable startup failure', async t => {
  const { options } = await fixture(t);
  const missingProgram = new AdminServer({ ...options, programDir: options.root });
  await assert.rejects(missingProgram.start(), /папке программы/);
  const missingPython = new AdminServer({ ...options, python: path.join(options.root, 'missing-python') });
  await assert.rejects(missingPython.start(), /путь к интерпретатору/);
  assert.equal((await missingPython.status()).state, 'stopped');
});
