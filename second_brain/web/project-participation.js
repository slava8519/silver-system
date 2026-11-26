export function relationshipFacts(row, counted) {
  const facts = [];
  if (row.explicitlyIncluded) facts.push("Проект включён в план");
  if (row.taskCount) facts.push(counted(row.taskCount, ["работа", "работы", "работ"]) + " в плане");
  else if (row.explicitlyIncluded) facts.push("Работы пока не выбраны");
  if (row.okrTaskCount) facts.push("Из них в КР — " + row.okrTaskCount);
  if (row.contextResultCount) facts.push("Контекст для " + row.contextResultCount + " КР");
  if (row.status === "closed") facts.push("План закрыт");
  if (row.missing) facts.push("Карточка проекта недоступна");
  return facts;
}

export function eligibleWork(projectItems, planItems) {
  const existing = new Set(planItems.map(row => row.id));
  return projectItems.filter(row => !existing.has(row.id));
}

export function planProjects(ui, planId, {open, currentPlan, canEdit, refreshChart}) {
  const {api, node, button, link, option, labelled, notice, counted} = ui;
  const box = node("details", "plan-projects");
  const heading = node("summary", "text-link", "Проекты плана");
  const body = node("div", "plan-projects-body");
  const status = node("p", "plan-projects-status");
  status.setAttribute("role", "status");
  box.append(heading, body, status);
  let reading = 0;
  let activeEditor = null;
  let saving = false;

  function closeEditor() {
    activeEditor?.remove();
    activeEditor = null;
  }

  async function edit(host, trigger, build) {
    if (saving || !canEdit()) return;
    if (activeEditor?.parentElement === host) { closeEditor(); trigger.focus(); return; }
    closeEditor();
    status.textContent = "";
    const form = node("form", "plan-project-form");
    host.append(form);
    activeEditor = form;
    const errorBox = node("div");
    form.append(node("p", "gantt-context", "Загрузка…"));
    try {
      const config = await build();
      if (!form.isConnected) return;
      form.replaceChildren();
      const cancel = button("Отмена", "text-link plan-project-action", () => { closeEditor(); trigger.focus(); });
      const controls = node("div", "work-tools");
      const save = node("button", "button button-secondary", config.submit);
      save.type = "submit";
      let select;
      if (config.rows) {
        if (!config.rows.length) {
          form.append(node("p", "gantt-context", config.empty), cancel);
          cancel.focus({preventScroll:true});
          return;
        }
        select = node("select");
        select.required = true;
        select.append(option("", "Выберите из списка"));
        for (const row of config.rows) select.append(option(row.id, row.label || row.title));
        form.append(labelled(config.label, select));
      }
      if (config.hint) form.append(node("p", "gantt-context", config.hint));
      controls.append(save, cancel);
      form.append(controls, errorBox);
      (select || cancel).focus({preventScroll:true});
      form.addEventListener("submit", async event => {
        event.preventDefault();
        if (saving || !canEdit()) return;
        saving = save.disabled = cancel.disabled = true;
        if (select) select.disabled = true;
        errorBox.replaceChildren();
        try {
          const result = await config.write(select?.value);
          if (!box.isConnected) return;
          status.textContent = result.changed === false ? "Состав плана уже актуален." : config.saved;
          await refreshChart();
          if (!box.isConnected) return;
          await load();
          heading.focus({preventScroll:true});
        } catch (error) {
          errorBox.append(notice(error.message));
          if (error.status === 409) {
            errorBox.append(button("Перечитать состав", "text-link plan-project-action", async () => {
              try {
                await refreshChart();
                await load();
                heading.focus({preventScroll:true});
              } catch (refreshError) {
                errorBox.replaceChildren(notice(refreshError.message));
              }
            }));
          }
        } finally {
          saving = save.disabled = cancel.disabled = false;
          if (select) select.disabled = false;
        }
      });
    } catch (error) {
      if (form.isConnected) form.replaceChildren(notice(error.message), button("Закрыть", "text-link plan-project-action", () => { closeEditor(); trigger.focus(); }));
    }
  }

  function action(host, label, build) {
    const control = button(label, "text-link plan-project-action", () => edit(host, control, build));
    host.append(control);
  }

  async function load() {
    const version = ++reading;
    closeEditor();
    body.replaceChildren(node("p", "gantt-context", "Загрузка проектов…"));
    try {
      const catalog = await api("plan/activity");
      if (!body.isConnected || version !== reading) return;
      const plan = catalog.plans.find(row => row.id === planId);
      if (!plan) throw new Error("План отсутствует в каталоге.");
      body.replaceChildren();
      const editable = plan.status !== "closed";
      const included = new Set(plan.projects.filter(p => p.explicitlyIncluded).map(p => p.id));
      const member = (projectId, present) => api("plan/project-membership", {id:planId, projectId, present, revision:plan.revision});
      const list = node("ul", "plan-project-list");
      for (const row of plan.projects) {
        const item = node("li");
        const title = row.title || row.id;
        item.append(row.missing ? node("strong", "", title) : link(title, "#project-gantt/" + encodeURIComponent(row.id), "text-link"));
        item.append(node("p", "plan-project-facts", relationshipFacts(row, counted).join(" · ")));
        if (editable && row.explicitlyIncluded) {
          if (!row.missing) {
            const picker = node("div", "plan-project-control");
            action(picker, "Выбрать работу", async () => {
              const project = await api("plan/project-gantt?" + new URLSearchParams({id:row.id}));
              return {label:"Работа проекта", rows:eligibleWork(project.items, currentPlan().items),
                empty:project.items.length ? "Все работы проекта уже входят в план." : "У проекта пока нет работ.",
                submit:"Добавить в план", saved:"Работа добавлена в план.",
                write:taskId => api("plan/project-task", {id:planId, projectId:row.id, taskId, revision:plan.revision})};
            });
            item.append(picker);
          }
          const removal = node("div", "plan-project-control");
          action(removal, "Снять отметку", () => ({
            hint:"Снять отметку о включении проекта «" + title + "»? Выбранные работы и связи с КР сохранятся.",
            submit:"Подтвердить снятие", saved:"Отметка о включении проекта снята.", write:() => member(row.id, false)
          }));
          item.append(removal);
        }
        list.append(item);
      }
      if (list.children.length) body.append(list);
      else body.append(node("p", "gantt-context", "Связанных проектов пока нет."));
      if (editable) {
        const available = catalog.projects.filter(row => !included.has(row.id));
        if (available.length) {
          const add = node("div", "plan-project-add");
          action(add, "Включить проект в план", () => ({
            label:"Проект", rows:available.map(p => ({id:p.id, title:p.title, label:p.title + (p.directionTitle ? " · " + p.directionTitle : "")})),
            hint:"Работы проекта для Ганта выбираются отдельно.", submit:"Включить в план",
            saved:"Проект включён в план.", write:projectId => member(projectId, true)
          }));
          body.append(add);
        } else if (!catalog.projects.length) body.append(link("Создать проект", "#new/project", "text-link"));
      }
    } catch (error) {
      if (body.isConnected && version === reading) body.replaceChildren(notice(error.message), button("Обновить список", "text-link plan-project-action", load));
    }
  }

  box.addEventListener("toggle", () => { if (box.open && !saving) load(); });
  box.open = open;
  return box;
}
