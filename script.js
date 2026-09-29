"use strict";

// --- Settings ---

const NEW_MS = 15 * 60e3;         // a story counts as New for 15 minutes
const REFRESH_MS = 5 * 60e3;      // background check for fresh data
const TICK_MS = 30e3;             // update ages, New and Trending
const DAY_MS = 864e5;
const READ_TTL_MS = 7 * DAY_MS;   // forget read links after a week
const FIRST_PAINT = 40;           // rows drawn before the rest, so the top of the page appears at once
const THEMES = { poden: "#000000", mono: "#000000", blossom: "#ffedf5" };
const KEYS = {
  feed: "onimugen_v3_feed",
  bookmarks: "onimugen_v1_bookmarks",
  read: "onimugen_v1_read",
  sources: "onimugen_v1_muted_sources",
  keywords: "onimugen_v1_muted_keywords",
  filter: "om-filter",
  theme: "om-theme",
};
const TAG_LABELS = { playstation: "PlayStation", xbox: "Xbox", nintendo: "Nintendo", pc: "PC" };
const ICON_SAVE = '<svg viewBox="0 0 24 24"><path d="M18 21l-6-4-6 4V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z"/></svg>';
const ICON_CHEVRON = '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>';


// --- Storage and state ---

const store = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } },
};

const state = {
  data: null,
  rows: [],               // { s: story, el, save, time, text, outlets, isNew, isHot }
  rowById: new Map(),
  filter: store.get(KEYS.filter, "all"),
  query: "",
  keywordRe: null,
  bookmarks: store.get(KEYS.bookmarks, {}),
  read: loadRead(),
  muted: new Set(Object.keys(store.get(KEYS.sources, {}))),
  keywords: store.get(KEYS.keywords, []),
  lastCheck: 0,
};


// --- Helpers ---

