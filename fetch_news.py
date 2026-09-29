"""Build data.json for OniMugen+ from the feeds in sources.json, using the rules in config.json.

Run by .github/workflows/update.yml every 10 minutes. State kept between runs in .cache/:
  feeds.json         ETag/Last-Modified, last entries and last success of each feed. An unchanged
                     feed costs a tiny 304 reply; a failing feed falls back to its last copy.
  translations.json  Japanese title -> English, so each title is translated once.
  icons/             One small PNG per site, refreshed every two weeks and published with the site.

Translation uses Google Cloud Translation when the GOOGLE_TRANSLATE_API_KEY secret is set
(dependable, free at this volume), otherwise Google's public endpoint (may be blocked).
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
from urllib.parse import quote, urlsplit, urlunsplit

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
FREE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
CLOUD_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"
TRANSLATE_BATCH_CHARS = 1500         # characters per request on the free endpoint (~25 titles)
CLOUD_BATCH_SIZE = 100               # titles per request on Cloud Translation (limit 128)
TRANSLATE_DELAY = 0.5                # pause between free-endpoint requests
ICON_URL = "https://www.google.com/s2/favicons?sz=64&domain="

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

def fetch_feed(src: dict, feed_cache: dict) -> tuple[list[dict], dict]:
    """Return ([{title, link, date}], status) for one feed. Falls back to the cached copy on failure."""
    url, name = src["rss"], src["name"]
    cached = feed_cache.setdefault(url, {})
    headers = {}
    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("modified"):
        headers["If-Modified-Since"] = cached["modified"]

    def failed(reason: str):
        log.warning("[%s] %s", name, reason)
        return cached.get("entries", []), {"ok": False, "error": reason, "lastOk": cached.get("lastOk")}

    resp, error = None, "no response"
    for attempt in range(3):
        try:
            resp = SESSION.get(url, headers=headers, timeout=TIMEOUT)
            if resp.status_code == 304:
                cached["lastOk"] = now_ms()
                return cached.get("entries", []), {"ok": True, "lastOk": cached["lastOk"]}
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            error = f"HTTP {status}" if status else type(exc).__name__
            resp = None
            if status and status < 500 and status != 429:
                break  # 403/404 and similar will not fix themselves on retry
            if attempt < 2:
                time.sleep(2 ** attempt)
    if resp is None or resp.status_code >= 300:
        return failed(error)

    feed = feedparser.parse(resp.content)
    if not feed.entries:
        return failed("feed is empty or unreadable")

    fetched_at = now_ms()
    assume_jst = src.get("lang") == "ja"
    entries = []
    for e in feed.entries:
        link = (e.get("link") or "").strip()
        if not e.get("title") or not link.lower().startswith(("http://", "https://")):
            continue  # also rejects javascript: and other unsafe links
        date = parse_date(e, assume_jst, fetched_at)
        if date >= fetched_at - WINDOW_MS:
            entries.append({"title": clean_title(e.get("title")), "link": normalise_url(link), "date": date})

    cached.update(etag=resp.headers.get("ETag"), modified=resp.headers.get("Last-Modified"),
                  entries=entries, lastOk=fetched_at)
    return entries, {"ok": True, "lastOk": fetched_at}


# --- Translation ---

def translate_free(titles: list[str]) -> list[str]:
    """Google's public endpoint. Titles go one per line; falls back to one by one if lines merge."""
    def call(text: str) -> str:
        resp = SESSION.post(FREE_TRANSLATE_URL, params={"client": "gtx", "sl": "ja", "tl": "en", "dt": "t"},
                            data={"q": text}, timeout=TIMEOUT)
        resp.raise_for_status()
        return "".join(seg[0] for seg in resp.json()[0] if seg and seg[0])

    lines = [line.strip() for line in call("\n".join(titles)).split("\n")]
    if len(lines) == len(titles) and all(lines):
        return lines
    return [call(t).strip() for t in titles]


def translate_cloud(titles: list[str], key: str) -> list[str]:
    """Google Cloud Translation v2: one request per batch, results in the same order."""
    resp = SESSION.post(CLOUD_TRANSLATE_URL, params={"key": key},
                        json={"q": titles, "source": "ja", "target": "en", "format": "text"}, timeout=TIMEOUT)
    resp.raise_for_status()
    out = [html.unescape(t["translatedText"]).strip() for t in resp.json()["data"]["translations"]]
    if len(out) != len(titles):
        raise ValueError("translation count mismatch")
    return out


def batches(titles: list[str], cloud: bool) -> list[list[str]]:
    if cloud:
        return [titles[i:i + CLOUD_BATCH_SIZE] for i in range(0, len(titles), CLOUD_BATCH_SIZE)]
    out, batch, size = [], [], 0
    for t in titles:
        if batch and size + len(t) > TRANSLATE_BATCH_CHARS:
            out.append(batch)
            batch, size = [], 0
        batch.append(t)
        size += len(t) + 1
    return out + ([batch] if batch else [])


def translate_titles(articles: list[dict], cache: dict, now: int) -> dict:
    """Translate Japanese titles in place (cached). Returns a status for the site."""
    key = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "").strip()
    mode = "cloud" if key else "free"
    pending = sorted({a["title"] for a in articles
                      if a.get("lang") == "ja" and CJK_RE.search(a["title"]) and a["title"] not in cache})
    error = None
    for batch in batches(pending, cloud=bool(key)):
        try:
            result = translate_cloud(batch, key) if key else translate_free(batch)
            for original, english in zip(batch, result):
                if english:
                    cache[original] = [english, now]
            if not key:
                time.sleep(TRANSLATE_DELAY)
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            error = f"HTTP {status}" if status else type(exc).__name__
            log.warning("Translation (%s) failed: %s", mode, exc)
            break

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
    log.info("Translation (%s): %d titles still in Japanese%s", mode, untranslated, f", error {error}" if error else "")
    status = {"mode": mode, "ok": error is None, "untranslated": untranslated}
    if error:
        status["error"] = error
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

    # Platform tags come from every article in the group (title words or a platform-only site).
    # Rumour only from the lead: a confirmed story that a leak also covered is not a rumour.
    tags = set()
    for a in group:
        tags |= (rules.title_tags(a["title"]) | set(a.get("tags", []))) - {"rumour"}
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
