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
from datetime import timezone, timedelta

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

# --- REGEX ---
_CJK_RE = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")

# --- SOURCES ---
SOURCES = [
    {"name": "IGN Games", "rss": "https://www.ign.com/rss/v2/articles/feed?categorySlug=games", "domain": "ign.com"},
    {"name": "GameSpot", "rss": "https://www.gamespot.com/feeds/mashup/", "domain": "gamespot.com"},
    {"name": "PC Gamer", "rss": "https://www.pcgamer.com/rss/", "domain": "pcgamer.com"},
    {"name": "Eurogamer", "rss": "https://www.eurogamer.net/feed", "domain": "eurogamer.net"},
    {"name": "Kotaku", "rss": "https://kotaku.com/rss", "domain": "kotaku.com"},
    {"name": "Polygon", "rss": "https://www.polygon.com/rss/gaming/index.xml", "domain": "polygon.com"},
    {"name": "VGC", "rss": "https://www.videogameschronicle.com/feed/", "domain": "videogameschronicle.com"},
    {"name": "Rock Paper Shotgun", "rss": "https://feeds.feedburner.com/RockPaperShotgun", "domain": "rockpapershotgun.com"},
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
    {"name": "GamesRadar+", "rss": "https://www.gamesradar.com/news/rss/", "domain": "gamesradar.com"},
    {"name": "ComicBook Gaming", "rss": "https://comicbook.com/gaming/rss", "domain": "comicbook.com"},

    {"name": "Insider Gaming", "rss": "https://insider-gaming.com/feed/", "domain": "insider-gaming.com"},
    {"name": "DualShockers", "rss": "https://www.dualshockers.com/feed/", "domain": "dualshockers.com"},
    {"name": "Siliconera", "rss": "https://www.siliconera.com/feed/", "domain": "siliconera.com"},
    {"name": "RPG Site", "rss": "https://www.rpgsite.net/rss", "domain": "rpgsite.net"},
    {"name": "Reddit Leaks", "rss": "https://www.reddit.com/r/GamingLeaksAndRumours/new/.rss?sort=new", "domain": "reddit.com", "isReddit": True},

    {"name": "GamesIndustry.biz", "rss": "https://www.gamesindustry.biz/rss/gamesindustry_news_feed.rss", "domain": "gamesindustry.biz"},

    {"name": "Nintendo Life", "rss": "https://www.nintendolife.com/feeds/latest", "domain": "nintendolife.com"},
    {"name": "Push Square", "rss": "https://www.pushsquare.com/feeds/latest", "domain": "pushsquare.com"},
    {"name": "Pure Xbox", "rss": "https://www.purexbox.com/feeds/latest", "domain": "purexbox.com"},

    {"name": "Steam News", "rss": "https://store.steampowered.com/feeds/news.xml", "domain": "store.steampowered.com"},
    {"name": "IndieDB", "rss": "https://www.indiedb.com/rss/news", "domain": "indiedb.com"},

    {"name": "4Gamer.net", "rss": "https://www.4gamer.net/rss/index.xml", "domain": "4gamer.net", "translate": True, "tz_jst": True},
    {"name": "Famitsu", "rss": "https://www.famitsu.com/rss/fcom_all.rdf", "domain": "famitsu.com", "translate": True, "tz_jst": True},
    {"name": "Dengeki Online", "rss": "https://dengekionline.com/index.xml", "domain": "dengekionline.com", "translate": True, "tz_jst": True},
]

# --- HELPERS ---

def _fix_mojibake(text: str) -> str:
    """
    Fix UTF-8 mojibake like 'ã‚²ãƒ¼ãƒ ' → 'ゲーム'
    """
    try:
        return text.encode("latin1").decode("utf-8")
    except Exception:
        return text


def _is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _parse_timestamp(entry, assume_jst, now_ms):

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
                return int(dt.timestamp() * 1000)

            elif assume_jst:
                dt_jst = dt.replace(tzinfo=JST)
                return int(dt_jst.timestamp() * 1000)

            else:
                return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)

        except Exception:
            pass

    if hasattr(entry, "published_parsed") and entry.published_parsed:
        ts = int(calendar.timegm(entry.published_parsed) * 1000)

        if assume_jst:
            ts -= 9 * 60 * 60 * 1000

        return ts

    return now_ms


def _translate_titles(titles, source_name):

    result = list(titles)

    for i, title in enumerate(titles):

        if not _is_cjk(title):
            continue

        try:
            result[i] = translator.translate(title)
            time.sleep(0.5)

        except Exception as exc:
            log.warning("[%s] Translation failed: %s", source_name, exc)

    return result


# --- FETCH SOURCE ---

def _fetch_source(src, cutoff_ms):

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
            log.warning("[%s] Attempt %d failed: %s", name, attempt + 1, exc)

            time.sleep(wait)

    else:
        return []

    # IMPORTANT: parse raw bytes
    feed = feedparser.parse(resp.content)

    raw_titles = []
    raw_entries = []

    for entry in feed.entries[:15]:

        ts = _parse_timestamp(entry, assume_jst, now_ms)

        if ts < cutoff_ms:
            continue

        title = html.unescape(entry.title)

        # FIX encoding issues
        title = _fix_mojibake(title)

        raw_titles.append(title)

        raw_entries.append(
            {
                "link": entry.link,
                "date": ts,
            }
        )

    if src.get("translate"):
        raw_titles = _translate_titles(raw_titles, name)

    for title, meta in zip(raw_titles, raw_entries):

        articles.append(
            {
                "title": title,
                "link": meta["link"],
                "date": meta["date"],
                "domain": src["domain"],
                "sourceName": name,
                "isTranslated": src.get("translate", False),
            }
        )

    return articles


# --- DEDUPLICATION ---

def _deduplicate(articles):

    by_url = {}

    for a in articles:
        url = a["link"]

        if url not in by_url or a["date"] < by_url[url]["date"]:
            by_url[url] = a

    def _norm(title):
        t = title.lower()
        t = re.sub(r"[^\w\s]", "", t)
        return " ".join(t.split())[:60]

    by_title = {}

    for a in by_url.values():

        key = _norm(a["title"])

        if key not in by_title or a["date"] < by_title[key]["date"]:
            by_title[key] = a

    return list(by_title.values())


# --- MAIN FETCH ---

def fetch_all():

    now_ms = int(time.time() * 1000)

    cutoff_ms = now_ms - (48 * 60 * 60 * 1000)

    all_articles = []

    with ThreadPoolExecutor(max_workers=10) as pool:

        futures = {
            pool.submit(_fetch_source, src, cutoff_ms): src["name"]
            for src in SOURCES
        }

        for future in as_completed(futures):

            source = futures[future]

            try:

                results = future.result()

                all_articles.extend(results)

                log.info("[%s] %d articles", source, len(results))

            except Exception as exc:

                log.error("[%s] Error: %s", source, exc)

    unique = _deduplicate(all_articles)

    sorted_data = sorted(unique, key=lambda x: x["date"], reverse=True)

    dir_name = os.path.dirname(os.path.abspath(DATA_FILE)) or "."

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=dir_name,
        delete=False,
        suffix=".tmp",
    ) as tmp:

        json.dump(sorted_data, tmp, indent=2, ensure_ascii=False)

        tmp_path = tmp.name

    os.replace(tmp_path, DATA_FILE)

    log.info("Saved %d articles.", len(sorted_data))


if __name__ == "__main__":
    fetch_all()
