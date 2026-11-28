const obsidianOrigin = "app://obsidian.md";
const workspacePath = /^\/(?:$|about\.html$|(?:source|report-source|evidence|original|attachment)\/)/;

function localRoute(url) {
  return url.pathname + url.search + url.hash;
}

export function browserRoute(href) {
  const url = new URL(href);
  url.searchParams.delete("embed");
  url.searchParams.delete("binding");
  return localRoute(url);
}

// Keep hash-only links intact: map and Gantt also inspect their original href.
export function workspaceLink(href, current) {
  if (href.startsWith("#")) return href;
  const page = new URL(current);
  if (page.searchParams.get("embed") !== "workspace") return href;
  const url = new URL(href, page);
  if (url.origin !== page.origin || !workspacePath.test(url.pathname)) return href;
  url.searchParams.set("embed", "workspace");
  url.searchParams.set("binding", page.searchParams.get("binding") || "");
  return localRoute(url);
}

export function openObsidianSource(path, line, subpath) {
  window.parent.postMessage({type: "second-brain-open-source", path, line, ...(subpath ? {subpath} : {})}, obsidianOrigin);
}

export function connectObsidianWorkspace(frame) {
  if (frame.parent === frame || new URLSearchParams(frame.location.search).get("embed") !== "workspace") return;
  for (const link of frame.document.querySelectorAll("a[href]")) {
    const href = link.getAttribute("href");
    const bound = workspaceLink(href, frame.location.href);
    if (bound !== href) link.setAttribute("href", bound);
  }
  // The embedded workspace has no popup permission. Follow local documents in
  // this frame and pass the reader's external web links to the desktop host.
  function openLink(event) {
    if (event.defaultPrevented || (event.type === "auxclick" ? event.button !== 1 : event.button !== 0)) return;
    const link = event.target.closest?.('a[target="_blank"][href]');
    if (!link || link.hasAttribute("download")) return;
    let dest;
    try { dest = new URL(link.href, frame.location.href); } catch { return; }
    if (!["https:", "http:"].includes(dest.protocol)) return;
    if (dest.origin === frame.location.origin) {
      if (!workspacePath.test(dest.pathname)) return;
      event.preventDefault();
      frame.location.assign(workspaceLink(dest.href, frame.location.href));
    } else {
      event.preventDefault();
      frame.parent.postMessage({type: "second-brain-open-web-link", href: dest.href}, obsidianOrigin);
    }
  }
  frame.addEventListener("click", openLink);
  frame.addEventListener("auxclick", openLink);
  function report() {
    frame.parent.postMessage({type: "second-brain-location", route: browserRoute(frame.location.href)}, obsidianOrigin);
  }
  // Chromium reports pushState/replaceState here too (selected Gantt row, map filters).
  frame.navigation?.addEventListener("currententrychange", report);
  frame.addEventListener("hashchange", report);
  frame.addEventListener("pageshow", report);
  report();
}

if (typeof window !== "undefined") connectObsidianWorkspace(window);
