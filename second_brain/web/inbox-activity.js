const kinds = {
  meeting: ["Созвоны", "Созвон"], report: ["Отчёты", "Отчёт"], thread: ["Треды", "Тред"],
  recipe: ["Рецепты", "Рецепт"], image: ["Изображения", "Изображение"],
  attachment: ["Вложения", "Вложение"], other: ["Другие материалы", "Материал"]
};
const dateFormat = new Intl.DateTimeFormat("ru-RU", {day: "numeric", month: "long", year: "numeric", timeZone: "UTC"});
const displayDate = value => dateFormat.format(new Date(value + "T00:00:00Z"));

export async function mountInboxActivity(panel, {node, link, button, option, notice, api}) {
  const head = node("div", "arrival-head");
  const filter = node("select", "arrival-filter");
  filter.setAttribute("aria-label", "Вид входящих");
  filter.append(option("", "Все материалы"), ...Object.entries(kinds).map(([id, [label]]) => option(id, label)));
  head.append(panel.firstElementChild, filter);
  const latest = node("p", "arrival-latest");
  const list = node("div", "arrival-days");
  const feedback = node("div");
  feedback.setAttribute("role", "status");
  let offset = 0;
  let generation = 0;
  const more = button("Предыдущие дни", "arrival-more", () => load());
  more.hidden = true;
  panel.append(head, latest, list, feedback, more);

  function source(item) {
    const name = item.href ? link(item.title, item.href, "text-link") : node("span", "", item.title);
    if (item.path) name.title = item.path;
    if (item.href?.startsWith("/attachment/")) name.download = "";
    return name;
  }

  function dayRow(day, kind) {
    const row = node("details", "arrival-day");
    const summary = node("summary");
    const title = node("strong", "", day.day ? displayDate(day.day) : "Дата поступления неизвестна");
    const counts = Object.entries(day.kinds).map(([id, n]) => kinds[id][0] + " · " + n);
    summary.append(title, node("span", "arrival-kinds", counts.join("   /   ")));
    summary.append(node("span", "arrival-example", day.example));
    const items = node("ul", "arrival-items");
    const errorBox = node("div");
    let shown = 0;
    const next = button("Ещё материалы", "arrival-more", () => loadItems());
    next.hidden = true;
    row.append(summary, items, errorBox, next);
    let loading = false;
    async function loadItems() {
      if (loading) return;
      loading = true;
      next.disabled = true;
      items.setAttribute("aria-busy", "true");
      try {
        const page = await api("inbox/arrivals?" + new URLSearchParams({kind, day: day.day || "undated", offset: shown, limit: 10}));
        if (!row.isConnected) return;
        errorBox.replaceChildren();
        for (const item of page.items) {
          const entry = node("li");
          const label = kinds[item.kind][1] + (item.meetingDate ? " · " + displayDate(item.meetingDate) : "");
          entry.append(source(item), node("small", "", label));
          if (!item.href && item.path) entry.append(node("code", "record-path", item.path));
          items.append(entry);
        }
        shown += page.items.length;
        next.textContent = "Ещё материалы";
        next.hidden = !page.truncated;
      } catch (error) {
        errorBox.replaceChildren(notice(error.message));
        next.textContent = "Повторить";
        next.hidden = false;
      } finally {
        next.disabled = false;
        loading = false;
        items.removeAttribute("aria-busy");
      }
    }
    row.addEventListener("toggle", () => { if (row.open && !shown) loadItems(); });
    return row;
  }

  async function load() {
    const current = generation;
    const kind = filter.value;
    more.disabled = true;
    list.setAttribute("aria-busy", "true");
    try {
      const page = await api("inbox/arrivals?" + new URLSearchParams({kind, offset, limit: 3}));
      if (!panel.isConnected || current !== generation) return;
      feedback.replaceChildren();
      if (!offset && page.latestMeeting) {
        const item = page.latestMeeting;
        latest.append("Последняя встреча с известной датой · ", node("strong", "", displayDate(item.meetingDate)), " · ", source(item));
      }
      list.append(...page.items.map(day => dayRow(day, kind)));
      offset += page.items.length;
      if (!page.total) feedback.append(node("p", "muted", kind ? "Входящих этого вида пока нет." : "Материалов пока нет."),
        link("Открыть входящие", "#inbox", "text-link"));
      more.textContent = "Предыдущие дни";
      more.hidden = !page.truncated;
    } catch (error) {
      if (current !== generation) return;
      feedback.replaceChildren(notice(error.message));
      more.textContent = "Повторить загрузку";
      more.hidden = false;
    } finally {
      if (current === generation) {
        more.disabled = false;
        list.removeAttribute("aria-busy");
      }
    }
  }

  filter.addEventListener("change", () => {
    ++generation;
    offset = 0;
    list.replaceChildren();
    latest.replaceChildren();
    feedback.replaceChildren();
    more.hidden = true;
    load();
  });
  await load();
}
