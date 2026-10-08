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
from content_filter import classify

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
    def test_platform_and_rumour_tags_only_from_sources(self):
        sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
        tagged = {s["name"]: s["tags"] for s in sources if s.get("tags")}
        self.assertEqual(tagged, {"PlayStation Blog": ["playstation"], "Xbox Wire": ["xbox"],
                                  "Nintendo News": ["nintendo"], "Steam News": ["steam"], "Reddit Leaks": ["rumour"]})

    def make(self, title, src, tags=None, minutes=0):
        return {"title": title, "link": f"https://x.test/{src}/{abs(hash(title))}", "date": fn.now_ms() - minutes * 60_000,
                "source": src, "domain": f"{src}.test", **({"tags": tags} if tags else {})}

    def test_rumour_only_from_reddit(self):
        idx = {"kotaku": 0, "reddit": 1}
        leak_word = fn.build_story([self.make("Xbox handheld leak reportedly shows new design", "kotaku")], idx, RULES)
        self.assertNotIn("tags", leak_word)  # rumour words in a headline no longer tag it
        reddit = fn.build_story([self.make("Switch 2 Lite spotted in filings", "reddit", ["rumour"])], idx, RULES)
        self.assertEqual(reddit["tags"], ["rumour"])
        confirmed = fn.build_story([self.make("Switch 2 Lite announced", "kotaku", minutes=10),
                                    self.make("Switch 2 Lite spotted in filings", "reddit", ["rumour"])], idx, RULES)
        self.assertNotIn("tags", confirmed)  # confirmed elsewhere first: not a rumour

    def test_platform_tag_needs_official_source(self):
        idx = {"push": 0, "blog": 1}
        plain = fn.build_story([self.make("Astro Bot 2 announced for PS5", "push")], idx, RULES)
        self.assertNotIn("tags", plain)
        official = fn.build_story([self.make("Astro Bot 2 announced for PS5", "push", minutes=5),
                                   self.make("Astro Bot 2 is coming", "blog", ["playstation"])], idx, RULES)
        self.assertEqual(official["tags"], ["playstation"])

    def test_blocked(self):
        for title in ["Top 10 RPGs of 2026", "The 25 best games ever", "Save $20 on DualSense", "How to beat Malenia",
                      "Every Zelda game ranked", "All FC 27 icons"]:
            self.assertTrue(RULES.is_blocked(title), title)
        for title in ["Ranking up fast in Marvel Rivals", "Topaz DLC out now", "Nintendo shows off Switch 2"]:
            self.assertFalse(RULES.is_blocked(title), title)


