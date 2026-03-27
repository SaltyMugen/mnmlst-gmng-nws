import calendar
import feedparser
import html
import json
import logging
import math
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timezone, timedelta, datetime
from urllib.parse import urlparse, urlunparse

import requests
from dateutil import parser as dateutil_parser
from deep_translator import GoogleTranslator
from rapidfuzz import fuzz

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# --- CONFIGURATION ---
DATA_FILE = "data_gaming.json"

# A story needs to clear this score to get the Trending badge in the UI.
# Score = unique source count × time-decay (halves every 12h).
# Roughly: 3 fresh sources = ~3.0, 3 sources at 12h old = ~1.5, 5 at 2h = ~4.7.
# Raise this if too many stories are trending; lower it if too few.
TRENDING_THRESHOLD = 2.1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

translator = GoogleTranslator(source="ja", target="en")

JST = timezone(timedelta(hours=9))

SOURCES = [
    # --- BIG OUTLETS ---
    {"name": "IGN Games",         "rss": "https://www.ign.com/rss/v2/articles/feed?categorySlug=games",             "domain": "ign.com"},
    {"name": "GameSpot",          "rss": "https://www.gamespot.com/feeds/mashup/",                                  "domain": "gamespot.com"},
    {"name": "PC Gamer",          "rss": "https://www.pcgamer.com/rss/",                                            "domain": "pcgamer.com"},
    {"name": "Eurogamer",         "rss": "https://www.eurogamer.net/feed",                                          "domain": "eurogamer.net"},
    {"name": "Kotaku",            "rss": "https://kotaku.com/rss",                                                  "domain": "kotaku.com"},
    {"name": "Polygon",           "rss": "https://www.polygon.com/rss/gaming/index.xml",                            "domain": "polygon.com"},
    {"name": "VGC",               "rss": "https://www.videogameschronicle.com/feed/",                               "domain": "videogameschronicle.com"},
    {"name": "Rock Paper Shotgun","rss": "https://feeds.feedburner.com/RockPaperShotgun",                           "domain": "rockpapershotgun.com"},
    {"name": "VG247",             "rss": "https://www.vg247.com/feed",                                              "domain": "vg247.com"},
    {"name": "Destructoid",       "rss": "https://www.destructoid.com/feed/",                                       "domain": "destructoid.com"},
    {"name": "TheGamer",          "rss": "https://www.thegamer.com/feed/",                                          "domain": "thegamer.com"},
    {"name": "Gematsu",           "rss": "https://www.gematsu.com/feed",                                            "domain": "gematsu.com"},
    {"name": "The Verge",         "rss": "https://www.theverge.com/rss/index.xml",                                  "domain": "theverge.com", "filter": True},

    # --- OFFICIAL PLATFORMS ---
    {"name": "PlayStation Blog",  "rss": "https://blog.playstation.com/feed/",                                      "domain": "blog.playstation.com"},
    {"name": "Xbox Wire",         "rss": "https://news.xbox.com/en-us/feed/",                                       "domain": "news.xbox.com"},
    {"name": "Nintendo News",     "rss": "https://www.nintendo.com/en-gb/news.xml",                                 "domain": "nintendo.com"},

    # --- NEWS MACHINES ---
    {"name": "Game Rant",         "rss": "https://gamerant.com/feed/",                                              "domain": "gamerant.com"},
    {"name": "Dexerto",           "rss": "https://www.dexerto.com/gaming/feed/",                                    "domain": "dexerto.com"},
    {"name": "GamesRadar+",       "rss": "https://www.gamesradar.com/all-platforms/news/rss/",                                    "domain": "gamesradar.com"},

    # --- SPECIALISTS & LEAKS ---
    {"name": "Insider Gaming",    "rss": "https://insider-gaming.com/feed/",                                        "domain": "insider-gaming.com"},
    {"name": "DualShockers",      "rss": "https://www.dualshockers.com/feed/",                                      "domain": "dualshockers.com"},
    {"name": "Siliconera",        "rss": "https://www.siliconera.com/feed/",                                        "domain": "siliconera.com"},
    {"name": "RPG Site",          "rss": "https://www.rpgsite.net/rss",                                             "domain": "rpgsite.net"},
    {"name": "Reddit Leaks",      "rss": "https://www.reddit.com/r/GamingLeaksAndRumours/new/.rss?sort=new",        "domain": "reddit.com", "isReddit": True},
    {"name": "GamesBeat",         "rss": "https://gamesbeat.com/category/gameplay/feed/",                        "domain": "gamesbeat.com"},
    # --- INDUSTRY ---
    {"name": "GamesIndustry.biz", "rss": "https://www.gamesindustry.biz/rss/gamesindustry_news_feed.rss",           "domain": "gamesindustry.biz"},

    # --- PLATFORM FANSITES ---
    {"name": "Nintendo Life",     "rss": "https://www.nintendolife.com/feeds/latest",                               "domain": "nintendolife.com"},
    {"name": "Push Square",       "rss": "https://www.pushsquare.com/feeds/latest",                                 "domain": "pushsquare.com"},
    {"name": "Pure Xbox",         "rss": "https://www.purexbox.com/feeds/latest",                                   "domain": "purexbox.com"},

    # --- PC / PLATFORM ---
    {"name": "Steam News",        "rss": "https://store.steampowered.com/feeds/news/collection/steam",                           "domain": "store.steampowered.com"},

    # --- INDIE ---
    {"name": "IndieDB",           "rss": "https://rss.indiedb.com/articles/feed/rss.xml",                                        "domain": "indiedb.com"},

    # --- ADDITIONAL GENERAL ---
    {"name": "Game Informer",     "rss": "https://www.gameinformer.com/rss.xml",                                    "domain": "gameinformer.com"},
    {"name": "Noisy Pixel",       "rss": "https://noisypixel.net/feed/",                                            "domain": "noisypixel.net"},
    {"name": "TweakTown",         "rss": "https://www.tweaktown.com/rss/index.xml",                                 "domain": "tweaktown.com"},

    # --- NEWSLETTERS ---
    {"name": "GameDiscoverCo",    "rss": "https://newsletter.gamediscover.co/feed",                                 "domain": "newsletter.gamediscover.co"},
    {"name": "Game File",         "rss": "https://www.gamefile.news/feed",                                          "domain": "gamefile.news"},
    {"name": "Hit Points",        "rss": "https://newsletter.hitpoints.co/feed",                                    "domain": "newsletter.hitpoints.co"},
    {"name": "Knowledge",         "rss": "https://www.knowledge.me/feed",                                           "domain": "knowledge.me"},

    # --- JP SOURCES ---
    # automaton-media.com/en/ publishes in UTC with proper offsets — no correction needed.
    {"name": "4Gamer.net", "rss": "https://www.4gamer.net/rss/index.xml",                                           "domain": "4gamer.net", "translate": True, "tz_jst": True},
    {"name": "Automaton Media",   "rss": "https://automaton-media.com/en/feed/",                                    "domain": "automaton-media.com"},
    {"name": "Famitsu",           "rss": "https://famitsu.com",                                "domain": "famitsu.com",              "translate": True, "tz_jst": True},
    {"name": "Dengeki Online",    "rss": "https://dengekionline.com/index.xml",                                     "domain": "dengekionline.com",         "translate": True, "tz_jst": True},
    {"name": "GameBiz",           "rss": "https://gamebiz.jp/feed.rss",                                             "domain": "gamebiz.jp",                "translate": True, "tz_jst": True},
    {"name": "Denfaminicogamer",  "rss": "https://news.denfaminicogamer.jp/feed",                                   "domain": "news.denfaminicogamer.jp",  "translate": True, "tz_jst": True},
    {"name": "Game Spark",        "rss": "https://www.gamespark.jp/rss20/index.rdf",                                "domain": "gamespark.jp",              "translate": True, "tz_jst": True},
    {"name": "Gamer.ne.jp",       "rss": "https://www.gamer.ne.jp/feed/news.rdf",                                   "domain": "gamer.ne.jp",               "translate": True, "tz_jst": True},
    {"name": "IGN Japan",         "rss": "https://jp.ign.com/feed.xml",                                             "domain": "jp.ign.com",                "translate": True, "tz_jst": True},
    {"name": "Automaton Media JP","rss": "https://automaton-media.com/feed/",                                       "domain": "automaton-media.com",       "translate": True, "tz_jst": True},
]

