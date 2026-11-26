const dateFormat = new Intl.DateTimeFormat("ru-RU", {day: "numeric", month: "short", year: "numeric", timeZone: "UTC"});

function sourceLink(ui, entry) {
  const source = entry.source;
  if (!source?.href) return null;
  const a = ui.link("Источник", source.href, "direction-source");
  const location = source.path + (source.line ? " · строка " + source.line : "");
  a.title = location;
  a.setAttribute("aria-label", "Источник · " + location);
  return a;
}

function directionCard(ui, area) {
  const {node, link, recordURL, counted} = ui;
  const card = node("article", "direction-state-card");
  const header = node("header", "direction-state-card-head");
  const heading = node("h3", "direction-name");
  const open = link(area.title, recordURL(area.id), "direction-open");
  const arrow = node("span", "direction-arrow", "→");
  arrow.setAttribute("aria-hidden", "true");
  open.append(arrow);
  heading.append(open);
  header.append(heading);
  const counts = [];
  if (area.projectCount) counts.push(counted(area.projectCount, ["проект", "проекта", "проектов"]));
  if (area.taskCount) counts.push(counted(area.taskCount, ["работа", "работы", "работ"]));
  if (counts.length) header.append(node("span", "direction-counts", counts.join(" · ")));
  card.append(header);

  const briefs = area.briefs;
  if (!briefs.length && !area.latest && !area.hasUndated) {
    card.append(node("p", "direction-empty", "Сводка и события пока не записаны."));
    return card;
  }
  const summary = node("div", "direction-summary");
  const isDescription = briefs.length === 1 && briefs[0].kind === "description";
  const labelText = area.briefTotal > briefs.length ? "Коротко · " + briefs.length + " из " + area.briefTotal : "Коротко";
  summary.append(node("h4", "direction-label", isDescription ? "О направлении" : labelText));
  for (const entry of briefs) {
    const line = node("p", "direction-fact");
    if (entry.record.id !== area.id) {
      const name = entry.source?.href
        ? link(entry.record.title, entry.source.href, "direction-origin")
        : node("strong", "direction-origin", entry.record.title);
      if (entry.source) name.title = entry.source.path + " · строка " + entry.source.line;
      line.append(name, " — ");
    }
    line.append(entry.text);
    if (entry.record.id === area.id) {
      const source = sourceLink(ui, entry);
      if (source) line.append(" ", source);
    }
    if (!entry.source) line.append(" ", node("small", "direction-source-note", "Источник недоступен"));
    summary.append(line);
  }
  if (!briefs.length) summary.append(node("p", "direction-empty", "Сводка пока не записана."));
  card.append(summary);

  const event = area.latest;
  const history = node("div", "direction-latest");
  const label = node("div", "direction-event-head");
  label.append(node("h4", "direction-label", area.hasUndated && event?.date ? "Последнее по дате" : "Последнее событие"));
  if (event?.date) {
    const stamp = node("time", "direction-date", dateFormat.format(new Date(event.date + "T00:00:00Z")));
    stamp.dateTime = event.date;
    label.append(stamp);
  } else if (event) label.append(node("span", "direction-date", "Дата не указана"));
  history.append(label);
  if (event) {
    history.append(node("p", "direction-event-text", event.text));
    const origin = node("div", "direction-evidence");
    if (event.record.id !== area.id) origin.append(node("span", "", event.record.title));
    const source = sourceLink(ui, event);
    if (source) origin.append(source);
    if (origin.childElementCount) history.append(origin);
  } else {
    history.append(node("p", "direction-empty", area.hasUndated
      ? "У событий нет дат. Определить последнее пока нельзя." : "События пока не записаны."));
  }
  card.append(history);
  return card;
}

export async function mountDirectionStates(panel, ui) {
  const {node, link, section, button, notice, api} = ui;
  const title = section("Состояние направлений", "direction-section");
  const head = node("div", "direction-section-head");
  const jump = button("К командам ↓", "overview-command-jump", () => {
    const heading = document.getElementById("overview-commands-heading");
    heading.tabIndex = -1;
    heading.focus({preventScroll: true});
    heading.scrollIntoView({block: "start"});
  });
  head.append(title.firstElementChild, jump);
  const list = node("div", "direction-state-list");
  const feedback = node("div", "direction-feedback");
  let offset = 0;
  const more = button("Показать ещё", "button button-secondary", () => load(true));
  more.hidden = true;
  title.append(head, list, feedback, more);
  panel.append(title);
  async function load(extend) {
    more.disabled = true;
    list.setAttribute("aria-busy", "true");
    feedback.replaceChildren();
    if (!offset) feedback.append(node("p", "direction-empty", "Загружаем направления…"));
    try {
      const page = await api("direction-states?" + new URLSearchParams({offset, limit: 5}));
      if (!panel.isConnected) return;
      const cards = page.items.map(area => directionCard(ui, area));
      list.append(...cards);
      offset += cards.length;
      feedback.replaceChildren();
      if (!page.total) {
        feedback.append(node("p", "direction-empty", "В хранилище пока нет направлений."),
          link("Создать направление", "#new/direction", "text-link"));
      }
      more.textContent = "Показать ещё";
      more.hidden = !page.truncated;
      if (extend && cards.length) cards[0].querySelector("h3 a").focus();
    } catch (error) {
      feedback.replaceChildren(notice(error.message));
      more.textContent = "Повторить загрузку";
      more.hidden = false;
    } finally {
      list.removeAttribute("aria-busy");
      more.disabled = false;
    }
  }
  await load(false);
}
