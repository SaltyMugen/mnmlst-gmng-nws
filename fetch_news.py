import calendar
import feedparser
import html
import json
import logging
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timezone, timedelta, datetime

import requests
from dateutil import parser as dateutil_parser
from deep_translator import GoogleTranslator

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# --- CONFIGURATION ---
DATA_FILE = "data_gaming.json"
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
    {"name": "GamesRadar+",       "rss": "https://www.gamesradar.com/news/rss/",                                    "domain": "gamesradar.com"},
    {"name": "ComicBook Gaming",  "rss": "https://comicbook.com/gaming/rss",                                        "domain": "comicbook.com"},

    # --- SPECIALISTS & LEAKS ---
    {"name": "Insider Gaming",    "rss": "https://insider-gaming.com/feed/",                                        "domain": "insider-gaming.com"},
    {"name": "DualShockers",      "rss": "https://www.dualshockers.com/feed/",                                      "domain": "dualshockers.com"},
    {"name": "Siliconera",        "rss": "https://www.siliconera.com/feed/",                                        "domain": "siliconera.com"},
    {"name": "RPG Site",          "rss": "https://www.rpgsite.net/rss",                                             "domain": "rpgsite.net"},
    {"name": "Reddit Leaks",      "rss": "https://www.reddit.com/r/GamingLeaksAndRumours/new/.rss?sort=new",        "domain": "reddit.com", "isReddit": True},

    # --- INDUSTRY ---
    {"name": "GamesIndustry.biz", "rss": "https://www.gamesindustry.biz/rss/gamesindustry_news_feed.rss",           "domain": "gamesindustry.biz"},

    # --- PLATFORM FANSITES ---
    {"name": "Nintendo Life",     "rss": "https://www.nintendolife.com/feeds/latest",                               "domain": "nintendolife.com"},
    {"name": "Push Square",       "rss": "https://www.pushsquare.com/feeds/latest",                                 "domain": "pushsquare.com"},
    {"name": "Pure Xbox",         "rss": "https://www.purexbox.com/feeds/latest",                                   "domain": "purexbox.com"},

    # --- PC / PLATFORM ---
    {"name": "Steam News",        "rss": "https://store.steampowered.com/feeds/news.xml",                           "domain": "store.steampowered.com"},

    # --- INDIE ---
    {"name": "IndieDB",           "rss": "https://www.indiedb.com/rss/news",                                        "domain": "indiedb.com"},

    # --- NEWSLETTERS ---
    [
    {"name": "GameDiscoverCo", "rss": "https://newsletter.gamediscover.co/feed",                                    "domain": "newsletter.gamediscover.co"},
    {"name": "Game File", "rss": "https://www.gamefile.news/feed",                                                  "domain": "gamefile.news"},
    {"name": "GamesIndustry.biz", "rss": "https://www.gamesindustry.biz/rss/news",                                  "domain": "gamesindustry.biz"},
    {"name": "Hit Points", "rss": "https://newsletter.hitpoints.co/feed", "domain":                                 "newsletter.hitpoints.co"},
    {"name": "Knowledge", "rss": "https://www.knowledge.me/feed",                                                   "domain": "knowledge.me"},

        # --- ADDITIONAL GENERAL ---
    {"name": "Game Informer",     "rss": "https://www.gameinformer.com/rss.xml",                  "domain": "gameinformer.com"},
    {"name": "Shacknews",         "rss": "https://www.shacknews.com/feed/all",                    "domain": "shacknews.com"},
    {"name": "Giant Bomb",        "rss": "https://www.giantbomb.com/feeds/mashup/",               "domain": "giantbomb.com"},
    {"name": "Digital Foundry",   "rss": "https://www.eurogamer.net/feed/df",                     "domain": "eurogamer.net/digitalfoundry"},
    
    # --- MOBILE & SPECIALISTS ---
    {"name": "Noisy Pixel",       "rss": "https://noisypixel.net/feed/",                          "domain": "noisypixel.net"},
    {"name": "TweakTown",         "rss": "https://www.tweaktown.com/rss/index.xml",               "domain": "tweaktown.com"},

    # --- NEWSLETTERS (FLATTENED) ---
    {"name": "GameDiscoverCo",    "rss": "https://newsletter.gamediscover.co/feed",               "domain": "newsletter.gamediscover.co"},
    {"name": "Game File",         "rss": "https://www.gamefile.news/feed",                        "domain": "gamefile.news"},
    {"name": "Hit Points",        "rss": "https://newsletter.hitpoints.co/feed",                  "domain": "newsletter.hitpoints.co"},
    {"name": "Knowledge",         "rss": "https://www.knowledge.me/feed",                         "domain": "knowledge.me"},
    
    # --- JP SOURCES ---
    {"name": "4Gamer.net", "rss": "https://www.4gamer.net/rss/index.xml",                                           "domain": "4gamer.net", "translate": True, "tz_jst": True},
    {"name": "Automaton Media",   "rss": "https://automaton-media.com/en/feed/",                                    "domain": "automaton-media.com"},
    {"name": "Famitsu",           "rss": "https://www.famitsu.com/rss/fcom_all.rdf",                                "domain": "famitsu.com",              "translate": True, "tz_jst": True},
    {"name": "Dengeki Online",    "rss": "https://dengekionline.com/index.xml",                                     "domain": "dengekionline.com",         "translate": True, "tz_jst": True},
    {"name": "GameBusiness.jp",   "rss": "https://www.gamebusiness.jp/rss/index.rdf",                               "domain": "gamebusiness.jp",           "translate": True, "tz_jst": True},
    {"name": "GameBiz",           "rss": "https://gamebiz.jp/feed.rss",                                             "domain": "gamebiz.jp",                "translate": True, "tz_jst": True},
    {"name": "Denfaminicogamer",  "rss": "https://news.denfaminicogamer.jp/feed",                                   "domain": "news.denfaminicogamer.jp",  "translate": True, "tz_jst": True},
    {"name": "Game Spark",        "rss": "https://www.gamespark.jp/rss20/index.rdf",                                "domain": "gamespark.jp",              "translate": True, "tz_jst": True},
    {"name": "Inside Games",      "rss": "https://www.inside-games.jp/rss20/index.rdf",                             "domain": "inside-games.jp",           "translate": True, "tz_jst": True},
    {"name": "Gamer.ne.jp",       "rss": "https://www.gamer.ne.jp/feed/news.rdf",                                   "domain": "gamer.ne.jp",               "translate": True, "tz_jst": True},
    {"name": "IGN Japan",         "rss": "https://jp.ign.com/feed.xml",                                             "domain": "jp.ign.com",                "translate": True, "tz_jst": True},
    {"name": "Automaton Media JP","rss": "https://automaton-media.com/feed/",                                       "domain": "automaton-media.com",       "translate": True, "tz_jst": True},
]
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

    try:
        return text.encode("latin1").decode("utf-8")
    except Exception:
        return text

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

    for entry in feed.entries[:15]:
        ts = _parse_timestamp(entry, assume_jst, now_ms)

        if ts < cutoff_ms:
            continue

        # Decode HTML entities (e.g. &#8217; → ', &#8211; → –, &amp; → &)
        title = html.unescape(entry.title)

        # Fix possible UTF-8 mojibake
        title = _fix_mojibake(title)

        if src.get("filter") and not any(k in title.lower() for k in KEYWORDS):
            continue

        raw_titles.append(title)
        raw_entries.append({"link": entry.link, "date": ts})

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


