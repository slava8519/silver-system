// Markdown destinations are relative to the task card, including cards in subfolders.
export function localNote(cardPath, href, {wiki = false} = {}) {
  const hash = href.indexOf("#");
  const relative = hash < 0 ? href : href.slice(0, hash);
  let subpath;
  if (hash >= 0) {
    try { subpath = wiki ? href.slice(hash) : decodeURIComponent(href.slice(hash)); } catch { return null; }
    if (/[\u0000-\u001f\u007f]/.test(subpath)) return null;
    if (subpath === "#") subpath = undefined;
  }
  if (!relative) return subpath ? {path: cardPath, subpath} : null;
  if (/^(?:\/|[a-z][\w+.-]*:)/i.test(relative) || relative.includes("?")) return null;
  const wikiPath = wiki && relative.includes("/") && !relative.startsWith("./") && !relative.startsWith("../");
  const parts = wikiPath ? [] : cardPath.split("/").slice(0, -1);
  for (const raw of relative.split("/")) {
    let part;
    try { part = wiki ? raw : decodeURIComponent(raw); } catch { return null; }
    if (part === ".") continue;
    if (part === "..") {
      if (raw !== ".." || !parts.length) return null;
      parts.pop();
    } else {
      if (!part || part.startsWith(".") || /[\\/#:\u0000-\u001f\u007f]/.test(part)) return null;
      parts.push(part);
    }
  }
  if (wiki && parts.length && !/\.md$/i.test(parts.at(-1))) parts[parts.length - 1] += ".md";
  return /\.md$/i.test(parts.at(-1) || "") ? {path: parts.join("/"), subpath} : null;
}