class ContentFilter(unittest.TestCase):
    """Real headlines from 29-30 September. Guides, lists and deals go; news, updates, reviews and previews stay."""

    DROP = [
        "Kingdom Rush 6: Genesis TD Spells tier list",
        "Fire Emblem Fortune's Weave: Dates location to recruit Halvin",
        "Where to find yuna mei in Fire Emblem Fortune's Weave",
        "Cold War Class Tier List – Best NATO & PACT Classes",
        "October Prime Day gaming mouse deals",
        "Guide: Best Fire Emblem Games Of All Time",
        "What are the right answers to the FBC Terminals in Control Resonant?",
        "Should you eat the Mold in Control Resonant?",
        "20 Brilliantly Inventive Cosplay Fits At Dragon Con Atlanta 2026",
        "When does the new Gears of War come out?",
        "Transport Fever 3 release countdown: Exact date and time",
        "Best players for FC 27 Flank Identity Evolution",
        "All taxi locations in Control Resonant",
        "The MSI Codex Z2C RTX 5070 Prebuilt Gaming PC Drops to $1399 and Includes Control: Resonant",
        "Wordle Hints and Answer for September 29, 2026: Game #1928",
        "NYT Connections Hints Today (#1206)—Answers for Tuesday, September 29",
        "Best new video games of 2026 (so far)",
        "When Does Game Informer's GTA 6 Magazine Come Out? Release Times, Explained",
        "Most Revolutionary RPGs Every Fan Needs to Experience",
        "Family Matters Quest Choices and Outcomes in The Witcher 3",
        "10 Essential NES Games Still Missing From Switch Online",
        "Should You Play Witcher 3 Remastered On The Switch 2?",
        "Where to pre-order God of War Laufey, Faye's journey hits the shelves tomorrow",
        "It's Time for Siliconera's 2026 Site Survey",
        "New Action Roguelike PC Game is Essentially Minecraft Meets Vampire Survivors",
        "Enhance Your Ace Combat 8 Experience with Two Rival PS5 Flight Sticks, Out This Week",
        "Top 10 Soulslikes You Need To Play",
        "Elden Ring Nightreign: Best builds after the latest patch",
        "Black Friday 2026: Best PS5 deals",
        "Genshin Impact redeem codes for October",
    ]
    KEEP = {
        "Microsoft boss Nadella says Xbox has to invent \"sustainable business model\"": "news",
        "As Wardogs sells 3 million copies, lead admits the devs are \"overwhelmed\"": "news",
        "AMD is acquiring AI company World Labs in a deal worth more than $8 billion": "news",
        "Guide: These 21+ PS5 and PS Plus Games Are Coming Out This Week (28th-4th October)": "news",
        "Five New Xbox Games Are Finishing Off The Month, Including A Free-To-Play Shadow Drop (September 30)": "news",
        "PSA: The Witcher 3 Remastered File Size And Release Time Confirmed, Separate Purchase Required": "news",
        "The Witcher 3: Wild Hunt – Remastered Update Patch Notes Include PC-Specific and Console-Specific Changes": "news",
        "NHL 27 Title Update 2 Tweaks Boarding and Charging Penalties": "news",
        "Synduality: Echo of Ada Service Ends Worldwide as It Goes Offline": "news",
        "Report: Sony surveying developers about dropping PlayStation disc support": "news",
        "Two new The Last of Us projects in very early stages at Naughty Dog": "news",
        "The Last of Us Season 3 Casts John Goodman": "news",
        "3 Warhammer 40,000 sickos share their strongest held beliefs": "news",
        "11-year-old wins esports gold at Asian Games after eating eels to prepare": "news",
        "Former Elder Scrolls writer is '100% convinced' a bit of series lore was inspired by a typo": "news",
        "Ace Combat 8 Beats Out Titans to Become the Best Selling Game in the US": "news",
        "Rockstar takes legal action after modders port GTA 5 to Nintendo Switch": "news",
        "Top 5 studios hit by layoffs this year": "news",
        "Minecraft Dungeons 2 Review": "review",
        "TOEM 2: The Kotaku Review": "review",
        "Round Up: The First Reviews For The Witcher 3 Remastered Are In": "review",
        "Review: Train Sim World 7 (PS5) - A Familiar Journey with a Few New Stops": "review",
        "Hands-on: Resident Evil Requiem is the scariest in years": "preview",
        "Ghost of Yotei preview: we played the first three hours": "preview",
        "With Overwhelmingly Positive Steam reviews and surging players, indie dev says \"I may have peaked\"": "news",
    }

    def test_drops_guides_lists_and_deals(self):
        for title in self.DROP:
            with self.subTest(title=title):
                self.assertEqual(classify(title), "drop")

    def test_keeps_news_reviews_and_previews(self):
        for title, kind in self.KEEP.items():
            with self.subTest(title=title):
                self.assertEqual(classify(title), kind)

    def test_guide_sections_in_the_address(self):
        self.assertEqual(classify("Control Resonant: Ashtray Maze", "https://www.polygon.com/guides/control-resonant-ashtray"), "drop")
        self.assertEqual(classify("Control Resonant gets a big update", "https://www.polygon.com/news/control-update"), "news")

    def test_story_tagged_review(self):
        idx = {"ign": 0}
        story = fn.build_story([{"title": "Minecraft Dungeons 2 Review", "link": "https://ign.com/r", "date": fn.now_ms(),
                                 "source": "ign", "domain": "ign.com"}], idx, RULES)
        self.assertEqual(story["tags"], ["review"])


