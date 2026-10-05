"""Build the published site from index.html and data.json. Run after fetch_news.py.

Search engines and link previews read the HTML before any JavaScript runs, so this writes into the page:
  - the newest stories as real links (the first screen, readable without JavaScript)
  - structured data (schema.org) describing the site and the story list
  - the last-updated time, canonical address and preview tags
It also writes feed.xml (RSS of the top stories), sitemap.xml, apple-touch-icon.png and
Safari's pinned-tab icon, then copies everything to _site/ for GitHub Pages.

Usage: python build_site.py [output folder, default _site]
"""

import html
import json
import shutil
import struct
import sys
import zlib
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE_URL = "https://onimugen.com/"
SITE_NAME = "OniMugen+"
PRERENDER = 40          # stories written into the HTML (the first screen and a bit more)
FEED_ITEMS = 50         # stories in feed.xml
STATIC = ["index.html", "style.css", "script.js", "favicon.svg", "safari-pinned-tab.svg",
          "robots.txt", "manifest.webmanifest", "CNAME"]
TAG_LABELS = {"playstation": "PlayStation", "xbox": "Xbox", "nintendo": "Nintendo", "steam": "Steam"}


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(ms: int, now: int) -> str:
    m = (now - ms) // 60_000
    return "now" if m < 1 else f"{m}m" if m < 60 else f"{m // 60}h"


def safe_url(u: str) -> str:
    return u if u.lower().startswith(("http://", "https://")) else "#"


def icon_html(src: dict) -> str:
    if src.get("icon"):
        return f'<img class="fav" src="icons/{esc(src["domain"])}.png" alt="" width="20" height="20" loading="lazy" decoding="async">'
    return '<span class="fav plus" aria-hidden="true"></span>'


def row_html(i: int, s: dict, sources: list[dict], now: int) -> str:
    """Must produce the same markup as rowHtml() in script.js, so the page doesn't shift when it takes over."""
    src = sources[s["src"]]
    tags = []
    if "review" in s.get("tags", []):
        tags.append('<span class="tag review">Review</span>')
    if "preview" in s.get("tags", []):
        tags.append('<span class="tag review">Preview</span>')
    if "rumour" in s.get("tags", []):
        tags.append('<span class="tag rumour">Rumour</span>')
    for t in ("playstation", "xbox", "nintendo", "steam"):
        if t in s.get("tags", []):
            tags.append(f'<span class="tag {t}">{TAG_LABELS[t]}</span>')
    if s.get("translated"):
        tags.append('<span class="tag">JP → EN</span>')
    more = ""
    if s.get("group"):
        label = f'{s["sources"]} sources' if s["sources"] > 1 else f'{len(s["group"]) + 1} articles'
        more = (f'<button type="button" class="more" aria-expanded="false">{label}'
                '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></button>')
    return (f'<li class="row" data-i="{i}">{icon_html(src)}'
            f'<a class="title" href="{esc(safe_url(s["link"]))}" target="_blank" rel="noopener">{esc(s["title"])}</a>'
            '<button type="button" class="save" aria-label="Bookmark"><svg viewBox="0 0 24 24">'
            '<path d="M18 21l-6-4-6 4V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z"/></svg></button>'
            f'<div class="meta"><span class="src">{esc(src["name"])}</span>'
            f'<time datetime="{iso(s["date"])}">{ago(s["date"], now)}</time>'
            '<span class="tag new" hidden>New</span><span class="tag hot" hidden>Trending</span>'
            f'{"".join(tags)}{more}</div></li>')


def structured_data(stories: list[dict], sources: list[dict], generated: int) -> str:
    graph = [
        {"@type": "WebSite", "@id": SITE_URL + "#site", "url": SITE_URL, "name": SITE_NAME,
         "alternateName": "OniMugen", "inLanguage": "en-GB",
         "description": "Gaming news, leaks and rumours from 45+ sites in one compact feed, updated every 10 minutes.",
         "potentialAction": {"@type": "SearchAction", "target": SITE_URL + "?q={search_term_string}",
                             "query-input": "required name=search_term_string"}},
        {"@type": "CollectionPage", "@id": SITE_URL + "#feed", "url": SITE_URL, "isPartOf": {"@id": SITE_URL + "#site"},
         "name": "Latest gaming news, leaks and rumours", "dateModified": iso(generated),
         "mainEntity": {"@type": "ItemList", "numberOfItems": len(stories), "itemListElement": [
             {"@type": "ListItem", "position": n + 1, "url": s["link"], "name": s["title"]}
             for n, s in enumerate(stories[:20])]}},
    ]
    return json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False,
                      separators=(",", ":")).replace("</", "<\\/")


