import assert from "node:assert/strict";
import test from "node:test";
import {treeRows, branchDates, initialFolds, kindPrefix} from "../second_brain/web/gantt-tree.js";

test("the initial OKR overview reveals the linked task in its own branch", () => {
  const plan = {objectives:[{id:"goal", results:[]}], tree:[
    {key:"r1", kind:"result"}, {key:"shared", kind:"task", id:"t", parentKey:"r1", contextOnly:true},
    {key:"r2", kind:"result"}, {key:"parent", kind:"task", id:"p", parentKey:"r2"},
    {key:"child", kind:"task", id:"t", parentKey:"parent"}, {key:"outside", kind:"outside"}
  ]};
  assert.deepEqual([...initialFolds(plan, "", null)], ["r1", "r2", "outside"]);
  assert.deepEqual([...initialFolds(plan, "", "t")], ["r1", "outside"]);
  assert.deepEqual([...initialFolds(plan, "r1", null)], []);
  assert.deepEqual([...initialFolds({...plan, objectives:[]}, "", null)], []);
});

test("shared work has one bar; its second branch remains visible after filtering or folding", () => {
  const tree = [{key:"g", kind:"objective", id:"g"}];
  for (const id of ["r", "s"]) {
    tree.push({key:id, id, kind:"result", parentKey:"g", resultId:id});
    tree.push({key:id+"/a", id:"a", kind:"task", parentKey:id, resultId:id});
    tree.push({key:id+"/b", id:"b", kind:"task", parentKey:id+"/a", resultId:id});
  }
  const plan = {tree, objectives:[{id:"g",results:["r","s"].map(id => ({id, taskIds:["a"]}))}],
    items:[{id:"a", start:"2026-11-20"}, {id:"b", forecast:"2027-01-10"}]};
  const all = treeRows(plan, "", new Set());
  assert.equal(all.filter(e => e.row && !e.reference).length, 2);
  assert.equal(all.filter(e => e.reference).length, 2);
  assert.deepEqual(branchDates(all, ["start","forecast"]).get("g"), {start:"2026-11-20", end:"2027-01-10"});
  assert.equal(treeRows(plan, "", new Set(["r"])).find(e => e.key === "s/a").reference, false);
  assert.deepEqual(treeRows(plan, "s", new Set()).map(e => e.key), ["g", "s", "s/a", "s/b"]);
  assert.deepEqual(treeRows(plan, "unlinked", new Set()).map(e => e.id), ["b"]);
});

test("another result's parent does not widen the branch schedule", () => {
  const plan = {
    items: [{id:"a", due:"2027-07-30"}, {id:"b", due:"2026-11-28"}],
    objectives: [{id:"goal", results:[{id:"kr", taskIds:["b"]}]}],
    tree: [
      {key:"kr", id:"kr", kind:"result", resultId:"kr"},
      {key:"parent", id:"a", kind:"task", resultId:"kr", parentKey:"kr", contextOnly:true},
      {key:"child", id:"b", kind:"task", resultId:"kr", parentKey:"parent"}
    ]
  };
  const rows = treeRows(plan, "kr", new Set());
  assert.equal(rows.find(row => row.key === "parent").reference, true);
  assert.equal(rows.find(row => row.key === "child").reference, false);
  assert.deepEqual(branchDates(rows, ["due"]).get("kr"), {start:"2026-11-28", end:"2026-11-28"});
});

test("Imported OKR titles remain intact without repeating their kind", () => {
  assert.equal(kindPrefix("КР", "КР 2.1 · Smoke"), "");
  assert.equal(kindPrefix("КР", "Ключевой результат 2 — Smoke"), "");
  assert.equal(kindPrefix("Цель", "Цель 2 · Инфраструктура"), "");
  assert.equal(kindPrefix("КР", "Критерии для запуска"), "КР");
  assert.equal(kindPrefix("Цель", "Готовность к сезону"), "Цель");
});