KEYWORDS = [
"game", "gaming", "videogame", "video game", "gameplay", "gamer", "gaming industry",
"game developer", "game studio", "game publisher", "indie game", "game release",
"game launch", "game update", "patch notes", "dlc", "expansion", "season pass",
"live service", "battle pass", "microtransactions", "early access", "beta", "alpha build",
"mod", "modding",

"nintendo", "switch", "switch oled", "switch 2",
"playstation", "ps5", "ps4", "psvr", "psvr2", "sony interactive entertainment",
"xbox", "xbox series x", "xbox series s", "xbox game pass", "microsoft gaming",
"steam", "steam deck", "valve",
"console", "handheld console", "portable console", "pc gaming",

"gpu", "graphics card", "gaming pc", "gaming laptop",
"amd", "nvidia", "rtx", "radeon", "dlss", "fsr", "ray tracing", "frame generation",

"activision", "blizzard", "activision blizzard",
"electronic arts", "ea",
"ubisoft",
"take two", "take-two", "take two interactive",
"rockstar", "rockstar games",
"square enix",
"capcom",
"bandai namco",
"sega",
"konami",
"cd projekt", "cd projekt red",
"bethesda",
"zenimax",
"epic games",
"riot games",
"valve corporation",
"paradox interactive",
"embracer group",
"fromsoftware",
"larian studios",

"unreal engine", "unity engine", "cryengine", "game engine", "game development", "dev kit", "sdk",

"xbox game pass", "playstation plus", "ps plus",
"epic games store", "steam sale", "gog", "gog galaxy", "nintendo eshop", "subscription gaming",

"e3", "summer game fest", "gamescom", "tokyo game show", "tgs",
"game awards", "state of play", "nintendo direct", "xbox showcase", "playstation showcase", "blizzcon",

"call of duty", "battlefield", "halo", "forza",
"minecraft", "fortnite",
"grand theft auto", "gta",
"elder scrolls", "fallout",
"final fantasy", "dragon quest", "persona",
"monster hunter",
"zelda", "mario", "pokemon", "metroid",
"dark souls", "elden ring", "bloodborne",
"cyberpunk", "witcher",
"assassin's creed", "far cry", "rainbow six",
"destiny", "overwatch", "diablo",
"league of legends", "valorant", "dota", "counter strike",

"mobile game", "ios game", "android game", "gacha", "app store game", "play store game",

"publisher", "studio acquisition", "game director", "creative director",
"open world", "multiplayer", "single player", "co-op", "esports",

]

