import calendar
import feedparser
import html
import json
import logging
import os
import re
import tempfile
import time
import threading
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
translate_lock = threading.Lock()

BLOCKED_TITLE_PATTERNS = [
    "top 3", "top 5", "top 6", "top 7", "top 8", "top 9", "top 10",
    "top 15", "top 20", "top 25", "top 50", "top 100", "best games",
    "games you need to play", "games to play", "ranked",
    "every game ranked", "how to ", "april Fools", "% off",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

translator = GoogleTranslator(source="ja", target="en")
JST = timezone(timedelta(hours=9))

# (SOURCES and KEYWORDS lists remain identical to your original provided snippet)
SOURCES = [
    {"name": "IGN Games", "rss": "https://www.ign.com/rss/v2/articles/feed?categorySlug=games", "domain": "ign.com"},
    {"name": "GameSpot", "rss": "https://www.gamespot.com/feeds/mashup/", "domain": "gamespot.com"},
    {"name": "PC Gamer", "rss": "https://www.pcgamer.com/rss/", "domain": "pcgamer.com"},
    {"name": "Eurogamer", "rss": "https://www.eurogamer.net/feed", "domain": "eurogamer.net"},
    {"name": "Kotaku", "rss": "https://kotaku.com/rss", "domain": "kotaku.com"},
    {"name": "Polygon", "rss": "https://www.polygon.com/rss/gaming/index.xml", "domain": "polygon.com"},
    {"name": "VGC", "rss": "https://www.videogameschronicle.com/feed/", "domain": "videogameschronicle.com"},
    {"name": "Rock Paper Shotgun","rss": "https://feeds.feedburner.com/RockPaperShotgun", "domain": "rockpapershotgun.com"},
    {"name": "VG247", "rss": "https://www.vg247.com/feed", "domain": "vg247.com"},
    {"name": "Destructoid", "rss": "https://www.destructoid.com/feed/", "domain": "destructoid.com"},
    {"name": "TheGamer", "rss": "https://www.thegamer.com/feed/", "domain": "thegamer.com"},
    {"name": "Gematsu", "rss": "https://www.gematsu.com/feed", "domain": "gematsu.com"},
    {"name": "The Verge", "rss": "https://www.theverge.com/rss/index.xml", "domain": "theverge.com", "filter": True},
    {"name": "PlayStation Blog", "rss": "https://blog.playstation.com/feed/", "domain": "blog.playstation.com"},
    {"name": "Xbox Wire", "rss": "https://news.xbox.com/en-us/feed/", "domain": "news.xbox.com"},
    {"name": "Nintendo News", "rss": "https://www.nintendo.com/en-gb/news.xml", "domain": "nintendo.com"},
    {"name": "Game Rant", "rss": "https://gamerant.com/feed/", "domain": "gamerant.com"},
    {"name": "Dexerto", "rss": "https://www.dexerto.com/gaming/feed/", "domain": "dexerto.com"},
    {"name": "GamesRadar+", "rss": "https://www.gamesradar.com/all-platforms/news/rss/", "domain": "gamesradar.com"},
    {"name": "Insider Gaming", "rss": "https://insider-gaming.com/feed/", "domain": "insider-gaming.com"},
    {"name": "DualShockers", "rss": "https://www.dualshockers.com/feed/", "domain": "dualshockers.com"},
    {"name": "Siliconera", "rss": "https://www.siliconera.com/feed/", "domain": "siliconera.com"},
    {"name": "RPG Site", "rss": "https://www.rpgsite.net/rss", "domain": "rpgsite.net"},
    {"name": "Reddit Leaks", "rss": "https://www.reddit.com/r/GamingLeaksAndRumours/new/.rss?sort=new", "domain": "reddit.com", "isReddit": True},
    {"name": "GamesBeat", "rss": "https://gamesbeat.com/category/gameplay/feed/", "domain": "gamesbeat.com"},
    {"name": "GamesIndustry.biz", "rss": "https://www.gamesindustry.biz/rss/gamesindustry_news_feed.rss", "domain": "gamesindustry.biz"},
    {"name": "Nintendo Life", "rss": "https://www.nintendolife.com/feeds/latest", "domain": "nintendolife.com"},
    {"name": "Push Square", "rss": "https://www.pushsquare.com/feeds/latest", "domain": "pushsquare.com"},
    {"name": "Pure Xbox", "rss": "https://www.purexbox.com/feeds/latest", "domain": "purexbox.com"},
    {"name": "Steam News", "rss": "https://store.steampowered.com/feeds/news/collection/steam", "domain": "store.steampowered.com"},
    {"name": "IndieDB", "rss": "https://rss.indiedb.com/articles/feed/rss.xml", "domain": "indiedb.com"},
    {"name": "Game Informer", "rss": "https://www.gameinformer.com/rss.xml", "domain": "gameinformer.com"},
    {"name": "Noisy Pixel", "rss": "https://noisypixel.net/feed/", "domain": "noisypixel.net"},
    {"name": "TweakTown", "rss": "https://www.tweaktown.com/rss/index.xml", "domain": "tweaktown.com"},
    {"name": "GameDiscoverCo", "rss": "https://newsletter.gamediscover.co/feed", "domain": "newsletter.gamediscover.co"},
    {"name": "Game File", "rss": "https://www.gamefile.news/feed", "domain": "gamefile.news"},
    {"name": "Hit Points", "rss": "https://newsletter.hitpoints.co/feed", "domain": "newsletter.hitpoints.co"},
    {"name": "Knowledge", "rss": "https://www.knowledge.me/feed", "domain": "knowledge.me"},
    {"name": "4Gamer.net", "rss": "https://www.4gamer.net/rss/index.xml", "domain": "4gamer.net", "translate": True, "tz_jst": True},
    {"name": "Automaton Media", "rss": "https://automaton-media.com/en/feed/", "domain": "automaton-media.com"},
    {"name": "Famitsu", "rss": "https://famitsu.com", "domain": "famitsu.com", "translate": True, "tz_jst": True},
    {"name": "Dengeki Online", "rss": "https://dengekionline.com/index.xml", "domain": "dengekionline.com", "translate": True, "tz_jst": True},
    {"name": "GameBiz", "rss": "https://gamebiz.jp/feed.rss", "domain": "gamebiz.jp", "translate": True, "tz_jst": True},
    {"name": "Denfaminicogamer", "rss": "https://news.denfaminicogamer.jp/feed", "domain": "news.denfaminicogamer.jp", "translate": True, "tz_jst": True},
    {"name": "Game Spark", "rss": "https://www.gamespark.jp/rss20/index.rdf", "domain": "gamespark.jp", "translate": True, "tz_jst": True},
    {"name": "Gamer.ne.jp", "rss": "https://www.gamer.ne.jp/feed/news.rdf", "domain": "gamer.ne.jp", "translate": True, "tz_jst": True},
    {"name": "IGN Japan", "rss": "https://jp.ign.com/feed.xml", "domain": "jp.ign.com", "translate": True, "tz_jst": True},
    {"name": "Automaton Media JP","rss": "https://automaton-media.com/feed/", "domain": "automaton-media.com", "translate": True, "tz_jst": True},
]

KEYWORDS = [
    "game", "gaming", "videogame", "video game", "gameplay", "gamer", "gaming industry",
    "game developer", "game studio", "game publisher", "indie game", "game release",
    "game launch", "game update", "patch notes", "dlc", "expansion", "season pass",
    "live service", "battle pass", "microtransactions", "early access", "beta", "alpha build",
    "mod", "modding", "nintendo", "switch", "switch oled", "switch 2",
    "playstation", "ps5", "ps4", "psvr", "psvr2", "sony interactive entertainment",
    "xbox", "xbox series x", "xbox series s", "xbox game pass", "microsoft gaming",
    "steam", "steam deck", "valve", "console", "handheld console", "portable console", "pc gaming",
    "gpu", "graphics card", "gaming pc", "gaming laptop", "amd", "nvidia", "rtx", "radeon", "dlss", 
    "fsr", "ray tracing", "frame generation", "activision", "blizzard", "activision blizzard",
    "electronic arts", "ea", "ubisoft", "take two", "take-two", "take two interactive",
    "rockstar", "rockstar games", "square enix", "capcom", "bandai namco", "sega",
    "konami", "cd projekt", "cd projekt red", "bethesda", "zenimax", "epic games",
    "riot games", "valve corporation", "paradox interactive", "embracer group",
    "fromsoftware", "larian studios", "unreal engine", "unity engine", "cryengine", 
    "game engine", "game development", "dev kit", "sdk", "xbox game pass", 
    "playstation plus", "ps plus", "epic games store", "steam sale", "gog", 
    "gog galaxy", "nintendo eshop", "subscription gaming", "e3", "summer game fest", 
    "gamescom", "tokyo game show", "tgs", "game awards", "state of play", 
    "nintendo direct", "xbox showcase", "playstation showcase", "blizzcon",
    "call of duty", "battlefield", "halo", "forza", "minecraft", "fortnite",
    "grand theft auto", "gta", "elder scrolls", "fallout", "final fantasy", 
    "dragon quest", "persona", "monster hunter", "zelda", "mario", "pokemon", 
    "metroid", "dark souls", "elden ring", "bloodborne", "cyberpunk", "witcher",
    "assassin's creed", "far cry", "rainbow six", "destiny", "overwatch", "diablo",
    "league of legends", "valorant", "dota", "counter strike", "mobile game", 
    "ios game", "android game", "gacha", "app store game", "play store game",
    "publisher", "studio acquisition", "game director", "creative director",
    "open world", "multiplayer", "single player", "co-op", "esports",
]

_CJK_RE = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")

def _is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))

