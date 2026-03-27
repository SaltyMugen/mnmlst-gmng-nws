const NEW_THRESHOLD_MS = 15 * 60 * 1000;
const REFRESH_INTERVAL = 5 * 60 * 1000;
const TRENDING_THRESHOLD = 2.1; // keep in sync with fetch_news.py
const CACHE_KEY = "onimugen_v1_gaming";
const BOOKMARKS_KEY = "onimugen_v1_bookmarks";
const READ_KEY = "onimugen_v1_read";
const MUTED_SOURCES_KEY = "onimugen_v1_muted_sources";

let allArticles = [];
let currentFilter = "all";
let lastDataHash = null;
let bookmarks = {};
let readLinks = {};
let mutedSources = {};

// --- Utilities ---

function hashArticles(articles) {
    return articles.map((a) => a.link + a.date).join("|");
}

function safeDomain(url) {
    try {
        return new URL(url).hostname;
    } catch (_) {
        return "";
    }
}

function formatTime(ts) {
    const d = Date.now() - ts;
    if (d < 60000) return "just now";
    if (d < 3600000) return Math.floor(d / 60000) + "m";
    if (d < 86400000) return Math.floor(d / 3600000) + "h";
    return Math.floor(d / 86400000) + "d";
}

function makeBadge(text, cls) {
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
    applyFilters();
    input.focus();
}

// --- Read history ---

function loadRead() {
    try {
        readLinks = JSON.parse(localStorage.getItem(READ_KEY)) || {};
    } catch (_) {
        readLinks = {};
    }
}

function markRead(link) {
    readLinks[link] = true;
    localStorage.setItem(READ_KEY, JSON.stringify(readLinks));
}

// --- Bookmarks ---

function loadBookmarks() {
    try {
        bookmarks = JSON.parse(localStorage.getItem(BOOKMARKS_KEY)) || {};
    } catch (_) {
        bookmarks = {};
    }
}

function saveBookmarks() {
    localStorage.setItem(BOOKMARKS_KEY, JSON.stringify(bookmarks));
}

function toggleBookmark(link, title, domain, sourceName, btn) {
    if (bookmarks[link]) {
        delete bookmarks[link];
        btn.classList.remove("bookmarked");
    } else {
        bookmarks[link] = { title, link, domain, sourceName };
        btn.classList.add("bookmarked");
    }
    saveBookmarks();
}

function openBookmarks() {
    const overlay = document.getElementById("bookmarks-overlay");
    const feed = document.getElementById("bookmarks-feed");
    const empty = document.getElementById("bookmarks-empty");

    feed.innerHTML = "";
    const items = Object.values(bookmarks);
    empty.style.display = items.length === 0 ? "block" : "none";

    items.forEach((b) => {
        const li = document.createElement("li");
        li.className = "bm-item";

        const logo = document.createElement("img");
        logo.className = "bm-item-logo";
        logo.alt = "";
        if (b.domain) {
            logo.src = "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(b.domain) + "&sz=64";
            logo.onerror = function () {
                this.classList.add("hidden");
            };
        } else {
            logo.classList.add("hidden");
        }

        const main = document.createElement("div");
        main.className = "bm-item-main";

        const a = document.createElement("a");
        a.href = b.link;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = b.title;

        const sourceRow = document.createElement("div");
        sourceRow.className = "bm-item-source";
        const domainSpan = document.createElement("span");
        domainSpan.className = "bm-item-domain";
        domainSpan.textContent = b.sourceName || b.domain || "";
        sourceRow.appendChild(domainSpan);

        main.appendChild(a);
        main.appendChild(sourceRow);

        const del = document.createElement("button");
        del.className = "bm-remove";
        del.innerHTML = "&times;";
        del.onclick = () => {
            delete bookmarks[b.link];
            saveBookmarks();
            li.remove();
            if (feed.children.length === 0) empty.style.display = "block";
            const inlineBtn = document.querySelector('.bm-btn[data-link="' + CSS.escape(b.link) + '"]');
            if (inlineBtn) inlineBtn.classList.remove("bookmarked");
        };

        li.appendChild(logo);
        li.appendChild(main);
        li.appendChild(del);
        feed.appendChild(li);
    });

    overlay.style.display = "flex";
}