_CJK_RE = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


def _is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _fix_mojibake(text: str) -> str:
    """
    Fix UTF-8 mojibake such as 'ã‚²ãƒ¼ãƒ ' → 'ゲーム'.
    Only attempted when the text contains no CJK characters — if CJK is
    already present the string decoded correctly and latin1→utf8 would corrupt it.
    """
    if _is_cjk(text):
        return text
    try:
        return text.encode("latin1").decode("utf-8")
    except Exception:
        return text


def _normalise_url(url: str) -> str:
    """
    Strip tracking parameters and fragments so that AMP/canonical variants
    of the same article collapse to one URL during deduplication.
    Keeps scheme + netloc + path only.
    """
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))
    except Exception:
        return url


def _parse_timestamp(entry: feedparser.FeedParserDict, assume_jst: bool, now_ms: int) -> int:
    """
    Extract a correct UTC millisecond timestamp from a feed entry.

    JP RDF feeds typically use Dublin Core dc:date in ISO 8601 format
    (e.g. "2024-03-16T10:00:00+09:00"). feedparser maps this to entry.published
    but may fail to populate published_parsed if it can't parse the format.

    Resolution order:
    1. Try all raw date string fields with dateutil.parser — handles both
       RFC 2822 (pubDate) and ISO 8601 (dc:date), with or without tz offset.
       If a tz offset is present (+09:00), it is used directly and is correct.
       If no tz offset is present AND assume_jst is True, we interpret as JST.
    2. Fall back to feedparser's published_parsed struct via calendar.timegm,
       applying JST correction if assume_jst and no offset was detected.
    3. Fall back to now_ms if no date info exists at all.
    """
    raw = (
        entry.get("published")
        or entry.get("updated")
        or entry.get("dc_date")
        or entry.get("date")
        or ""
    )

    if raw:
        try:
            dt = dateutil_parser.parse(raw)
            if dt.tzinfo is not None:
                # Explicit timezone present — trust it completely
                return int(dt.timestamp() * 1000)
            elif assume_jst:
                # No timezone in string — assume JST
                dt_jst = dt.replace(tzinfo=JST)
                return int(dt_jst.timestamp() * 1000)
            else:
                # No tz, not a JP source — treat as UTC
                return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except Exception:
            pass

    # Fallback: feedparser's pre-parsed struct (feedparser treats as UTC)
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        ts_utc = int(calendar.timegm(entry.published_parsed) * 1000)
        if assume_jst:
            # feedparser wrongly assumed UTC on a bare JST datetime — correct it
            ts_utc -= 9 * 60 * 60 * 1000
        return ts_utc

    return now_ms


