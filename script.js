// how long ago something counts as "new"
const NEW_THRESHOLD_MS = 15 * 60 * 1000;

// how often we quietly check for fresh articles in the background
const REFRESH_INTERVAL = 5 * 60 * 1000;

// the score a story needs to earn the Trending badge
// calculated in fetch_news.py using source count + time decay — keep in sync
const TRENDING_THRESHOLD = 2.5;

const CACHE_KEY         = "onimugen_v1_gaming";
const BOOKMARKS_KEY     = "onimugen_v1_bookmarks";
const READ_KEY          = "onimugen_v1_read";
const MUTED_SOURCES_KEY = "onimugen_v1_muted_sources";

let allArticles  = [];
let activeFilter = "all";
let lastHash     = null;
let bookmarks    = {};
let readLinks    = {};
let mutedSources = {};


// --- Tiny helpers ---

function hashArticles(articles) {
    return articles.map(a => a.link + a.date).join("|");
}

function getDomain(url) {
    try { return new URL(url).hostname; } catch (_) { return ""; }
}

function timeAgo(ts) {
    const diff = Date.now() - ts;
    if (diff < 60000)     return "just now";
    if (diff < 3600000)   return Math.floor(diff / 60000) + "m";
    if (diff < 86400000)  return Math.floor(diff / 3600000) + "h";
    if (diff < 172800000) return "yesterday";
    return Math.floor(diff / 86400000) + "d";
}

function badge(text, cls) {
    const b = document.createElement("span");
    b.className = "badge " + cls;
    b.textContent = text;
    return b;
}


// --- Search ---

function clearSearch() {
    const input = document.getElementById("search-input");
    input.value = "";
    document.getElementById("search-clear").style.display = "none";
    filterFeed();
    input.focus();
}


// --- Read history ---

function loadRead() {
    try { readLinks = JSON.parse(localStorage.getItem(READ_KEY)) || {}; }
    catch (_) { readLinks = {}; }
}

function markRead(link) {
    readLinks[link] = true;
    localStorage.setItem(READ_KEY, JSON.stringify(readLinks));
}


// --- Bookmarks ---

function loadBookmarks() {
    try { bookmarks = JSON.parse(localStorage.getItem(BOOKMARKS_KEY)) || {}; }
    catch (_) { bookmarks = {}; }
}

function saveBookmarks() {
    try {
        localStorage.setItem(BOOKMARKS_KEY, JSON.stringify(bookmarks));
    } catch (e) {
        // localStorage quota exceeded — notify user
        console.warn("Bookmark storage full:", e);
        alert("Bookmark storage is full. Please remove some bookmarks before adding new ones.");
    }
}

function toggleBookmark(link, title, domain, sourceName, btn) {
    if (bookmarks[link]) {
        delete bookmarks[link];
        btn.classList.remove("bookmarked");
    } else {
        // savedAt lets us sort by most recently bookmarked
        bookmarks[link] = { title, link, domain, sourceName, savedAt: Date.now() };
        btn.classList.add("bookmarked");
    }
    saveBookmarks();
}

function openBookmarks() {
    const overlay = document.getElementById("bookmarks-overlay");
    const feed    = document.getElementById("bookmarks-feed");
    const empty   = document.getElementById("bookmarks-empty");

    feed.innerHTML = "";

    // newest bookmark first
    const saved = Object.values(bookmarks).sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0));
    empty.style.display = saved.length === 0 ? "block" : "none";

    saved.forEach(b => {
        const li = document.createElement("li");
        li.className = "bm-item";

        const logo = document.createElement("img");
        logo.className = "bm-item-logo";
        logo.alt = "";
        if (b.domain) {
            logo.src = "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(b.domain) + "&sz=64";
            logo.onerror = function() { this.classList.add("hidden"); };
        } else {
            logo.classList.add("hidden");
        }

        const main = document.createElement("div");
        main.className = "bm-item-main";

        const a = document.createElement("a");
        a.href    = b.link;
        a.target  = "_blank";
        a.rel     = "noopener noreferrer";
        a.textContent = b.title;

        const sourceRow   = document.createElement("div");
        sourceRow.className = "bm-item-source";
        const sourceLabel = document.createElement("span");
        sourceLabel.className   = "bm-item-domain";
        sourceLabel.textContent = b.sourceName || b.domain || "";
        sourceRow.appendChild(sourceLabel);

        main.appendChild(a);
        main.appendChild(sourceRow);

        const removeBtn = document.createElement("button");
        removeBtn.className = "bm-remove";
        removeBtn.innerHTML = "&times;";
        removeBtn.onclick = () => {
            delete bookmarks[b.link];
            saveBookmarks();
            li.remove();
            if (feed.children.length === 0) empty.style.display = "block";
            // un-highlight the bookmark button in the feed if it's visible
            const feedBtn = document.querySelector('.bm-btn[data-link="' + CSS.escape(b.link) + '"]');
            if (feedBtn) feedBtn.classList.remove("bookmarked");
        };

        li.appendChild(logo);
        li.appendChild(main);
        li.appendChild(removeBtn);
        feed.appendChild(li);
    });

    overlay.style.display = "flex";
}