def notice_html(data: dict) -> str:
    """Same text as renderNotice() in script.js, so the list doesn't move when the script takes over."""
    parts = []
    n = (data.get("translation") or {}).get("untranslated", 0)
    if n:
        parts.append(f"<b>{n} Japanese headline{'' if n == 1 else 's'}</b> couldn't be translated yet and "
                     f"{'is' if n == 1 else 'are'} shown as published.")
    failing = sum(1 for o in data["sources"] if not o.get("ok"))
    if failing >= 5:
        parts.append(f"<b>{failing} sources</b> aren't responding right now.")
    return f'<div class="notice" id="notice"{"" if parts else " hidden"}>{" ".join(parts)}</div>'


def build_page(template: str, data: dict) -> str:
    now = data["generatedAt"]
    stories = sorted(data["articles"], key=lambda s: -s["date"])
    top = stories[:PRERENDER]
    rows = "".join(row_html(i, s, data["sources"], now) for i, s in enumerate(top))
    heads = ", ".join(s["title"] for s in stories[:3] if s.get("title"))
    description = (f"Gaming news, leaks and rumours from {len(data['sources'])} sites in one fast feed. "
                   f"Latest: {heads}")[:300]
    stamp = datetime.fromtimestamp(now / 1000, timezone.utc).strftime("%-d %b %Y, %H:%M UTC")
    replacements = {
        "<!--ROWS-->": rows,
        "<!--NOTICE-->": notice_html(data),
        "<!--JSONLD-->": structured_data(stories, data["sources"], now),
        "<!--UPDATED-->": f'Updated <time datetime="{iso(now)}">{esc(stamp)}</time>',
        "__DESCRIPTION__": esc(description),
        "__MODIFIED__": iso(now),
        "__SOURCE_COUNT__": str(len(data["sources"])),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def build_feed(data: dict) -> str:
    stories = sorted(data["articles"], key=lambda s: -s["date"])[:FEED_ITEMS]
    names = [o["name"] for o in data["sources"]]
    items = "".join(
        f"<item><title>{esc(s['title'])}</title><link>{esc(s['link'])}</link>"
        f"<guid isPermaLink=\"true\">{esc(s['link'])}</guid>"
        f"<pubDate>{format_datetime(datetime.fromtimestamp(s['date'] / 1000, timezone.utc))}</pubDate>"
        f"<source url=\"{SITE_URL}feed.xml\">{esc(names[s['src']])}</source>"
        + "".join(f"<category>{TAG_LABELS[t]}</category>" for t in s.get("tags", []) if t in TAG_LABELS)
        + "</item>" for s in stories)
    built = format_datetime(datetime.fromtimestamp(data["generatedAt"] / 1000, timezone.utc))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
            f'<channel><title>{SITE_NAME} — Gaming news, leaks and rumours</title><link>{SITE_URL}</link>'
            f'<atom:link href="{SITE_URL}feed.xml" rel="self" type="application/rss+xml"/>'
            '<description>The newest gaming headlines from 45+ sites, grouped when several cover the same story.</description>'
            f'<language>en-gb</language><lastBuildDate>{built}</lastBuildDate><ttl>10</ttl>{items}</channel></rss>\n')


def build_sitemap(generated: int) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f'<url><loc>{SITE_URL}</loc><lastmod>{iso(generated)}</lastmod><changefreq>always</changefreq>'
            '<priority>1.0</priority></url></urlset>\n')


def touch_icon(size: int = 180) -> bytes:
    """The logo (yellow circle with a black plus on black) as a PNG, drawn here so no image tools are needed."""
    c, r, arm, bar = size / 2, size * 0.36, size * 0.2, size * 0.045
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            px, py = x + 0.5 - c, y + 0.5 - c
            inside = px * px + py * py <= r * r
            plus = (abs(px) <= bar and abs(py) <= arm) or (abs(py) <= bar and abs(px) <= arm)
            rows += bytes((255, 209, 0)) if inside and not plus else bytes((0, 0, 0))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site"
    data = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
    if out.exists():
        shutil.rmtree(out)
    (out / "icons").mkdir(parents=True)

    for name in STATIC:
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, out / name)
    (out / "index.html").write_text(build_page((ROOT / "index.html").read_text(encoding="utf-8"), data), encoding="utf-8")
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "feed.xml").write_text(build_feed(data), encoding="utf-8")
    (out / "sitemap.xml").write_text(build_sitemap(data["generatedAt"]), encoding="utf-8")
    (out / "apple-touch-icon.png").write_bytes(touch_icon())
    for icon in (ROOT / ".cache" / "icons").glob("*.png"):
        shutil.copy2(icon, out / "icons" / icon.name)
    print(f"Built {out} with {min(PRERENDER, len(data['articles']))} stories in the page")


if __name__ == "__main__":
    main()
