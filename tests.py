"""Checks run by the GitHub Action before every deploy. If any fail, the live site is left unchanged.

Run locally with:  python tests.py
No network access: feeds, translation and icons are replaced with fakes.
"""

import json
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest import mock

import fetch_news as fn

ROOT = Path(__file__).resolve().parent
RULES = fn.Rules(json.loads((ROOT / "config.json").read_text(encoding="utf-8")))


def rfc(ms: int) -> str:
    return format_datetime(datetime.fromtimestamp(ms / 1000, timezone.utc))


class Resp:
    def __init__(self, status=200, content=b"", headers=None, payload=None):
        self.status_code, self.content, self.headers, self._payload = status, content, headers or {}, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            err = fn.requests.HTTPError(str(self.status_code))
            err.response = self
            raise err

    @property
    def text(self):
        return self.content.decode("utf-8", "ignore")

    def json(self):
        return self._payload


class Tags(unittest.TestCase):
    CASES = {
        "Sony Fighting PS5 Pro Scalpers in Japan": {"playstation"},
        "Here's What The Witcher 3 Remastered Looks Like On Xbox Series X|S": {"xbox"},
        "Should You Play Witcher 3 Remastered On The Switch 2?": {"nintendo"},
        "Tales Of Eternia Is Getting A Proper Physical Release On Switch": {"nintendo"},
        "Wolverine Now Boots On PC Thanks To PS5 Emulation": {"pc", "playstation"},
        "Pokemon Winds and Waves leak reveals map and first look at Grass Gym": {"nintendo", "rumour"},
        "I'm More Hyped About This LA Noire 2 Rumor Than GTA 6": {"rumour"},
        # things that must NOT be tagged
        "Pokémon Happy Meals Are Returning To McDonald's": set(),
        "Pokemon TCG Reveals 4 New Delta Reign Promo Cards": set(),
        "GTA 6's weather system finally confirmed after years of rumors": set(),
        "Studio runs out of steam after layoffs": set(),
        "PC Gamer's best keyboards": set(),
        "The team decided to switch engines": set(),
        "Memory leak fixed in latest patch": set(),
        "Nintendo switches up its release plans": {"nintendo"},
    }

    def test_title_tags(self):
        for title, expected in self.CASES.items():
            with self.subTest(title=title):
                self.assertEqual(RULES.title_tags(title), expected)

    def test_blocked(self):
        for title in ["Top 10 RPGs of 2026", "The 25 best games ever", "Save $20 on DualSense", "How to beat Malenia",
                      "Every Zelda game ranked", "All FC 27 icons"]:
            self.assertTrue(RULES.is_blocked(title), title)
        for title in ["Ranking up fast in Marvel Rivals", "Topaz DLC out now", "Nintendo shows off Switch 2"]:
            self.assertFalse(RULES.is_blocked(title), title)


class Grouping(unittest.TestCase):
    def arts(self, titles, gap_ms=60_000):
        now = fn.now_ms()
        return [{"title": t, "link": f"https://x.test/{i}", "date": now - i * gap_ms, "source": f"S{i}", "domain": f"s{i}.test"}
                for i, t in enumerate(titles)]

    def test_same_story_groups(self):
        groups = fn.group_articles(self.arts([
            "Minecraft Dungeons 2 Review",
            "Minecraft Dungeons 2 review: a diamond in the rift",
            "Minecraft Dungeons II Review - Built Better Than Before",
        ]), RULES)
        self.assertEqual(len(groups), 1)

    def test_different_stories_stay_apart(self):
        groups = fn.group_articles(self.arts([
            "Nintendo Switch 2 price increase announced in Europe",
            "Nintendo Switch 2 sales pass 20 million",
            "Nintendo Switch Online adds three GameCube games",
            "Xbox Game Pass October wave one revealed",
            "Xbox Game Pass loses five games next week",
        ]), RULES)
        self.assertEqual(len(groups), 5)

    def test_topic_merge_needs_close_dates(self):
        # Similar enough to share a topic, too different for the title-similarity pass.
        titles = ["Elden Ring Nightreign gets a surprise boss rush mode tomorrow",
                  "FromSoftware confirms Elden Ring Nightreign physical edition for collectors",
                  "Elden Ring Nightreign director talks about balancing co-op for three players"]
        self.assertEqual(len(fn.group_articles(self.arts(titles, gap_ms=60_000), RULES)), 1)
        self.assertEqual(len(fn.group_articles(self.arts(titles, gap_ms=10 * fn.HOUR_MS), RULES)), 3)


