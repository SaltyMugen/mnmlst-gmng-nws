import feedparser
import json
import time
from datetime import datetime

# Your exact sources from gaming.html
SOURCES = [
    {"name": "IGN News", "rss": "https://www.ign.com/rss/v2/articles/news", "domain": "ign.com"},
    {"name": "IGN Reviews", "rss": "https://www.ign.com/rss/v2/articles/reviews", "domain": "ign.com"},
    {"name": "GameSpot", "rss": "https://www.gamespot.com/feeds/news/", "domain": "gamespot.com"},
    {"name": "PC Gamer", "rss": "https://www.pcgamer.com/rss/", "domain": "pcgamer.com"},
    {"name": "Eurogamer", "rss": "https://www.eurogamer.net/feed", "domain": "eurogamer.net"},
    {"name": "Kotaku", "rss": "https://kotaku.com/rss", "domain": "kotaku.com"},
    {"name": "Polygon", "rss": "https://www.polygon.com/rss/gaming/index.xml", "domain": "polygon.com"},
    {"name": "VGC", "rss": "https://www.videogameschronicle.com/feed/", "domain": "videogameschronicle.com"},
    {"name": "Rock Paper Shotgun", "rss": "https://www.rockpapershotgun.com/feed", "domain": "rockpapershotgun.com"},
    {"name": "Nintendo Life", "rss": "https://www.nintendolife.com/feeds/latest", "domain": "nintendolife.com"},
    {"name": "Push Square", "rss": "https://www.pushsquare.com/feeds/latest", "domain": "pushsquare.com"},
    {"name": "Pure Xbox", "rss": "https://www.purexbox.com/feeds/latest", "domain": "purexbox.com"},
    {"name": "VG247", "rss": "https://www.vg247.com/feed", "domain": "vg247.com"},
    {"name": "Destructoid", "rss": "https://www.destructoid.com/feed/", "domain": "destructoid.com"},
    {"name": "PCGamesN", "rss": "https://www.pcgamesn.com/mainrss.xml", "domain": "pcgamesn.com"},
    {"name": "TheGamer", "rss": "https://www.thegamer.com/feed/", "domain": "thegamer.com"},
    {"name": "Gematsu", "rss": "https://www.gematsu.com/feed", "domain": "gematsu.com"},
    {"name": "RPG Site", "rss": "https://www.rpgsite.net/rss", "domain": "rpgsite.net"},
    {"name": "Game Developer", "rss": "https://www.gamedeveloper.com/rss.xml", "domain": "gamedeveloper.com"},
    {"name": "PlayStation Blog", "rss": "https://blog.playstation.com/feed/", "domain": "blog.playstation.com"},
    {"name": "The Verge", "rss": "https://www.theverge.com/games/rss/index.xml", "domain": "theverge.com", "filter": True},
    {"name": "GamesIndustry.biz", "rss": "https://www.gamesindustry.biz/feed", "domain": "gamesindustry.biz"},
    {"name": "Handheld Players", "rss": "https://handheldplayers.com/feed/", "domain": "handheldplayers.com"},
    {"name": "Reddit Leaks", "rss": "https://www.reddit.com/r/GamingLeaksAndRumours/new/.rss?sort=new", "domain": "reddit.com/r/GamingLeaksAndRumours", "isReddit": True}
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
