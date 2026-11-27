import {dateText} from "./gantt-axis.js";
import {inspectorText} from "./gantt-detail-text.js";

export function workEvents(ui, settings) {
  const {node, button, notice} = ui;
  const {load, selectMember, evidence, markdownLink, authorText} = settings;
  const section = node("details", "gantt-work-events");
  section.append(node("summary", "", "События вложенных работ"));
  const events = node("div");
  const status = node("div", "gantt-byline");
  status.setAttribute("role", "status");
  let offset = 0, loading = false, loaded = false;
  const more = button("Показать ещё", "gantt-action", next);
  more.hidden = true;
  section.append(events, status, more);
  section.addEventListener("toggle", () => {
    if (section.open && !loaded) next();
  });
  section.open = settings.open;

  async function next() {
    if (loading) return;
    loading = true;
    more.disabled = true;
    status.textContent = "Загрузка событий…";
    try {
      const page = await load(offset);
      if (!section.isConnected) return;
      for (const item of page.items) {
        const card = node("article", "gantt-event");
        const heading = node("div", "gantt-event-head");
        const date = node(item.date ? "time" : "span", "gantt-event-date", dateText(item.date));
        if (item.date) date.dateTime = item.date;
        heading.append(date, button(item.taskTitle, "gantt-action", () => selectMember(item.taskId)));
        const author = authorText(item);
        if (author) heading.append(node("span", "gantt-byline", author));
        card.append(heading, inspectorText(ui, "gantt-event-text", item.text, item.taskPath, markdownLink, true), evidence(item));
        events.append(card);
      }
      offset += page.items.length;
      loaded = true;
      status.textContent = page.total ? "" : "У вложенных работ пока нет событий.";
      more.hidden = !page.truncated;
      more.textContent = "Показать ещё";
    } catch (error) {
      if (!section.isConnected) return;
      status.replaceChildren(notice(error.message));
      more.textContent = "Повторить загрузку";
      more.hidden = false;
    } finally {
      loading = false;
      more.disabled = false;
    }
  }
  return section;
}