class Pipeline(unittest.TestCase):
    """Runs main() end to end with fake feeds."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(fn, name, self.tmp / path) for name, path in
                        [("OUTPUT_FILE", "data.json"), ("CACHE_DIR", ".cache")]]
        for p in self.patches:
            p.start()
        mock.patch.object(fn, "TRANSLATE_DELAY", 0).start()
        self.now = fn.now_ms()
        self.calls = {"feed": 0, "translate": 0, "icon": 0}
        self.feeds = {}
        sources = [
            {"name": "Push Square", "rss": "https://feeds.test/ps", "domain": "pushsquare.com", "tags": ["playstation"]},
            {"name": "Kotaku", "rss": "https://feeds.test/kotaku", "domain": "kotaku.com"},
            {"name": "Reddit Leaks", "rss": "https://feeds.test/reddit", "domain": "reddit.com", "tags": ["rumour"]},
            {"name": "Famitsu", "rss": "https://feeds.test/famitsu", "domain": "famitsu.com", "lang": "ja"},
            {"name": "The Verge", "rss": "https://feeds.test/verge", "domain": "theverge.com", "keywordFilter": True},
            {"name": "Dead Feed", "rss": "https://feeds.test/dead", "domain": "dead.test"},
        ]
        mock.patch.object(fn, "load_sources", lambda: sources).start()
        n = self.now
        self.feeds = {
            "https://feeds.test/ps": [("Astro Bot DLC out now", "https://pushsquare.com/a?utm=1", n - 60_000),
                                      ("Top 10 PS5 games", "https://pushsquare.com/top", n - 60_000)],
            "https://feeds.test/kotaku": [("Astro Bot DLC is out now and it rules", "https://kotaku.com/a", n - 120_000),
                                          ("Old story", "https://kotaku.com/old", n - 3 * fn.DAY_MS),
                                          ("Unsafe link", "javascript:alert(1)", n - 60_000),
                                          ("Future &amp; dated", "https://kotaku.com/f", n + fn.DAY_MS)],
            "https://feeds.test/reddit": [("Astro Bot sequel leak shows new world", "https://reddit.com/r/1", n - 30_000)],
            "https://feeds.test/famitsu": [("新作ゲームの発表", "https://famitsu.com/1", n - 90_000)],
            "https://feeds.test/verge": [("Apple announces a new iPad", "https://theverge.com/ipad", n - 60_000),
                                         ("Valve's new Steam Deck is here", "https://theverge.com/deck", n - 60_000)],
        }

        self.blocked_unless_reader = set()
        self.home_pages = {}
        self.google_news = {}
        self.ms_status = 200
        self.google_status = 200

        def rss(items):
            body = "".join(f"<item><title>{t}</title><link>{l}</link><pubDate>{rfc(d)}</pubDate></item>" for t, l, d in items)
            return f"<rss version='2.0'><channel>{body}</channel></rss>".encode()

        def get(url, headers=None, timeout=None, params=None):
            headers = headers or {}
            if url.startswith(fn.ICON_URL):
                self.calls["icon"] += 1
                png = b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (64).to_bytes(4, "big") + b"\0" * 20
                return Resp(200, png)
            if url == fn.MS_AUTH_URL:
                return Resp(self.ms_status, b"token", payload=None) if self.ms_status != 200 else Resp(200, b"token")
            if url == fn.FREE_GOOGLE_URL:
                self.calls["translate"] += 1
                if self.google_status != 200:
                    return Resp(self.google_status)
                lines = params["q"].split("\n")
                return Resp(200, payload=[[["\n".join("EN " + str(len(l)) for l in lines), "x"]]])
            if url.startswith("https://news.google.com/"):
                for domain, items in self.google_news.items():
                    if fn.quote("site:" + domain) in url:
                        return Resp(200, rss(items))
                return Resp(200, rss([]))
            if url in self.home_pages:
                return Resp(200, self.home_pages[url].encode())
            self.calls["feed"] += 1
            if url in self.blocked_unless_reader and headers.get("User-Agent") != fn.READER_UA:
                return Resp(403)
            if headers.get("If-None-Match") == "v1":
                return Resp(304)
            if url not in self.feeds:
                return Resp(404)
            return Resp(200, rss(self.feeds[url]), {"ETag": "v1"})

        def post(url, params=None, data=None, json=None, timeout=None, headers=None):
            self.calls["translate"] += 1
            if url == fn.MS_TRANSLATE_URL:
                if self.ms_status != 200:
                    return Resp(self.ms_status)
                return Resp(200, payload=[{"translations": [{"text": "MS " + str(len(i["Text"]))}]} for i in json])
            return Resp(404)

        mock.patch.object(fn.SESSION, "get", side_effect=get).start()
        mock.patch.object(fn.SESSION, "post", side_effect=post).start()
        mock.patch.dict("os.environ", {"GOOGLE_TRANSLATE_API_KEY": "", "DEEPL_API_KEY": ""}).start()

    def tearDown(self):
        mock.patch.stopall()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self):
        fn.main()
        return json.loads((self.tmp / "data.json").read_text())

    def test_end_to_end(self):
        out = self.run_main()
        arts = out["articles"]
        every = [a for s in arts for a in [s, *s.get("group", [])]]
        titles = {a["title"] for a in every}
        names = [o["name"] for o in out["sources"]]

        self.assertNotIn("Top 10 PS5 games", titles)
        self.assertNotIn("Old story", titles)
        self.assertNotIn("Unsafe link", titles)
        self.assertNotIn("Apple announces a new iPad", titles)
        self.assertIn("Valve's new Steam Deck is here", titles)
        self.assertIn("Future & dated", titles)
        self.assertTrue(all(a["date"] <= out["generatedAt"] for a in every))
        self.assertTrue(all("?" not in a["link"] for a in every))

        astro = next(s for s in arts if "Astro Bot DLC" in s["title"])
        self.assertEqual(astro["sources"], 2)
        self.assertIn("playstation", astro["tags"])
        self.assertNotIn("rumour", astro.get("tags", []))
        leak = next(s for s in arts if "leak" in s["title"])
        self.assertIn("rumour", leak["tags"])

        jp = next(a for a in every if a["link"] == "https://famitsu.com/1")
        self.assertTrue(jp["title"].startswith("MS ") and jp.get("translated"))
        self.assertTrue(out["translation"]["ok"])
        self.assertEqual(out["translation"]["via"], ["Microsoft"])

        dead = out["sources"][names.index("Dead Feed")]
        self.assertFalse(dead["ok"])
        self.assertEqual(dead["error"], "HTTP 404")  # its feed, its home page and Google News all failed
        self.assertTrue(all(o["ok"] for o in out["sources"] if o["name"] != "Dead Feed"))
        self.assertTrue(all(o["icon"] for o in out["sources"]))
        self.assertTrue(all(0 <= a["src"] < len(names) for a in every))

        # Second run: every feed answers 304, nothing is translated or downloaded again.
        self.calls.update(feed=0, translate=0, icon=0)
        again = self.run_main()
        self.assertEqual([a["link"] for a in again["articles"]], [a["link"] for a in arts])
        self.assertEqual(self.calls["translate"], 0)
        self.assertEqual(self.calls["icon"], 0)

    def test_translation_falls_through_to_next_service(self):
        self.ms_status = 403
        out = self.run_main()
        jp = next(a for s in out["articles"] for a in [s, *s.get("group", [])] if a["link"] == "https://famitsu.com/1")
        self.assertTrue(jp["title"].startswith("EN "), jp["title"])
        self.assertEqual(out["translation"]["via"], ["Google"])
        self.assertIn("Microsoft", out["translation"]["errors"])
        self.assertTrue(out["translation"]["ok"])

    def test_translation_failure_is_reported(self):
        self.ms_status = self.google_status = 429
        with mock.patch.object(fn, "translate_mymemory", side_effect=ValueError("daily limit reached")):
            out = self.run_main()
        self.assertFalse(out["translation"]["ok"])
        self.assertEqual(out["translation"]["untranslated"], 1)
        self.assertEqual(set(out["translation"]["errors"]), {"Microsoft", "Google", "MyMemory"})

    def test_blocked_feed_retried_as_feed_reader(self):
        self.blocked_unless_reader.add("https://feeds.test/kotaku")
        out = self.run_main()
        kotaku = next(o for o in out["sources"] if o["name"] == "Kotaku")
        self.assertTrue(kotaku["ok"])
        self.assertNotIn("via", kotaku)

    def test_dead_feed_found_on_home_page(self):
        n = self.now
        self.home_pages["https://dead.test/"] = ('<html><head><link rel="alternate" type="application/rss+xml" '
                                                 'href="/new-feed.xml"></head></html>')
        self.feeds["https://dead.test/new-feed.xml"] = [("Revived story", "https://dead.test/1", n - 60_000)]
        out = self.run_main()
        dead = next(o for o in out["sources"] if o["name"] == "Dead Feed")
        self.assertTrue(dead["ok"])
        self.assertEqual(dead["via"], "discovered")
        self.assertIn("Revived story", {s["title"] for s in out["articles"]})

    def test_dead_feed_falls_back_to_google_news(self):
        self.google_news["dead.test"] = [("Story via Google - Dead Feed", "https://dead.test/2", self.now - 60_000)]
        out = self.run_main()
        dead = next(o for o in out["sources"] if o["name"] == "Dead Feed")
        self.assertTrue(dead["ok"])
        self.assertEqual(dead["via"], "googlenews")
        self.assertIn("Story via Google", {s["title"] for s in out["articles"]})  # " - Publisher" removed

    def test_nothing_fetched_keeps_site(self):
        self.feeds = {}
        self.home_pages = {}
        with self.assertRaises(SystemExit):
            self.run_main()
        self.assertFalse((self.tmp / "data.json").exists())


class Files(unittest.TestCase):
    def test_sources(self):
        sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
        known = set(RULES.tags)
        for s in sources:
            with self.subTest(source=s.get("name")):
                self.assertTrue(s["rss"].startswith("https://"))
                self.assertTrue(s["domain"] and "/" not in s["domain"])
                self.assertLessEqual(set(s.get("tags", [])), known)
                self.assertTrue(all(u.startswith("https://") for u in s.get("alt", [])))
        rss = [s["rss"] for s in sources]
        self.assertEqual(len(rss), len(set(rss)), "a feed is listed twice")

    def test_site_files(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        for name in ["style.css", "script.js", "favicon.svg", "data.json"]:
            self.assertIn(name, html)
        filters = {"all", "new", "trending", "jp"} | set(RULES.tags)
        for f in filters:
            self.assertIn(f'data-f="{f}"', html, f"missing filter chip for {f}")


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1).result
    sys.exit(0 if result.wasSuccessful() else 1)
