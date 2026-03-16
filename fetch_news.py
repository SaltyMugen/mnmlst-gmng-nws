import feedparser
import json
import time
import requests
from datetime import datetime
from deep_translator import GoogleTranslator

# --- CONFIGURATION ---
DATA_FILE = "data_gaming.json"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}


translator = GoogleTranslator(source='ja', target='en')

SOURCES = [
    # --- BIG OUTLETS ---
    {"name": "IGN", "rss": "https://feeds.feedburner.com/ign/all", "domain": "ign.com"},
    {"name": "GameSpot", "rss": "https://www.gamespot.com/feeds/news/", "domain": "gamespot.com"},
    {"name": "PC Gamer", "rss": "https://www.pcgamer.com/rss/", "domain": "pcgamer.com"},
    {"name": "Eurogamer", "rss": "https://www.eurogamer.net/feed", "domain": "eurogamer.net"},
    {"name": "Kotaku", "rss": "https://kotaku.com/rss", "domain": "kotaku.com"},
    {"name": "Polygon", "rss": "https://www.polygon.com/rss/gaming/index.xml", "domain": "polygon.com"},
    {"name": "VGC", "rss": "https://www.videogameschronicle.com/feed/", "domain": "videogameschronicle.com"},
    {"name": "Rock Paper Shotgun", "rss": "https://www.rockpapershotgun.com/feed", "domain": "rockpapershotgun.com"},
    {"name": "VG247", "rss": "https://www.vg247.com/feed", "domain": "vg247.com"},
    {"name": "Destructoid", "rss": "https://www.destructoid.com/feed/", "domain": "destructoid.com"},
    {"name": "TheGamer", "rss": "https://www.thegamer.com/feed/", "domain": "thegamer.com"},
    {"name": "Gematsu", "rss": "https://www.gematsu.com/feed", "domain": "gematsu.com"},
    {"name": "The Verge", "rss": "https://www.theverge.com/games/rss/index.xml", "domain": "theverge.com", "filter": True},

    # --- OFFICIAL PLATFORMS ---
    {"name": "PlayStation Blog", "rss": "https://blog.playstation.com/feed/", "domain": "blog.playstation.com"},
    {"name": "Xbox Wire", "rss": "https://news.xbox.com/en-us/feed/", "domain": "news.xbox.com"},
    {"name": "Nintendo News", "rss": "https://www.nintendo.com/us/whatsnew/rss/", "domain": "nintendo.com"},

    # --- NEWS MACHINES ---
    {"name": "Game Rant", "rss": "https://gamerant.com/feed/", "domain": "gamerant.com"},
    {"name": "Dexerto", "rss": "https://www.dexerto.com/gaming/feed/", "domain": "dexerto.com"},
    {"name": "GamesRadar+", "rss": "https://www.gamesradar.com/news/rss/", "domain": "gamesradar.com"},
    {"name": "ComicBook Gaming", "rss": "https://comicbook.com/gaming/rss", "domain": "comicbook.com"},

    # --- SPECIALISTS & LEAKS ---
    {"name": "Insider Gaming", "rss": "https://insider-gaming.com/feed/", "domain": "insider-gaming.com"},
    {"name": "DualShockers", "rss": "https://www.dualshockers.com/feed/", "domain": "dualshockers.com"},
    {"name": "Siliconera", "rss": "https://www.siliconera.com/feed/", "domain": "siliconera.com"},
    {"name": "RPG Site", "rss": "https://www.rpgsite.net/rss", "domain": "rpgsite.net"},
    {"name": "Reddit Leaks", "rss": "https://www.reddit.com/r/GamingLeaksAndRumours/new/.rss?sort=new", "domain": "reddit.com", "isReddit": True},

    # --- HARDWARE ---
    {"name": "GamingOnLinux", "rss": "https://www.gamingonlinux.com/article_rss.php", "domain": "gamingonlinux.com"},
    {"name": "VideoCardz", "rss": "https://videocardz.com/feed", "domain": "videocardz.com"},
    {"name": "Digital Foundry", "rss": "https://www.eurogamer.net/feed/digitalfoundry", "domain": "eurogamer.net"},
    {"name": "TouchArcade", "rss": "https://toucharcade.com/feed/", "domain": "toucharcade.com"},

    # --- JP SOURCES (with Translation) ---
    {"name": "Automaton Media", "rss": "https://automaton-media.com/en/feed/", "domain": "automaton-media.com"},
    {"name": "Famitsu", "rss": "https://www.famitsu.com/rss/famitsu.rdf", "domain": "famitsu.com", "translate": True},
    {"name": "4Gamer.net", "rss": "https://www.4gamer.net/rss/index.xml", "domain": "4gamer.net", "translate": True},
    {"name": "Dengeki Online", "rss": "https://dengekionline.com/index.xml", "domain": "dengekionline.com", "translate": True},
    {"name": "Nikkei Asia", "rss": "https://info.asia.nikkei.com/rss", "domain": "asia.nikkei.com"},
    {"name": "GameBusiness.jp", "rss": "https://www.gamebusiness.jp/rss20/index.rdf", "domain": "gamebusiness.jp", "translate": True},
    {"name": "GameBiz", "rss": "https://gamebiz.jp/news/feed/rss", "domain": "gamebiz.jp", "translate": True},
    {"name": "Denfaminicogamer", "rss": "https://news.denfaminicogamer.jp/feed", "domain": "news.denfaminicogamer.jp", "translate": True},
    {"name": "Game Spark", "rss": "https://www.gamespark.jp/rss20/index.rdf", "domain": "gamespark.jp", "translate": True},
    {"name": "Inside Games", "rss": "https://www.inside-games.jp/rss20/index.rdf", "domain": "inside-games.jp", "translate": True},
    {"name": "Gamer.ne.jp", "rss": "https://www.gamer.ne.jp/rss/", "domain": "gamer.ne.jp", "translate": True},
    {"name": "IGN Japan", "rss": "https://jp.ign.com/feed.xml", "domain": "jp.ign.com", "translate": True},
    {"name": "Automaton Media JP", "rss": "https://automaton-media.com/feed/", "domain": "automaton-media.com", "translate": True}
]

