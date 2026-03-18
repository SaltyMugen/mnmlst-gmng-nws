// ─── Constants ────────────────────────────────────────────────────
const NEW_THRESHOLD_MS  = 15 * 60 * 1000;   // 15 minutes
const CUTOFF_MS         = 48 * 60 * 60 * 1000; // 48 hours (used for future age-gating)
const REFRESH_INTERVAL  = 5 * 60 * 1000;    // 5 minutes
const CACHE_KEY         = "onimugen_v1_gaming";

// ─── State ────────────────────────────────────────────────────────
let allArticles   = [];   // master copy of current articles
let currentFilter = 'all';
let lastDataHash  = null; // hash to detect real content changes

// ─── Utilities ────────────────────────────────────────────────────

function hashArticles(articles) {
    return articles.map(a => a.link + a.date).join('|');
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(String(str)));
    return div.innerHTML;
}

function safeDomain(url) {
    try {
        return new URL(url).hostname;
    } catch (_) {
        return '';
    }
}

function formatTime(ts) {
    const d = Date.now() - ts;
    if (d < 3600000)  return Math.floor(d / 60000)    + 'm';
    if (d < 86400000) return Math.floor(d / 3600000)  + 'h';
    return Math.floor(d / 86400000) + 'd';
}

// ─── Theme ────────────────────────────────────────────────────────
function toggleTheme() {
    const d    = document.documentElement;
    const next = d.getAttribute('data-theme') === 'day' ? 'night' : 'day';
    d.setAttribute('data-theme', next);
    localStorage.setItem('om-theme', next);
}

// ─── Controls ─────────────────────────────────────────────────────
function toggleControl(type) {
    const s = document.getElementById('search-wrap');
    const f = document.getElementById('filter-wrap');
    if (type === 'search') {
        f.classList.remove('active');
        s.classList.toggle('active');
        if (s.classList.contains('active')) document.getElementById('search-input').focus();
    } else {
        s.classList.remove('active');
        f.classList.toggle('active');
    }
}

function setFilter(t, el) {
    document.querySelectorAll('.filter-opt').forEach(opt => {
        opt.classList.remove('active');
        opt.setAttribute('aria-pressed', 'false');
    });
    el.classList.add('active');
    el.setAttribute('aria-pressed', 'true');
    currentFilter = t;
    applyFilters();
}

function applyFilters() {
    const q = document.getElementById('search-input').value.toLowerCase();
    document.querySelectorAll('.item').forEach(item => {
        const title       = item.getAttribute('data-title').toLowerCase();
        const matchSearch = title.includes(q);
        let   matchTab    = true;
        if (currentFilter === 'new') matchTab = item.getAttribute('data-new') === 'true';
        if (currentFilter === 'hot') matchTab = item.getAttribute('data-hot') === 'true';
        item.classList.toggle('hidden', !(matchSearch && matchTab));
    });
}

// ─── Scroll ───────────────────────────────────────────────────────
window.addEventListener('scroll', () => {
    document.getElementById('main-header')
        .classList.toggle('scrolled', window.pageYOffset > 20);
});

// ─── Keyboard: filter buttons ─────────────────────────────────────
document.addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('filter-opt')) {
        e.preventDefault();
        e.target.click();
    }
});