class Grouping(unittest.TestCase):
    """Each case is a real mistake or a real success from the rating on 29 September."""

    def arts(self, pairs, gap_ms=60_000):
        now = fn.now_ms()
        return [{"title": t, "link": f"https://x.test/{i}", "date": now - i * gap_ms, "source": src,
                 "domain": src.lower().replace(" ", "") + ".test"} for i, (src, t) in enumerate(pairs)]

    def count(self, pairs, **kw):
        return len(fn.group_articles(self.arts(pairs, **kw), RULES))

    def test_same_story_groups(self):
        self.assertEqual(self.count([("IGN", "Minecraft Dungeons 2 Review"),
                                     ("Destructoid", "Minecraft Dungeons 2 review: a diamond in the rift"),
                                     ("Game Informer", "Minecraft Dungeons II Review - Built Better Than Before")]), 1)

    def test_missed_pairs_now_group(self):
        self.assertEqual(self.count([("VGC", "Sony is officially skipping CES for the first time in decades"),
                                     ("TheGamer", "For The First Time Ever, Sony Skips CES, Which Was Home To One Of PlayStation's Silliest Announcements")]), 1)
        self.assertEqual(self.count([("IGN", "GTA 6 Has an 'Advanced' Weather System, but How Will It Work?"),
                                     ("Kotaku", "GTA 6's 'advanced' weather system finally confirmed after years of rumors")]), 1)

    def test_one_event_is_one_group(self):
        groups = fn.group_articles(self.arts([
            ("Push Square", "New The Last of Us Projects in 'Very Early Stages' of Development"),
            ("Gematsu", "Two new The Last of Us projects in very early stages at Naughty Dog"),
            ("DualShockers", "Naughty Dog Teases Life After Ellie With Two New Last Of Us Projects"),
            ("GamesBeat", "Neil Druckmann confirms Naughty Dog is working on a couple of projects"),
            ("Destructoid", "Rare Naughty Dog update confirms 2 new The Last of Us projects, no Intergalactic news"),
        ]), RULES)
        self.assertEqual(len(groups), 1)

    def test_same_site_template_headlines_stay_apart(self):
        self.assertEqual(self.count([("Destructoid", "Transport Fever 3 release countdown: Exact date and time"),
                                     ("Destructoid", "Nivalis Nights release countdown: Exact date and time")]), 2)
        self.assertEqual(self.count([("PC Gamer", "October Prime Day gaming mouse deals"),
                                     ("PC Gamer", "October Prime Day gaming keyboard deals")]), 2)
        self.assertEqual(self.count([("VGC", "Fire Emblem Fortune's Weave: Dates location to recruit Halvin"),
                                     ("Polygon", "Where to find yuna mei in Fire Emblem Fortune's Weave")]), 2)

    def test_different_stories_stay_apart(self):
        self.assertEqual(self.count([
            ("VGC", "Nintendo Switch 2 price increase announced in Europe"),
            ("IGN", "Nintendo Switch 2 sales pass 20 million"),
            ("Polygon", "Nintendo Switch Online adds three GameCube games"),
            ("Pure Xbox", "Xbox Game Pass October wave one revealed"),
            ("Kotaku", "Xbox Game Pass loses five games next week"),
        ]), 5)

    def test_no_outlet_twice_in_a_group(self):
        for g in fn.group_articles(self.arts([("VGC", "Hollow Knight Silksong patch adds new boss"),
                                              ("VGC", "Hollow Knight Silksong patch notes"),
                                              ("IGN", "Hollow Knight Silksong patch adds a new boss fight")]), RULES):
            self.assertEqual(len({a["source"] for a in g}), len(g))

    def test_todays_date_does_not_join_stories(self):
        # Screenshot, 30 September: an art book, Xbox releases and Witcher 3 mods were one "5 sources" story.
        groups = fn.group_articles(self.arts([
            ("Famitsu", "The official art book for *Culdcept Begins* goes on sale today, 30 September. A limited edition "
                        "featuring a canvas board with an original cover illustration by lead designer Sei Matsuura is also now available!"),
            ("Dengeki Online", "The official art book for *Culdcept Begins* goes on sale today, 30 September. A limited edition "
                               "featuring a canvas board with an original cover illustration by lead designer Sei Matsuura is also now available!"),
            ("GameBiz", "KADOKAWA releases the 'Culdcept Begins Official Art Book' today! Packed with highlights, including an "
                        "original cover illustration by Sei Matsuura and previously unseen sketches."),
            ("Pure Xbox", "Five New Xbox Games Are Finishing Off The Month, Including A Free-To-Play Shadow Drop (September 30)"),
            ("Game Rant", "The Witcher 3: All Mods Now Available On Console, September 30"),
        ]), RULES)
        by_source = {a["source"]: n for n, g in enumerate(groups) for a in g}
        self.assertEqual(by_source["Famitsu"], by_source["GameBiz"])         # the art book is one story
        self.assertEqual(by_source["Famitsu"], by_source["Dengeki Online"])
        self.assertNotEqual(by_source["Famitsu"], by_source["Pure Xbox"])    # the date alone joins nothing
        self.assertNotEqual(by_source["Pure Xbox"], by_source["Game Rant"])
        self.assertEqual(len(groups), 3)

    def test_merges_need_close_dates(self):
        titles = [("Gematsu", "Intergalactic: The Heretic Prophet to be fully revealed in 2027"),
                  ("VGC", "Naughty Dog will fully reveal Intergalactic in 2027"),
                  ("Insider Gaming", "No Updates On Intergalactic: The Heretic Prophet Coming This Year"),
                  ("GamesRadar+", "Intergalactic: The Heretic Prophet is testing the bounds of what Naughty Dog can do")]
        self.assertEqual(self.count(titles, gap_ms=60_000), 1)
        self.assertGreater(self.count(titles, gap_ms=10 * fn.HOUR_MS), 1)