def _fix_mojibake(text: str) -> str:
    if _is_cjk(text):
        return text
    try:
        return text.encode("latin1").decode("utf-8")
    except Exception:
        return text

def _normalise_url(url: str) -> str:
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))
    except Exception:
        return url

def _parse_timestamp(entry: feedparser.FeedParserDict, assume_jst: bool, now_ms: int) -> int:
    raw = (entry.get("published") or entry.get("updated") or entry.get("dc_date") or entry.get("date") or "")
    if raw:
        try:
            dt = dateutil_parser.parse(raw)
            if dt.tzinfo is not None:
                return int(dt.timestamp() * 1000)
            elif assume_jst:
                dt_jst = dt.replace(tzinfo=JST)
                return int(dt_jst.timestamp() * 1000)
            else:
                return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except Exception:
            pass
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        ts_utc = int(calendar.timegm(entry.published_parsed) * 1000)
        if assume_jst:
            ts_utc -= 9 * 60 * 60 * 1000
        return ts_utc
    return now_ms

def _translate_titles(titles: list[str], source_name: str) -> list[str]:
    result = list(titles)
    for i, title in enumerate(titles):
        if not _is_cjk(title):
            continue
        try:
            # Global lock to prevent multi-threaded rate limiting
            with translate_lock:
                result[i] = translator.translate(title)
                time.sleep(0.5)
        except Exception as exc:
            log.warning("[%s] Translation failed for %r: %s", source_name, title, exc)
    return result

