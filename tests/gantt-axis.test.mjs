import assert from "node:assert/strict";
import test from "node:test";
import {dayOf, fitWindow, isoDay, plot, scheduleMarks, ticks} from "../second_brain/web/gantt-axis.js";

const november = {start: dayOf("2026-11-01"), size: 30};
test("Milestones are points even when a card contains an old start", () => {
  const row = {kind: "milestone", start: "2026-01-01", due: "2026-11-15", forecast: "2026-11-18"};
  assert.deepEqual(scheduleMarks(row, november).map(mark => mark.kind), ["due", "forecast"]);
  assert.deepEqual(fitWindow([row], november.start), fitWindow([{due: row.due, forecast: row.forecast}], november.start));
});
test("A deadline alone is a point, while a duration requires two recorded dates", () => {
  assert.deepEqual(plot({}, november), {kind: "undated"});
  assert.deepEqual(plot({due: "2026-11-15"}, november), {kind: "due", at: 14.5/30});
  assert.deepEqual(plot({start: "2026-11-15"}, november), {kind: "start", at: 14.5/30});
  assert.deepEqual(plot({start: "2026-11-15", due: "2026-11-15"}, november),
    {kind: "range", at: 14/30, end: 15/30, before: false, after: false});
});
test("The viewport clips spans and keeps out-of-window tasks in the grid", () => {
  assert.equal(plot({due: "2026-10-31"}, november).kind, "earlier");
  assert.equal(plot({start: "2026-12-01"}, november).kind, "later");
  assert.equal(plot({start: "2026-11-03", due: "2026-11-01"}, november).kind, "invalid");
  assert.deepEqual(plot({start: "2026-10-30", due: "2027-01-01"}, november),
    {kind: "range", at: 0, end: 1, before: true, after: true});
});
test("Fit uses recorded dates and only uses today as the fallback for an empty timeline", () => {
  const empty = fitWindow([{}], dayOf("2026-11-10"));
  assert.equal(isoDay(empty.start), "2026-11-08");
  const full = fitWindow([{due:"2026-11-11"}, {start:"2026-12-11"}], dayOf("2001-01-01"));
  assert.ok(full.start < dayOf("2026-11-11") && full.start + full.size > dayOf("2026-12-11"));
  assert.ok(ticks(full).length <= 10);
});

test("Moving a forecast leaves the planned span and deadline in place", () => {
  const agreed = {start:"2026-11-09", due:"2026-11-15"};
  const baseline = scheduleMarks(agreed, november);
  const moved = scheduleMarks({...agreed, forecast:"2026-11-19"}, november);
  assert.deepEqual(moved, [...baseline, {kind:"forecast", at:18.5/30}]);
  assert.equal(baseline[1].kind, "due");
  assert.equal(baseline[1].at, 14.5/30);
  const exact = scheduleMarks({...agreed, forecast:agreed.due, completed:agreed.due}, november);
  assert.deepEqual(exact.slice(1).map(mark => mark.kind), ["due","forecast","completed"]);
  assert.ok(exact.slice(1).every(mark => mark.at === 14.5/30));
});
test("A forecast or completion can stand alone and extend the fitted window", () => {
  assert.deepEqual(scheduleMarks({forecast:"2026-11-20"}, november), [{kind:"forecast", at:19.5/30}]);
  assert.deepEqual(scheduleMarks({completed:"2026-11-14"}, november), [{kind:"completed", at:13.5/30}]);
  const row = {forecast:"2026-10-01", completed:"2026-12-02"};
  assert.deepEqual(scheduleMarks(row, november), [{kind:"outside"}]);
  const fitted = fitWindow([row], dayOf("2020-03-08"));
  assert.deepEqual(scheduleMarks(row, fitted).map(mark=>mark.kind), ["forecast","completed"]);
  assert.deepEqual(scheduleMarks({forecast:"2026-02-30"}, november), [{kind:"invalid"}]);
});