def _translate_titles(titles: list[str], source_name: str) -> list[str]:
    """
    Translate titles from Japanese to English using deep-translator.
    Only titles containing CJK characters are sent. A short sleep between
    calls avoids throttling. Any failure keeps the original title.
    """
    result = list(titles)
    for i, title in enumerate(titles):
        if not _is_cjk(title):
            continue
        try:
            result[i] = translator.translate(title)
            time.sleep(0.5)
        except Exception as exc:
            log.warning("[%s] Translation failed for %r: %s", source_name, title, exc)
    return result


def _fetch_source(src: dict, cutoff_ms: int) -> list[dict]:
    """Fetch a single RSS source and return a list of article dicts."""
    name = src["name"]
    now_ms = int(time.time() * 1000)
    assume_jst = src.get("tz_jst", False)
    articles = []

    for attempt in range(3):
        try:
            resp = requests.get(src["rss"], headers=HEADERS, timeout=15)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            wait = 2 ** attempt
            log.warning("[%s] Attempt %d failed: %s. Retrying in %ds…", name, attempt + 1, exc, wait)
            if attempt < 2:
                time.sleep(wait)
    else:
        log.error("[%s] All attempts failed. Skipping.", name)
        return []

    feed = feedparser.parse(resp.content)

    raw_titles = []
    raw_entries = []

    # FIX: removed the hard [:15] cap — the cutoff_ms filter already gates
    # volume and the cap was silently dropping valid recent articles on busy
    # sources when older entries happened to appear first in the feed.
    for entry in feed.entries:
        ts = _parse_timestamp(entry, assume_jst, now_ms)

        if ts < cutoff_ms:
            continue

        # Decode HTML entities (e.g. &#8217; → ', &#8211; → –, &amp; → &)
        title = html.unescape(entry.title)

        # FIX: only attempt mojibake repair when no CJK is present.
        # Applying latin1→utf8 to a correctly decoded CJK string corrupts it.
        title = _fix_mojibake(title)

        if src.get("filter") and not any(k in title.lower() for k in KEYWORDS):
            continue

        raw_titles.append(title)
        raw_entries.append({"link": _normalise_url(entry.link), "date": ts})

    if src.get("translate"):
        raw_titles = _translate_titles(raw_titles, name)

    for title, entry_meta in zip(raw_titles, raw_entries):
        articles.append(
            {
                "title": title,
                "link": entry_meta["link"],
                "date": entry_meta["date"],
                "domain": src["domain"],
                "sourceName": name,
                "isTranslated": src.get("translate", False),
            }
        )

    return articles


def _url_dedupe(articles: list[dict]) -> list[dict]:
    """
    Phase 1 deduplication: collapse identical URLs, keeping the
    earliest-dated entry so the original publish time is preserved.
    """
    by_url: dict[str, dict] = {}
    for a in articles:
        url = a["link"]
        if url not in by_url or a["date"] < by_url[url]["date"]:
            by_url[url] = a
    return list(by_url.values())


def _norm_title(title: str) -> str:
    """Normalise a title for fuzzy comparison."""
    t = title.lower()
    t = re.sub(r"[^\w\s]", "", t)
    return " ".join(t.split())


def _extract_topic_key(title: str) -> str | None:
    """
    Extract a prominent multi-word proper noun (2-4 capitalised words) from a title
    to use as a topic grouping key. Returns None if no clear topic is found.
    Examples: "Crimson Desert", "Elden Ring", "GTA VI", "Call of Duty"
    """
    # Match sequences of 2-4 title-cased or uppercase words (allows short words like 'of', 'in')
    pattern = re.compile(r'\b([A-Z][a-zA-Z]+(?:\s+(?:of|in|the|a|an|to|for|and|or|vs|II|III|IV|VI|VII|VIII|IX|X|[A-Z][a-zA-Z]*))*\s+[A-Z][a-zA-Z]+)\b')
    matches = pattern.findall(title)
    if not matches:
        return None
    # Return the longest match as it's most likely the game/topic name
    return max(matches, key=len).strip()


