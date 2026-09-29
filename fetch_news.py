"""Build data.json for OniMugen+ from the RSS feeds listed in sources.json.

Run by .github/workflows/update.yml every 10 minutes. State kept between runs in .cache/:
  feeds.json         ETag/Last-Modified and the last entries of each feed, so an unchanged
                     feed costs a tiny 304 reply and a failing feed falls back to its last copy.
  translations.json  Japanese title -> English, so each title is translated only once.
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
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests
from dateutil import parser as dateparser
from deep_translator import GoogleTranslator
from rapidfuzz import fuzz

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch")

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.json"
OUTPUT_FILE = ROOT / "data.json"
CACHE_DIR = ROOT / ".cache"
FEED_CACHE = CACHE_DIR / "feeds.json"
TRANSLATION_CACHE = CACHE_DIR / "translations.json"

HOUR_MS = 3_600_000
WINDOW_MS = 24 * HOUR_MS             # only keep the last 24 hours
TRANSLATION_TTL_MS = 7 * 24 * HOUR_MS

# Sent to the browser, which works out Trending itself so it never goes stale:
# score = unique sites covering the story * 0.5 ** (age_hours / HALF_LIFE_HOURS)
TRENDING_THRESHOLD = 2.5
HALF_LIFE_HOURS = 13

SIMILARITY_THRESHOLD = 69            # rapidfuzz token_set_ratio needed to group two titles
MIN_TOPIC_GROUP = 3                  # a shared named topic needs 3+ articles to be merged

MAX_WORKERS = 12
TIMEOUT = 15
TRANSLATE_DELAY = 0.3                # pause between translation calls to avoid throttling
TRANSLATE_MAX_FAILURES = 3           # stop translating for this run after this many in a row

JST = timezone(timedelta(hours=9))

# Titles matching any of these are dropped (case-insensitive).
BLOCKED_TITLES = re.compile("|".join([
    r"\btop \d+\b", r"\b\d+ best\b", r"best games", r"games (?:you need )?to play",
    r"\branked\b", r"\bhow to ", r"april fools?", r"% off", r"save \$", r"just \$",
    r"music video", r"\ball fc\b",
]), re.IGNORECASE)

# Only used for general-news sources marked "keywordFilter" (The Verge, Bloomberg).
KEYWORDS = [
    "game", "gaming", "videogame", "video game", "gameplay", "gamer",
    "game developer", "game studio", "game publisher", "indie game", "patch notes", "dlc",
    "expansion", "season pass", "live service", "battle pass", "microtransactions",
    "early access", "modding", "esports", "multiplayer", "co-op", "open world",
    "nintendo", "switch 2", "playstation", "ps5", "ps4", "psvr2", "xbox", "game pass",
    "steam", "steam deck", "valve", "console", "handheld", "pc gaming", "gaming pc",
    "gaming laptop", "gpu", "graphics card", "nvidia", "amd", "radeon", "rtx", "dlss",
    "ray tracing", "activision", "blizzard", "electronic arts", "ea", "ubisoft",
    "take-two", "take two", "rockstar", "square enix", "capcom", "bandai namco", "sega",
    "konami", "cd projekt", "bethesda", "zenimax", "epic games", "riot games",
    "paradox interactive", "embracer", "fromsoftware", "larian", "unreal engine", "unity",
    "game engine", "ps plus", "playstation plus", "gog", "eshop", "summer game fest",
    "gamescom", "tokyo game show", "game awards", "state of play", "nintendo direct",
    "xbox showcase", "call of duty", "battlefield", "halo", "forza", "minecraft", "fortnite",
    "grand theft auto", "gta", "elder scrolls", "fallout", "final fantasy", "dragon quest",
    "persona", "monster hunter", "zelda", "mario", "pokemon", "pokémon", "metroid",
    "elden ring", "dark souls", "bloodborne", "cyberpunk", "witcher", "assassin's creed",
    "far cry", "rainbow six", "destiny", "overwatch", "diablo", "league of legends",
    "valorant", "dota", "counter-strike", "counter strike", "gacha", "mobile game",
]
KEYWORDS_RE = re.compile(r"\b(?:" + "|".join(map(re.escape, KEYWORDS)) + r")s?\b", re.IGNORECASE)

CJK_RE = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")
MOJIBAKE_RE = re.compile(r"[\u00c0-\u00ff]")
PUNCT_RE = re.compile(r"[^\w\s]")
# 2+ capitalised words, allowing short joiners and roman numerals: "Call of Duty", "GTA VI"
TOPIC_RE = re.compile(
    r"\b[A-Z][a-zA-Z]+(?:\s+(?:of|in|the|a|an|to|for|and|or|vs|[IVX]+|[A-Z][a-zA-Z]+))*\s+[A-Z][a-zA-Z]+\b"
)
TOPIC_STOPWORDS = {"the", "a", "an", "of", "in", "to", "for", "and", "or", "vs", "new", "this", "that"}

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


def clean_title(raw: str) -> str:
    title = " ".join(html.unescape(raw or "").split())
    # Repair UTF-8 that was read as Latin-1 ("ã‚²ãƒ¼ãƒ " -> "ゲーム"). Correct text fails to re-decode and is kept.
    if MOJIBAKE_RE.search(title) and not CJK_RE.search(title):
        try:
            title = title.encode("latin1").decode("utf-8")
        except UnicodeError:
            pass
    return title


def normalise_url(url: str) -> str:
    """Drop query strings and fragments so tracking and AMP variants collapse to one URL."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path.rstrip("/"), "", ""))


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


