"""Build data.json for OniMugen+ from the feeds in sources.json, using the rules in config.json.

Run by .github/workflows/update.yml every 10 minutes. State kept between runs in .cache/:
  feeds.json         ETag/Last-Modified, last entries and last success of each feed. An unchanged
                     feed costs a tiny 304 reply; a failing feed falls back to its last copy.
  translations.json  Japanese title -> English, so each title is translated once.
  icons/             One small PNG per site, refreshed every two weeks and published with the site.

Translation tries, in order: Google Cloud (GOOGLE_TRANSLATE_API_KEY secret), DeepL (DEEPL_API_KEY
secret), then Microsoft's, Google's and MyMemory's free services. When a feed fails, alternate
addresses, the feed listed on the site's home page, and Google News for that site are tried.
"""

import calendar
import html
import json
import logging
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import feedparser
import requests
from dateutil import parser as dateparser
from rapidfuzz import fuzz

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch")

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.json"
CONFIG_FILE = ROOT / "config.json"
OUTPUT_FILE = ROOT / "data.json"
CACHE_DIR = ROOT / ".cache"

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
WINDOW_MS = DAY_MS                   # only keep the last 24 hours
TRANSLATION_TTL_MS = 7 * DAY_MS      # forget translations not needed for a week
ICON_TTL_MS = 14 * DAY_MS            # refresh site icons every two weeks
ICON_RETRY_MS = 3 * DAY_MS           # retry sites without an icon every three days

SIMILARITY_THRESHOLD = 69            # rapidfuzz token_set_ratio needed to group two titles
MIN_SHARED_WORDS = 2                 # ...and they must share this many distinctive words
MIN_TOPIC_GROUP = 3                  # a shared named topic needs 3+ articles to be merged
TOPIC_WINDOW_MS = 12 * HOUR_MS       # ...all published within 12 hours

MAX_WORKERS = 12
TIMEOUT = 15
ICON_URL = "https://www.google.com/s2/favicons?sz=64&domain="

# Feeds: some sites refuse requests that look like a browser script but allow feed readers.
FEED_HEADERS = {
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "en-GB,en;q=0.9,ja;q=0.8",
}
READER_UA = "Mozilla/5.0 (compatible; OniMugenBot/2.0; +https://onimugen.com)"
BLOCKED_CODES = {401, 403, 406, 429}
DISCOVERY_EVERY_MS = 6 * HOUR_MS     # look for a site's own feed address at most every 6 hours
FEED_LINK_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={gl}:{lang}"

# Translation providers, tried in order until one works. Keys are optional repository secrets.
CLOUD_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"
DEEPL_URL = "https://api{free}.deepl.com/v2/translate"
MS_AUTH_URL = "https://edge.microsoft.com/translate/auth"
MS_TRANSLATE_URL = "https://api-edge.cognitive.microsofttranslator.com/translate"
FREE_GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
MYMEMORY_URL = "https://api.mymemory.translated.net/get"
TRANSLATE_DELAY = 0.3                # pause between requests to the free providers

JST = timezone(timedelta(hours=9))
CJK_RE = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")
MOJIBAKE_RE = re.compile(r"[\u00c0-\u00ff]")
WORD_RE = re.compile(r"[a-z0-9]+")
# 2+ capitalised words, allowing short joiners and roman numerals: "Call of Duty", "GTA VI"
TOPIC_RE = re.compile(
    r"\b[A-Z][a-zA-Z]+(?:\s+(?:of|in|the|a|an|to|for|and|or|vs|[IVX]+|[A-Z][a-zA-Z]+))*\s+[A-Z][a-zA-Z]+\b"
)
STOPWORDS = set("""
a an and are as at be but by for from has have how in into is it its new of on or out over the this that
to up vs was were what when where who why will with your you after before first more now just gets get
""".split())
# Words too common in games news to show two titles are about the same story.
GENERIC_WORDS = set("""
game games gaming nintendo switch xbox playstation ps5 ps4 pc steam series review reviews trailer update
patch release date dlc season launch launches released announced announces reveal revealed report says
free edition version players player official week year day days hands preview
""".split())

