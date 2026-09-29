"use strict";

// --- Settings ---

const NEW_MS = 15 * 60e3;          // a story counts as "New" for 15 minutes
const REFRESH_MS = 5 * 60e3;       // background check for fresh data
const TICK_MS = 30e3;              // update ages, New and Trending
const DAY_MS = 864e5;
const READ_TTL_MS = 7 * DAY_MS;    // forget read links after a week
const ANIMATED_ROWS = 20;          // only rows on the first screen fade in
const THEMES = { day: "#fafaf8", night: "#120a1a", forest: "#0b0e14" };
const KEYS = {
  feed: "onimugen_v2_feed",
  bookmarks: "onimugen_v1_bookmarks",
  read: "onimugen_v1_read",
  sources: "onimugen_v1_muted_sources",
  keywords: "onimugen_v1_muted_keywords",
  theme: "om-theme",
};
const BADGES = [  // [label, class, test]
  ["New", "badge-new", () => true],                         // shown by CSS only while .is-new
  ["Rumour", "badge-rumour", s => has(s, "reddit")],
  ["PlayStation", "badge-ps", s => s.official && has(s, "playstation")],
  ["Xbox", "badge-xbox", s => s.official && has(s, "xbox")],
  ["Nintendo", "badge-nintendo", s => s.official && has(s, "nintendo")],
  ["Trending", "badge-hot", () => true],                    // shown by CSS only while .is-hot
  ["JP-EN", "badge-jp", s => s.translated],
];
const BOOKMARK_ICON = '<svg viewBox="0 0 24 24"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>';


// --- Storage ---

const store = {
  get(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
  },
};

const state = {
  data: null,       // { generatedAt, trendingThreshold, halfLifeHours, articles }
  rows: [],         // one entry per rendered story
  rowByItem: new WeakMap(),
  filter: "all",
  query: "",
  keywordRe: null,
  bookmarks: store.get(KEYS.bookmarks, {}),
  read: loadRead(),
  mutedSources: new Set(Object.keys(store.get(KEYS.sources, {}))),
  keywords: store.get(KEYS.keywords, []),
  lastCheck: 0,
};


// --- Helpers ---