function closeBookmarks() {
    document.getElementById("bookmarks-overlay").style.display = "none";
}

// --- Theme ---

const THEMES = ["day", "night", "forest"];

function cycleTheme() {
    const root = document.documentElement;
    const current = root.getAttribute("data-theme") || "day";
    const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
    root.setAttribute("data-theme", next);
    localStorage.setItem("om-theme", next);
    updateThemeDots(next);
}

function updateThemeDots(theme) {
    document.querySelectorAll(".theme-dot").forEach((dot) => {
        dot.classList.toggle("active", dot.getAttribute("data-t") === theme);
    });
}

// --- Header controls ---

function toggleControl(type) {
    const search = document.getElementById("search-wrap");
    const filter = document.getElementById("filter-wrap");
    if (type === "search") {
        filter.classList.remove("active");
        search.classList.toggle("active");
        if (search.classList.contains("active")) document.getElementById("search-input").focus();
    } else {
        search.classList.remove("active");
        filter.classList.toggle("active");
    }
}

// close search/filter and sources menu when clicking outside their containers
document.addEventListener("click", (e) => {
    const controls = document.querySelector(".controls");
    if (controls && !controls.contains(e.target)) {
        document.getElementById("search-wrap")?.classList.remove("active");
        document.getElementById("filter-wrap")?.classList.remove("active");
    }

    const sourcesWrap = document.getElementById("sources-filter-wrap");
    if (sourcesWrap && !sourcesWrap.contains(e.target)) {
        document.getElementById("sources-menu")?.classList.remove("open");
        document.getElementById("sources-btn")?.classList.remove("open");
    }
});

function setFilter(type, el) {
    document.querySelectorAll(".filter-opt").forEach((opt) => opt.classList.remove("active"));
    el.classList.add("active");
    currentFilter = type;
    applyFilters();
}

function applyFilters() {
    const q = (document.getElementById("search-input")?.value ?? "").toLowerCase();
    const clearBtn = document.getElementById("search-clear");
    if (clearBtn) clearBtn.style.display = q.length > 0 ? "flex" : "none";

    document.querySelectorAll(".item").forEach((item) => {
        const title = item.getAttribute("data-title").toLowerCase();
        const source = item.getAttribute("data-source") || "";
        const matchText = title.includes(q);
        const matchSource = !mutedSources[source];
        let matchTab = true;
        if (currentFilter === "new") matchTab = item.getAttribute("data-new") === "true";
        if (currentFilter === "trending") matchTab = item.getAttribute("data-hot") === "true";
        item.classList.toggle("hidden", !(matchText && matchTab && matchSource));
    });
}

// --- Sources menu ---

function loadMutedSources() {
    try {
        mutedSources = JSON.parse(localStorage.getItem(MUTED_SOURCES_KEY)) || {};
    } catch (_) {
        mutedSources = {};
    }
}

function saveMutedSources() {
    localStorage.setItem(MUTED_SOURCES_KEY, JSON.stringify(mutedSources));
}

function buildSourcesMenu(articles) {
    const inner = document.getElementById("sources-menu-inner");
    if (!inner) return;
    inner.innerHTML = "";

    const seen = new Set();
    articles.forEach((a) => {
        if (a.sourceName) seen.add(a.sourceName);
    });
    const names = [...seen].sort((a, b) => a.localeCompare(b));

    names.forEach((name) => {
        const row = document.createElement("div");
        row.className = "sources-menu-row";
        if (!mutedSources[name]) row.classList.add("selected");

        const match = articles.find((a) => a.sourceName === name);
        const domain = match ? match.domain || safeDomain(match.link) : "";

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
        label.className = "sources-menu-label";
        label.textContent = name;

        const check = document.createElement("span");
        check.className = "sources-menu-check";
        check.innerHTML =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';

        row.appendChild(img);
        row.appendChild(label);
        row.appendChild(check);

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
            applyFilters();
        });

        inner.appendChild(row);
    });
}

