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
from urllib.parse import urlparse, urlunparse

import requests
from dateutil import parser as dateutil_parser
from deep_translator import GoogleTranslator
from rapidfuzz import fuzz

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_FILE = "data_gaming.json"
TRENDING_THRESHOLD = 2.5

translator = GoogleTranslator(source="ja", target="en")
JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# --- HELPERS ---

def _keyword_overlap(a: str, b: str) -> int:
    sa = set(a.split())
    sb = set(b.split())
    return len(sa & sb)

def _norm_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^\w\s]", "", t)
    return " ".join(t.split())

def _extract_topic_key(title: str) -> str | None:
    pattern = re.compile(r'\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+)\b')
    matches = pattern.findall(title)
    return max(matches, key=len) if matches else None

def _normalise_url(url: str) -> str:
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))
    except Exception:
        return url

def _parse_timestamp(entry, assume_jst, now_ms):
    raw = entry.get("published") or entry.get("updated") or ""
    if raw:
        try:
            dt = dateutil_parser.parse(raw)
            if dt.tzinfo:
                return int(dt.timestamp() * 1000)
            if assume_jst:
                return int(dt.replace(tzinfo=JST).timestamp() * 1000)
            return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except Exception:
            pass

    if hasattr(entry, "published_parsed") and entry.published_parsed:
        ts = int(calendar.timegm(entry.published_parsed) * 1000)
        if assume_jst:
            ts -= 9 * 60 * 60 * 1000
        return ts

    return now_ms

# --- GROUPING (FIXED) ---

def _group_articles(articles: list[dict]) -> list[dict]:
    SIMILARITY_THRESHOLD = 60
    TOPIC_TIME_WINDOW_MS = 24 * 60 * 60 * 1000
    MIN_TOPIC_GROUP_SIZE = 3

    norms = [_norm_title(a["title"]) for a in articles]
    used = set()
    groups = []

    # Pass 1
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

            score = fuzz.token_set_ratio(norms[i], norms[j])

            if (
                score >= SIMILARITY_THRESHOLD
                or _keyword_overlap(norms[i], norms[j]) >= 3
            ):
                group.append(articles[j])
                used.add(j)

        groups.append(group)

    # Pass 2 (topic merge)
    singleton_indices = [i for i, g in enumerate(groups) if len(g) == 1]
    topic_map = {}

    for gi in singleton_indices:
        topic = _extract_topic_key(groups[gi][0]["title"])
        if not topic:
            continue

        topic_key = topic.lower()
        matched = False

        for existing in topic_map:
            if fuzz.token_set_ratio(topic_key, existing) > 75:
                topic_map[existing].append(gi)
                matched = True
                break

        if not matched:
            topic_map[topic_key] = [gi]

    merged = set()

    for gis in topic_map.values():
        if len(gis) < MIN_TOPIC_GROUP_SIZE:
            continue

        base = gis[0]
        for gi in gis[1:]:
            groups[base].extend(groups[gi])
            merged.add(gi)

    groups = [g for i, g in enumerate(groups) if i not in merged]

    # Final shaping
    result = []

    for group in groups:
        lead = min(group, key=lambda x: x["date"])
        members = [m for m in group if m is not lead]

        lead["hotScore"] = len({x["domain"] for x in group})
        if members:
            lead["groupMembers"] = members

        result.append(lead)

    return result

# --- FETCHING ---

def _fetch_source(src, cutoff_ms):
    now_ms = int(time.time() * 1000)
    articles = []

    try:
        resp = requests.get(src["rss"], headers=HEADERS, timeout=10)
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning("Failed %s: %s", src["name"], e)
        return []

    for entry in feed.entries:
        ts = _parse_timestamp(entry, False, now_ms)
        if ts < cutoff_ms:
            continue

        title = html.unescape(entry.title)

        articles.append({
            "title": title,
            "link": _normalise_url(entry.link),
            "date": ts,
            "domain": src["domain"],
            "sourceName": src["name"]
        })

    return articles

# --- MAIN ---

def fetch_all():
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - (24 * 60 * 60 * 1000)

    SOURCES = [
        {"name": "IGN", "rss": "https://www.ign.com/rss/v2/articles/feed?categorySlug=games", "domain": "ign.com"},
        {"name": "GameSpot", "rss": "https://www.gamespot.com/feeds/mashup/", "domain": "gamespot.com"},
    ]

    all_articles = []

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_fetch_source, s, cutoff_ms) for s in SOURCES]

        for f in as_completed(futures):
            all_articles.extend(f.result())

    grouped = _group_articles(all_articles)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(grouped, f, indent=2)

    log.info("Saved %d articles", len(grouped))


if __name__ == "__main__":
    fetch_all()