KEYWORDS = ["game", "gaming", "nintendo", "xbox", "playstation", "gpu", "steam", "deck", "ps5", "sony", "ubisoft", "activision", "blizzard", "leak", "console", "switch"]

def fetch_all():
    all_articles = []
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - (48 * 60 * 60 * 1000)

    for src in SOURCES:
        try:
           
            resp = requests.get(src['rss'], headers=HEADERS, timeout=15)
            feed = feedparser.parse(resp.content)
            
            for entry in feed.entries[:15]:
                ts = int(time.mktime(entry.published_parsed) * 1000) if hasattr(entry, 'published_parsed') else now_ms
                if ts < cutoff: continue

                title = entry.title
                if src.get('filter') and not any(k in title.lower() for k in KEYWORDS):
                    continue

                # Translation for JP sources
                if src.get('translate') and any(ord(c) > 127 for c in title):
                    try:
                        title = translator.translate(title)
                    except:
                        pass 

                all_articles.append({
                    "title": title,
                    "link": entry.link,
                    "date": ts,
                    "domain": src['domain'],
                    "sourceName": src['name'],
                    "isReddit": src.get('isReddit', False),
                    "isTranslated": src.get('translate', False)
                })
        except Exception as e:
            print(f"Error fetching {src['name']}: {e}")

    unique = {a['link']: a for a in all_articles}.values()
    sorted_data = sorted(list(unique), key=lambda x: x['date'], reverse=True)

    with open(DATA_FILE, "w", encoding='utf-8') as f:
        json.dump(sorted_data, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    fetch_all()