function toggleSourcesMenu() {
    const menu = document.getElementById("sources-menu");
    const btn = document.getElementById("sources-btn");
    const isOpen = menu.classList.toggle("open");
    btn.classList.toggle("open", isOpen);
}

function resetSources() {
    mutedSources = {};
    saveMutedSources();
    document.querySelectorAll(".sources-menu-row").forEach((r) => r.classList.add("selected"));
    updateMutedCount();
    applyFilters();
}

function updateMutedCount() {
    const count = Object.keys(mutedSources).length;
    const el = document.getElementById("sources-muted-count");
    if (el) el.textContent = count > 0 ? "(" + count + " hidden)" : "";
}

// --- Scroll ---

window.addEventListener("scroll", () => {
    document.getElementById("main-header").classList.toggle("scrolled", window.pageYOffset > 20);
});

// --- Keyboard support for filter buttons ---

document.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("filter-opt")) {
        e.preventDefault();
        e.target.click();
    }
});

// --- Render ---

function render(articles) {
    allArticles = articles;

    const feed = document.getElementById("feed");
    feed.innerHTML = "";

    articles
        .slice()
        .sort((a, b) => b.date - a.date)
        .forEach((a, i) => {
            if (a.groupMember) return;

            const isNew = Date.now() - a.date < NEW_THRESHOLD_MS;
            const domain = safeDomain(a.link);
            const isHot = a.hotScore != null && a.hotScore >= TRENDING_THRESHOLD;
            const isReddit = a.link.includes("reddit.com");
            const isPS = a.link.includes("playstation.com");
            const isNintendo = a.link.includes("nintendo.com");
            const isXbox = a.link.includes(".xbox.com");
            const hasGroup = a.groupMembers && a.groupMembers.length > 0;

            const li = document.createElement("li");
            li.className = "item";
            if (hasGroup) li.classList.add("has-group");
            li.setAttribute("data-title", a.title);
            li.setAttribute("data-new", isNew);
            li.setAttribute("data-hot", isHot);
            li.setAttribute("data-source", a.sourceName || "");

            const dateSpan = document.createElement("span");
            dateSpan.className = "item-date";
            dateSpan.textContent = formatTime(a.date);

            const body = document.createElement("div");
            body.className = "item-body";

            const link = document.createElement("a");
            link.href = a.link;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = a.title;
            if (readLinks[a.link]) li.classList.add("read-dimmed");
            link.addEventListener("click", () => {
                markRead(a.link);
                li.classList.add("read-dimmed");
            });

            if (isNew) link.appendChild(makeBadge("New", "badge-new"));
            if (isReddit) link.appendChild(makeBadge("Rumour", "badge-rumour"));
            if (isPS) link.appendChild(makeBadge("PlayStation", "badge-ps"));
            if (isNintendo) link.appendChild(makeBadge("Nintendo", "badge-nintendo"));
            if (isXbox) link.appendChild(makeBadge("Xbox", "badge-xbox"));
            if (isHot) link.appendChild(makeBadge("Trending", "badge-hot"));
            if (a.isTranslated) link.appendChild(makeBadge("JP-EN", "badge-jp"));

            body.appendChild(link);

            const logoWrap = document.createElement("span");
            logoWrap.className = "logo-wrap";

            if (hasGroup) {
                const seenForCount = new Set([a.link]);
                const uniqueForCount = a.groupMembers.filter((m) => {
                    if (seenForCount.has(m.link)) return false;
                    seenForCount.add(m.link);
                    return true;
                });
                const totalSources = uniqueForCount.length + 1;

                const sourcesLabel = document.createElement("span");
                sourcesLabel.className = "sources-count-label";
                sourcesLabel.innerHTML = totalSources + ' Sources <span class="sources-chevron">&#9662;</span>';
                sourcesLabel.addEventListener("click", () => {
                    const drawer = li.querySelector(".item-sources");
                    const chevron = li.querySelector(".sources-chevron");
                    const open = drawer.classList.toggle("open");
                    chevron.classList.toggle("open", open);
                });
                logoWrap.appendChild(sourcesLabel);
            } else {
                const img = document.createElement("img");
                img.className = "item-logo";
                img.alt = "";

                const placeholder = document.createElement("div");
                placeholder.className = "icon-placeholder";
                placeholder.textContent = a.sourceName ? a.sourceName.charAt(0).toUpperCase() : "?";
                placeholder.style.display = "none";

                if (domain) {
                    img.src = "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(domain) + "&sz=64";
                    img.onload = function () {
                        if (this.naturalHeight <= 16) {
                            this.classList.add("hidden");
                            placeholder.style.display = "flex";
                        }
                    };
                    img.onerror = function () {
                        this.classList.add("hidden");
                        placeholder.style.display = "flex";
                    };
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
            bmBtn.innerHTML =
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>';
            if (bookmarks[a.link]) bmBtn.classList.add("bookmarked");
            bmBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                toggleBookmark(a.link, a.title, a.domain || domain, a.sourceName, bmBtn);
            });
            li.appendChild(bmBtn);

            if (hasGroup) {
                const drawer = document.createElement("div");
                drawer.className = "item-sources";

                const seenLinks = new Set([a.link]);
                const uniqueMembers = a.groupMembers.filter((m) => {
                    if (seenLinks.has(m.link)) return false;
                    seenLinks.add(m.link);
                    return true;
                });

                // oldest source at top — that's usually the original report
                const allMembers = [
                    { title: a.title, link: a.link, sourceName: a.sourceName, domain, date: a.date },
                    ...uniqueMembers
                ].sort((x, y) => x.date - y.date);

                allMembers.forEach((member) => {
                    const row = document.createElement("div");
                    row.className = "source-row";

                    const memberDomain = safeDomain(member.link);
                    const initial = member.sourceName ? member.sourceName.charAt(0).toLowerCase() : "?";

                    const favicon = document.createElement("img");
                    favicon.className = "source-row-logo";
                    favicon.alt = "";

                    const fallback = document.createElement("div");
                    fallback.className = "source-row-placeholder";
                    fallback.textContent = initial;
                    fallback.style.display = "none";

                    if (memberDomain) {
                        favicon.src =
                            "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(memberDomain) + "&sz=64";
                        favicon.onload = function () {
                            if (this.naturalHeight <= 16) {
                                this.classList.add("hidden");
                                fallback.style.display = "flex";
                            }
                        };
                        favicon.onerror = function () {
                            this.classList.add("hidden");
                            fallback.style.display = "flex";
                        };
                    } else {
                        favicon.classList.add("hidden");
                        fallback.style.display = "flex";
                    }

                    const articleLink = document.createElement("a");
                    articleLink.href = member.link;
                    articleLink.target = "_blank";
                    articleLink.rel = "noopener noreferrer";
                    articleLink.textContent = member.title;

                    const timestamp = document.createElement("span");
                    timestamp.className = "source-row-date";
                    timestamp.textContent = formatTime(member.date);

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

    const cached = localStorage.getItem(CACHE_KEY);
    if (cached) {
        try {
            const data = JSON.parse(cached);
            allArticles = data;
            lastDataHash = hashArticles(data);
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
            if (hash !== lastDataHash) {
                lastDataHash = hash;
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
    lastDataHash = null;
    silentRefresh();
}

async function silentRefresh() {
    try {
        const fresh = await fetchFresh();
        if (!fresh || fresh.length === 0) return;
        const hash = hashArticles(fresh);
        if (hash !== lastDataHash) {
            lastDataHash = hash;
            allArticles = fresh;
            render(fresh);
            localStorage.setItem(CACHE_KEY, JSON.stringify(fresh));
        }
    } catch (_) {}
}

document.addEventListener("DOMContentLoaded", () => {
    const theme = localStorage.getItem("om-theme") || "day";
    updateThemeDots(theme);
    init();
    setInterval(silentRefresh, REFRESH_INTERVAL);

    const splash = document.getElementById("splash");
    splash.addEventListener(
        "transitionend",
        () => {
            if (splash.classList.contains("fade")) splash.remove();
        },
        { once: true }
    );
});