// ─── Render ───────────────────────────────────────────────────────
function render(articles) {
    allArticles = articles; 

    const feed = document.getElementById('feed');
    feed.innerHTML = '';

    articles
        .slice() 
        .sort((a, b) => b.date - a.date)
        .forEach((a, i) => {
            // Hide non-lead group members — they appear in the lead's drawer instead
            if (a.groupMember) return;

            const isNew    = (Date.now() - a.date) < NEW_THRESHOLD_MS;
            const domain   = safeDomain(a.link);
            const isHot    = a.hotScore != null && a.hotScore >= 4.0;
            const isReddit    = a.link.includes('reddit.com');
            const isPS        = a.link.includes('playstation.com');
            const isNintendo  = a.link.includes('nintendo.com');
            const isXbox      = a.link.includes('.xbox.com');
            const hasGroup    = a.groupMembers && a.groupMembers.length > 0;

            const li = document.createElement('li');
            li.className = 'item';
            if (hasGroup) li.classList.add('has-group');
            li.setAttribute('data-title', a.title);
            li.setAttribute('data-new', isNew);
            li.setAttribute('data-hot', isHot);

            const dateSpan = document.createElement('span');
            dateSpan.className = 'item-date';
            dateSpan.textContent = formatTime(a.date);

            const body = document.createElement('div');
            body.className = 'item-body';

            const link = document.createElement('a');
            link.href   = a.link;
            link.target = '_blank';
            link.rel    = 'noopener noreferrer';
            link.textContent = a.title;

            if (isNew)      link.appendChild(makeBadge('New',        'badge-new'));
            if (isReddit)   link.appendChild(makeBadge('Rumour',     'badge-rumour'));
            if (isPS)       link.appendChild(makeBadge('PlayStation','badge-ps'));
            if (isNintendo) link.appendChild(makeBadge('Nintendo',   'badge-nintendo'));
            if (isXbox)     link.appendChild(makeBadge('Xbox',       'badge-xbox'));
            if (isHot)      link.appendChild(makeBadge('Hot',        'badge-hot'));
            if (a.isTranslated) {
                link.appendChild(makeBadge('JP-EN', 'badge-jp'));
            }

            body.appendChild(link);

            const logoWrap = document.createElement('span');
            logoWrap.setAttribute('aria-hidden', 'true');
            logoWrap.style.display = 'flex';
            logoWrap.style.alignItems = 'center';
            logoWrap.style.justifyContent = 'center';

            if (hasGroup) {
                const memberLinks = new Set(a.groupMembers.map(m => m.link));
                memberLinks.delete(a.link);
                const totalSources = memberLinks.size + 1;

                const sourcesLabel = document.createElement('span');
                sourcesLabel.style.cssText = 'font-size:9px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:1px;cursor:pointer;padding:2px 4px;display:flex;align-items:center;gap:2px;';
                sourcesLabel.innerHTML = `${totalSources} Sources <span class="sources-chevron">&#9662;</span>`;
                sourcesLabel.addEventListener('click', () => {
                    const drawer = li.querySelector('.item-sources');
                    const chevron = li.querySelector('.sources-chevron');
                    const isOpen = drawer.classList.toggle('open');
                    chevron.classList.toggle('open', isOpen);
                });
                logoWrap.appendChild(sourcesLabel);
            } else {
                const img = document.createElement('img');
                img.className = 'item-logo';
                img.alt = '';
                
                const placeholder = document.createElement('div');
                placeholder.className = 'icon-placeholder';
                placeholder.textContent = 'M+';
                placeholder.style.display = 'none';

                if (domain) {
                    img.src = `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`;
                    img.onload = function() {
                        if (this.naturalHeight <= 16) {
                            this.classList.add('hidden');
                            placeholder.style.display = 'flex';
                        }
                    };
                    img.onerror = function() {
                        this.classList.add('hidden');
                        placeholder.style.display = 'flex';
                    };
                } else {
                    img.classList.add('hidden');
                    placeholder.style.display = 'flex';
                }

                logoWrap.appendChild(img);
                logoWrap.appendChild(placeholder);
            }

            li.appendChild(dateSpan);
            li.appendChild(body);
            li.appendChild(logoWrap);

            if (hasGroup) {
                const drawer = document.createElement('div');
                drawer.className = 'item-sources';

                const seenLinks = new Set([a.link]);
                const uniqueMembers = a.groupMembers.filter(m => {
                    if (seenLinks.has(m.link)) return false;
                    seenLinks.add(m.link);
                    return true;
                });

                const allMembers = [
                    { title: a.title, link: a.link, sourceName: a.sourceName, domain: domain, date: a.date },
                    ...uniqueMembers
                ].sort((x, y) => y.date - x.date);

                allMembers.forEach(m => {
                    const row = document.createElement('div');
                    row.className = 'source-row';

                    const mDomain = safeDomain(m.link);
                    const mLetter = m.sourceName ? m.sourceName.charAt(0).toLowerCase() : '?';

                    const mImg = document.createElement('img');
                    mImg.className = 'source-row-logo';
                    mImg.alt = '';

                    const mPlaceholder = document.createElement('div');
                    mPlaceholder.className = 'source-row-placeholder';
                    mPlaceholder.textContent = mLetter;
                    mPlaceholder.style.display = 'none';

                    if (mDomain) {
                        mImg.src = `https://www.google.com/s2/favicons?domain=${encodeURIComponent(mDomain)}&sz=64`;
                        mImg.onload = function() {
                            if (this.naturalHeight <= 16) { this.classList.add('hidden'); mPlaceholder.style.display = 'flex'; }
                        };
                        mImg.onerror = function() { this.classList.add('hidden'); mPlaceholder.style.display = 'flex'; };
                    } else {
                        mImg.classList.add('hidden');
                        mPlaceholder.style.display = 'flex';
                    }

                    const mLink = document.createElement('a');
                    mLink.href = m.link;
                    mLink.target = '_blank';
                    mLink.rel = 'noopener noreferrer';
                    mLink.textContent = m.title;

                    const mDate = document.createElement('span');
                    mDate.className = 'source-row-date';
                    mDate.textContent = formatTime(m.date);

                    row.appendChild(mImg);
                    row.appendChild(mPlaceholder);
                    row.appendChild(mLink);
                    row.appendChild(mDate);
                    drawer.appendChild(row);
                });

                li.appendChild(drawer);
            }

            feed.appendChild(li);

            setTimeout(() => li.classList.add('show'), i < 30 ? i * 15 : 0);
        });
}

