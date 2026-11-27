import assert from "node:assert/strict";
import test from "node:test";
import {calendarYear, dayOf, fitWindow, intervalEnd, isoDay, monthBands, plot, progressText, progressValue, scheduleMarks, scheduleState, tickAlignments, ticks, visibleFill} from "../second_brain/web/gantt-axis.js";

const november = {start: dayOf("2026-11-01"), size: 30};
test("annual scale follows calendar months when stepping into and out of a leap year", () => {
  const leap = calendarYear(dayOf("2027-11-22"), 1);
  assert.equal(isoDay(leap.start), "2028-01-01");
  assert.equal(leap.size, 366);
  assert.equal(ticks(leap).length, 12);
  assert.equal(isoDay(ticks(leap)[2].day), "2028-03-01");
  assert.equal(isoDay(leap.start + leap.size - 1), "2028-12-31");
  assert.equal(calendarYear(leap.start, -1).size, 365);
});
test("quarterly headers clip calendar months at the edges and date ticks start on a Monday", () => {
  const period = {start: dayOf("2027-12-29"), size: 91};
  const bands = monthBands(period);
  assert.deepEqual(bands.map(m => isoDay(m.day)), ["2027-12-29", "2028-01-01", "2028-02-01", "2028-03-01"]);
  assert.deepEqual(bands.map(m => m.width), [3/91, 31/91, 29/91, 28/91]);
  assert.deepEqual(bands.map(m => m.at), [0, 3/91, 34/91, 63/91]);
  const weeks = ticks(period);
  assert.equal(weeks[0].label, "03.01");
  assert.ok(weeks.every((week, i) => week.at === (5 + 7*i)/91));
  for (const size of [36, 42, 112, 113, 183]) {
    assert.ok(ticks({...period, size}).every(tick => new Date(tick.day * 86400000).getUTCDay() === 1));
  }
  for (const window of [november, {...period, size: 730}, calendarYear(period.start)]) {
    assert.deepEqual(monthBands(window), []);
  }
});
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
test("fit can include the project's own due date after the last task", () => {
  const end = "2027-12-31";
  const tasks = [{start:"2027-01-12", due:"2027-04-15"}, {}];
  const span = fitWindow(tasks, november.start, {due:end});
  assert.ok(span.start <= dayOf("2027-01-12") && span.start + span.size > dayOf(end));
  assert.deepEqual(tasks[1], {});
  const wholeYear = fitWindow([], november.start, {projectStart:"2027-01-01", due:end});
  assert.ok(wholeYear.start <= dayOf("2027-01-01") && wholeYear.start + wholeYear.size > dayOf(end));
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

test("Forecast, actual and baseline date produce an interval when no due date is agreed", () => {
  const row = {start:"2026-11-05", baseline:"2026-11-11", forecast:"2026-11-17", completed:"2026-11-15"};
  assert.equal(intervalEnd(row),row.completed);
  const range = scheduleMarks(row, november);
  assert.deepEqual(range.map(mark => mark.kind), ["range","baseline","forecast","completed"]);
  assert.equal(range[0].end, 15/30);
  assert.equal(intervalEnd({...row,completed:null}), row.forecast);
  assert.equal(intervalEnd({...row,completed:null,forecast:null}), row.baseline);
  assert.equal(scheduleMarks({start:row.start,forecast:row.forecast},november)[0].end, 17/30);
  assert.equal(scheduleMarks({start:row.start,baseline:row.baseline},november)[0].end, 11/30);
  assert.equal(scheduleMarks({start:row.start,completed:row.completed},november)[0].end, 15/30);
  assert.deepEqual(scheduleMarks({start:"2026-11-19",baseline:"2026-11-10"},november).map(m => m.kind), ["start","baseline"]);
  assert.equal(scheduleMarks({baseline:"2026-11-10"},november)[0].kind, "baseline");
});

test("Readiness distinguishes zero and unknown, keeps fractional estimates and clips the filled part", () => {
  assert.equal(progressText("66.775"),"66,775%");
  assert.equal(progressText("0"),"0%");
  for (const value of [null,undefined,"",false,Infinity,-1,"101"]) assert.equal(progressValue(value),null);
  const zoom={start:dayOf("2026-11-06"),size:10};
  assert.equal(visibleFill("2026-11-01","2026-11-20",zoom,50),50);
  assert.equal(visibleFill("2026-11-01","2026-11-20",zoom,25),0);
  assert.equal(visibleFill("2026-11-01","2026-11-20",zoom,75),100);
  assert.equal(visibleFill("2026-11-01","2026-11-20",zoom,null),null);
  assert.equal(visibleFill("2026-11-10","2026-11-10",zoom,100),100);
});

test("Overdue calendar dates, a future slip, and terminal work have different meanings", () => {
  const review = dayOf("2026-11-27");
  const skipped = {baseline:"2026-10-30", forecast:"2026-11-26", readiness:{kind:"estimate",value:"100"}};
  const postponed = {baseline:"2026-11-06", forecast:"2026-12-02"};
  assert.equal(scheduleState(skipped,review).kind,"overdue");
  assert.equal(scheduleState(postponed,review).kind,"delay");
  assert.equal(scheduleState(skipped,dayOf("2026-11-26")).kind,"delay");
  assert.equal(scheduleState({due:"2026-11-26",forecast:"2026-12-02"},review).kind,"overdue");
  assert.equal(scheduleState({baseline:"2026-11-26"},review).kind,"");
  assert.equal(scheduleState({forecast:"2026-11-26"},null).kind,"");
  assert.equal(scheduleState({...skipped,completed:"2026-11-24"},review).kind,"completion");
  assert.equal(scheduleState({...skipped,status:"✅ завершён"},review).kind,"finished");
  assert.equal(scheduleState({...postponed,status:"Отменено"},review).kind,"cancelled");
  assert.equal(scheduleState({...skipped,status:"closed"},review).kind,"closed");
  assert.equal(scheduleState({...skipped,status:"не завершено"},review).kind,"overdue");
});


test("week labels in December and January stay within their month without touching on narrow scales", () => {
  const period = {start:dayOf("2026-09-21"), size:115};
  const weeks = ticks(period), months = monthBands(period), widths = weeks.map(() => 38);
  for (const width of [730, 470, 316, 160]) {
    const sides = tickAlignments(weeks, months, width, widths);
    let previous = -1;
    for (const [i, tick] of weeks.entries()) {
      if (sides[i] === "hidden") continue;
      const start = tick.at * width - (sides[i] === "before" ? widths[i] : 0);
      const end = start + widths[i];
      const month = months.filter(m => m.day <= tick.day).at(-1);
      assert.ok(start > previous && start + .01 >= month.at * width);
      assert.ok(end <= (month.at + month.width) * width + .01);
      previous = end;
    }
    assert.ok(sides.includes("after"));
  }
});