# --- Fetching ---

def fetch_feed(src: dict, feed_cache: dict) -> list[dict]:
    """Return [{title, link, date}] for one feed, reusing the cached copy on 304 or failure."""
    url, name = src["rss"], src["name"]
    cached = feed_cache.get(url, {})
    headers = {}
    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("modified"):
        headers["If-Modified-Since"] = cached["modified"]

    for attempt in range(3):
        try:
            resp = SESSION.get(url, headers=headers, timeout=TIMEOUT)
            if resp.status_code == 304:
                return cached.get("entries", [])
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            log.warning("[%s] attempt %d failed: %s", name, attempt + 1, exc)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status and status < 500 and status != 429:
                break  # 403/404 and similar will not fix themselves on retry
            if attempt < 2:
                time.sleep(2 ** attempt)
    else:
        return cached.get("entries", [])
    if resp.status_code >= 300:
        return cached.get("entries", [])

    feed = feedparser.parse(resp.content)
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

    feed_cache[url] = {"etag": resp.headers.get("ETag"), "modified": resp.headers.get("Last-Modified"), "entries": entries}
    return entries


def keep(entry: dict, src: dict, cutoff: int) -> bool:
    title = entry["title"]
    return (
        entry["date"] >= cutoff
        and not BLOCKED_TITLES.search(title)
        and (not src.get("keywordFilter") or KEYWORDS_RE.search(title) is not None)
    )


def translate_titles(articles: list[dict], cache: dict, now: int) -> None:
    """Translate Japanese titles in place, using and updating the cache."""
    translator = GoogleTranslator(source="ja", target="en")
    failures = 0
    for a in articles:
        if a.get("lang") != "ja" or not CJK_RE.search(a["title"]):
            continue
        hit = cache.get(a["title"])
        if hit is None and failures < TRANSLATE_MAX_FAILURES:
            try:
                hit = cache[a["title"]] = [translator.translate(a["title"]), now]
                failures = 0
                time.sleep(TRANSLATE_DELAY)
            except Exception as exc:  # the translator raises many unrelated types
                failures += 1
                log.warning("Translation failed (%d in a row): %s", failures, exc)
        if hit and hit[0]:
            hit[1] = now
            a["title"] = hit[0]
            a["translated"] = True
            if BLOCKED_TITLES.search(hit[0]):
                a["blocked"] = True
    if failures >= TRANSLATE_MAX_FAILURES:
        log.warning("Translator stopped responding; remaining titles stay in Japanese this run.")


# --- Grouping ---

def dedupe_by_url(articles: list[dict]) -> list[dict]:
    """Keep the earliest copy of each URL."""
    best: dict[str, dict] = {}
    for a in articles:
        if a["link"] not in best or a["date"] < best[a["link"]]["date"]:
            best[a["link"]] = a
    return list(best.values())