SESSION = requests.Session()
SESSION.headers["User-Agent"] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS))


# --- Helpers ---

def now_ms() -> int:
    return int(time.time() * 1000)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def write_json(path: Path, data, **dump_args) -> None:
    """Write atomically so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as tmp:
        json.dump(data, tmp, ensure_ascii=False, **dump_args)
    os.replace(tmp.name, path)


def phrase_regex(phrases: list[str], plurals: bool = False) -> re.Pattern | None:
    """Whole-phrase, case-insensitive matcher. '#' stands for any number."""
    parts = []
    for p in sorted({p.strip().lower() for p in phrases if p.strip()}, key=len, reverse=True):
        body = re.escape(p).replace(r"\#", r"\d+").replace("#", r"\d+")
        start = r"(?<!\w)" if (p[0].isalnum() or p[0] == "#") else ""
        end = r"(?!\w)" if (p[-1].isalnum() or p[-1] == "#") else ""
        if plurals and p[-1].isalpha():
            body += "s?"
        parts.append(start + body + end)
    return re.compile("|".join(parts), re.IGNORECASE) if parts else None


def clean_title(raw: str) -> str:
    title = " ".join(html.unescape(raw or "").split())
    # Repair UTF-8 read as Latin-1 ("ã‚²ãƒ¼ãƒ " -> "ゲーム"). Correct text fails to re-decode and is kept.
    if MOJIBAKE_RE.search(title) and not CJK_RE.search(title):
        try:
            title = title.encode("latin1").decode("utf-8")
        except UnicodeError:
            pass
    return title


def normalise_url(url: str) -> str:
    """Drop query strings and fragments so tracking and AMP variants collapse to one URL."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def parse_date(entry, assume_jst: bool, fallback: int) -> int:
    """UTC milliseconds. JP feeds without a timezone are read as JST. Never later than now."""
    raw = entry.get("published") or entry.get("updated") or entry.get("dc_date") or ""
    try:
        dt = dateparser.parse(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST if assume_jst else timezone.utc)
        return min(int(dt.timestamp() * 1000), fallback)
    except (ValueError, OverflowError, TypeError):
        pass
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        ts = calendar.timegm(parsed) * 1000  # feedparser's struct is UTC
        return min(ts - (9 * HOUR_MS if assume_jst else 0), fallback)
    return fallback


# --- Rules from config.json ---

class Rules:
    def __init__(self, cfg: dict):
        self.trending = float(cfg.get("trendingThreshold", 2.5))
        self.half_life = float(cfg.get("halfLifeHours", 13))
        self.blocked = phrase_regex(cfg.get("blockedTitles", []))
        self.keywords = phrase_regex(cfg.get("keywordFilter", []), plurals=True)
        self.tags = {}
        for tag, rule in cfg.get("tags", {}).items():
            self.tags[tag] = (phrase_regex(rule.get("match", []), plurals=False),
                              phrase_regex(rule.get("exclude", [])))
        self.common_topics = {t.lower() for t in cfg.get("commonTopics", [])}
        self.common_words = {w for t in self.common_topics for w in WORD_RE.findall(t)}

    def is_blocked(self, title: str) -> bool:
        return bool(self.blocked and self.blocked.search(title))

    def title_tags(self, title: str) -> set[str]:
        found = set()
        for tag, (match, exclude) in self.tags.items():
            text = exclude.sub(" ", title) if exclude else title
            if match and match.search(text):
                found.add(tag)
        return found


# --- Fetching ---
# For each source, in order: its feed address, any "alt" addresses in sources.json, the last address
# that worked, the feed the site advertises on its home page, then Google News for that site.
# Sources marked "fallback": false (feeds for one section of a bigger site) skip the last two.

