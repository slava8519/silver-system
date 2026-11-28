import {timelineDates} from "./gantt-axis.js";

const storageKey = "second-brain.gantt.layers.v1";
const defaults = ["range", "now", "due"];
const keys = new Set([...defaults, ...Object.keys(timelineDates), "project", "relations", "notes", "legend"]);

export function readLayers() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    if (Array.isArray(saved)) return new Set(saved.filter(key => keys.has(key)));
  } catch { /* Storage is optional in an embedded editor. */ }
  return new Set(defaults);
}

export function saveLayers(layers) {
  try { localStorage.setItem(storageKey, JSON.stringify([...layers])); } catch { /* Keep the session preference. */ }
}

export function layerPicker(node, layers, cursorLabel, projectView, change) {
  const trigger = node("button", "gantt-tool gantt-layer-trigger", "Вид");
  trigger.type = "button";
  trigger.setAttribute("aria-label", "Вид диаграммы");
  const panel = node("div", "gantt-layer-picker");
  panel.popover = "auto";
  panel.setAttribute("role", "group");
  panel.setAttribute("aria-label", "Вид диаграммы");
  trigger.popoverTargetElement = panel;
  const groups = [
    ["Шкала", [["range", "Полосы готовности"], ["now", cursorLabel]]],
    ["Даты работ и вех", Object.entries(timelineDates)],
    ["Дополнительно", [...(projectView ? [["project", "Границы проекта"]] : []),
      ["relations", "Связи выбранной работы"], ["notes", "Подписи и знаки статуса"], ["legend", "Легенда"]]]
  ];
  for (const [title, options] of groups) {
    const group = node("fieldset");
    group.append(node("legend", "", title));
    for (const [key, label] of options) {
      const line = node("label");
      const check = node("input");
      check.type = "checkbox";
      check.checked = layers.has(key);
      check.addEventListener("change", () => {
        if (check.checked) layers.add(key);
        else layers.delete(key);
        saveLayers(layers);
        change();
      });
      line.append(check, node("span", "", label));
      group.append(line);
    }
    panel.append(group);
  }
  panel.addEventListener("beforetoggle", event => {
    if (event.newState === "open") positionPicker(trigger, panel);
  });
  return [trigger, panel];
}

// A top-layer popover stays clear of the scrolling toolbar's clipping edge.
export function positionPicker(trigger, panel) {
  const rect = trigger.getBoundingClientRect();
  const margin = 8;
  const width = Math.min(258, document.documentElement.clientWidth - 2 * margin);
  panel.style.width = width + "px";
  panel.style.left = Math.max(margin, Math.min(rect.right - width, innerWidth - width - margin)) + "px";
  const below = innerHeight - rect.bottom, above = rect.top;
  panel.style.top = below >= above ? rect.bottom + 3 + "px" : "auto";
  panel.style.bottom = below >= above ? "auto" : innerHeight - rect.top + 3 + "px";
  panel.style.maxHeight = Math.max(40, Math.max(above, below) - margin - 3) + "px";
}