function closeBookmarks() {
    document.getElementById("bookmarks-overlay").style.display = "none";
}


// --- Theme ---

const THEMES = ["day", "night", "forest"];
const THEME_COLORS = { day: "#fafaf8", night: "#111110", forest: "#1e2f23" };

function cycleTheme() {
    const root    = document.documentElement;
    const current = root.getAttribute("data-theme") || "day";
    const next    = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
    root.setAttribute("data-theme", next);
    localStorage.setItem("om-theme", next);
    updateThemeDots(next);
    // keep browser chrome colour in sync
    const meta = document.getElementById("meta-theme-color");
    if (meta) meta.setAttribute("content", THEME_COLORS[next] || "#fafaf8");
}

function updateThemeDots(theme) {
    document.querySelectorAll(".theme-dot").forEach(dot => {
        dot.classList.toggle("active", dot.getAttribute("data-t") === theme);
    });
}


// --- Header controls ---

function toggleControl(type) {
    const searchWrap = document.getElementById("search-wrap");
    const filterMenu = document.getElementById("filter-menu");
    const filterBtn  = document.getElementById("filter-btn");

    if (type === "search") {
        filterMenu.classList.remove("open");
        filterBtn.classList.remove("open");
        searchWrap.classList.toggle("active");
        if (searchWrap.classList.contains("active")) {
            document.getElementById("search-input").focus();
        }
    } else {
        searchWrap.classList.remove("active");
        const nowOpen = filterMenu.classList.toggle("open");
        filterBtn.classList.toggle("open", nowOpen);
    }
}

// close any open dropdowns when clicking outside the header controls
document.addEventListener("click", e => {
    const controls = document.querySelector(".controls");
    if (controls && !controls.contains(e.target)) {
        document.getElementById("search-wrap")?.classList.remove("active");
        document.getElementById("filter-menu")?.classList.remove("open");
        document.getElementById("filter-btn")?.classList.remove("open");
    }

    const sourcesWrap = document.getElementById("sources-filter-wrap");
    if (sourcesWrap && !sourcesWrap.contains(e.target)) {
        document.getElementById("sources-menu")?.classList.remove("open");
        document.getElementById("sources-btn")?.classList.remove("open");
    }
});

function pickFilter(type, el) {
    document.querySelectorAll(".filter-opt").forEach(opt => opt.classList.remove("active"));
    el.classList.add("active");
    activeFilter = type;
    // close the dropdown after picking
    document.getElementById("filter-menu").classList.remove("open");
    document.getElementById("filter-btn").classList.remove("open");
    filterFeed();
}

function filterFeed() {
    const q        = (document.getElementById("search-input")?.value ?? "").toLowerCase();
    const clearBtn = document.getElementById("search-clear");
    if (clearBtn) clearBtn.style.display = q.length > 0 ? "flex" : "none";

    let anyVisible = false;
    document.querySelectorAll(".item").forEach(item => {
        const title    = item.getAttribute("data-title").toLowerCase();
        const source   = item.getAttribute("data-source") || "";
        const platform = item.getAttribute("data-platform") || "";

        const matchesSearch = title.includes(q);
        const sourceVisible = !mutedSources[source];

        let matchesTab = true;
        if (activeFilter === "new")         matchesTab = item.getAttribute("data-new") === "true";
        if (activeFilter === "trending")    matchesTab = item.getAttribute("data-hot") === "true";
        if (activeFilter === "playstation") matchesTab = platform === "playstation";
        if (activeFilter === "xbox")        matchesTab = platform === "xbox";
        if (activeFilter === "nintendo")    matchesTab = platform === "nintendo";
        if (activeFilter === "reddit")      matchesTab = platform === "reddit";

        const visible = matchesSearch && matchesTab && sourceVisible;
        item.classList.toggle("hidden", !visible);
        if (visible) anyVisible = true;
    });

    const emptyState = document.getElementById("feed-empty");
    if (emptyState) emptyState.style.display = anyVisible ? "none" : "block";
}