def _trending_score(group: list[dict]) -> float:
    """
    How "trending" is this story right now?

    Combines two signals: how many distinct outlets covered it, and how
    recently. A story picked up by 4 sources an hour ago should rank much
    higher than the same coverage spread over 40 hours.

    Decay is exponential with a 12-hour half-life — after 12h the raw
    source count is halved, after 24h it's quartered, etc.
    """
    now_ms = int(time.time() * 1000)
    source_count = len({item["domain"] for item in group})

    # Use the oldest article as the story's birth time — that's when
    # the first outlet picked it up, not when we happened to process it.
    oldest_ms = min(item["date"] for item in group)
    age_hours = (now_ms - oldest_ms) / 3_600_000

    # ln(2) / 12  →  score halves every 12 hours
    decay = math.exp(-0.0578 * age_hours)

    return round(source_count * decay, 3)


def _group_articles(articles: list[dict]) -> list[dict]:
    SIMILARITY_THRESHOLD = 50
    TOPIC_TIME_WINDOW_MS = 48 * 60 * 60 * 1000  # 48 hours for topic grouping
    MIN_TOPIC_GROUP_SIZE = 3  # only topic-group if 3+ articles share the same subject

    norms = [_norm_title(a["title"]) for a in articles]
    used: set[int] = set()
    groups: list[list[dict]] = []

    # --- Pass 1: fuzzy title similarity ---
    for i, a in enumerate(articles):
        if i in used:
            continue
        group = [a]
        used.add(i)
        for j in range(i + 1, len(articles)):
            if j in used:
                continue
            if abs(articles[i]["date"] - articles[j]["date"]) > 86400000:
                continue
            score = fuzz.ratio(norms[i], norms[j])
            if score >= SIMILARITY_THRESHOLD:
                group.append(articles[j])
                used.add(j)
        groups.append(group)

    # --- Pass 2: topic-key grouping for same-subject articles ---
    # Only runs on singletons — already-grouped articles are left alone.
    singleton_indices = [i for i, g in enumerate(groups) if len(g) == 1]

    topic_map: dict[str, list[int]] = {}
    for gi in singleton_indices:
        article = groups[gi][0]
        topic = _extract_topic_key(article["title"])
        if topic:
            topic_key = topic.lower()
            topic_map.setdefault(topic_key, []).append(gi)

    topic_merged: set[int] = set()
    for topic_key, gis in topic_map.items():
        if len(gis) < MIN_TOPIC_GROUP_SIZE:
            continue

        gis_sorted = sorted(gis, key=lambda gi: groups[gi][0]["date"])
        oldest = groups[gis_sorted[0]][0]["date"]
        newest = groups[gis_sorted[-1]][0]["date"]
        if newest - oldest > TOPIC_TIME_WINDOW_MS:
            continue

        base_gi = gis_sorted[0]
        for gi in gis_sorted[1:]:
            groups[base_gi].extend(groups[gi])
            topic_merged.add(gi)

    groups = [g for i, g in enumerate(groups) if i not in topic_merged]

    result: list[dict] = []
    for group in groups:
        lead = max(group, key=lambda x: x["date"])
        members = [m for m in group if m is not lead]

        lead["hotScore"] = _trending_score(group)

        if members:
            lead["groupMembers"] = members

        result.append(lead)

    return result


def fetch_all() -> None:
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - (48 * 60 * 60 * 1000)

    all_articles: list[dict] = []

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_source, src, cutoff_ms): src["name"] for src in SOURCES}
        for future in as_completed(futures):
            source_name = futures[future]
            try:
                results = future.result()
                all_articles.extend(results)
                log.info("[%s] Fetched %d articles.", source_name, len(results))
            except Exception as exc:
                log.error("[%s] Unexpected error: %s", source_name, exc)

    # Phase 1: collapse exact/near-exact URL duplicates
    url_deduped = _url_dedupe(all_articles)

    # Phase 2: fuzzy-group articles covering the same story
    grouped = _group_articles(url_deduped)

    sorted_data = sorted(grouped, key=lambda x: x["date"], reverse=False)

    dir_name = os.path.dirname(os.path.abspath(DATA_FILE)) or "."
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=dir_name, delete=False, suffix=".tmp"
    ) as tmp:
        json.dump(sorted_data, tmp, indent=2, ensure_ascii=False)
        tmp_path = tmp.name

    os.replace(tmp_path, DATA_FILE)
    log.info("Saved %d articles to %s.", len(sorted_data), DATA_FILE)


if __name__ == "__main__":
    fetch_all()