def http_get(url: str, headers: dict | None = None):
    """GET with retries. Returns (response, None) or (None, error). A block gets one retry as a feed reader."""
    error, as_reader = "no response", False
    for attempt in range(3):
        h = {**FEED_HEADERS, **(headers or {})}
        if as_reader:
            h["User-Agent"] = READER_UA
        status = None
        try:
            resp = SESSION.get(url, headers=h, timeout=TIMEOUT)
            status = resp.status_code
            if status < 400:
                return resp, None
            error = f"HTTP {status}"
        except requests.RequestException as exc:
            error = "timed out" if isinstance(exc, requests.Timeout) else type(exc).__name__
        if status in BLOCKED_CODES and not as_reader:
            as_reader = True
            continue
        if status and status < 500 and status != 429:
            break  # 404 and similar will not fix themselves on retry
        if attempt < 2:
            time.sleep(2 ** attempt)
    return None, error


def parse_entries(resp, src: dict, via: str) -> list[dict] | None:
    """Entries from the last 24 hours, or None if the response isn't a feed."""
    feed = feedparser.parse(resp.content)
    if not feed.entries and not feed.get("version"):
        return None
    fetched_at = now_ms()
    assume_jst = src.get("lang") == "ja"
    entries = []
    for e in feed.entries:
        link = (e.get("link") or "").strip()
        if not e.get("title") or not link.lower().startswith(("http://", "https://")):
            continue  # also rejects javascript: and other unsafe links
        date = parse_date(e, assume_jst, fetched_at)
        if date < fetched_at - WINDOW_MS:
            continue
        title = clean_title(e.get("title"))
        if via == "googlenews" and " - " in title:
            title = title.rsplit(" - ", 1)[0]  # Google News adds " - Publisher"
        entries.append({"title": title, "link": normalise_url(link), "date": date})
    return entries


def discover_feeds(domain: str) -> list[str]:
    """Feed addresses the site lists in its home page (<link rel="alternate" type="application/rss+xml">)."""
    resp, _ = http_get(f"https://{domain}/", {"Accept": "text/html,application/xhtml+xml"})
    if resp is None:
        return []
    page = resp.content[:400_000].decode("utf-8", "ignore")
    found = []
    for tag in FEED_LINK_RE.findall(page):
        if re.search(r"""type\s*=\s*["']application/(?:rss|atom)\+xml""", tag, re.IGNORECASE):
            href = re.search(r"""href\s*=\s*["']([^"']+)""", tag, re.IGNORECASE)
            if href:
                found.append(urljoin(getattr(resp, "url", None) or f"https://{domain}/", html.unescape(href.group(1))))
    return list(dict.fromkeys(found))[:3]


def google_news_url(src: dict) -> str:
    ja = src.get("lang") == "ja"
    return GOOGLE_NEWS_URL.format(q=quote(f"site:{src['domain']} when:1d"), hl="ja" if ja else "en-GB",
                                  gl="JP" if ja else "GB", lang="ja" if ja else "en")


def fetch_feed(src: dict, feed_cache: dict) -> tuple[list[dict], dict]:
    """Return ([{title, link, date}], status) for one source, trying fallbacks when its feed fails."""
    name = src["name"]
    cached = feed_cache.setdefault(src["rss"], {})
    now = now_ms()
    tried: set[str] = set()
    errors: list[str] = []

    def attempt(url: str, via: str) -> list[dict] | None:
        if not url or url in tried:
            return None
        tried.add(url)
        headers = {}
        if cached.get("url") == url:  # conditional request, so an unchanged feed is a tiny 304
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("modified"):
                headers["If-Modified-Since"] = cached["modified"]
        resp, error = http_get(url, headers)
        if resp is not None and resp.status_code == 304:
            return cached.get("entries", [])
        entries = parse_entries(resp, src, via) if resp is not None else None
        if entries is None or (via == "googlenews" and not entries):
            errors.append(error or ("no articles" if via == "googlenews" else "not a feed"))
            return None
        cached.update(url=url, via=via, etag=resp.headers.get("ETag"),
                      modified=resp.headers.get("Last-Modified"), entries=entries)
        return entries

    def ok(entries: list[dict], via: str):
        cached["lastOk"] = now
        if via != "direct":
            log.warning("[%s] feed %s failed (%s); using %s%s", name, src["rss"], errors[0] if errors else "?",
                        {"alternate": "alternate address", "discovered": "the feed listed on the site",
                         "googlenews": "Google News"}.get(via, via),
                        f": {cached['url']} (worth putting in sources.json)" if via == "discovered" else "")
        status = {"ok": True}
        if via != "direct":
            status["via"] = via
        return entries, status

    plan = [(src["rss"], "direct")] + [(u, "alternate") for u in src.get("alt", [])]
    if cached.get("url") and cached.get("via") in ("alternate", "discovered"):
        plan.append((cached["url"], cached["via"]))
    for url, via in plan:
        if (entries := attempt(url, via)) is not None:
            return ok(entries, via)

    if src.get("fallback", True):
        if now - cached.get("discoveredAt", 0) > DISCOVERY_EVERY_MS:
            cached["discoveredAt"] = now
            for url in discover_feeds(src["domain"]):
                if (entries := attempt(url, "discovered")) is not None:
                    return ok(entries, "discovered")
        if (entries := attempt(google_news_url(src), "googlenews")) is not None:
            return ok(entries, "googlenews")

    error = errors[0] if errors else "no response"
    log.warning("[%s] not responding: %s", name, "; ".join(dict.fromkeys(errors)) or error)
    return cached.get("entries", []), {"ok": False, "error": error, "lastOk": cached.get("lastOk")}