const $ = id => document.getElementById(id);
const has = (story, tag) => !!story.tags?.includes(tag);
const safeUrl = url => (/^https?:\/\//i.test(url) ? url : "#");

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function link(url, text) {
  const a = el("a", null, text);
  a.href = safeUrl(url);
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

function ago(ts, now) {
  const m = Math.floor((now - ts) / 60e3);
  return m < 1 ? "Now" : m < 60 ? m + "m" : Math.floor(m / 60) + "h";
}

function loadRead() {
  const now = Date.now();
  const read = {};
  for (const [url, ts] of Object.entries(store.get(KEYS.read, {}))) {
    const t = ts === true ? now : ts;  // the old site stored `true`
    if (now - t < READ_TTL_MS) read[url] = t;
  }
  store.set(KEYS.read, read);
  return read;
}


// --- Favicons ---
// Google's favicon service, with a coloured letter when a site has no usable icon.
// Once a domain fails it goes straight to the letter for the rest of the visit.

const badIcons = new Set();

function logo(domain, name, className) {
  const initial = (name || "?").charAt(0).toUpperCase();
  if (!domain || badIcons.has(domain)) return el("span", className + " letter", initial);
  const img = el("img", className);
  img.alt = "";
  img.loading = "lazy";
  img.decoding = "async";
  img.dataset.initial = initial;
  img.dataset.domain = domain;
  img.src = "https://www.google.com/s2/favicons?sz=64&domain=" + encodeURIComponent(domain);
  return img;
}

function useLetter(img) {
  badIcons.add(img.dataset.domain);
  img.replaceWith(el("span", img.className + " letter", img.dataset.initial));
}

// Image events do not bubble, so listen in the capture phase once for the whole page.
document.addEventListener("error", e => { if (e.target.dataset?.initial) useLetter(e.target); }, true);
document.addEventListener("load", e => {
  // Unknown sites get a 16px globe; treat that as missing.
  if (e.target.dataset?.initial && e.target.naturalWidth <= 16) useLetter(e.target);
}, true);


// --- Feed rendering ---

function buildRow(story, index) {
  const item = el("li", "item");
  const outlets = [story.source, ...(story.group || []).map(m => m.source)];

  if (index < ANIMATED_ROWS) {
    item.classList.add("enter");
    item.style.animationDelay = index * 15 + "ms";
  }
  if (state.read[story.link]) item.classList.add("read");

  const time = el("time", "item-date");
  const body = el("div", "item-body");
  const title = link(story.link, story.title);
  for (const [label, cls, test] of BADGES) if (test(story)) title.append(el("span", "badge " + cls, label));
  body.append(title);

  const side = el("span", "item-side");
  if (story.group) {
    item.classList.add("has-group");
    const label = story.sources > 1 ? story.sources + " Sources " : story.group.length + 1 + " Articles ";
    const toggle = el("button", "group-toggle", label);
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.append(el("span", "chevron", "▾"));
    side.append(toggle);
  } else {
    side.append(logo(story.domain, story.source, "item-logo"));
  }

  const bm = el("button", "bm-btn");
  bm.type = "button";
  bm.setAttribute("aria-label", "Bookmark");
  bm.innerHTML = BOOKMARK_ICON;
  bm.classList.toggle("on", !!state.bookmarks[story.link]);

  item.append(time, body, side, bm);
  return {
    story, item, bm, time, outlets,
    text: (story.title + " " + outlets.join(" ")).toLowerCase(),
    isNew: false,
    isHot: false,
  };
}

function render() {
  const cutoff = Date.now() - DAY_MS;
  const stories = state.data.articles.filter(s => s.date >= cutoff).sort((a, b) => b.date - a.date);
  state.rows = stories.map(buildRow);
  state.rowByItem = new WeakMap(state.rows.map(r => [r.item, r]));

  const frag = document.createDocumentFragment();
  for (const r of state.rows) frag.append(r.item);
  $("feed").replaceChildren(frag);

  buildSourcesMenu();
  tick();
}

// The list of every article in a group is only built the first time it is opened.
function buildDrawer(row) {
  const now = Date.now();
  const drawer = el("div", "drawer");
  for (const src of [row.story, ...row.story.group].sort((a, b) => a.date - b.date)) {
    const line = el("div", "drawer-row");
    line.append(logo(src.domain, src.source, "drawer-logo"), link(src.link, src.title), el("span", "drawer-date", ago(src.date, now)));
    drawer.append(line);
  }
  row.item.append(drawer);
  return drawer;
}

// Everything that depends on the current time, so nothing goes stale between fetches.
function tick() {
  if (!state.data) return;
  const now = Date.now();
  const { trendingThreshold, halfLifeHours } = state.data;
  for (const r of state.rows) {
    const s = r.story;
    const label = ago(s.date, now);
    if (r.time.textContent !== label) r.time.textContent = label;
    r.isNew = now - s.date < NEW_MS;
    r.isHot = s.sources * 0.5 ** ((now - s.date) / 36e5 / halfLifeHours) >= trendingThreshold;
    r.item.classList.toggle("is-new", r.isNew);
    r.item.classList.toggle("is-hot", r.isHot);
  }
  const age = Math.floor((now - state.data.generatedAt) / 60e3);
  $("last-updated").textContent =
    "Updated " + (age < 1 ? "just now" : age < 60 ? age + "m ago" : Math.floor(age / 60) + "h ago");
  applyFilter();
}


// --- Filtering ---

function matchesFilter(r) {
  switch (state.filter) {
    case "all": return true;
    case "new": return r.isNew;
    case "trending": return r.isHot;
    case "saved": return !!state.bookmarks[r.story.link];
    default: return has(r.story, state.filter);
  }
}

function applyFilter() {
  const { query, keywordRe, mutedSources } = state;
  let shown = 0;
  for (const r of state.rows) {
    const visible =
      (!query || r.text.includes(query)) &&
      !(keywordRe && keywordRe.test(r.story.title)) &&
      !r.outlets.every(o => mutedSources.has(o)) &&  // stays while any covering outlet is shown
      matchesFilter(r);
    r.item.hidden = !visible;
    shown += visible;
  }
  $("feed-empty").hidden = shown > 0 || !state.data;
}

function compileKeywords() {
  // Whole words only, so muting "ea" does not hide "death".
  const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  state.keywordRe = state.keywords.length
    ? new RegExp("(?<![\\p{L}\\p{N}])(?:" + state.keywords.map(escape).join("|") + ")(?![\\p{L}\\p{N}])", "iu")
    : null;
  $("keyword-btn").classList.toggle("has-muted", state.keywords.length > 0);
}


// --- Sources menu ---

function buildSourcesMenu() {
  const domains = new Map();
  for (const r of state.rows) {
    for (const s of [r.story, ...(r.story.group || [])]) if (!domains.has(s.source)) domains.set(s.source, s.domain);
  }
  // Keep outlets the reader hid even when they have nothing in today's feed, so they can be shown again.
  for (const name of state.mutedSources) if (!domains.has(name)) domains.set(name, "");

  const frag = document.createDocumentFragment();
  for (const name of [...domains.keys()].sort((a, b) => a.localeCompare(b))) {
    const row = el("button", "source-row");
    row.type = "button";
    row.dataset.name = name;
    row.append(logo(domains.get(name), name, "source-logo"), el("span", "source-name", name), el("span", "source-check", "✓"));
    frag.append(row);
  }
  $("sources-list").replaceChildren(frag);
  syncSources();
}

function syncSources() {
  for (const row of $("sources-list").children) row.classList.toggle("on", !state.mutedSources.has(row.dataset.name));
  const n = state.mutedSources.size;
  $("sources-count").textContent = n ? "(" + n + " hidden)" : "";
}

function saveSources() {
  store.set(KEYS.sources, Object.fromEntries([...state.mutedSources].map(n => [n, true])));
  syncSources();
  applyFilter();
}


// --- Bookmarks, read history, keywords ---

function toggleBookmark(row) {
  const s = row.story;
  if (state.bookmarks[s.link]) delete state.bookmarks[s.link];
  else state.bookmarks[s.link] = { title: s.title, link: s.link, domain: s.domain, sourceName: s.source, savedAt: Date.now() };
  if (!store.set(KEYS.bookmarks, state.bookmarks)) alert("Bookmark storage is full. Remove some bookmarks and try again.");
  row.bm.classList.toggle("on", !!state.bookmarks[s.link]);
  if (state.filter === "saved") applyFilter();
}

function removeBookmark(url) {
  delete state.bookmarks[url];
  store.set(KEYS.bookmarks, state.bookmarks);
  for (const r of state.rows) if (r.story.link === url) r.bm.classList.remove("on");
  if (state.filter === "saved") applyFilter();
}

function markRead(row) {
  state.read[row.story.link] = Date.now();
  store.set(KEYS.read, state.read);
  row.item.classList.add("read");
}

function renderBookmarks() {
  const saved = Object.values(state.bookmarks).sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0));
  const frag = document.createDocumentFragment();
  for (const b of saved) {
    const li = el("li", "panel-row");
    const main = el("div", "panel-main");
    main.append(link(b.link, b.title), el("span", "panel-sub", b.sourceName || b.domain || ""));
    const remove = el("button", "remove", "×");
    remove.type = "button";
    remove.dataset.link = b.link;
    remove.setAttribute("aria-label", "Remove bookmark");
    li.append(logo(b.domain, b.sourceName, "panel-logo"), main, remove);
    frag.append(li);
  }
  $("bookmarks-list").replaceChildren(frag);
  $("bookmarks-empty").hidden = saved.length > 0;
}

