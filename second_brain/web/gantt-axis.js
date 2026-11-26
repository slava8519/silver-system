// Date-only fields are calendar days. A DST transition must occupy the same width as any other day.
const dayMs = 86400000;
const print = (parts) => new Intl.DateTimeFormat("ru-RU", {...parts, timeZone: "UTC"});
const fullDate = print({day: "numeric", month: "short", year: "numeric"});
const dayMonth = print({day: "numeric", month: "short"});
const monthYear = print({month: "long", year: "numeric"});

export const dateLabels = Object.freeze({
  start: "Начало", due: "Согласованный срок", forecast: "Прогноз", completed: "Факт завершения"
});

export function dayOf(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return null;
  const time = Date.parse(value + "T00:00:00Z");
  if (!Number.isFinite(time) || new Date(time).toISOString().slice(0, 10) !== value) return null;
  return time / dayMs;
}

export function todayDay() {
  const date = new Date();
  return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / dayMs;
}

export function dateText(value) {
  const day = dayOf(value);
  if (day === null) return value ? "Проверить дату" : "Без даты";
  return fullDate.format(day * dayMs);
}

export function shortDay(day) { return dayMonth.format(day * dayMs); }
export function isoDay(day) { return new Date(day * dayMs).toISOString().slice(0, 10); }

export function calendarYear(day, offset = 0) {
  const year = new Date(day * dayMs).getUTCFullYear() + offset;
  const start = Date.UTC(year, 0, 1) / dayMs;
  return {start, size: Date.UTC(year + 1, 0, 1) / dayMs - start, zoom: "year"};
}

export function fitWindow(rows, today, project) {
  let first = Infinity, last = -Infinity;
  if (project) {
    for (const boundary of [project.projectStart, project.due]) {
      const day = dayOf(boundary);
      if (day !== null) { first = Math.min(first, day); last = Math.max(last, day); }
    }
  }
  for (const row of rows) {
    for (const field of Object.keys(dateLabels)) {
      if (row.kind === "milestone" && field === "start") continue;
      const day = dayOf(row[field]);
      if (day !== null) { first = Math.min(first, day); last = Math.max(last, day); }
    }
  }
  if (!Number.isFinite(first)) first = last = today;
  const padding = Math.max(2, Math.ceil((last - first) * .035));
  const start = first - padding;
  return {start, size: Math.max(14, last - start + padding + 1)};
}

export function ticks({start, size, zoom}) {
  if (zoom === "year") {
    const year = new Date(start * dayMs).getUTCFullYear();
    const monthName = print({month: "short"});
    return Array.from({length: 12}, (_, month) => {
      const day = Date.UTC(year, month, 1) / dayMs;
      return {day, at: (day - start) / size, label: monthName.format(day * dayMs)};
    });
  }
  const step = [1, 2, 3, 7, 14, 28, 60, 90, 180, 365].find(number => size / number <= 10) || Math.ceil(size / 8);
  const ticks = [];
  for (let day = start; day < start + size; day += step) {
    const at = (day - start) / size;
    const label = size <= 35 ? String(new Date(day * dayMs).getUTCDate()) :
      (size > 700 ? fullDate : dayMonth).format(day * dayMs);
    ticks.push({day, at, label});
  }
  return ticks;
}

export function windowTitle({start, size}) {
  const first = new Date(start * dayMs), last = new Date((start + size - 1) * dayMs);
  if (first.getUTCFullYear() === last.getUTCFullYear()) {
    if (first.getUTCMonth() === last.getUTCMonth()) return monthYear.format(first);
    return dayMonth.format(first) + " — " + fullDate.format(last);
  }
  return fullDate.format(first) + " — " + fullDate.format(last);
}

export function plot(row, window) {
  const start = row.kind === "milestone" ? null : dayOf(row.start), due = dayOf(row.due);
  if (start === null && due === null) return {kind: "undated"};
  if (start !== null && due !== null && start > due) return {kind: "invalid"};
  const first = start ?? due, last = due ?? start;
  const end = window.start + window.size;
  if (last < window.start) return {kind: "earlier"};
  if (first >= end) return {kind: "later"};
  if (start === null || due === null) {
    return {kind: start === null ? "due" : "start", at: (first - window.start + .5) / window.size};
  }
  return {kind: "range", at: Math.max(0, (first - window.start) / window.size),
    end: Math.min(1, (last + 1 - window.start) / window.size), before: first < window.start, after: last >= end};
}

export function scheduleMarks(row, window) {
  const base = plot(row, window);
  const marks = [];
  if (["range", "start", "invalid"].includes(base.kind)) marks.push(base);
  const outside = new Set();
  if (base.kind !== "invalid" && Object.keys(dateLabels).some(field => row[field] && dayOf(row[field]) === null)) {
    marks.push({kind: "invalid"});
  }
  for (const field of ["due", "forecast", "completed"]) {
    if (dayOf(row[field]) === null) continue;
    const point = plot({due: row[field]}, window);
    if (point.kind === "due") marks.push({kind: field, at: point.at});
    else outside.add(point.kind);
  }
  if (marks.length) return marks;
  if (base.kind === "earlier" || base.kind === "later") outside.add(base.kind);
  return [{kind: outside.size > 1 ? "outside" : [...outside][0] || "undated"}];
}
