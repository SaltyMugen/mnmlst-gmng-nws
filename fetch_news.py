import feedparser
import json
import time
from datetime import datetime

# Your exact sources from gaming.html
SOURCES = [
    # --- YOUR CURRENT BIG OUTLETS ---
    {"name": "IGN News", "rss": "https://www.ign.com/rss/v2/articles/news", "domain": "ign.com"},
    {"name": "IGN Reviews", "rss": "https://www.ign.com/rss/v2/articles/reviews", "domain": "ign.com"},
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

    # --- OFFICIAL PLATFORM NEWS (Primary Sources) ---
    {"name": "PlayStation Blog", "rss": "https://blog.playstation.com/feed/", "domain": "blog.playstation.com"},
    {"name": "Xbox Wire", "rss": "https://news.xbox.com/en-us/feed/", "domain": "news.xbox.com"},
    {"name": "Nintendo News", "rss": "https://www.nintendo.com/us/whatsnew/rss/", "domain": "nintendo.com"},

    # --- THE "NEWS MACHINES" (High Volume) ---
    {"name": "Game Rant", "rss": "https://gamerant.com/feed/", "domain": "gamerant.com"},
    {"name": "Dexerto", "rss": "https://www.dexerto.com/gaming/feed/", "domain": "dexerto.com"},
    {"name": "GamesRadar+", "rss": "https://www.gamesradar.com/news/rss/", "domain": "gamesradar.com"},
    {"name": "ComicBook Gaming", "rss": "https://comicbook.com/gaming/rss", "domain": "comicbook.com"},

    # --- INSIDER LEAKS & SPECIALISTS ---
    {"name": "Insider Gaming", "rss": "https://insider-gaming.com/feed/", "domain": "insider-gaming.com"},
    {"name": "DualShockers", "rss": "https://www.dualshockers.com/feed/", "domain": "dualshockers.com"},
    {"name": "Siliconera", "rss": "https://www.siliconera.com/feed/", "domain": "siliconera.com"},
    {"name": "RPG Site", "rss": "https://www.rpgsite.net/rss", "domain": "rpgsite.net"},

    # --- HARDWARE & PORTABLES (Steam Deck/GPU) ---
    {"name": "GamingOnLinux", "rss": "https://www.gamingonlinux.com/article_rss.php", "domain": "gamingonlinux.com"},
    {"name": "VideoCardz", "rss": "https://videocardz.com/feed", "domain": "videocardz.com"},
    {"name": "Digital Foundry", "rss": "https://www.eurogamer.net/feed/digitalfoundry", "domain": "eurogamer.net"},
    {"name": "TouchArcade", "rss": "https://toucharcade.com/feed/", "domain": "toucharcade.com"},

    # --- REDDIT ---
    {"name": "Reddit Leaks", "rss": "https://www.reddit.com/r/GamingLeaksAndRumours/new/.rss?sort=new", "domain": "reddit.com", "isReddit": True}
]

KEYWORDS = ["game", "gaming", "nintendo", "xbox", "playstation", "gpu", "steam", "deck", "ps5", "sony", "ubisoft", "activision", "blizzard", "leak", "console", "switch"]

def fetch_all():
    all_articles = []
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - (48 * 60 * 60 * 1000) # 48 hours

    for src in SOURCES:
        try:
            feed = feedparser.parse(src['rss'])
            for entry in feed.entries[:15]:
                # Convert published time to timestamp ms
                ts = int(time.mktime(entry.published_parsed) * 1000) if hasattr(entry, 'published_parsed') else now_ms
                
                if ts < cutoff: continue

                title = entry.title
                if src.get('filter') and not any(k in title.lower() for k in KEYWORDS):
                    continue

                all_articles.append({
                    "title": title,
                    "link": entry.link,
                    "date": ts,
                    "domain": src['domain'],
                    "sourceName": src['name'],
                    "isReddit": src.get('isReddit', False)
                })
        except Exception as e:
            print(f"Error fetching {src['name']}: {e}")

    # Remove duplicates by link
    unique_articles = {a['link']: a for a in all_articles}.values()
    
    with open("data_gaming.json", "w") as f:
        json.dump(list(unique_articles), f, indent=2)

if __name__ == "__main__":
    fetch_all()
