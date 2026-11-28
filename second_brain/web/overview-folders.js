export function renderOverviewFolders({node, counted}, folders) {
  const panel = node("details", "work-panel overview-folders");
  panel.open = true;
  panel.append(node("summary", "work-heading", "Папки хранилища"),
    node("p", "muted", "Оба счётчика включают подпапки. Карточки входят в общее число видимых файлов."));

  const children = new Map();
  for (const folder of folders) {
    const parent = folder.path.substring(0, folder.path.lastIndexOf("/"));
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(folder);
  }

  function list(parent) {
    const items = node("ul", "folder-list");
    for (const folder of children.get(parent) || []) {
      const branch = children.has(folder.path);
      const row = node(branch ? "summary" : "div", "folder-row");
      const name = node("code", "folder-name", folder.path.slice(folder.path.lastIndexOf("/") + 1));
      name.title = folder.path;
      const files = counted(folder.files, ["файл", "файла", "файлов"]);
      const cards = counted(folder.count, ["карточка", "карточки", "карточек"]);
      row.append(name, node("span", "folder-counts", files + " · " + cards));
      const item = node("li");
      if (branch) {
        const details = node("details");
        details.append(row, list(folder.path));
        item.append(details);
      } else {
        item.append(row);
      }
      items.append(item);
    }
    return items;
  }

  panel.append(list(""));
  return panel;
}
