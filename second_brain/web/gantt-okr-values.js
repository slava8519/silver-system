export const boardURL = id => id ? "#okr?" + new URLSearchParams({plan: id}) : "#activity";
export const resultURL = (id, plan) => "#okr/" + encodeURIComponent(id) + (plan ? "?" + new URLSearchParams({plan}) : "");
export const planName = row => row.title + (row.status === "closed" ? " · закрыт" : "");
export const quantity = (value, unit) => String(value).replace(".", ",") + (unit ? " " + unit : "");
export const primaryOf = row => row.criteria?.find(item => item.primary);
export const symbols = {"<=": "≤", ">=": "≥", "=": "="};
export function threshold(row) {
  const primary = primaryOf(row);
  if (primary) return primary.target == null ? "—" : symbols[primary.operator] + " " + quantity(primary.target, primary.unit);
  return row.target != null ? quantity(row.target, row.unit) : "—";
}
export function reading(row) {
  const latest = primaryOf(row)?.measurement || row.measurement;
  return latest ? quantity(latest.value, primaryOf(row)?.unit || row.unit) : "—";
}
export function checks(row) {
  const all = row.criteria || [];
  return all.length ? all.filter(c => c.met === true).length + " из " + all.length : "";
}
export function checkSummary(row) {
  if (!row.criteria?.length) return "";
  if (!row.criteria.some(c => c.measurement)) return "Нет проверок";
  if (row.criteria.every(c => c.met == null)) return "Проверки без подтверждения";
  return "Выполнено " + checks(row);
}
export function readiness(progress) {
  return progress?.value != null ? quantity(progress.value, "%") : "—";
}
export function fractionPercent(weight) {
  if (weight == null) return "—";
  return new Intl.NumberFormat("ru-RU", {style: "percent", maximumFractionDigits: 4}).format(Number(weight));
}

export function contributionPoints(progress, id) {
  const value = progress?.contributions?.[id];
  return value == null ? "—" : quantity(value, "п. п.");
}