// keyboard support — Enter or Space triggers filter buttons
document.addEventListener("keydown", e => {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("filter-opt")) {
        e.preventDefault();
        e.target.click();
    }
});


// --- Sources menu ---

function loadMutedSources() {
    try { mutedSources = JSON.parse(localStorage.getItem(MUTED_SOURCES_KEY)) || {}; }
    catch (_) { mutedSources = {}; }
}

function saveMutedSources() {
    localStorage.setItem(MUTED_SOURCES_KEY, JSON.stringify(mutedSources));
}

function buildSourcesMenu(articles) {
    const inner = document.getElementById("sources-menu-inner");
    if (!inner) return;
    inner.innerHTML = "";

    // collect unique source names, sorted alphabetically
    const seen = new Set();
    articles.forEach(a => { if (a.sourceName) seen.add(a.sourceName); });
    const names = [...seen].sort((a, b) => a.localeCompare(b));

    names.forEach(name => {
        const row = document.createElement("div");
        row.className = "sources-menu-row";
        if (!mutedSources[name]) row.classList.add("selected");

        const match  = articles.find(a => a.sourceName === name);
        const domain = match ? (match.domain || getDomain(match.link)) : "";

        const img = document.createElement("img");
        img.className = "sources-menu-logo";
        img.alt = "";
        if (domain) {
            img.src = "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(domain) + "&sz=64";
            img.onerror = () => img.classList.add("hidden");
        } else {
            img.classList.add("hidden");
        }

        const label = document.createElement("span");
        label.className   = "sources-menu-label";
        label.textContent = name;

        const tick = document.createElement("span");
        tick.className = "sources-menu-check";
        tick.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';

        row.appendChild(img);
        row.appendChild(label);
        row.appendChild(tick);

        row.addEventListener("click", () => {
            if (mutedSources[name]) {
                delete mutedSources[name];
                row.classList.add("selected");
            } else {
                mutedSources[name] = true;
                row.classList.remove("selected");
            }
            saveMutedSources();
            updateMutedCount();
            filterFeed();
        });

        inner.appendChild(row);
    });
}

function toggleSourcesMenu() {
    const menu   = document.getElementById("sources-menu");
    const btn    = document.getElementById("sources-btn");
    const isOpen = menu.classList.toggle("open");
    btn.classList.toggle("open", isOpen);
}

function resetSources() {
    mutedSources = {};
    saveMutedSources();
    document.querySelectorAll(".sources-menu-row").forEach(r => r.classList.add("selected"));
    updateMutedCount();
    filterFeed();
}

function updateMutedCount() {
    const count = Object.keys(mutedSources).length;
    const el    = document.getElementById("sources-muted-count");
    if (el) el.textContent = count > 0 ? "(" + count + " hidden)" : "";
}


// --- Scroll ---

window.addEventListener("scroll", () => {
    document.getElementById("main-header").classList.toggle("scrolled", window.scrollY > 20);
});


// --- Render ---

