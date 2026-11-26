import test from "node:test";
import assert from "node:assert/strict";
import {changedLines} from "../second_brain/web/report-template-changes.js";

test("template comparison isolates separate editorial changes and preserves list indentation", () => {
  const old = "## Встреча\r\n- Факт A\r\n  - Уточнение\r\n- Планы\r\n- Ждём решения";
  const next = "## Встреча\n- Итог A\n  - Уточнение\n- Планы\n  - Подтвердить срок";
  assert.deepEqual(changedLines(old, next), [
    {before: ["- Факт A"], after: ["- Итог A"]}, {before: ["- Ждём решения"], after: ["  - Подтвердить срок"]}
  ]);
});

test("comparison handles empty fields, unchanged versions and long wholesale replacements", () => {
  assert.deepEqual(changedLines("", "Для участников"), [{before: [], after: ["Для участников"]}]);
  assert.deepEqual(changedLines("A\r\nB", "A\nB"), []);
  const old = Array(900).fill("старая строка"), next = Array(900).fill("новая строка");
  assert.deepEqual(changedLines(old.join("\n"), next.join("\n")), [{before: old, after: next}]);
});
