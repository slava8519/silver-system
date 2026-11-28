import assert from "node:assert/strict";
import test from "node:test";
import {renderOverviewFolders} from "../second_brain/web/overview-folders.js";

function node(tag, className, textContent) {
  return {tag, className, textContent, children: [], open: false,
    append(...children) { this.children.push(...children); }};
}

test("the open folder panel contains top-level rows and nested closed disclosures", () => {
  const counts = [];
  const counted = (value, forms) => {
    counts.push([value, ...forms]);
    return String(value);
  };
  const folders = [
    {path: "Sources/Originals/Audio", files: 12, count: 0},
    {path: "Notes", files: 22, count: 21},
    {path: "Sources", files: 12, count: 0},
    {path: "Sources/Originals", files: 12, count: 0}
  ];
  const panel = renderOverviewFolders({node, counted}, folders);
  assert.equal(panel.open, true);
  assert.equal(panel.children[0].tag, "summary");
  const root = panel.children[2];
  assert.equal(root.children.length, 2);
  const note = root.children[0].children[0];
  assert.equal(note.tag, "div");
  assert.equal(note.children[0].textContent, "Notes");
  assert.equal(note.children[1].textContent, "22 · 21");
  assert.deepEqual(counts.slice(0, 2), [
    [22, "файл", "файла", "файлов"], [21, "карточка", "карточки", "карточек"]
  ]);
  const sources = root.children[1].children[0];
  const originals = sources.children[1].children[0].children[0];
  for (const [details, label] of [[sources, "Sources"], [originals, "Originals"]]) {
    assert.equal(details.tag, "details");
    assert.equal(details.open, false);
    assert.equal(details.children[0].tag, "summary");
    assert.equal(details.children[0].children[0].textContent, label);
    assert.equal(details.children[0].children[1].textContent, "12 · 0");
  }
  const audio = originals.children[1].children[0].children[0];
  assert.equal(audio.tag, "div");
  assert.equal(audio.children[0].textContent, "Audio");
  assert.equal(audio.children[0].title, "Sources/Originals/Audio");
});
