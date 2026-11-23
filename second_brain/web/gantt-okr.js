import {dateText} from "./gantt-axis.js";
import {cardLinks} from "./gantt-detail.js";

const catalogTitle = "Цели и ключевые результаты";
const quantity = (value, unit) => String(value).replace(".", ",") + " " + unit;

export function okrLink(ui) {
  return ui.link(catalogTitle, "#okr", "text-link");
}

export async function openOKR(ui, view, id, planURL) {
  const {api, node, link, pageHead, embedded} = ui;
  if (!id) {
    const catalog = await api("plan/okrs");
    if (!view.isConnected) return;
    view.append(link("← Все планы", "#plans", "back-link"), pageHead(catalog.name, catalogTitle));
    if (!catalog.items.length) {
      view.append(node("p", "gantt-empty", "Ключевых результатов пока нет. Чтобы добавить первый, укажите в Codex направление, цель и единицу измерения."));
      return;
    }
    const groups = new Map();
    for (const item of catalog.items) {
      const groupKey = JSON.stringify([item.direction, item.objective]);
      if (!groups.has(groupKey)) {
        const group = node("section", "work-panel okr-panel");
        group.append(node("p", "gantt-context", item.directionTitle || "Направление недоступно"),
          node("h2", "work-heading", item.objective));
        view.append(group);
        groups.set(groupKey, group);
      }
      const row = link("", "#okr/" + encodeURIComponent(item.id), "record-row");
      let summary = item.measurement
        ? quantity(item.measurement.value, item.unit) + " · " + dateText(item.measurement.date)
        : "Нет измерения";
      if (item.measurement?.sourceUnavailable) summary += " · источник недоступен";
      row.append(node("strong", "record-main row-title", item.title), node("span", "record-side", summary));
      groups.get(groupKey).append(row);
    }
    return;
  }

  const card = await api("plan/okr?id=" + encodeURIComponent(id));
  if (!view.isConnected) return;
  const openNote = cardLinks(ui, card.root);
  view.append(link("← " + catalogTitle, "#okr", "back-link"),
    pageHead(card.directionTitle || card.name, card.title, openNote("Открыть в Obsidian ↗", card.path)));

  const panel = node("section", "work-panel okr-panel");
  panel.append(node("h2", "work-heading", "Цель"), node("p", "", card.objective));
  const facts = node("dl", "record-facts");
  function fact(label, value) {
    const field = node("div");
    field.append(node("dt", "", label), node("dd", "", value));
    facts.append(field);
  }
  fact("Последний замер", card.measurement ? quantity(card.measurement.value, card.unit) : "Нет измерения");
  if (card.measurement) fact("Дата замера", dateText(card.measurement.date));
  else fact("Единица измерения", card.unit);
  if (card.target != null) fact("Ориентир", quantity(card.target, card.unit));
  panel.append(facts);

  function evidence(source) {
    const block = node("div", "okr-source");
    block.append(node("blockquote", "", source.quote));
    const label = source.path + " · строка " + source.line;
    if (source.unavailable) {
      block.append(node("p", "gantt-context", "Сохранённая цитата. Исходная строка изменилась или недоступна."),
        node("span", "record-path", label));
      return block;
    }
    if (embedded) block.append(openNote(label, source.path, source));
    else {
      const url = new URL(source.href, location.href);
      if (url.origin === location.origin && url.pathname.startsWith("/source/")) {
        const a = link(label, url.href, "text-link");
        a.target = "_blank";
        a.rel = "noopener";
        block.append(a);
      }
    }
    return block;
  }
  if (card.measurement) panel.append(evidence(card.measurement.source));
  if (card.history.length > 1) {
    const history = node("details", "record-source");
    history.append(node("summary", "text-link", "Предыдущие замеры"));
    for (const row of card.history.slice(0, -1).reverse()) {
      history.append(node("p", "", dateText(row.date) + " · " + quantity(row.value, card.unit)), evidence(row.source));
    }
    panel.append(history);
  }

  const work = node("section", "work-panel okr-panel");
  work.append(node("h2", "work-heading", "Связанные работы"));
  for (const task of card.tasks) {
    const row = node("div", "okr-task");
    if (task.path) row.append(openNote(task.title + " ↗", task.path));
    else row.append(node("span", "gantt-missing", "Карточка недоступна · " + task.id));
    if (task.plans.length) {
      const plans = node("div", "work-tools");
      for (const plan of task.plans) {
        const title = plan.title + (plan.status === "closed" ? " · закрыт" : "");
        plans.append(task.path || plan.status === "closed"
          ? link(title, planURL(plan.id, task.id), "text-link")
          : node("span", "gantt-context", title));
      }
      row.append(node("span", "gantt-context", "В планах"), plans);
    } else row.append(node("small", "gantt-context", "Работа пока не входит в планы."));
    work.append(row);
  }
  if (!card.tasks.length) work.append(node("p", "muted", "Связанных работ пока нет."));
  view.append(panel, work);
}
