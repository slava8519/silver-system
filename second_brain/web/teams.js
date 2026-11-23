const teamURL = id => "#people?" + new URLSearchParams({team:id});
const personURL = id => "#person/" + encodeURIComponent(id);
const teamsURL = "#people?view=teams";
const pageSize = 20;

export function teamViews(ui) {
  const {node, link, button, labelled, notice, section, api, go, pageHead, counted} = ui;
  const reload = () => go(location.hash);
  const endpoint = (name, args) => name + "?" + new URLSearchParams(args);

  function identity(row, kind) {
    const identity = node("div", "team-identity");
    if (row.missing) {
      identity.append(node("strong", "", "Карточка недоступна"), node("small", "record-path", row.id));
    } else {
      const dest = kind === "team" ? teamURL(row.id) : personURL(row.id);
      identity.append(link(row.name, dest, "text-link"), node("small", "record-path", row.path));
    }
    return identity;
  }

  function action(label, style, feedback, save) {
    const control = button(label, style, async () => {
      if (control.disabled) return;
      control.disabled = true;
      feedback.replaceChildren();
      const origin = location.hash;
      try {
        const destination = await save();
        if (destination !== false && control.isConnected && location.hash === origin) {
          go(typeof destination === "string" ? destination : origin);
        }
      } catch (error) {
        feedback.replaceChildren(notice(error.message));
        if (error.status === 409) {
          feedback.append(button("Перечитать страницу", "intake-action", reload));
        }
      } finally {
        control.disabled = false;
      }
    });
    return control;
  }

  function namedForm(caption, label, submit, initial, save) {
    const details = node("details", "team-form");
    details.append(node("summary", "text-link", caption));
    const form = node("form", "people-search");
    const input = node("input");
    input.value = initial;
    input.required = true;
    const feedback = node("div", "editor-feedback");
    const control = action(submit, "button button-primary", feedback,
      () => form.reportValidity() ? save(input.value.trim()) : false);
    form.addEventListener("submit", e => { e.preventDefault(); control.click(); });
    form.append(labelled(label, input), control);
    details.append(form, feedback);
    return details;
  }

  function createForm(kind) {
    const team = kind === "team";
    const name = team ? "Создать команду" : "Создать карточку человека";
    let key, lastName;
    return namedForm(name, team ? "Название команды" : "Имя человека", "Создать", "", async value => {
      if (value !== lastName) { key = crypto.randomUUID(); lastName = value; }
      const card = await api((team ? "teams" : "people") + "/create", {name:value, requestId:key});
      return team ? teamURL(card.id) : personURL(card.id);
    });
  }

  // Searches and page turns share the same request counter so a late reply cannot replace newer choices.
  async function pages(host, {load, row, empty, searchLabel, first, itemsKey="items"}) {
    const list = node("div", "team-list");
    list.setAttribute("aria-live", "polite");
    let sequence = 0, match = "";
    if (searchLabel) {
      const form = node("form", "people-search");
      const input = node("input");
      input.type = "search";
      const find = node("button", "button button-secondary", "Найти");
      form.append(labelled(searchLabel, input), find);
      form.addEventListener("submit", event => {
        event.preventDefault(); match = input.value.trim(); show(0);
      });
      host.append(form);
    }
    host.append(list);
    async function show(offset, cached) {
      const run = ++sequence;
      try {
        const page = cached || await load({limit:pageSize, offset, match});
        if (run !== sequence || !list.isConnected) return;
        const items = page[itemsKey];
        list.replaceChildren(...items.map(item => row(item, page)));
        if (!items.length) list.append(node("p", "muted", match ? "Совпадений не найдено." : empty));
        if (offset || offset + items.length < page.total) {
          const nav = node("div", "work-tools intake-pager");
          if (offset) nav.append(button("Назад", "intake-action", () => show(Math.max(0,offset-pageSize))));
          if (offset + items.length < page.total) nav.append(button("Дальше", "intake-action", () => show(offset+items.length)));
          list.append(nav);
        }
      } catch (error) { if (run === sequence) list.replaceChildren(notice(error.message)); }
    }
    await show(0,first);
  }

  function picker(searchFor, caption, choose) {
    const team = searchFor === "team";
    const details = node("details", "team-picker");
    details.append(node("summary", "text-link", caption));
    const host = node("div");
    details.append(host);
    details.addEventListener("toggle", () => {
      if (!details.open || host.childNodes.length) return;
      pages(host, {
        searchLabel: team ? "Найти команду" : "Найти человека",
        load: args => api(endpoint(team ? "teams" : "people", args)),
        row: item => {
          const group = node("div", "team-item"), feedback = node("div", "editor-feedback");
          group.append(identity(item, searchFor), action("Выбрать", "intake-action", feedback, () => choose(item)), feedback);
          return group;
        },
        empty: team ? "Сначала создайте команду в разделе «Люди»." : "Сначала создайте карточку человека в разделе «Люди»."
      });
    });
    return details;
  }

  function setContext(context, field, value) {
    return api("people/context", {revision:context.revision, [field]:value});
  }

  async function readContext() {
    try { return await api("people/context?limit=1"); }
    catch (error) { return {error:error.message}; }
  }

  function contextPanel(context) {
    const box = section("Я и моя команда", "work-panel team-context");
    if (context.error) {
      box.append(notice(context.error), button("Перечитать профиль", "intake-action", reload));
      return box;
    }
    const fields = node("div", "team-context-fields");
    for (const [field,title] of [["owner","Я"],["team","Моя команда"]]) {
      const group = node("div");
      const value = context[field], isTeam = field === "team";
      const kind = isTeam ? "team" : "person";
      group.append(node("h3", "", title), value ? identity(value, kind) : node("p", "muted", isTeam ? "Команда не выбрана." : "Карточка не выбрана."));
      if (isTeam && value && !value.missing) {
        group.append(node("small", "muted", counted(context.total, ["участник","участника","участников"])));
      }
      const choices = node("div", "team-choices"), feedback = node("div", "editor-feedback");
      const caption = isTeam ? (value ? "Сменить команду" : "Выбрать команду") : (value ? "Сменить карточку" : "Выбрать себя");
      choices.append(picker(kind, caption, item => setContext(context, field, item.id)));
      if (value) choices.append(action("Снять выбор", "intake-action", feedback, () => setContext(context, field, null)));
      group.append(choices, feedback);
      fields.append(group);
    }
    box.append(fields);
    return box;
  }

  function directoryNav(active) {
    const nav = node("nav", "team-nav");
    nav.setAttribute("aria-label", "Справочник");
    for (const [title,href] of [["Люди","#people"],["Команды",teamsURL]]) {
      const a = link(title, href, "text-link");
      if (title === active) a.setAttribute("aria-current","page");
      nav.append(a);
    }
    return nav;
  }

  async function directory(view, {params,vault,directions}, personRow) {
    const id = params.get("team");
    if (id) return teamCard(view, id, vault);
    const teams = params.get("view") === "teams";
    const direction = params.get("direction");
    const area = directions.find(d => d.id === direction);
    view.append(pageHead(vault, area ? "Люди · " + area.title : "Люди"));
    const context = await readContext();
    if (!view.isConnected) return;
    view.append(contextPanel(context));
    const box = node("section", "work-panel");
    box.append(directoryNav(teams ? "Команды" : "Люди"), createForm(teams ? "team" : "person"));
    if (area) box.append(link("Все люди", "#people", "text-link"));
    view.append(box);
    await pages(box, {
      searchLabel:teams ? "Название команды" : "Имя или другое название",
      load:args => api(endpoint(teams ? "teams" : "people", {...args, ...(!teams && direction ? {direction} : {})})),
      row: item => {
        if (!teams) return personRow(item,direction);
        const row = node("div", "team-item");
        row.append(identity(item,"team"), node("span", "record-side", counted(item.total,["участник","участника","участников"])));
        return row;
      },
      empty:teams ? "Команд пока нет." : "Карточек людей пока нет."
    });
  }

  function changeMember(id, person, present, revision) {
    return api("teams/member", {id,person,present,revision});
  }

  async function teamCard(view, id, vault) {
    const load = ({limit,offset}) => api(endpoint("team",{id,limit,offset}));
    const [team,context] = await Promise.all([load({limit:pageSize,offset:0}), readContext()]);
    if (!view.isConnected) return;
    const mine = context.team?.id === id;
    view.append(pageHead(vault + " · Команды", team.name, link("Все команды", teamsURL, "button button-secondary")));
    const settings = node("section", "work-panel team-settings");
    settings.append(node("p","record-path",team.path));
    const info = node("div","work-tools"), feedback = node("div","editor-feedback");
    if (mine) info.append(node("strong", "", "Моя команда"));
    if (context.error) info.append(notice(context.error));
    else info.append(action(mine ? "Снять выбор моей команды" : "Указать как мою команду", "intake-action", feedback,
      () => setContext(context,"team",mine ? null : id)));
    settings.append(info,feedback,namedForm("Переименовать команду","Название команды","Сохранить название",team.name,
      name => api("teams/rename",{id,name,revision:team.revision})));
    view.append(settings);
    const members = section("Участники");
    members.append(picker("person","Добавить участника",item => changeMember(id,item.id,true,team.revision)));
    view.append(members);
    await pages(members,{
      load, first:team, itemsKey:"members", empty:"В команде пока нет участников.",
      row:(item,page) => {
        const row = node("div", "team-item"), feedback = node("div","editor-feedback");
        row.append(identity(item,"person"), action("Убрать из команды", "intake-action", feedback,
          () => changeMember(id,item.id,false,page.revision)),feedback);
        return row;
      }
    });
  }

  async function personTeams(view, person) {
    const context = await readContext();
    if (!view.isConnected) return;
    const mine = context.owner?.id === person.id;
    const box = section("Команды человека");
    const tools = node("div","team-choices"), feedback = node("div","editor-feedback");
    if (mine) tools.append(node("strong", "", "Моя карточка"));
    if (context.error) tools.append(notice(context.error));
    else tools.append(action(mine ? "Снять отметку «Я»" : "Это я", "intake-action", feedback,
      () => setContext(context,"owner",mine ? null : person.id)));
    tools.append(picker("team","Добавить в команду",team => changeMember(team.id,person.id,true,team.revision)));
    box.append(tools,feedback);
    view.querySelector(".work-head").after(box);
    await pages(box,{
      load:({limit,offset}) => api(endpoint("person/teams",{id:person.id,limit,offset})),
      empty:"Человек пока не входит в команды.",
      row:team => {
        const row = node("div","team-item"), feedback = node("div","editor-feedback");
        row.append(identity(team,"team"),action("Убрать из команды","intake-action",feedback,
          () => changeMember(team.id,person.id,false,team.revision)),feedback);
        return row;
      }
    });
  }

  return {directory,personTeams};
}
