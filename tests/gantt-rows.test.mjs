import assert from "node:assert/strict";
import test from "node:test";
import {describeRow, recordedSpan, selectionParams} from "../second_brain/web/gantt-rows.js";

const plan = {
  id:"period", title:"План", items:[
    {id:"a", project:"one", projectTitle:"Первый", direction:"area", directionTitle:"Направление", start:"2026-11-01"},
    {id:"b", kind:"milestone", start:"2025-01-01", forecast:"2026-11-30"}
  ],
  tree:[
    {key:"objective/g", kind:"objective", id:"g", title:"Цель", readiness:51},
    {key:"result/r", kind:"result", id:"r", title:"КР", parentKey:"objective/g", readiness:"42.5", hasEstimates:true},
    {key:"r/task/b", kind:"task", id:"b", resultId:"r", parentKey:"result/r", contextOnly:true},
    {key:"r/task/a", kind:"task", id:"a", resultId:"r", parentKey:"r/task/b"},
    {key:"outside", kind:"outside", title:"Другие работы плана"},
    {key:"outside/task/b", kind:"task", id:"b", resultId:null, parentKey:"outside"}
  ]
};

test("group journals identify the whole plan, each OKR and their own member tasks", () => {
  for (const [key, kind, id, members] of [
    ["plan/period","plan","period",["a","b"]],
    ["objective/g","objective","g",["a"]], ["result/r","key_result","r",["a"]],
    ["group/outside","group","outside",["b"]], ["group/project/one","group","project/one",["a"]],
    ["group/project/","group","project/",["b"]], ["group/direction/area","group","direction/area",["a"]]
  ]) {
    const group = describeRow(plan, key);
    assert.deepEqual([group.kind, group.id, group.rows.map(row => row.id)], [kind, id, members]);
  }
  assert.equal(describeRow(plan, "group/project/missing"), null);
  assert.equal(describeRow(plan, "result/missing"), null);
  assert.equal(describeRow(plan, "project/period"), null);
  assert.equal(describeRow({...plan, scope:"project"}, "project/period").kind, "project");
  assert.deepEqual(recordedSpan(plan.items), {start:"2026-11-01",end:"2026-11-30"});
});

test("group deep links and existing task links preserve opaque IDs and a separate OKR filter", () => {
  assert.deepEqual([...selectionParams("group/project/","goal",true)], [["row","group/project/"],["okr","goal"]]);
  assert.deepEqual([...selectionParams("record/alias","goal")], [["task","record/alias"],["okr","goal"]]);
  assert.equal(selectionParams(null).toString(), "");
});