const $ = id => document.getElementById(id);
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = s => String(s).replace(/[&<>"']/g, c => ESC[c]);
const safeUrl = url => (/^https?:\/\//i.test(url) ? url : "#");
const has = (s, tag) => !!s.tags && s.tags.includes(tag);

function ago(ts, now) {
  const m = Math.floor((now - ts) / 60e3);
  return m < 1 ? "now" : m < 60 ? m + "m" : Math.floor(m / 60) + "h";
}

function loadRead() {
  const now = Date.now(), read = {};
  for (const [url, ts] of Object.entries(store.get(KEYS.read, {}))) {
    const t = ts === true ? now : ts;  // the first version stored `true`
    if (now - t < READ_TTL_MS) read[url] = t;
  }
  store.set(KEYS.read, read);
  return read;
}

// Site icons are published with the site (icons/<domain>.png); a letter when there is none.
function iconHtml(src, cls = "") {
  const o = state.data.sources[src];
  return o && o.icon
    ? `<img class="fav ${cls}" src="icons/${esc(o.domain)}.png" alt="" width="20" height="20" loading="lazy" decoding="async">`
    : `<span class="letter ${cls}" aria-hidden="true">${esc((o ? o.name : "?").charAt(0))}</span>`;
}

const sourceName = i => state.data.sources[i]?.name || "";


// --- Rendering ---

function rowHtml(s) {
  const tags = [];
  if (has(s, "rumour")) tags.push('<span class="tag rumour">Rumour</span>');
  for (const t of ["playstation", "xbox", "nintendo", "pc"]) if (has(s, t)) tags.push(`<span class="tag">${TAG_LABELS[t]}</span>`);
  if (s.translated) tags.push('<span class="tag">JP → EN</span>');

  const group = s.group
    ? `<button type="button" class="more" aria-expanded="false">${s.sources > 1 ? s.sources + " sources" : s.group.length + 1 + " articles"}${ICON_CHEVRON}</button>`
    : "";
  return `${iconHtml(s.src)}<a class="title" href="${esc(safeUrl(s.link))}" target="_blank" rel="noopener">${esc(s.title)}</a>` +
    `<button type="button" class="save${state.bookmarks[s.link] ? " on" : ""}" aria-label="Bookmark">${ICON_SAVE}</button>` +
    `<div class="meta"><span class="src">${esc(sourceName(s.src))}</span><time></time>` +
    `<span class="tag new" hidden>New</span><span class="tag hot" hidden>Trending</span>${tags.join("")}${group}</div>`;
}

function buildRow(s, i) {
  const el = document.createElement("li");
  el.className = "row";
  el.dataset.i = i;
  if (state.read[s.link]) el.classList.add("read");
  el.innerHTML = rowHtml(s);
  const outlets = [s.src, ...(s.group || []).map(m => m.src)];
  const tags = el.querySelectorAll(".meta .tag");
  return {
    s, el,
    save: el.querySelector(".save"),
    time: el.querySelector("time"),
    newTag: tags[0],
    hotTag: tags[1],
    outlets,
    text: (s.title + " " + outlets.map(sourceName).join(" ")).toLowerCase(),
    isNew: false,
    isHot: false,
  };
}

function render() {
  const cutoff = Date.now() - DAY_MS;
  const stories = state.data.articles.filter(s => s.date >= cutoff).sort((a, b) => b.date - a.date);
  state.rows = stories.map(buildRow);
  const feed = $("feed");

  // Draw the first screen now and the rest straight after, so the page is readable before all rows exist.
  // A timer rather than requestAnimationFrame: that never fires in background tabs.
  const first = document.createDocumentFragment();
  for (const r of state.rows.slice(0, FIRST_PAINT)) first.append(r.el);
  feed.replaceChildren(first);
  tick();
  const rows = state.rows;
  setTimeout(() => {
    if (rows !== state.rows) return;  // a newer render already replaced these
    const rest = document.createDocumentFragment();
    for (const r of rows.slice(FIRST_PAINT)) rest.append(r.el);
    feed.append(rest);
    applyFilter();
  }, 0);

  renderNotice();
  renderSources();
}

// Every article in a grouped story; built the first time it's opened.
function openGroup(row, btn) {
  let list = row.el.querySelector(".group");
  if (!list) {
    const now = Date.now();
    list = document.createElement("ol");
    list.className = "group";
    list.innerHTML = [row.s, ...row.s.group].sort((a, b) => a.date - b.date).map(a =>
      `<li>${iconHtml(a.src)}<a href="${esc(safeUrl(a.link))}" target="_blank" rel="noopener">${esc(a.title)}` +
      `<span class="sr"> — ${esc(sourceName(a.src))}</span></a><time>${ago(a.date, now)}</time></li>`).join("");
    list.hidden = true;
    row.el.append(list);
  }
  list.hidden = !list.hidden;
  row.el.classList.toggle("open", !list.hidden);
  btn.setAttribute("aria-expanded", String(!list.hidden));
}

// Everything that depends on the current time, so nothing goes stale between fetches.
function tick() {
  if (!state.data) return;
  const now = Date.now();
  const { trendingThreshold: limit, halfLifeHours: half } = state.data;
  for (const r of state.rows) {
    const s = r.s;
    const label = ago(s.date, now);
    if (r.time.textContent !== label) r.time.textContent = label;
    const isNew = now - s.date < NEW_MS;
    const isHot = s.sources * 0.5 ** ((now - s.date) / 36e5 / half) >= limit;
    if (isNew !== r.isNew) { r.isNew = isNew; r.newTag.hidden = !isNew; }
    if (isHot !== r.isHot) { r.isHot = isHot; r.hotTag.hidden = !isHot; r.el.classList.toggle("hot", isHot); }
  }
  const age = Math.floor((now - state.data.generatedAt) / 60e3);
  const status = $("status");
  status.textContent = "Updated " + (age < 1 ? "just now" : age < 60 ? age + " min ago" : Math.floor(age / 60) + " h ago");
  status.classList.toggle("warn", age > 60);  // the updater has stopped
  applyFilter();
}


// --- Filtering ---

function matches(r) {
  switch (state.filter) {
    case "all": return true;
    case "new": return r.isNew;
    case "trending": return r.isHot;
    case "jp": return !!r.s.jp;
    default: return has(r.s, state.filter);
  }
}

function applyFilter() {
  const { query, keywordRe, muted } = state;
  const counts = { all: 0, new: 0, trending: 0, rumour: 0, playstation: 0, xbox: 0, nintendo: 0, pc: 0, jp: 0 };
  let shown = 0;
  for (const r of state.rows) {
    // A story stays while any outlet that covered it is shown.
    const allowed = (!query || r.text.includes(query)) && !(keywordRe && keywordRe.test(r.s.title)) &&
      !r.outlets.every(o => muted.has(sourceName(o)));
    if (allowed) {
      counts.all++;
      if (r.isNew) counts.new++;
      if (r.isHot) counts.trending++;
      if (r.s.jp) counts.jp++;
      if (r.s.tags) for (const t of r.s.tags) counts[t]++;
    }
    const visible = allowed && matches(r);
    if (r.el.hidden === visible) r.el.hidden = !visible;
    shown += visible;
  }
  for (const chip of $("filters").children) {
    const n = counts[chip.dataset.f];
    const span = chip.querySelector(".n") || chip.appendChild(Object.assign(document.createElement("span"), { className: "n" }));
    span.textContent = n || "";
    chip.classList.toggle("zero", !n);
  }
  $("empty").hidden = shown > 0 || !state.data;
}

function setFilter(f) {
  state.filter = f;
  store.set(KEYS.filter, f);
  for (const c of $("filters").children) {
    c.classList.toggle("on", c.dataset.f === f);
    c.setAttribute("aria-pressed", String(c.dataset.f === f));
  }
  applyFilter();
}

function compileKeywords() {
  // Whole words only, so muting "ea" does not hide "death".
  const escRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  state.keywordRe = state.keywords.length
    ? new RegExp("(?<![\\p{L}\\p{N}])(?:" + state.keywords.map(escRe).join("|") + ")(?![\\p{L}\\p{N}])", "iu")
    : null;
  $("settings-btn").classList.toggle("dot", state.keywords.length > 0 || state.muted.size > 0);
}


// --- Source status, notices and settings ---

function renderNotice() {
  const d = state.data, parts = [];
  const failing = d.sources.filter(o => !o.ok);
  if (d.translation && d.translation.untranslated) {
    const n = d.translation.untranslated;
    parts.push(`<b>${n} Japanese headline${n === 1 ? "" : "s"}</b> couldn't be translated yet and ${n === 1 ? "is" : "are"} shown as published.`);
  }
  if (failing.length >= 5) parts.push(`<b>${failing.length} sources</b> aren't responding right now.`);
  $("notice").innerHTML = parts.join(" ");
  $("notice").hidden = !parts.length;
}

// How a source was read when its own feed failed, and plain-language reasons for failures.
const VIA = { alternate: "backup address", discovered: "new feed address", googlenews: "via Google News" };
function reason(error) {
  const code = /HTTP (\d+)/.exec(error)?.[1];
  if (code === "404" || code === "410") return "feed removed";
  if (code === "401" || code === "403" || code === "429") return "blocked";
  if (code && code >= 500) return "site error";
  return error;
}

function renderSources() {
  const now = Date.now();
  // Not responding first, so problems are seen straight away.
  const order = state.data.sources.map((o, i) => i).sort((a, b) => state.data.sources[a].ok - state.data.sources[b].ok);
  const list = order.map(i => {
    const o = state.data.sources[i];
    const on = !state.muted.has(o.name);
    const sub = o.ok
      ? `${o.count} ${o.count === 1 ? "story" : "stories"} today${VIA[o.via] ? " · " + VIA[o.via] : ""}`
      : `Not responding${o.error ? ` (${esc(reason(o.error))})` : ""}${o.lastOk ? " · last worked " + ago(o.lastOk, now) + " ago" : ""}`;
    return `<li><button type="button" role="switch" aria-checked="${on}" data-name="${esc(o.name)}">${iconHtml(i)}` +
      `<span class="main"><span class="name">${esc(o.name)}</span><span class="sub${o.ok ? "" : " bad"}">${sub}</span></span>` +
      `<span class="switch" aria-hidden="true"></span></button></li>`;
  });
  // Outlets the reader hid that no longer exist stay listed, so they can be shown again.
  for (const name of state.muted) {
    if (!state.data.sources.some(o => o.name === name)) {
      list.push(`<li><button type="button" role="switch" aria-checked="false" data-name="${esc(name)}"><span class="letter">${esc(name.charAt(0))}</span>` +
        `<span class="main"><span class="name">${esc(name)}</span><span class="sub">No longer in the feed</span></span><span class="switch"></span></button></li>`);
    }
  }
  $("src-list").innerHTML = list.join("");
  const failing = state.data.sources.filter(o => !o.ok).length;
  $("src-count").textContent = `${state.data.sources.length} · ${state.muted.size} hidden${failing ? ` · ${failing} not responding` : ""}`;
}

function saveMuted() {
  store.set(KEYS.sources, Object.fromEntries([...state.muted].map(n => [n, true])));
  compileKeywords();
  if (state.data) renderSources();
  applyFilter();
}

function renderKeywords() {
  $("mute-list").innerHTML = [...state.keywords].sort()
    .map(k => `<button type="button" data-kw="${esc(k)}" aria-label="Unmute ${esc(k)}">${esc(k)}</button>`).join("");
}

function setKeywords(list) {
  state.keywords = list;
  store.set(KEYS.keywords, list);
  compileKeywords();
  renderKeywords();
  applyFilter();
}

function renderBookmarks() {
  const saved = Object.values(state.bookmarks).sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0));
  $("saved-list").innerHTML = saved.map(b =>
    `<li><span class="main"><a href="${esc(safeUrl(b.link))}" target="_blank" rel="noopener">${esc(b.title)}</a>` +
    `<span class="sub">${esc(b.sourceName || b.domain || "")}</span></span>` +
    `<button type="button" class="x" data-link="${esc(b.link)}" aria-label="Remove bookmark">×</button></li>`).join("");
  $("saved-empty").hidden = saved.length > 0;
}

function toggleBookmark(row) {
  const s = row.s;
  if (state.bookmarks[s.link]) delete state.bookmarks[s.link];
  else state.bookmarks[s.link] = { title: s.title, link: s.link, sourceName: sourceName(s.src), savedAt: Date.now() };
  if (!store.set(KEYS.bookmarks, state.bookmarks)) alert("Bookmark storage is full. Remove some bookmarks and try again.");
  row.save.classList.toggle("on", !!state.bookmarks[s.link]);
}

function markRead(row) {
  state.read[row.s.link] = Date.now();
  store.set(KEYS.read, state.read);
  row.el.classList.add("read");
}

function setTheme(t) {
  // Switch instantly: with transitions on, colours already mid-transition keep the old theme.
  const root = document.documentElement;
  root.classList.add("no-anim");
  root.dataset.theme = t;
  getComputedStyle(root).color;  // apply the new colours before transitions come back
  requestAnimationFrame(() => root.classList.remove("no-anim"));
  $("theme-color").content = THEMES[t];
  for (const b of $("themes").children) b.setAttribute("aria-checked", String(b.dataset.t === t));
}


// --- Sheets ---

let opener = null;

function openSheet(id, from) {
  opener = from;
  if (id === "saved") renderBookmarks();
  else renderKeywords();
  $(id).hidden = false;
  document.body.classList.add("locked");
  $(id).querySelector("[data-close]").focus();
}

function closeSheet() {
  const open = document.querySelector(".sheet-wrap:not([hidden])");
  if (!open) return;
  open.hidden = true;
  document.body.classList.remove("locked");
  opener?.focus();
}


// --- Data ---

async function load(force = false) {
  state.lastCheck = Date.now();
  try {
    // "no-cache" asks the server whether the file changed, so an unchanged file is a tiny 304.
    const res = await fetch("data.json", { cache: force ? "reload" : "no-cache" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    if (!Array.isArray(data.articles) || !Array.isArray(data.sources)) throw new Error("unexpected data format");
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
      $("empty").textContent = "The feed couldn't be loaded. Check your connection and try again.";
      $("empty").hidden = false;
    }
  }
}


// --- Events: one delegated listener per area ---

function wire() {
  const feed = $("feed");
  const rowOf = el => state.rows[el.closest(".row")?.dataset.i];

  feed.addEventListener("click", e => {
    const row = rowOf(e.target);
    if (!row) return;
    if (e.target.closest(".save")) return toggleBookmark(row);
    const more = e.target.closest(".more");
    if (more) return openGroup(row, more);
    if (e.target.closest(".title")) markRead(row);
  });
  feed.addEventListener("auxclick", e => {  // middle-click opens a tab too
    if (e.button === 1 && e.target.closest(".title")) markRead(rowOf(e.target));
  });

  $("filters").addEventListener("click", e => {
    const chip = e.target.closest(".chip");
    if (chip) setFilter(chip.dataset.f);
  });

  let typing;
  $("q").addEventListener("input", e => {
    clearTimeout(typing);
    typing = setTimeout(() => { state.query = e.target.value.trim().toLowerCase(); applyFilter(); }, 60);
  });

  $("refresh").addEventListener("click", () => { scrollTo({ top: 0 }); load(true); });

  for (const b of document.querySelectorAll("[data-open]")) b.addEventListener("click", () => openSheet(b.dataset.open, b));
  for (const w of document.querySelectorAll(".sheet-wrap")) {
    w.addEventListener("click", e => { if (e.target === w || e.target.closest("[data-close]")) closeSheet(); });
  }

  $("saved-list").addEventListener("click", e => {
    const x = e.target.closest(".x");
    if (!x) return;
    delete state.bookmarks[x.dataset.link];
    store.set(KEYS.bookmarks, state.bookmarks);
    x.closest("li").remove();
    $("saved-empty").hidden = $("saved-list").children.length > 0;
    for (const r of state.rows) if (r.s.link === x.dataset.link) r.save.classList.remove("on");
  });

  $("themes").addEventListener("click", e => {
    const b = e.target.closest("[data-t]");
    if (!b) return;
    localStorage.setItem(KEYS.theme, b.dataset.t);
    setTheme(b.dataset.t);
  });

  $("mute-form").addEventListener("submit", e => {
    e.preventDefault();
    const kw = $("mute-input").value.trim().toLowerCase();
    $("mute-input").value = "";
    if (kw && !state.keywords.includes(kw)) setKeywords([...state.keywords, kw]);
  });
  $("mute-list").addEventListener("click", e => {
    const b = e.target.closest("[data-kw]");
    if (b) setKeywords(state.keywords.filter(k => k !== b.dataset.kw));
  });

  $("src-list").addEventListener("click", e => {
    const b = e.target.closest("[data-name]");
    if (!b) return;
    if (!state.muted.delete(b.dataset.name)) state.muted.add(b.dataset.name);
    saveMuted();
  });
  document.querySelector(".src-actions").addEventListener("click", e => {
    const b = e.target.closest("[data-all]");
    if (!b || !state.data) return;
    state.muted = b.dataset.all === "hide" ? new Set(state.data.sources.map(o => o.name)) : new Set();
    saveMuted();
  });

  document.addEventListener("keydown", e => {
    if (e.key === "Escape") {
      if (document.activeElement === $("q") && $("q").value) { $("q").value = ""; $("q").dispatchEvent(new Event("input")); }
      closeSheet();
    }
    if (e.key === "/" && !e.target.closest?.("input, textarea") && document.querySelector(".sheet-wrap:not([hidden])") === null) {
      e.preventDefault();
      $("q").focus();
    }
  });

  let queued = false;
  addEventListener("scroll", () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; $("to-top").classList.toggle("show", scrollY > 600); });
  }, { passive: true });
  $("to-top").addEventListener("click", () => scrollTo({ top: 0, behavior: "smooth" }));

  // Pause background work while the tab is hidden; catch up as soon as it's visible again.
  setInterval(() => { if (!document.hidden) tick(); }, TICK_MS);
  setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    if (Date.now() - state.lastCheck > REFRESH_MS) load(); else tick();
  });
}


// --- Start ---

setTheme(THEMES[document.documentElement.dataset.theme] ? document.documentElement.dataset.theme : "poden");
if (!$("filters").querySelector(`[data-f="${state.filter}"]`)) state.filter = "all";
setFilter(state.filter);
compileKeywords();
wire();

// Show the last copy straight away, then check for a newer one.
const cached = store.get(KEYS.feed, null);
if (Array.isArray(cached?.articles) && Array.isArray(cached?.sources)) {
  state.data = cached;
  render();
}
for (const old of ["onimugen_v1_gaming", "onimugen_v2_feed"]) localStorage.removeItem(old);
load();