def _fetch_source(src: dict, cutoff_ms: int) -> list[dict]:
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
            if attempt < 2: time.sleep(wait)
    else:
        return []

    feed = feedparser.parse(resp.content)
    raw_titles, raw_entries = [], []

    for entry in feed.entries:
        ts = _parse_timestamp(entry, assume_jst, now_ms)
        if ts < cutoff_ms:
            continue

        title = html.unescape(entry.title)
        title = _fix_mojibake(title)

        if src.get("filter") and not any(k in title.lower() for k in KEYWORDS):
            continue
        if any(p in title.lower() for p in BLOCKED_TITLE_PATTERNS):
            continue

        raw_titles.append(title)
        raw_entries.append({"link": _normalise_url(entry.link), "date": ts})

    if src.get("translate"):
        raw_titles = _translate_titles(raw_titles, name)

    for title, entry_meta in zip(raw_titles, raw_entries):
        articles.append({
            "title": title,
            "link": entry_meta["link"],
            "date": entry_meta["date"],
            "domain": src["domain"],
            "sourceName": name,
            "isTranslated": src.get("translate", False),
        })
    return articles

def _url_dedupe(articles: list[dict]) -> list[dict]:
    by_url: dict[str, dict] = {}
    for a in articles:
        url = a["link"]
        if url not in by_url or a["date"] < by_url[url]["date"]:
            by_url[url] = a
    return list(by_url.values())