class Trending(unittest.TestCase):
    def story(self, sites, newest_min_ago, spread_min=30):
        now = fn.now_ms()
        return [{"title": "t", "link": f"https://x.test/{i}", "date": now - (newest_min_ago + i * spread_min / max(1, sites)) * 60_000,
                 "source": f"S{i}", "domain": f"s{i}.test"} for i in range(sites)]

    def test_needs_three_recent_sites(self):
        groups = [self.story(2, 5), self.story(3, 5), self.story(6, 5)] + [self.story(1, 30)] * 60
        stories = [{} for _ in groups]
        bar = fn.mark_trending(stories, groups, fn.now_ms())
        hot = [fn.trend_score(s.get("recent", 0), fn.now_ms() - s.get("latest", 0)) >= bar for s in stories]
        self.assertEqual(hot[:3], [False, True, True])
        self.assertFalse(any(hot[3:]))

    def test_old_coverage_does_not_count(self):
        groups = [self.story(5, 7 * 60)]  # five sites, but all over 6 hours ago
        stories = [{}]
        fn.mark_trending(stories, groups, fn.now_ms())
        self.assertNotIn("recent", stories[0])

    def test_at_most_top_five_percent(self):
        groups = [self.story(3 + (i % 4), 5 + i) for i in range(40)] + [self.story(1, 30)] * 160
        stories = [{} for _ in groups]
        now = fn.now_ms()
        bar = fn.mark_trending(stories, groups, now)
        hot = sum(fn.trend_score(s.get("recent", 0), now - s.get("latest", 0)) >= bar for s in stories)
        self.assertTrue(1 <= hot <= round(len(groups) * fn.TREND_MAX_SHARE) + 1, hot)


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
            {"name": "Push Square", "rss": "https://feeds.test/ps", "domain": "pushsquare.com"},
            {"name": "PlayStation Blog", "rss": "https://feeds.test/psblog", "domain": "blog.playstation.com", "tags": ["playstation"]},
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
            "https://feeds.test/psblog": [("Astro Bot DLC is out now", "https://blog.playstation.com/a", n - 150_000)],
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
        self.assertEqual(astro["sources"], 3)
        self.assertEqual(astro["tags"], ["playstation"])  # because PlayStation Blog covered it
        self.assertNotIn("rumour", astro.get("tags", []))
        leak = next(s for s in arts if "leak" in s["title"])
        self.assertEqual(leak["tags"], ["rumour"])  # from the Reddit source
        self.assertIn("trendingAt", out)

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
        known = {"rumour", "playstation", "xbox", "nintendo", "steam"}
        for s in sources:
            with self.subTest(source=s.get("name")):
                self.assertTrue(s["rss"].startswith("https://"))
                self.assertTrue(s["domain"] and "/" not in s["domain"])
                self.assertLessEqual(set(s.get("tags", [])), known)
                self.assertTrue(all(u.startswith("https://") for u in s.get("alt", [])))
        self.assertNotIn("Knowledge", {s["name"] for s in sources})
        rss = [s["rss"] for s in sources]
        self.assertEqual(len(rss), len(set(rss)), "a feed is listed twice")

    def test_site_files(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        for name in ["style.css", "script.js", "favicon.svg", "data.json", "feed.xml", "apple-touch-icon.png",
                     "manifest.webmanifest", "<!--ROWS-->", "<!--JSONLD-->", "<!--NOTICE-->", "__DESCRIPTION__"]:
            self.assertIn(name, html)
        filters = {"all", "new", "trending", "reviews", "jp", "rumour", "playstation", "xbox", "nintendo", "steam"}
        self.assertIn('id="more-news"', html)
        for theme in ("dark", "light", "arcade"):
            self.assertIn(f'data-t="{theme}"', html)
        self.assertNotIn('data-t="blossom"', html)
        for f in filters:
            self.assertIn(f'data-f="{f}"', html, f"missing filter chip for {f}")


class BuiltSite(unittest.TestCase):
    """build_site.py: what search engines and link previews see before any JavaScript runs."""

    @classmethod
    def setUpClass(cls):
        import build_site
        from xml.etree import ElementTree
        cls.bs, cls.ET = build_site, ElementTree
        now = fn.now_ms()
        cls.data = {"generatedAt": now, "trendingAt": 3, "translation": {"ok": True, "untranslated": 0},
                    "sources": [{"name": "Kotaku", "domain": "kotaku.com", "ok": True, "count": 2, "icon": True},
                                {"name": "Xbox Wire", "domain": "news.xbox.com", "ok": True, "count": 1, "icon": False}],
                    "articles": [{"title": f"Story {i} <b>&amp; \"quotes\"</b>", "link": f"https://kotaku.com/{i}",
                                  "date": now - i * 60_000, "src": i % 2, "sources": 1,
                                  **({"tags": ["xbox"]} if i % 2 else {})} for i in range(60)]}
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "data.json").write_text(json.dumps(cls.data))
        with mock.patch.object(build_site, "ROOT", ROOT), mock.patch.object(Path, "read_text", Path.read_text):
            template = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.page = build_site.build_page(template, cls.data)
        cls.feed = build_site.build_feed(cls.data)
        cls.sitemap = build_site.build_sitemap(now)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_placeholders_filled(self):
        for marker in ["<!--ROWS-->", "<!--NOTICE-->", "<!--JSONLD-->", "<!--UPDATED-->", "__DESCRIPTION__", "__MODIFIED__", "__SOURCE_COUNT__"]:
            self.assertNotIn(marker, self.page)

    def test_stories_are_real_links_in_html(self):
        self.assertEqual(self.page.count('<li class="row"'), self.bs.PRERENDER)
        self.assertIn('href="https://kotaku.com/0"', self.page)
        feed = self.page.split('<ol class="feed" id="feed" data-prebuilt>')[1].split("</ol>")[0]
        self.assertNotIn("<b>", feed)  # titles are escaped, never HTML
        self.assertIn("&lt;b&gt;", feed)
        ld = self.page.split('<script type="application/ld+json">')[1].split("</script>")[0]
        self.assertNotIn("</", ld)  # nothing in the structured data can close the script early
        self.assertIn('<span class="tag xbox">Xbox</span>', self.page)
        self.assertIn('class="fav plus"', self.page)  # Xbox Wire has no icon: the logo is used

    def test_structured_data(self):
        start = self.page.index('<script type="application/ld+json">') + len('<script type="application/ld+json">')
        ld = json.loads(self.page[start:self.page.index("</script>", start)])
        types = {g["@type"] for g in ld["@graph"]}
        self.assertEqual(types, {"WebSite", "CollectionPage"})
        items = next(g for g in ld["@graph"] if g["@type"] == "CollectionPage")["mainEntity"]["itemListElement"]
        self.assertEqual(len(items), 20)
        self.assertEqual(items[0]["url"], "https://kotaku.com/0")

    def test_head_for_search_and_mac(self):
        for needle in ['<link rel="canonical" href="https://onimugen.com/">', 'rel="apple-touch-icon"', 'rel="mask-icon"',
                       'type="application/rss+xml"', 'property="og:image"', 'name="apple-mobile-web-app-title"',
                       '<h1 class="sr">', 'role="search"']:
            self.assertIn(needle, self.page)
        desc = self.page.split('<meta name="description" content="')[1].split('"')[0]
        self.assertTrue(40 < len(desc) <= 300 and "Story 0" in desc, desc)

    def test_feed_and_sitemap_are_valid_xml(self):
        rss = self.ET.fromstring(self.feed)
        self.assertEqual(len(rss.findall("./channel/item")), self.bs.FEED_ITEMS)
        self.assertEqual(rss.find("./channel/item/category").text if rss.find("./channel/item/category") is not None else "Xbox", "Xbox")
        sm = self.ET.fromstring(self.sitemap)
        self.assertEqual(sm[0][0].text, "https://onimugen.com/")

    def test_touch_icon_is_png(self):
        png = self.bs.touch_icon()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(fn.png_width(png), 180)


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1).result
    sys.exit(0 if result.wasSuccessful() else 1)