function renderKeywords() {
  const frag = document.createDocumentFragment();
  for (const kw of [...state.keywords].sort()) {
    const li = el("li", "panel-row");
    const remove = el("button", "remove", "×");
    remove.type = "button";
    remove.dataset.kw = kw;
    remove.setAttribute("aria-label", "Unmute " + kw);
    li.append(el("span", "panel-main", kw), remove);
    frag.append(li);
  }
  $("keyword-list").replaceChildren(frag);
  $("keyword-empty").hidden = state.keywords.length > 0;
}

function setKeywords(list) {
  state.keywords = list;
  store.set(KEYS.keywords, list);
  compileKeywords();
  renderKeywords();
  applyFilter();
}


// --- Menus, panels, theme ---

const MENUS = [["filter-menu", "filter-btn"], ["sources-menu", "sources-btn"]];

function closeMenus(except) {
  for (const [menu, btn] of MENUS) {
    if (menu === except) continue;
    $(menu).classList.remove("open");
    $(btn).setAttribute("aria-expanded", "false");
  }
  if (except !== "search-wrap" && !state.query) $("search-wrap").classList.remove("open");
}

function toggleMenu(menu, btn) {
  closeMenus(menu);
  const open = $(menu).classList.toggle("open");
  $(btn).setAttribute("aria-expanded", String(open));
}