function makeBadge(text, cls) {
    const b = document.createElement('span');
    b.className = `badge ${cls}`;
    b.textContent = text;
    return b;
}

// ─── Data fetching ────────────────────────────────────────────────
async function fetchFresh() {
    const r    = await fetch('./data_gaming.json?t=' + Date.now());
    if (!r.ok) throw new Error('Network response was not ok');
    return r.json();
}

async function init() {
    const cached = localStorage.getItem(CACHE_KEY);
    if (cached) {
        try {
            const cachedData = JSON.parse(cached);
            allArticles  = cachedData;
            lastDataHash = hashArticles(cachedData);
            render(cachedData);
        } catch (_) {
            localStorage.removeItem(CACHE_KEY);
        }
        document.getElementById('splash').classList.add('fade');
    }

    try {
        const freshData = await fetchFresh();
        if (freshData && freshData.length > 0) {
            const freshHash = hashArticles(freshData);
            if (freshHash !== lastDataHash) {
                lastDataHash = freshHash;
                allArticles  = freshData;
                render(freshData);
                localStorage.setItem(CACHE_KEY, JSON.stringify(freshData));
            }
        }
    } catch (e) {
        console.warn('Fetch failed:', e.message);
    } finally {
        document.getElementById('s-fill').style.width = '100%';
        setTimeout(() => {
            const splash = document.getElementById('splash');
            splash.classList.add('fade');
            splash.setAttribute('aria-hidden', 'true');
        }, 300);
    }
}

function hardRefresh() {
    localStorage.removeItem(CACHE_KEY);
    lastDataHash = null;
    init();
}

async function silentRefresh() {
    try {
        const freshData = await fetchFresh();
        if (!freshData || freshData.length === 0) return;
        const freshHash = hashArticles(freshData);
        if (freshHash !== lastDataHash) {
            lastDataHash = freshHash;
            allArticles  = freshData;
            render(freshData);
            localStorage.setItem(CACHE_KEY, JSON.stringify(freshData));
        }
    } catch (e) {
        console.log('Silent refresh failed silently.');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    init();
    setInterval(silentRefresh, REFRESH_INTERVAL);
});