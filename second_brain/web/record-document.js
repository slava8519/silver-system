import {reportDocument} from "./report-document.js";
import {localNote} from "./note-links.js";
import {openObsidianSource} from "./obsidian-workspace.js";

export function recordDocument(record, markdown, ui, {omitTitle, nativeLink, reportLinks = false} = {}) {
  function fileLink(label, href, wiki = false) {
    if (!wiki && (/^https?:\/\//i.test(href) ||
        (reportLinks && /^\/(?:#|source\/|report-source\/|evidence\/|original\/)/.test(href)))) {
      const a = ui.link(label, href, "text-link");
      if (!ui.embedded || !href.startsWith("/")) {
        a.target = "_blank";
        a.rel = "noopener noreferrer";
      }
      return a;
    }
    if (!record.path) return label;
    // In a saved project these generated links cite the exact line of a text original.
    const line = /^Источник, строка (\d+)$/.exec(label)?.[1];
    const toRoot = "../".repeat(record.path.split("/").length - 1);
    if (line && !wiki && Number.isSafeInteger(Number(line)) && Number(line) > 0 && href.startsWith(toRoot)) {
      const archive = /^Sources\/Originals\/([a-f0-9]{64})\.(?:md|txt)$/.exec(href.slice(toRoot.length));
      if (archive) return ui.link("Оригинал · строка " + line, "/original/" + archive[1] + "?start=" + line, "text-link");
    }
    const target = localNote(record.path, href, {wiki});
    if (!target) return label;
    const shortName = wiki && /^[^/#]+(?:#|$)/.test(href);
    const cardFolder = /^(?:Directions|Notes|Projects|Archive\/ProjectHistory)\//.test(target.path);
    let url;
    if (!ui.compact && (cardFolder || shortName)) {
      const query = new URLSearchParams();
      if (target.subpath) query.set("section", target.subpath.slice(1));
      if (target.path === record.path && record.id && record.type !== "markdown") {
        url = "#record/" + encodeURIComponent(record.id) + (query.size ? "?" + query : "");
      } else {
        query.set("path", cardFolder ? target.path : target.path.split("/").pop());
        if (shortName) query.set("short", "1");
        url = "#record?" + query;
      }
    } else {
      // Non-card folders have their own visibility rules and stay in the native reader.
      if (nativeLink) {
        const native = nativeLink(label, target.path, null, null, target.subpath);
        native.title = target.path + (target.subpath || "");
        return native;
      }
      if (!record.vaultName) return label;
      url = "obsidian://open?" + new URLSearchParams({vault:record.vaultName, file:target.path + (target.subpath || "")});
    }
    const a = ui.link(label, url, "text-link");
    a.title = target.path + (target.subpath || "");
    if (ui.embedded && url.startsWith("obsidian:")) a.addEventListener("click", event => {
      event.preventDefault();
      openObsidianSource(target.path, null, target.subpath);
    });
    return a;
  }
  const doc = reportDocument(markdown, fileLink, {wikiLink:(label, href) => fileLink(label, href, true), omitTitle});
  doc.className += " record-prose";
  return doc;
}

export function recordSection(container, section) {
  if (!section) return null;
  if (section.startsWith("^")) {
    return [...container.querySelectorAll('[id^="md-block-"]')].find(el => el.id === "md-block-" + section.slice(1));
  }
  const normalize = text => text.normalize("NFC").trim().toLowerCase();
  const wanted = normalize(section);
  const headings = [...container.querySelectorAll("h1,h2,h3,h4,h5,h6")];
  return headings.find(el => normalize(el.textContent) === wanted) ||
    headings.find(el => normalize(el.textContent).replace(/[^\p{L}\p{N}\s_-]/gu, "").replace(/\s+/g, "-") === wanted);
}