let panelOpener = null;

function openPanel(name, opener) {
  closeMenus();
  panelOpener = opener;
  if (name === "bookmarks") renderBookmarks();
  else renderKeywords();
  $(name + "-overlay").hidden = false;
  document.body.classList.add("locked");
  (name === "keywords" ? $("keyword-input") : $(name + "-overlay").querySelector("[data-close]")).focus();
}

function closePanels() {
  const open = document.querySelector(".overlay:not([hidden])");
  if (!open) return;
  open.hidden = true;
  document.body.classList.remove("locked");
  panelOpener?.focus();  // return focus to the button that opened the panel
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("meta-theme-color").content = THEMES[theme];
  for (const dot of document.querySelectorAll(".dot")) dot.classList.toggle("active", dot.dataset.t === theme);
}


// --- Data ---

async function load(force = false) {
  state.lastCheck = Date.now();
  try {
    // "no-cache" asks the server whether the file changed, so an unchanged file is a tiny 304.
    const res = await fetch("data.json", { cache: force ? "reload" : "no-cache" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    if (!Array.isArray(data.articles)) throw new Error("unexpected data format");
    if (data.generatedAt !== state.data?.generatedAt) {
      state.data = data;
      render();
      store.set(KEYS.feed, data);
    } else {
      tick();
    }
  } catch (err) {
    console.warn("Feed load failed:", err.message);
    if (!state.data) {
      $("feed-empty").textContent = "Could not load the feed. Please try again.";
      $("feed-empty").hidden = false;
    }
  } finally {
    hideSplash();
  }
}

function hideSplash() {
  const splash = $("splash");
  if (!splash || splash.classList.contains("fade")) return;
  splash.addEventListener("transitionend", () => splash.remove(), { once: true });
  setTimeout(() => splash.remove(), 1000);  // in case transitions are switched off
  splash.classList.add("fade");
}


// --- Events: one delegated listener per area, wired once ---

function wire() {
  const feed = $("feed");
  const rowOf = node => state.rowByItem.get(node.closest(".item"));

  feed.addEventListener("click", e => {
    const row = e.target.closest(".item") && rowOf(e.target);
    if (!row) return;
    if (e.target.closest(".bm-btn")) return toggleBookmark(row);
    const toggle = e.target.closest(".group-toggle");
    if (toggle) {
      const drawer = row.item.querySelector(".drawer") || buildDrawer(row);
      toggle.setAttribute("aria-expanded", String(drawer.classList.toggle("open")));
      return;
    }
    if (e.target.closest(".item-body a")) markRead(row);
  });
  feed.addEventListener("auxclick", e => {  // middle-click opens a tab too
    if (e.button === 1 && e.target.closest(".item-body a")) markRead(rowOf(e.target));
  });

  $("refresh-btn").addEventListener("click", () => load(true));

  $("search-btn").addEventListener("click", () => {
    closeMenus("search-wrap");
    if ($("search-wrap").classList.toggle("open")) $("search-input").focus();
  });
  $("search-input").addEventListener("input", e => {
    state.query = e.target.value.trim().toLowerCase();
    applyFilter();
  });

  $("filter-btn").addEventListener("click", () => toggleMenu("filter-menu", "filter-btn"));
  $("filter-menu").addEventListener("click", e => {
    const opt = e.target.closest(".opt");
    if (!opt) return;
    for (const o of $("filter-menu").children) o.classList.toggle("active", o === opt);
    state.filter = opt.dataset.filter;
    closeMenus();
    applyFilter();
  });

  $("sources-btn").addEventListener("click", () => toggleMenu("sources-menu", "sources-btn"));
  $("sources-menu").addEventListener("click", e => {
    const row = e.target.closest(".source-row");
    const bulk = e.target.closest("[data-sources]");
    if (row) {
      const name = row.dataset.name;
      if (!state.mutedSources.delete(name)) state.mutedSources.add(name);
    } else if (bulk) {
      state.mutedSources = bulk.dataset.sources === "hide"
        ? new Set([...$("sources-list").children].map(r => r.dataset.name))
        : new Set();
    } else {
      return;
    }
    saveSources();
  });

  for (const btn of document.querySelectorAll("[data-open]")) {
    btn.addEventListener("click", () => openPanel(btn.dataset.open, btn));
  }
  for (const overlay of document.querySelectorAll(".overlay")) {
    overlay.addEventListener("click", e => {
      if (e.target === overlay || e.target.closest("[data-close]")) closePanels();
    });
  }

  $("bookmarks-list").addEventListener("click", e => {
    const btn = e.target.closest(".remove");
    if (!btn) return;
    removeBookmark(btn.dataset.link);
    btn.closest("li").remove();
    $("bookmarks-empty").hidden = $("bookmarks-list").children.length > 0;
  });

  $("keyword-form").addEventListener("submit", e => {
    e.preventDefault();
    const input = $("keyword-input");
    const kw = input.value.trim().toLowerCase();
    input.value = "";
    if (kw && !state.keywords.includes(kw)) setKeywords([...state.keywords, kw]);
  });
  $("keyword-list").addEventListener("click", e => {
    const btn = e.target.closest(".remove");
    if (btn) setKeywords(state.keywords.filter(k => k !== btn.dataset.kw));
  });

  $("theme-btn").addEventListener("click", () => {
    const names = Object.keys(THEMES);
    const next = names[(names.indexOf(document.documentElement.dataset.theme) + 1) % names.length];
    localStorage.setItem(KEYS.theme, next);
    setTheme(next);
  });

  $("back-to-top").addEventListener("click", () => scrollTo({ top: 0, behavior: "smooth" }));

  document.addEventListener("click", e => {
    if (!e.target.closest?.(".controls, #sources-bar")) closeMenus();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") { closeMenus(); closePanels(); }
    // "/" jumps to search, like many news sites
    if (e.key === "/" && !e.target.closest?.("input, textarea")) {
      e.preventDefault();
      closeMenus("search-wrap");
      $("search-wrap").classList.add("open");
      $("search-input").focus();
    }
  });

  let scrollQueued = false;
  addEventListener("scroll", () => {
    if (scrollQueued) return;
    scrollQueued = true;
    requestAnimationFrame(() => {
      scrollQueued = false;
      $("main-header").classList.toggle("scrolled", scrollY > 20);
      $("back-to-top").classList.toggle("visible", scrollY > 400);
    });
  }, { passive: true });

  // Pause background work while the tab is hidden, and catch up as soon as it is visible again.
  setInterval(() => { if (!document.hidden) tick(); }, TICK_MS);
  setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    if (Date.now() - state.lastCheck > REFRESH_MS) load();
    else tick();
  });
}


// --- Start ---

setTheme(THEMES[document.documentElement.dataset.theme] ? document.documentElement.dataset.theme : "day");
compileKeywords();
wire();

// Show the last copy straight away, then check for a newer one.
const cached = store.get(KEYS.feed, null);
if (Array.isArray(cached?.articles)) {
  state.data = cached;
  render();
  hideSplash();
}
localStorage.removeItem("onimugen_v1_gaming");  // saved feed from the old site's format
load();
