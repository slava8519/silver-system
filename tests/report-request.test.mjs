import assert from "node:assert/strict";
import test from "node:test";
import {reportRequestText} from "../second_brain/web/reports.js";

const vault = {root: "/Volumes/Мои записи"};

test("a Codex request carries the chosen format, exact revision and separate subject", () => {
  for (const [id, subject] of [["weekly-direction", "Дом"], ["one-on-one", "Анна"], ["topic-agenda", "Выбор пробы"]]) {
    const template = {id, title: "Шаблон владельца", version: 2};
    const text = reportRequestText(vault, {template, title: "К встрече", start: "2026-11-16", end: "2026-11-22", asOf: "2026-11-23"}, "  " + subject + "  ");
    const lines = text.split("\n");
    for (const expected of ["Название выпуска: К встрече", "Шаблон «Шаблон владельца», ID " + id + ", версия 2.",
      "Тема, направление или участник: " + subject, "Период: 2026-11-16 — 2026-11-22.", "Данные на 2026-11-23.", vault.root]) {
      assert.ok(lines.includes(expected), expected);
    }
  }
});

test("the archive request works without a chosen format or subject", () => {
  const text = reportRequestText(vault, {}, "  ");
  assert.doesNotMatch(text, /Название выпуска:|Шаблон «|Тема, направление или участник:|Период:|Данные на /);
  assert.equal(text.split("\n").at(-1), vault.root);
});
