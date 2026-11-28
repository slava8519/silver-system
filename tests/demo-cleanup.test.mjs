import assert from "node:assert/strict";
import test from "node:test";
import {cleanupNotice} from "../second_brain/web/demo-cleanup.js";

test("the workspace receipt shows actual counts after keeping some or all examples", () => {
  const node = (_tag, _class, text = "") => ({
    text, setAttribute() {}, append(...children) { this.text += children.map(el => el.text).join(""); }
  });
  const location = new URL("http://localhost/#overview");
  globalThis.location = location;
  globalThis.history = {replaceState(state) { this.state = state; }};
  try {
    for (const removed of [0, 6]) {
      history.state = {cleanupReceipt:{archive:"Archive/DemoCleanup/test.zip", removeCount:removed, keepCount:8 - removed}};
      const receipt = cleanupNotice(node).text;
      assert.ok(receipt.includes("Учебных файлов в архиве — " + removed));
      assert.ok(receipt.includes("Осталось в хранилище — " + (8 - removed)));
      assert.equal(receipt.includes("test.zip"), removed > 0);
      assert.equal(cleanupNotice(node), null);
    }
  } finally {
    delete globalThis.location;
    delete globalThis.history;
  }
});