def _deduplicate(articles: list[dict]) -> list[dict]:
    """
    Deduplicate by URL (primary) and by normalised title (catches syndicated
    stories and amp/tracking-param variants). Keeps the earliest-dated entry
    per group so the original publish time is preserved.
    """
    by_url: dict[str, dict] = {}
    for a in articles:
        url = a["link"]
        if url not in by_url or a["date"] < by_url[url]["date"]:
            by_url[url] = a

    def _norm(title: str) -> str:
        t = title.lower()
        t = re.sub(r"[^\w\s]", "", t)
        return " ".join(t.split())[:60]

    by_title: dict[str, dict] = {}
    for a in by_url.values():
        key = _norm(a["title"])
        if key not in by_title or a["date"] < by_title[key]["date"]:
            by_title[key] = a

    return list(by_title.values())


def _compute_hot_scores(articles: list[dict]) -> None:
    """
    Compute a hotScore for each article and write it in-place.
    Also assigns groupId and groupMembers for stories covered by multiple sources.

    Logic:
    - Extract which KEYWORDS appear in each article's title (normalised, lowercase).
    - Group articles that share >= 2 keyword matches AND were published within 24 h
      of each other -- these are considered the same story covered by multiple sources.
    - For each group, score = number_of_sources / max(hours_since_earliest, 0.5).
    - Every article in a group receives that group's score.
    - Articles that belong to no group get hotScore 0.0.
    - The earliest article in each group is the lead; it receives a groupMembers list
      containing all other articles in the group. Non-lead group members are marked
      groupMember: true so the frontend can hide them from the main feed.
    """
    now_ms = int(time.time() * 1000)
    _24h_ms = 24 * 60 * 60 * 1000

    # Build keyword set per article (multi-word keywords checked as substrings)
    kw_lower = [k.lower() for k in KEYWORDS]

    def _title_keywords(title: str) -> frozenset:
        t = title.lower()
        return frozenset(k for k in kw_lower if k in t)

    kw_sets = [_title_keywords(a["title"]) for a in articles]

    # Initialise all scores to 0.0, no group info
    for a in articles:
        a["hotScore"]     = 0.0
        a["groupId"]      = None
        a["groupMember"]  = False
        a["groupMembers"] = []

    n = len(articles)
    assigned = set()  # indices already placed in a group

    for i in range(n):
        if i in assigned or not kw_sets[i]:
            continue
        group_indices = [i]
        for j in range(n):
            if j == i or j in assigned:
                continue
            if articles[j]["domain"] == articles[i]["domain"]:
                continue
            if abs(articles[i]["date"] - articles[j]["date"]) > _24h_ms:
                continue
            shared = kw_sets[i] & kw_sets[j]
            if len(shared) >= 2:
                group_indices.append(j)

        if len(group_indices) < 2:
            continue

        # Mark all indices in this group as assigned
        for k in group_indices:
            assigned.add(k)

        earliest_ms = min(articles[k]["date"] for k in group_indices)
        hours_old   = max((now_ms - earliest_ms) / 3_600_000, 0.5)
        score       = round(len(group_indices) / hours_old, 2)

        # Lead = earliest article in the group
        lead_idx = min(group_indices, key=lambda k: articles[k]["date"])
        group_id = articles[lead_idx]["link"]  # unique stable identifier

        for k in group_indices:
            articles[k]["hotScore"] = score
            articles[k]["groupId"]  = group_id

        # Build groupMembers list on the lead (all others in the group)
        articles[lead_idx]["groupMembers"] = [
            {
                "title":      articles[k]["title"],
                "link":       articles[k]["link"],
                "sourceName": articles[k]["sourceName"],
                "domain":     articles[k]["domain"],
                "date":       articles[k]["date"],
            }
            for k in group_indices if k != lead_idx
        ]

        # Mark non-lead members so the frontend can suppress them
        for k in group_indices:
            if k != lead_idx:
                articles[k]["groupMember"] = True


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

    unique = _deduplicate(all_articles)
    _compute_hot_scores(unique)
    sorted_data = sorted(unique, key=lambda x: x["date"], reverse=True)

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
