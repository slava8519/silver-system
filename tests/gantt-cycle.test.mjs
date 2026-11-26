import assert from "node:assert/strict";
import test from "node:test";
import {archiveStamp} from "../second_brain/web/gantt-cycle.js";
import {dateText} from "../second_brain/web/gantt-axis.js";
import {spawnSync} from "node:child_process";

test("Historical chart marker uses the displayed local closure day across midnight", () => {
  const probe = `
    import {closedDay, closedStamp} from "./second_brain/web/gantt-cycle.js";
    import {isoDay} from "./second_brain/web/gantt-axis.js";
    const at = "2026-11-23T22:15:00+00:00";
    console.log(JSON.stringify([isoDay(closedDay(at)), closedStamp(at)]));
  `;
  for (const [tz, day] of [["Europe/Minsk", "24"], ["America/New_York", "23"]]) {
    const check = spawnSync(process.execPath, ["--input-type=module", "-e", probe], {
      cwd: new URL("../", import.meta.url), env: {...process.env, TZ: tz}, encoding: "utf8"
    });
    assert.equal(check.status, 0, check.stderr);
    const [date, label] = JSON.parse(check.stdout);
    assert.equal(date, "2026-11-" + day);
    assert.ok(label.startsWith(day + " "));
  }
});

test("archived quarter distinguishes source closure, export and pilot capture", () => {
  const closedAt = "2026-11-26T22:15:00+00:00";
  assert.equal(archiveStamp({closedAt}), null);
  const archiveDates = {closedOn:null, exportedOn:"2025-06-20"};
  assert.deepEqual(archiveStamp({closedAt, archiveDates}), {
    text:"Архивный снимок · выгрузка " + dateText("2025-06-20"), date:"2025-06-20", label:"Выгрузка"
  });
  const closed = archiveStamp({closedAt, archiveDates:{...archiveDates, closedOn:"2025-06-18"}});
  assert.equal(closed.date, "2025-06-18");
  assert.equal(closed.label, "Закрытие");
});