function render(articles) {
    allArticles = articles;

    const feed = document.getElementById("feed");
    feed.innerHTML = "";

    articles.slice().sort((a, b) => b.date - a.date).forEach((a, i) => {

        const isNew      = (Date.now() - a.date) < NEW_THRESHOLD_MS;
        const isTrending = a.hotScore != null && a.hotScore >= TRENDING_THRESHOLD;
        const domain     = getDomain(a.link);
        const isReddit   = a.link.includes("reddit.com");
        const isPS       = a.link.includes("playstation.com") ||
                           /\b(playstation|ps5|ps4|psvr2?)\b/i.test(a.title);
        const isNintendo = a.link.includes("nintendo.com") ||
                           /\b(nintendo|switch\s*2?|switch oled)\b/i.test(a.title);
        const isXbox     = a.link.includes(".xbox.com") ||
                           /\b(xbox|game pass)\b/i.test(a.title);
        const hasGroup   = a.groupMembers && a.groupMembers.length > 0;

        // used by the platform + reddit filters
        const platform = isPS ? "playstation" : isXbox ? "xbox" : isNintendo ? "nintendo" : isReddit ? "reddit" : "";

        const li = document.createElement("li");
        li.className = "item";
        if (hasGroup) li.classList.add("has-group");
        li.setAttribute("data-title",    a.title);
        li.setAttribute("data-new",      isNew);
        li.setAttribute("data-hot",      isTrending);
        li.setAttribute("data-source",   a.sourceName || "");
        li.setAttribute("data-platform", platform);

        const dateSpan = document.createElement("span");
        dateSpan.className   = "item-date";
        dateSpan.textContent = timeAgo(a.date);

        const body = document.createElement("div");
        body.className = "item-body";

        const link = document.createElement("a");
        link.href   = a.link;
        link.target = "_blank";
        link.rel    = "noopener noreferrer";
        link.textContent = a.title;

        if (readLinks[a.link]) li.classList.add("read-dimmed");
        link.addEventListener("click", () => {
            markRead(a.link);
            li.classList.add("read-dimmed");
        });

        if (isNew)          link.appendChild(badge("New",         "badge-new"));
        if (isReddit)       link.appendChild(badge("Rumour",      "badge-rumour"));
        if (isPS)           link.appendChild(badge("PlayStation", "badge-ps"));
        if (isNintendo)     link.appendChild(badge("Nintendo",    "badge-nintendo"));
        if (isXbox)         link.appendChild(badge("Xbox",        "badge-xbox"));
        if (isTrending)     link.appendChild(badge("Trending",    "badge-hot"));
        if (a.isTranslated) link.appendChild(badge("JP-EN",       "badge-jp"));

        body.appendChild(link);

        // right-hand column — either a "N Sources" label for grouped items or a favicon
        const logoWrap = document.createElement("span");
        logoWrap.className = "logo-wrap";

        if (hasGroup) {
            // count unique sources (lead + deduplicated members)
            const seen = new Set([a.link]);
            const uniqueMembers = a.groupMembers.filter(m => {
                if (seen.has(m.link)) return false;
                seen.add(m.link);
                return true;
            });
            const sourceCount = uniqueMembers.length + 1;

            const sourcesLabel = document.createElement("span");
            sourcesLabel.className = "sources-count-label";
            sourcesLabel.innerHTML = sourceCount + ' Sources <span class="sources-chevron">&#9662;</span>';
            sourcesLabel.addEventListener("click", () => {
                const drawer  = li.querySelector(".item-sources");
                const chevron = li.querySelector(".sources-chevron");
                const open    = drawer.classList.toggle("open");
                chevron.classList.toggle("open", open);
            });
            logoWrap.appendChild(sourcesLabel);
        } else {
            const img = document.createElement("img");
            img.className = "item-logo";
            img.alt = "";

            const placeholder = document.createElement("div");
            placeholder.className   = "icon-placeholder";
            placeholder.textContent = a.sourceName ? a.sourceName.charAt(0).toUpperCase() : "?";
            placeholder.style.display = "none";

            if (domain) {
                img.src = "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(domain) + "&sz=64";
                img.onload = function() {
                    if (this.naturalHeight <= 16) { this.classList.add("hidden"); placeholder.style.display = "flex"; }
                };
                img.onerror = function() { this.classList.add("hidden"); placeholder.style.display = "flex"; };
            } else {
                img.classList.add("hidden");
                placeholder.style.display = "flex";
            }

            logoWrap.appendChild(img);
            logoWrap.appendChild(placeholder);
        }

        li.appendChild(dateSpan);
        li.appendChild(body);
        li.appendChild(logoWrap);

        const bmBtn = document.createElement("button");
        bmBtn.className = "bm-btn";
        bmBtn.setAttribute("data-link", a.link);
        bmBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>';
        if (bookmarks[a.link]) bmBtn.classList.add("bookmarked");
        bmBtn.addEventListener("click", e => {
            e.stopPropagation();
            toggleBookmark(a.link, a.title, a.domain || domain, a.sourceName, bmBtn);
        });
        li.appendChild(bmBtn);

        // expandable drawer listing all sources that covered this story
        if (hasGroup) {
            const drawer = document.createElement("div");
            drawer.className = "item-sources";

            const seenLinks = new Set([a.link]);
            const uniqueMembers = a.groupMembers.filter(m => {
                if (seenLinks.has(m.link)) return false;
                seenLinks.add(m.link);
                return true;
            });

            // oldest article first — that's the original source
            const allSources = [
                { title: a.title, link: a.link, sourceName: a.sourceName, domain, date: a.date },
                ...uniqueMembers
            ].sort((x, y) => x.date - y.date);

            allSources.forEach(src => {
                const row = document.createElement("div");
                row.className = "source-row";

                const srcDomain = getDomain(src.link);
                const initial   = src.sourceName ? src.sourceName.charAt(0).toLowerCase() : "?";

                const favicon = document.createElement("img");
                favicon.className = "source-row-logo";
                favicon.alt = "";

                const fallback = document.createElement("div");
                fallback.className   = "source-row-placeholder";
                fallback.textContent = initial;
                fallback.style.display = "none";

                if (srcDomain) {
                    favicon.src = "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(srcDomain) + "&sz=64";
                    favicon.onload = function() {
                        if (this.naturalHeight <= 16) { this.classList.add("hidden"); fallback.style.display = "flex"; }
                    };
                    favicon.onerror = function() { this.classList.add("hidden"); fallback.style.display = "flex"; };
                } else {
                    favicon.classList.add("hidden");
                    fallback.style.display = "flex";
                }

                const articleLink = document.createElement("a");
                articleLink.href   = src.link;
                articleLink.target = "_blank";
                articleLink.rel    = "noopener noreferrer";
                articleLink.textContent = src.title;

                const timestamp = document.createElement("span");
                timestamp.className   = "source-row-date";
                timestamp.textContent = timeAgo(src.date);

                row.appendChild(favicon);
                row.appendChild(fallback);
                row.appendChild(articleLink);
                row.appendChild(timestamp);
                drawer.appendChild(row);
            });

            li.appendChild(drawer);
        }

        feed.appendChild(li);
        setTimeout(() => li.classList.add("show"), i < 30 ? i * 15 : 0);
    });

    // empty-state message — shown when filters/search produce no visible items
    let emptyState = document.getElementById("feed-empty");
    if (!emptyState) {
        emptyState = document.createElement("li");
        emptyState.id = "feed-empty";
        emptyState.className = "feed-empty-state";
        emptyState.textContent = "No articles match your current filters.";
    }
    feed.appendChild(emptyState);
    // visibility is toggled by filterFeed(); hide it initially after a fresh render
    emptyState.style.display = "none";

    buildSourcesMenu(allArticles);
    updateMutedCount();
}


