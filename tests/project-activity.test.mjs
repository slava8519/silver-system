import assert from "node:assert/strict";
import test from "node:test";
import {dateText} from "../second_brain/web/gantt-axis.js";
import {catalogRows, projectDates} from "../second_brain/web/project-activity.js";
import {eligibleWork, relationshipFacts} from "../second_brain/web/project-participation.js";

test("project participation, selected work and KR context have distinct descriptions", () => {
  const counted = n => n + " работы";
  const contextOnly = {contextResultCount:1, taskCount:0};
  assert.deepEqual(relationshipFacts(contextOnly, counted), ["Контекст для 1 КР"]);
  assert.deepEqual(relationshipFacts({explicitlyIncluded:true, taskCount:0}, counted),
    ["Проект включён в план", "Работы пока не выбраны"]);
  assert.deepEqual(relationshipFacts({...contextOnly, taskCount:2, okrTaskCount:1}, counted),
    ["2 работы в плане", "Из них в КР — 1", "Контекст для 1 КР"]);
});

test("the work picker excludes all project work already visible on the plan chart", () => {
  const project = [{id:"direct"}, {id:"from-kr"}, {id:"available"}];
  const plan = [{id:"other-project"}, {id:"direct"}, {id:"from-kr"}];
  assert.deepEqual(eligibleWork(project, plan), [{id:"available"}]);
  assert.deepEqual(eligibleWork(project, []), project);
});

test("OKR filters plans by objectives or key results regardless of their period", () => {
  const annual = {id:"year", objectiveCount:1, resultCount:0};
  const quarter = {id:"quarter", objectiveCount:0, resultCount:1};
  const workPlan = {id:"work", objectiveCount:0, resultCount:0};
  const rows = {projects:[{id:"project"}], plans:[annual, quarter, workPlan], unassigned:[{id:"old-kr"}]};
  assert.deepEqual(catalogRows("okr", rows), {plans:[annual, quarter]});
  assert.deepEqual(catalogRows("plans", rows), {plans:[annual, quarter, workPlan]});
  assert.deepEqual(catalogRows("projects", rows), {projects:rows.projects});
});

test("key results outside plans stay accessible without contributing to a filtered plan count", () => {
  const rows = {projects:[], plans:[{id:"old-plan"}], unassigned:Array.from({length:13}, (_, id) => ({id}))};
  assert.deepEqual(catalogRows("okr", rows), {plans:[]});
  assert.deepEqual(catalogRows("unassigned", rows), {unassigned:rows.unassigned});
  assert.deepEqual(catalogRows("all", rows), rows);
});

test("agreed project period stays distinct from the shorter work schedule", () => {
  const dates = {projectStart:"2026-01-01",due:"2026-12-31",start:"2026-02-01",end:"2026-03-01"};
  assert.deepEqual(projectDates(dates), [
    "Период проекта · " + dateText(dates.projectStart) + " — " + dateText(dates.due),
    "Даты работ · " + dateText(dates.start) + " — " + dateText(dates.end)
  ]);
  assert.equal(projectDates({...dates,start:null,end:null}).length,1);
});

test("partial dates display their source and an empty project acquires no horizon", () => {
  assert.deepEqual(projectDates({}), []);
  assert.deepEqual(projectDates({projectStart:"2026-01-01"}), ["Начало проекта · " + dateText("2026-01-01")]);
  assert.deepEqual(projectDates({due:"после решения комиссии"}), ["Срок проекта · после решения комиссии"]);
  assert.deepEqual(projectDates({start:"2026-02-01",end:"2026-02-01"}), ["Даты работ · " + dateText("2026-02-01")]);
});
