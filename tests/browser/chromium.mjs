import {readFile} from "node:fs/promises";
import {setTimeout as delay} from "node:timers/promises";

if (typeof WebSocket !== "function") process.exit(77);

async function connect(portFile) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    try {
      const [port] = (await readFile(portFile, "utf8")).split("\n");
      const pages = await (await fetch("http://127.0.0.1:" + port + "/json/list")).json();
      const page = pages.find(item => item.type === "page");
      if (page) {
        const ws = new WebSocket(page.webSocketDebuggerUrl);
        await new Promise((resolve, reject) => {
          ws.onopen = resolve;
          ws.onerror = reject;
        });
        return ws;
      }
    } catch {
      // Chromium creates the port file before it starts accepting connections.
    }
    await delay(40);
  }
  throw Error("Chromium did not start its local debugging endpoint.");
}

export async function connectPage(portFile) {
  const ws = await connect(portFile);
  const diagnostics = [];
  const pending = new Map();
  let nextId = 0;
  ws.onmessage = ({data}) => {
    const {id, result, error, method, params} = JSON.parse(data);
    if (pending.has(id)) {
      const {resolve, reject} = pending.get(id);
      pending.delete(id);
      if (error) reject(Error(error.message));
      else resolve(result);
    } else if (method === "Runtime.exceptionThrown") {
      diagnostics.push(params.exceptionDetails.exception?.description || params.exceptionDetails.text);
    } else if (method === "Log.entryAdded" && params.entry.level === "error") {
      diagnostics.push([params.entry.url, params.entry.text].filter(Boolean).join(" "));
    }
  };
  function command(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++nextId;
      pending.set(id, {resolve, reject});
      ws.send(JSON.stringify({id, method, params}));
    });
  }
  await command("Runtime.enable");
  await command("Log.enable");
  async function close() {
    if (ws.readyState === WebSocket.OPEN) {
      // Let Chrome flush its temporary profile before unittest removes the directory.
      const closed = new Promise(resolve => ws.addEventListener("close", resolve, {once:true}));
      ws.send(JSON.stringify({id:++nextId, method:"Browser.close"}));
      await Promise.race([closed, delay(3000, undefined, {ref:false})]);
      ws.close();
    }
  }
  return {command, diagnostics, close};
}