def topic_key(title: str) -> str | None:
    """Longest capitalised phrase in a title ("Elden Ring"), used as a grouping key."""
    matches = [m for m in TOPIC_RE.findall(title) if not all(w.lower() in TOPIC_STOPWORDS for w in m.split())]
    return max(matches, key=len).lower() if matches else None


def group_articles(articles: list[dict]) -> list[list[dict]]:
    # Pass 1: similar titles. Everything is already inside the 24h window.
    norms = [" ".join(PUNCT_RE.sub("", a["title"].lower()).split()) for a in articles]
    used = [False] * len(articles)
    groups: list[list[dict]] = []
    for i, a in enumerate(articles):
        if used[i]:
            continue
        used[i] = True
        group = [a]
        for j in range(i + 1, len(articles)):
            if not used[j] and fuzz.token_set_ratio(norms[i], norms[j], score_cutoff=SIMILARITY_THRESHOLD):
                used[j] = True
                group.append(articles[j])
        groups.append(group)

    # Pass 2: merge leftover single articles that share a named topic, when there are enough of them.
    by_topic: dict[str, list[list[dict]]] = {}
    for g in groups:
        if len(g) == 1 and (key := topic_key(g[0]["title"])):
            by_topic.setdefault(key, []).append(g)
    for members in by_topic.values():
        if len(members) >= MIN_TOPIC_GROUP:
            for g in members[1:]:
                members[0].extend(g)
                g.clear()
    return [g for g in groups if g]


def public(a: dict) -> dict:
    """Only the fields the site uses. False or empty fields are left out to keep the file small."""
    out = {"title": a["title"], "link": a["link"], "date": a["date"], "source": a["source"], "domain": a["domain"]}
    if a.get("translated"):
        out["translated"] = True
    return out


def build_story(group: list[dict]) -> dict:
    """Lead = oldest non-Japanese article, because translated titles read awkwardly."""
    group.sort(key=lambda a: a["date"])
    lead = next((a for a in group if a.get("lang") != "ja"), group[0])
    story = public(lead)
    story["sources"] = len({a["domain"] for a in group})
    if lead.get("official"):
        story["official"] = True  # platform badges only for first-party news
    tags = set(lead.get("tags", []))
    for a in group:
        tags.update(t for t in a.get("tags", []) if t != "reddit")  # platform tags spread, rumour does not
    if tags:
        story["tags"] = sorted(tags)
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
    sources = load_sources()
    feed_cache = load_json(FEED_CACHE, {})
    translations = load_json(TRANSLATION_CACHE, {})

    with ThreadPoolExecutor(MAX_WORKERS) as pool:
        results = list(pool.map(lambda s: fetch_feed(s, feed_cache), sources))

    articles = []
    for src, entries in zip(sources, results):
        kept = [e for e in entries if keep(e, src, now - WINDOW_MS)]
        log.info("[%s] %d articles", src["name"], len(kept))
        for e in kept:
            a = {**e, "source": src["name"], "domain": src["domain"]}
            for field in ("lang", "tags", "official"):
                if src.get(field):
                    a[field] = src[field]
            articles.append(a)

    articles = dedupe_by_url(articles)
    translate_titles(articles, translations, now)
    articles = sorted((a for a in articles if not a.get("blocked")), key=lambda a: a["date"])
    stories = sorted((build_story(g) for g in group_articles(articles)), key=lambda s: -s["date"])

    # Save the caches even when nothing was fetched, so the next run starts warm.
    live = {s["rss"] for s in sources}
    write_json(FEED_CACHE, {k: v for k, v in feed_cache.items() if k in live})
    write_json(TRANSLATION_CACHE, {k: v for k, v in translations.items() if now - v[1] < TRANSLATION_TTL_MS})

    if not stories:
        log.error("No articles fetched; failing so the live site is left as it is.")
        sys.exit(1)

    write_json(OUTPUT_FILE, {
        "generatedAt": now_ms(),
        "trendingThreshold": TRENDING_THRESHOLD,
        "halfLifeHours": HALF_LIFE_HOURS,
        "articles": stories,
    }, separators=(",", ":"))
    log.info("Saved %d stories from %d articles in %.1fs", len(stories), len(articles), time.time() - started)


if __name__ == "__main__":
    main()