// --- Data fetching ---

async function fetchFresh() {
    const r = await fetch("./data_gaming.json?t=" + Date.now());
    if (!r.ok) throw new Error("fetch failed");
    return r.json();
}

async function init() {
    loadBookmarks();
    loadRead();
    loadMutedSources();

    // show cached data immediately while we fetch fresh stuff
    const cached = localStorage.getItem(CACHE_KEY);
    if (cached) {
        try {
            const data = JSON.parse(cached);
            allArticles = data;
            lastHash    = hashArticles(data);
            render(data);
            document.getElementById("splash").classList.add("fade");
        } catch (_) {
            localStorage.removeItem(CACHE_KEY);
        }
    }

    try {
        const fresh = await fetchFresh();
        if (fresh && fresh.length > 0) {
            const hash = hashArticles(fresh);
            if (hash !== lastHash) {
                lastHash    = hash;
                allArticles = fresh;
                render(fresh);
                localStorage.setItem(CACHE_KEY, JSON.stringify(fresh));
            }
        }
    } catch (e) {
        console.warn("fetch failed:", e.message);
    } finally {
        document.getElementById("s-fill").style.width = "100%";
        setTimeout(() => document.getElementById("splash").classList.add("fade"), 300);
    }
}

function hardRefresh() {
    localStorage.removeItem(CACHE_KEY);
    lastHash = null;
    const feed = document.getElementById("feed");

    async function doRefresh() {
        try {
            const fresh = await fetchFresh();
            if (!fresh || fresh.length === 0) return;
            const hash = hashArticles(fresh);
            if (hash !== lastHash) {
                lastHash    = hash;
                allArticles = fresh;
                render(fresh);
                localStorage.setItem(CACHE_KEY, JSON.stringify(fresh));
            }
        } catch (_) {
            // Re-render from the existing in-memory articles so the feed isn't blank
            if (allArticles.length > 0) {
                render(allArticles);
            } else {
                const emptyState = document.getElementById("feed-empty");
                if (emptyState) {
                    emptyState.textContent = "Could not load feed. Please try again.";
                    emptyState.style.display = "block";
                }
            }
        }
    }

    doRefresh();
}

async function silentRefresh() {
    try {
        const fresh = await fetchFresh();
        if (!fresh || fresh.length === 0) return;
        const hash = hashArticles(fresh);
        if (hash !== lastHash) {
            lastHash    = hash;
            allArticles = fresh;
            render(fresh);
            localStorage.setItem(CACHE_KEY, JSON.stringify(fresh));
        }
    } catch (_) {}
}

document.addEventListener("DOMContentLoaded", () => {
    const saved = localStorage.getItem("om-theme") || "day";
    updateThemeDots(saved);
    init();
    setInterval(silentRefresh, REFRESH_INTERVAL);

    // pull the splash out of the DOM entirely once it's faded
    const splash = document.getElementById("splash");
    splash.addEventListener("transitionend", () => {
        if (splash.classList.contains("fade")) splash.remove();
    }, { once: true });
});
