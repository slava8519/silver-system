export function checkSummary(result, counted) {
  const issues = counted(result.issues.total, ["замечание", "замечания", "замечаний"]);
  if (result.status === "incomplete") return "Проверка завершена частично. Зафиксировано " + issues + ".";
  if (result.status === "issues") return "Проверка выявила " + issues + ".";
  return result.scope === "source" ? "Проверка разбора прошла без замечаний." : "Проверка прошла без замечаний.";
}

export function issueLocation(issue) {
  return issue.path + (issue.line > 0 ? " · строка " + issue.line : "");
}

export async function showVaultCheck(view, params, {node, link, button, section, api, pageHead, counted, openSource, rerun}) {
  const pageSize = 10;
  const requested = Number(params.get("offset"));
  const offset = Number.isSafeInteger(requested) && requested > 0 ? requested : 0;
  const pageURL = index => index ? "#check?offset=" + index : "#check";
  view.append(pageHead("Хранилище", "Проверка целостности", button("Проверить снова", "button button-secondary", rerun),
    link("К обзору", "#overview", "text-link")));
  const status = node("p", "muted", "Проверяем записи и источники…");
  status.setAttribute("role", "status");
  view.append(status);
  const result = await api("check?" + new URLSearchParams({limit:pageSize, offset}));
  status.textContent = checkSummary(result, counted);
  const panel = section("Результат проверки");
  const {cards, receipts, originals, rules} = result.checked;
  panel.append(node("p", "", [
    counted(cards, ["карточка", "карточки", "карточек"]),
    counted(receipts, ["квитанция разбора", "квитанции разбора", "квитанций разбора"]),
    counted(originals, ["оригинал", "оригинала", "оригиналов"]),
    counted(rules, ["правило направления", "правила направлений", "правил направлений"])
  ].join(" · ")));
  const {items, total, truncated} = result.issues;
  if (items.length) {
    const list = node("ol", "vault-check-issues");
    list.start = offset + 1;
    for (const issue of items) {
      const item = node("li");
      item.append(node("span", "", issue.message), node("code", "record-path", issueLocation(issue)));
      if (openSource && issue.line > 0 && /\.md$/i.test(issue.path)) {
        item.append(button("Открыть в Obsidian", "intake-action", () => openSource(issue.path, issue.line)));
      }
      list.append(item);
    }
    panel.append(list);
  } else if (offset > 0) {
    panel.append(node("p", "muted", "На этой странице замечаний нет."));
  }
  const controls = node("nav", "work-tools batch-pager");
  controls.setAttribute("aria-label", "Страницы проверки");
  if (offset) controls.append(link("К началу", pageURL(0), "text-link"));
  if (offset > pageSize) controls.append(link("Назад", pageURL(offset - pageSize), "text-link"));
  if (items.length) controls.append(node("span", "muted", (offset + 1) + "–" + (offset + items.length) + " из " + total));
  if (truncated) controls.append(link("Дальше", pageURL(offset + items.length), "text-link"));
  panel.append(controls);
  view.append(panel);
}