# --- Translation ---
# Providers in order: Google Cloud and DeepL when their keys are set (dependable), then three free
# services. When one fails, the same titles go to the next. Each title is translated once and cached.

def translate_cloud(titles: list[str], key: str) -> list[str]:
    resp = SESSION.post(CLOUD_TRANSLATE_URL, params={"key": key},
                        json={"q": titles, "source": "ja", "target": "en", "format": "text"}, timeout=TIMEOUT)
    resp.raise_for_status()
    return [html.unescape(t["translatedText"]) for t in resp.json()["data"]["translations"]]


def translate_deepl(titles: list[str], key: str) -> list[str]:
    url = DEEPL_URL.format(free="-free" if key.endswith(":fx") else "")
    resp = SESSION.post(url, headers={"Authorization": f"DeepL-Auth-Key {key}"},
                        json={"text": titles, "source_lang": "JA", "target_lang": "EN-GB"}, timeout=TIMEOUT)
    resp.raise_for_status()
    return [t["text"] for t in resp.json()["translations"]]


def microsoft_translator():
    """Microsoft's free translator (used by the Edge browser): a short-lived token, then batches of titles."""
    token: dict[str, str] = {}

    def run(titles: list[str]) -> list[str]:
        if "value" not in token:
            auth = SESSION.get(MS_AUTH_URL, timeout=TIMEOUT)
            auth.raise_for_status()
            token["value"] = auth.text.strip()
        resp = SESSION.post(MS_TRANSLATE_URL, params={"from": "ja", "to": "en", "api-version": "3.0"},
                            headers={"Authorization": f"Bearer {token['value']}"},
                            json=[{"Text": t} for t in titles], timeout=TIMEOUT)
        resp.raise_for_status()
        return [item["translations"][0]["text"] for item in resp.json()]
    return run


def translate_google_free(titles: list[str]) -> list[str]:
    """Google's public endpoint. Titles go one per line; one by one if the lines come back merged."""
    def call(text: str) -> str:
        resp = SESSION.get(FREE_GOOGLE_URL, params={"client": "gtx", "sl": "ja", "tl": "en", "dt": "t", "q": text},
                           timeout=TIMEOUT)
        resp.raise_for_status()
        return "".join(seg[0] for seg in resp.json()[0] if seg and seg[0])

    lines = [line.strip() for line in call("\n".join(titles)).split("\n")]
    if len(lines) == len(titles) and all(lines):
        return lines
    return [call(t) for t in titles]


