// These four rows illustrate the same workshop used in examples/project_activity_vault.py.
const workshop = [
  {title:"Описать рабочие места", months:"февраль — март", start:2, end:3},
  {title:"Промаркировать ячейки выдачи", months:"октябрь", start:10, end:10, quarter:true},
  {title:"Проверить выдачу в вечернюю смену", months:"ноябрь — декабрь", start:11, end:12, quarter:true},
  {title:"Подготовить итоговую схему мастерской", months:"декабрь", start:12, end:12}
];

export function activityHelp(params) {
  const page = document.createElement("article");
  page.className = "activity-guide";
  page.innerHTML = `
    <a class="back-link" href="#activity">← К проектам и планам</a>
    <header><h1>Как устроена проектная деятельность</h1>
      <p>Проект объединяет работы ради конкретного результата. План собирает то, чем вы решили заняться за период или к событию. В одном плане могут встретиться работы нескольких проектов и самостоятельные задачи.</p>
    </header>
    <figure class="activity-map">
      <figcaption><h2>Что попадает в Гант</h2></figcaption>
      <div class="guide-route">
        <strong class="guide-origin">Проект</strong><span class="guide-arrow" aria-hidden="true"></span>
        <div class="guide-path"><b>Все работы проекта</b><small>Задачи и вехи с этим проектом в карточке</small></div>
        <span class="guide-arrow" aria-hidden="true"></span><strong class="guide-destination">Гант проекта</strong>
      </div>
      <div class="guide-route">
        <strong class="guide-origin">План</strong><span class="guide-arrow" aria-hidden="true"></span>
        <div class="guide-path">
          <div><b>Работы, выбранные напрямую</b></div>
          <div><b>Работы по OKR</b><small>Цель → ключевой результат → связанные работы</small></div>
        </div>
        <span class="guide-arrow" aria-hidden="true"></span><strong class="guide-destination">Гант плана</strong>
      </div>
      <p>Одна работа может входить в несколько планов. В каждом она учитывается один раз. Гант показывает сроки и зависимости, а доска OKR — цели, критерии и замеры ключевых результатов.</p>
    </figure>
    <section class="guide-links">
      <h2>Почему проект виден в плане</h2>
      <dl>
        <div><dt>Проект <span class="guide-relation">→</span> План</dt><dd>Вы включили проект в план. Работы для Ганта можно выбрать позже.</dd></div>
        <div><dt>Работа проекта <span class="guide-relation">→</span> План</dt><dd>Выбрана задача или веха проекта. Она видна на Ганте периода и на Ганте проекта.</dd></div>
        <div><dt>Проект <span class="guide-relation">→</span> Ключевой результат</dt><dd>Проект указан в КР как контекст. Его карточка даёт материалы для оценки результата. Работы для этого КР выбирают отдельно.</dd></div>
      </dl>
    </section>
    <section class="guide-example">
      <h2>Один проект в двух Гантах</h2>
      <p>Учебный пример «Обновление мастерской». Сравните весь год с работами, которые вошли в IV квартал.</p>
      <fieldset><legend class="sr-only">Какие работы показать в примере</legend>
        <label><input type="radio" name="workshop-view" value="project" checked><span>Гант проекта</span></label>
        <label><input type="radio" name="workshop-view" value="plan"><span>Гант квартала</span></label>
      </fieldset>
      <p class="guide-selection" role="status"></p>
      <div class="guide-quarter-axis" aria-hidden="true"><span>I кв.</span><span>II кв.</span><span>III кв.</span><span>IV кв.</span></div>
      <ul class="guide-timeline"></ul>
    </section>
    <section class="guide-changes">
      <h2>Где сохраняются изменения</h2>
      <p>Решение включить проект и выбор его работ сохраняются в карточке плана. Срок и прогноз живут в карточке работы и видны во всех активных планах с этой работой. Закрытый план хранит снимок на момент закрытия.</p>
      <p>После ручной правки в вебе попросите в чате «Разбери ручные правки плана». Codex прочитает изменения, сверит их с записями и отметит обработанные.</p>
    </section>`;
  const filters = new URLSearchParams();
  for (const key of ["view", "q", "direction", "page"]) {
    if (params.get(key)) filters.set(key, params.get(key));
  }
  if (filters.size) page.querySelector(".back-link").href = "#activity?" + filters;
  const list = page.querySelector(".guide-timeline");
  const status = page.querySelector(".guide-selection");
  function draw(quarter) {
    list.replaceChildren();
    status.textContent = quarter ? "2 работы для КР «Сократить время поиска инструмента»" : "4 работы годового проекта";
    for (const work of workshop.filter(row => !quarter || row.quarter)) {
      const row = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = work.title;
      const months = document.createElement("small");
      months.textContent = work.months;
      const track = document.createElement("span");
      track.setAttribute("aria-hidden", "true");
      track.className = "guide-track";
      const bar = document.createElement("i");
      bar.style.gridColumn = work.start + " / " + (work.end + 1);
      track.append(bar);
      row.append(name, months, track);
      list.append(row);
    }
  }
  page.querySelector("fieldset").addEventListener("change", event => draw(event.target.value === "plan"));
  draw(false);
  return page;
}