def _norm_title(title: str) -> str:
    """Enhanced normalization: Strips journalistic media noise and stop words."""
    t = title.lower()
    # 1. Media Noise
    media_noise = r"\b(review|preview|rumor|leak|news|video|watch|official|trailer|out now|gameplay|walkthrough|guide|impressions)\b"
    t = re.sub(media_noise, "", t)
    # 2. Stop words
    stop_words = r"\b(a|an|the|is|are|was|were|and|or|but|in|on|at|with|for|to|from|of)\b"
    t = re.sub(stop_words, "", t)
    # 3. Clean up
    t = re.sub(r"[^\w\s]", "", t)
    return " ".join(t.split())

def _extract_topic_key(title: str) -> str | None:
    """Strict subject extraction: strips platform noise to find the actual game/hardware name."""
    # Strip platform tails to prevent grouping different games on same platform
    clean_title = re.sub(r'\b(on|for|coming to|available for)\s+(PS5|Xbox|Switch|PC|PlayStation|Series X|Series S|Switch 2)\b.*', '', title, flags=re.I)
    # Match sequences of Title Cased words
    pattern = re.compile(r'\b([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*)\b')
    matches = pattern.findall(clean_title)
    return matches[0].strip() if matches else None

def _group_articles(articles: list[dict]) -> list[dict]:
    SIMILARITY_THRESHOLD = 80  # Increased for safety
    TIME_WINDOW_MS = 24 * 60 * 60 * 1000 # Only compare articles within 24h
    
    # Sort articles by date to enable sliding window optimization
    articles.sort(key=lambda x: x["date"])
    
    norms = [_norm_title(a["title"]) for a in articles]
    topics = [_extract_topic_key(a["title"]) for a in articles]
    
    used: set[int] = set()
    groups: list[list[dict]] = []

    for i in range(len(articles)):
        if i in used: continue
        
        group = [articles[i]]
        used.add(i)
        subj_i = topics[i]

        # Optimization: Only look forward in time until window expires
        for j in range(i + 1, len(articles)):
            if j in used: continue
            if (articles[j]["date"] - articles[i]["date"]) > TIME_WINDOW_MS:
                break
            
            # Topic Anchor Check: If both have distinct subjects, they MUST match
            subj_j = topics[j]
            if subj_i and subj_j and subj_i.lower() != subj_j.lower():
                continue

            score = fuzz.ratio(norms[i], norms[j])
            if score >= SIMILARITY_THRESHOLD:
                group.append(articles[j])
                used.add(j)
        
        groups.append(group)

    result: list[dict] = []
    for group in groups:
        lead = max(group, key=lambda x: x["date"])
        members = [m for m in group if m is not lead]
        lead["hotScore"] = len({item["domain"] for item in group})
        if members: lead["groupMembers"] = members
        result.append(lead)

    return result

def fetch_all() -> None:
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - (48 * 60 * 60 * 1000)
    all_articles = []

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_source, src, cutoff_ms): src["name"] for src in SOURCES}
        for future in as_completed(futures):
            try:
                results = future.result()
                all_articles.extend(results)
            except Exception as exc:
                log.error("Error: %s", exc)

    url_deduped = _url_dedupe(all_articles)
    grouped = _group_articles(url_deduped)
    sorted_data = sorted(grouped, key=lambda x: x["date"], reverse=True)

    dir_name = os.path.dirname(os.path.abspath(DATA_FILE)) or "."
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=dir_name, delete=False, suffix=".tmp") as tmp:
        json.dump(sorted_data, tmp, indent=2, ensure_ascii=False)
        tmp_path = tmp.name

    os.replace(tmp_path, DATA_FILE)
    log.info("Saved %d articles to %s.", len(sorted_data), DATA_FILE)

if __name__ == "__main__":
    fetch_all()
