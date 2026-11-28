import assert from "node:assert/strict";
import test from "node:test";
import {fitWindow, scheduleMarks, todayDay} from "../second_brain/web/gantt-axis.js";
import {readLayers, saveLayers} from "../second_brain/web/gantt-layers.js";

test("due-only tasks and milestones are visible on first open and respect saved preferences", t => {
  const data = new Map();
  const previous = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const storage = {getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value)};
  Object.defineProperty(globalThis, "localStorage", {configurable:true, value:storage});
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "localStorage", previous);
    else delete globalThis.localStorage;
  });
  const defaultLayers = ["range", "now", "due"];
  assert.deepEqual([...readLayers()], defaultLayers);
  const rows = ["task", "milestone"].map(kind => ({kind, due: "2026-11-27"}));
  const window = fitWindow(rows, todayDay());
  const visible = row => scheduleMarks(row, window).map(mark => mark.kind).filter(kind => readLayers().has(kind));
  for (const row of rows) assert.deepEqual(visible(row), ["due"]);
  saveLayers(new Set(["range", "now"]));
  for (const row of rows) assert.deepEqual(visible(row), []);
  saveLayers(new Set(["forecast", "relations"]));
  assert.deepEqual([...readLayers()], ["forecast", "relations"]);
  saveLayers(new Set());
  assert.deepEqual([...readLayers()], []);
  const [key] = data.keys();
  data.set(key, '["unknown-layer","due"]');
  assert.deepEqual([...readLayers()], ["due"]);
  data.set(key, "{");
  assert.deepEqual([...readLayers()], defaultLayers);
  storage.getItem = storage.setItem = () => { throw Error("Storage unavailable"); };
  assert.deepEqual([...readLayers()], defaultLayers);
  assert.doesNotThrow(() => saveLayers(new Set(["baseline"])));
});
