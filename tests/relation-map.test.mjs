import assert from "node:assert/strict";
import test from "node:test";
import {relationCardURL, relationSourceLabel} from "../second_brain/web/relation-map.js";

test("direction, project and note open their workspace cards and plans open their Gantt", () => {
  for (const type of ["direction", "project", "note"]) {
    assert.equal(relationCardURL({id:"A/B", type}), "#record/A%2FB");
  }
  assert.equal(relationCardURL({id:"week", type:"plan", status:"closed"}), "#plan/week");
});

test("a task opens selected in its project, or in its active plan", () => {
  const task = {id:"task+2", type:"task", planId:"week"};
  assert.equal(relationCardURL({...task, projectId:"mayak"}), "#project-gantt/mayak?task=task%2B2");
  assert.equal(relationCardURL(task), "#plan/week?task=task%2B2");
});

test("a standalone task opens its own Markdown in the selected vault", () => {
  const root = "/Volumes/Мои записи";
  const task = {id:"task", type:"task", path:"Tasks/А & Б #1.md"};
  const url = new URL(relationCardURL(task, root));
  assert.equal(url.protocol, "obsidian:");
  assert.equal(url.host, "open");
  assert.equal(url.searchParams.get("path"), root + "/" + task.path);
});

test("a damaged closed plan opens the Markdown file for inspection", () => {
  const href = relationCardURL({id:"plan", type:"plan", path:"Plans/План.md", status:"unavailable"}, "/vault");
  assert.equal(new URL(href).searchParams.get("path"), "/vault/Plans/План.md");
});

test("plan membership evidence exposes an escaped link label independently of a long encoded destination", () => {
  const link = String.raw`- [Проба \[3\] &amp; &lt;кедр&gt;](../Tasks/%D0%9F.md) <!-- task-id: abc -->`;
  assert.equal(relationSourceLabel(link), "Проба [3] & <кедр>");
  assert.equal(relationSourceLabel('direction: "abc"'), "");
});
