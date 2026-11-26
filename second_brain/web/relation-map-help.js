const examples = {
  work: {
    question: "В какие планы входит проверка восстановления базы?",
    start: "Проверить восстановление базы",
    relation: "входит в план",
    target: "Запуск Маяка",
    explanation: "У этой задачи две связи с планами. Выберите «Запуск Маяка» или «План недели». Источник в каждом случае ведёт в тот план, куда добавили задачу."
  },
  personal: {
    question: "Какие дела записаны прямо в направлении «Личное»?",
    start: "Личное",
    relation: "содержит задачу",
    target: "Забрать дорожный набор",
    explanation: "Откройте задачу из направления. Её карточка указывает на «Личное». От самой задачи можно перейти к плану «Подготовка к поездке»."
  },
  perfume: {
    question: "К какому проекту относится сравнение кедровых проб?",
    start: "Сравнить кедровые пробы",
    relation: "относится к проекту",
    target: "Выпуск кедровой серии",
    explanation: "Проект указан в карточке задачи. Откройте основание этой связи, чтобы увидеть строку с проектом. Из проекта можно перейти к его направлению «Парфюмерия»."
  }
};

export function relationMapHelp(params) {
  const page = document.createElement("article");
  page.className = "activity-guide";
  page.innerHTML = `
    <a class="back-link" href="#map">← К карте связей</a>
    <header><h1>Как читать карту связей</h1>
      <p>Карта показывает, с чем связана выбранная карточка и где записана каждая связь. Например, от задачи можно пройти к её проекту или найти планы, в которые её включили.</p>
    </header>
    <section class="guide-links">
      <h2>С чего начать</h2>
      <dl>
        <div><dt>Помните название</dt><dd>Введите его или другое имя карточки в поиск по карте. При совпадении названий сверьте тип и путь.</dd></div>
        <div><dt>Знаете направление</dt><dd>Выберите его на карте. Проекты, заметки и самостоятельные задачи собраны в отдельные группы. Число у группы показывает, сколько в ней связей. Откройте группу, чтобы просмотреть остальные записи.</dd></div>
        <div><dt>Ищете основание</dt><dd>У связи откройте источник. Путь и номер строки указывают, в какой Markdown-карточке сохранена ссылка. Она может находиться и в карточке на другом конце связи.</dd></div>
      </dl>
    </section>
    <section class="guide-links">
      <h2>Что означает связь</h2>
      <dl>
        <div><dt>Проект или направление</dt><dd>Принадлежность хранится в самой карточке. Задача может относиться к проекту либо непосредственно к направлению. Роли людей и состав команд можно проверить в разделе «Люди».</dd></div>
        <div><dt>Участие в плане</dt><dd>Карта показывает прямой выбор работы или проекта в плане. Для закрытого плана источником служит сохранённый снимок его состава. Гант и доска OKR дают подробности по зависимостям работ и ключевым результатам.</dd></div>
        <div><dt>Записи без направления</dt><dd>Проект или заметку можно оставить без направления. Записи, в которых ссылка на направление указана, но её адресат недоступен, собраны отдельно. Источник поможет проверить такую ссылку.</dd></div>
      </dl>
    </section>
    <section class="guide-example">
      <h2>Три маршрута по учебному хранилищу</h2>
      <fieldset><legend class="sr-only">Пример связи</legend>
        <label><input type="radio" name="map-example" value="work" checked><span>ИТ</span></label>
        <label><input type="radio" name="map-example" value="personal"><span>Личное</span></label>
        <label><input type="radio" name="map-example" value="perfume"><span>Парфюмерия</span></label>
      </fieldset>
      <div aria-live="polite">
        <p class="guide-selection"></p>
        <div class="guide-route">
          <strong class="guide-origin"></strong><span class="guide-arrow" aria-hidden="true"></span>
          <span class="guide-path"></span><span class="guide-arrow" aria-hidden="true"></span>
          <strong class="guide-destination"></strong>
        </div>
        <p class="map-example-explanation"></p>
      </div>
    </section>
    <section class="guide-changes">
      <h2>Как вернуться</h2>
      <p>Ссылка «К карте связей» вернёт вас туда, откуда вы открыли справку. При переходах по карте кнопка «Назад» браузера возвращает предыдущую карточку, группу или результаты поиска.</p>
    </section>`;
  const query = params.toString();
  const back = page.querySelector(".back-link");
  if (query) back.href = "#map?" + query;
  back.addEventListener("click", event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
        event.shiftKey || event.altKey || history.state?.mapParent !== back.hash) return;
    event.preventDefault();
    history.back();
  });
  function showExample(key) {
    const example = examples[key];
    page.querySelector(".guide-selection").textContent = example.question;
    page.querySelector(".guide-origin").textContent = example.start;
    page.querySelector(".guide-path").textContent = example.relation;
    page.querySelector(".guide-destination").textContent = example.target;
    page.querySelector(".map-example-explanation").textContent = example.explanation;
  }
  page.querySelector("fieldset").addEventListener("change", event => showExample(event.target.value));
  showExample("work");
  return page;
}