def translate_mymemory(titles: list[str]) -> list[str]:
    """MyMemory: free, one title per request, limited per day. Last resort."""
    out = []
    for t in titles:
        resp = SESSION.get(MYMEMORY_URL, params={"q": t, "langpair": "ja|en"}, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        text = html.unescape((data.get("responseData") or {}).get("translatedText") or "")
        if int(data.get("responseStatus") or 0) != 200 or text.upper().startswith("MYMEMORY WARNING"):
            raise ValueError(data.get("responseDetails") or "daily limit reached")
        out.append(text)
    return out


def translators() -> list[tuple[str, object, int, int]]:
    """(name, function, most titles per request, most characters per request), in the order to try."""
    chain = []
    if key := os.environ.get("GOOGLE_TRANSLATE_API_KEY", "").strip():
        chain.append(("Google Cloud", lambda b: translate_cloud(b, key), 100, 20_000))
    if key := os.environ.get("DEEPL_API_KEY", "").strip():
        chain.append(("DeepL", lambda b: translate_deepl(b, key), 50, 20_000))
    chain += [("Microsoft", microsoft_translator(), 50, 10_000),
              ("Google", translate_google_free, 25, 600),
              ("MyMemory", translate_mymemory, 10, 5_000)]
    return chain


def translate_titles(articles: list[dict], cache: dict, now: int) -> dict:
    """Translate Japanese titles in place (cached). Returns a status for the site."""
    pending = sorted({a["title"] for a in articles
                      if a.get("lang") == "ja" and CJK_RE.search(a["title"]) and a["title"] not in cache})
    chain, used, errors = translators(), [], {}
    while pending and chain:
        name, run, most, chars = chain[0]
        batch, size = [], 0
        for t in pending:
            if batch and (len(batch) >= most or size + len(t) > chars):
                break
            batch.append(t)
            size += len(t) + 1
        try:
            result = [r.strip() for r in run(batch)]
            if len(result) != len(batch):
                raise ValueError("wrong number of translations")
            done = {o: r for o, r in zip(batch, result) if r and r != o}
            if not done:
                raise ValueError("returned the text untranslated")
            for original, english in done.items():
                cache[original] = [english, now]
            pending = pending[len(batch):]
            if name not in used:
                used.append(name)
            if name not in ("Google Cloud", "DeepL"):
                time.sleep(TRANSLATE_DELAY)
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            errors[name] = f"HTTP {status}" if status else str(exc)[:80] or type(exc).__name__
            log.warning("Translation via %s failed (%s); trying the next service", name, errors[name])
            chain.pop(0)

    untranslated = 0
    for a in articles:
        if a.get("lang") != "ja" or not CJK_RE.search(a["title"]):
            continue
        hit = cache.get(a["title"])
        if hit:
            hit[1] = now
            a["title"] = hit[0]
            a["translated"] = True
        else:
            untranslated += 1
    log.info("Translation: %d titles still in Japanese; used %s%s", untranslated, ", ".join(used) or "cache only",
             "; failed: " + ", ".join(f"{k} ({v})" for k, v in errors.items()) if errors else "")
    status = {"ok": untranslated == 0, "untranslated": untranslated}
    if used:
        status["via"] = used
    if errors:
        status["errors"] = errors
    return status


# --- Site icons ---

def png_width(data: bytes) -> int:
    return int.from_bytes(data[16:20], "big") if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 24 else 0


def icon_name(domain: str) -> str:
    return re.sub(r"[^a-z0-9.-]", "", domain.lower()) + ".png"


def sync_icons(domains: set[str], now: int) -> set[str]:
    """Download one icon per site into .cache/icons. Returns the domains that have an icon."""
    folder = CACHE_DIR / "icons"
    folder.mkdir(parents=True, exist_ok=True)
    index = load_json(folder / "index.json", {})  # domain -> [has_icon, checked_at]

    def refresh(domain: str) -> None:
        has, checked = index.get(domain, [False, 0])
        if now - checked < (ICON_TTL_MS if has else ICON_RETRY_MS) and (not has or (folder / icon_name(domain)).exists()):
            return
        try:
            resp = SESSION.get(ICON_URL + quote(domain), timeout=TIMEOUT)
            ok = resp.status_code == 200 and png_width(resp.content) > 16  # 16px is Google's "unknown" globe
            if ok:
                (folder / icon_name(domain)).write_bytes(resp.content)
            index[domain] = [ok, now]
        except requests.RequestException as exc:
            log.warning("Icon for %s failed: %s", domain, exc)

    with ThreadPoolExecutor(MAX_WORKERS) as pool:
        list(pool.map(refresh, sorted(domains)))
    write_json(folder / "index.json", index)
    return {d for d in domains if index.get(d, [False])[0] and (folder / icon_name(d)).exists()}


# --- Grouping ---

def dedupe_by_url(articles: list[dict]) -> list[dict]:
    """Keep the earliest copy of each URL."""
    best: dict[str, dict] = {}
    for a in articles:
        if a["link"] not in best or a["date"] < best[a["link"]]["date"]:
            best[a["link"]] = a
    return list(best.values())


def topic_key(title: str, rules: Rules) -> str | None:
    """Longest capitalised phrase ("Elden Ring") that isn't a name shared by unrelated stories."""
    matches = [m.lower() for m in TOPIC_RE.findall(title)]
    matches = [m for m in matches
               if m not in rules.common_topics and not all(w in STOPWORDS for w in m.split())]
    return max(matches, key=len) if matches else None


def group_articles(articles: list[dict], rules: Rules) -> list[list[dict]]:
    ignore = STOPWORDS | GENERIC_WORDS | rules.common_words
    norms, words = [], []
    for a in articles:
        tokens = WORD_RE.findall(a["title"].lower())
        norms.append(" ".join(tokens))
        words.append({w for w in tokens if len(w) > 2 and w not in ignore})

    # Pass 1: similar titles that also share distinctive words ("Nintendo Switch 2 price" and
    # "Nintendo Switch 2 sales" score high on similarity but are different stories).
    used = [False] * len(articles)
    groups: list[list[dict]] = []
    for i, a in enumerate(articles):
        if used[i]:
            continue
        used[i] = True
        group = [a]
        for j in range(i + 1, len(articles)):
            if (not used[j] and len(words[i] & words[j]) >= MIN_SHARED_WORDS
                    and fuzz.token_set_ratio(norms[i], norms[j], score_cutoff=SIMILARITY_THRESHOLD)):
                used[j] = True
                group.append(articles[j])
        groups.append(group)

    # Pass 2: leftover single articles about the same named topic, published close together.
    by_topic: dict[str, list[list[dict]]] = {}
    for g in groups:
        if len(g) == 1 and (key := topic_key(g[0]["title"], rules)):
            by_topic.setdefault(key, []).append(g)
    for members in by_topic.values():
        members.sort(key=lambda g: g[0]["date"])
        cluster = [members[0]]
        for g in members[1:] + [None]:
            if g is not None and g[0]["date"] - cluster[0][0]["date"] <= TOPIC_WINDOW_MS:
                cluster.append(g)
                continue
            if len(cluster) >= MIN_TOPIC_GROUP:
                for other in cluster[1:]:
                    cluster[0].extend(other)
                    other.clear()
            if g is not None:
                cluster = [g]
    return [g for g in groups if g]


def build_story(group: list[dict], src_index: dict[str, int], rules: Rules) -> dict:
    """Lead = oldest non-Japanese article, because translated titles read awkwardly."""
    group.sort(key=lambda a: a["date"])
    lead = next((a for a in group if a.get("lang") != "ja"), group[0])

    def public(a: dict) -> dict:
        out = {"title": a["title"], "link": a["link"], "date": a["date"], "src": src_index[a["source"]]}
        if a.get("translated"):
            out["translated"] = True
        return out

    # Platform tags only from official sources (PlayStation Blog, Xbox Wire, Nintendo News, Steam News):
    # a story gets one when that official source is among its articles.
    # Rumour from the lead's headline or a rumour source: a confirmed story a leak also covered is not a rumour.
    tags = {t for a in group for t in a.get("tags", []) if t != "rumour"}
    if "rumour" in rules.title_tags(lead["title"]) | set(lead.get("tags", [])):
        tags.add("rumour")

    story = public(lead)
    story["sources"] = len({a["domain"] for a in group})
    if tags:
        story["tags"] = sorted(tags)
    if any(a.get("lang") == "ja" for a in group):
        story["jp"] = True
    if len(group) > 1:
        story["group"] = [public(a) for a in group if a is not lead]
    return story


# --- Main ---

def load_sources() -> list[dict]:
    seen: set[str] = set()
    sources = []
    for s in load_json(SOURCES_FILE, []):
        if not s.get("enabled", True):
            continue
        if s["rss"] in seen:
            log.warning("Duplicate feed skipped: %s", s["rss"])
            continue
        seen.add(s["rss"])
        sources.append(s)
    return sources


def main() -> None:
    started = time.time()
    now = now_ms()
    rules = Rules(load_json(CONFIG_FILE, {}))
    sources = load_sources()
    feed_cache = load_json(CACHE_DIR / "feeds.json", {})
    translations = load_json(CACHE_DIR / "translations.json", {})

    with ThreadPoolExecutor(MAX_WORKERS) as pool:
        results = list(pool.map(lambda s: fetch_feed(s, feed_cache), sources))

    articles = []
    outlets: dict[str, dict] = {}  # one status per outlet name (IGN has several feeds)
    for src, (entries, status) in zip(sources, results):
        kept = [e for e in entries
                if e["date"] >= now - WINDOW_MS and not rules.is_blocked(e["title"])
                and (not src.get("keywordFilter") or (rules.keywords and rules.keywords.search(e["title"])))]
        log.info("[%s] %d articles%s", src["name"], len(kept), "" if status["ok"] else f" (failing: {status['error']})")
        o = outlets.setdefault(src["name"], {"name": src["name"], "domain": src["domain"], "feeds": 0, "failed": 0, "count": 0})
        o["feeds"] += 1
        if status.get("via"):
            o["via"] = status["via"]
        if not status["ok"]:
            o["failed"] += 1
            o.setdefault("error", status["error"])
            last = status.get("lastOk")
            if last and last > o.get("lastOk", 0):
                o["lastOk"] = last
        for e in kept:
            a = {**e, "source": src["name"], "domain": src["domain"]}
            if src.get("lang"):
                a["lang"] = src["lang"]
            if src.get("tags"):
                a["tags"] = src["tags"]
            articles.append(a)

    articles = dedupe_by_url(articles)
    translation = translate_titles(articles, translations, now)
    articles = sorted((a for a in articles if not rules.is_blocked(a["title"])), key=lambda a: a["date"])

    icons = sync_icons({o["domain"] for o in outlets.values()}, now)
    source_list = sorted(outlets.values(), key=lambda o: o["name"].lower())
    src_index = {o["name"]: i for i, o in enumerate(source_list)}
    stories = sorted((build_story(g, src_index, rules) for g in group_articles(articles, rules)),
                     key=lambda s: -s["date"])
    for s in stories:
        for a in [s, *s.get("group", [])]:
            source_list[a["src"]]["count"] += 1
    for o in source_list:
        # An outlet with several feeds (IGN) only counts as failing when all of them fail.
        o["ok"] = o.pop("failed") < o.pop("feeds")
        o["icon"] = o["domain"] in icons
        if o["ok"]:
            o.pop("error", None)
            o.pop("lastOk", None)

    # Save the caches even when nothing was fetched, so the next run starts warm.
    live = {s["rss"] for s in sources}
    write_json(CACHE_DIR / "feeds.json", {k: v for k, v in feed_cache.items() if k in live})
    write_json(CACHE_DIR / "translations.json",
               {k: v for k, v in translations.items() if now - v[1] < TRANSLATION_TTL_MS})

    if not stories:
        log.error("No articles fetched; failing so the live site is left as it is.")
        sys.exit(1)

    write_json(OUTPUT_FILE, {
        "generatedAt": now_ms(),
        "trendingThreshold": rules.trending,
        "halfLifeHours": rules.half_life,
        "translation": translation,
        "sources": source_list,
        "articles": stories,
    }, separators=(",", ":"))
    failing = [o["name"] for o in source_list if not o["ok"]]
    log.info("Saved %d stories from %d articles in %.1fs. Failing feeds: %s",
             len(stories), len(articles), time.time() - started, ", ".join(failing) or "none")


if __name__ == "__main__":
    main()
