import assert from "node:assert/strict";
import test from "node:test";
import {readLayers, saveLayers} from "../second_brain/web/gantt-layers.js";

test("Gantt preferences default to bars and today and preserve even an empty selection", t => {
  const data = new Map();
  const previous = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const storage = {getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value)};
  Object.defineProperty(globalThis, "localStorage", {configurable:true, value:storage});
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "localStorage", previous);
    else delete globalThis.localStorage;
  });
  assert.deepEqual([...readLayers()], ["range", "now"]);
  saveLayers(new Set(["forecast", "relations"]));
  assert.deepEqual([...readLayers()], ["forecast", "relations"]);
  saveLayers(new Set());
  assert.deepEqual([...readLayers()], []);
  const [key] = data.keys();
  data.set(key, '["unknown-layer","due"]');
  assert.deepEqual([...readLayers()], ["due"]);
  data.set(key, "{");
  assert.deepEqual([...readLayers()], ["range", "now"]);
  storage.getItem = storage.setItem = () => { throw Error("Storage unavailable"); };
  assert.deepEqual([...readLayers()], ["range", "now"]);
  assert.doesNotThrow(() => saveLayers(new Set(["baseline"])));
});
