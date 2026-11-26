const obsidianOrigin = "app://obsidian.md";

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
  if (url.origin !== page.origin || !/^\/(?:$|about\.html$|(?:source|report-source|evidence|original|attachment)\/)/.test(url.pathname)) return href;
  url.searchParams.set("embed", "workspace");
  url.searchParams.set("binding", page.searchParams.get("binding") || "");
  return localRoute(url);
}

export function openObsidianSource(path, line) {
  window.parent.postMessage({type: "second-brain-open-source", path, line}, obsidianOrigin);
}

export function connectObsidianWorkspace(frame) {
  if (frame.parent === frame || new URLSearchParams(frame.location.search).get("embed") !== "workspace") return;
  for (const link of frame.document.querySelectorAll("a[href]")) {
    const href = link.getAttribute("href");
    const bound = workspaceLink(href, frame.location.href);
    if (bound !== href) link.setAttribute("href", bound);
  }
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
