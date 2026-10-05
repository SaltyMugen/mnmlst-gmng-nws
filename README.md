# OniMugen+

Gaming news, leaks and rumours from 45+ sites in one compact feed. Styled after the Poden+ app.

A GitHub Action runs every 10 minutes. It tests the code, fetches the feeds, translates Japanese
headlines, groups stories that several sites covered, saves each site's icon, then publishes to Pages.

## What gets in

`content_filter.py` keeps news and updates on games, the industry and nearby areas (films, TV, hardware),
reviews and previews, and drops guides, walkthroughs, answers and solutions, tier lists, rankings, "best X" and
top-N lists, deals, codes, quizzes, daily puzzles and release countdowns.

It errs towards keeping: anything that reports an event (layoffs, acquisitions, delays, announcements, leaks,
patches, sales figures) stays even if it looks like a list, and headlines that don't clearly look like a
guide stay. Each update's log shows how many were dropped. If something you want is being dropped, or a type
of guide gets through, the word lists at the top of `content_filter.py` are where to change it, and the real
examples in `tests.py` (class ContentFilter) check that nothing else breaks.

## How tags, grouping and Trending work

- **Tags** only come from sources: PlayStation, Xbox, Nintendo and Steam from their official sites (when one
  of them covered the story), Rumour only from the Reddit leaks board. Review and Preview come from the headline.
- **Grouping**: the same story from different sites is shown once, with "N sources". A site never appears
  twice in one group, guides, deals and countdowns are never grouped, and groups about the same event
  (for example "Naughty Dog" + "Intergalactic") merge when published within 6 hours.
- **Trending**: the top 5% of stories by how many different sites covered them in the last 6 hours
  (at least 3), fading as the newest article gets older.

## Files you might edit

| File | What it controls |
|---|---|
| `sources.json` | The feeds. Add `"tags": ["playstation"]` for platform-only sites, `"lang": "ja"` for Japanese ones, `"alt": ["https://..."]` for backup feed addresses, `"enabled": false` to switch one off. |
| `config.json` | Blocked headlines, names that shouldn't group unrelated stories, and the word list for general-news sources. |

Everything else is the site (`index.html`, `style.css`, `script.js`), the updater (`fetch_news.py`), the page builder (`build_site.py`) and the checks (`tests.py`).

## Publishing by upload

1. **Settings → Pages → Source: GitHub Actions**, custom domain `onimugen.com`.
2. **Add file → Upload files**: drag in every file except the `.github` folder. Commit.
3. The workflow file: open `.github/workflows/update.yml` in the repo, click ✏️, paste the new contents, commit.
4. Watch **Actions → Update feed**. If a test fails, the live site is left as it was.

## Translation

Japanese headlines are translated once and remembered. The updater tries these services in order and
moves on when one refuses:

1. **Google Cloud Translation** if the `GOOGLE_TRANSLATE_API_KEY` secret is set
2. **DeepL** if the `DEEPL_API_KEY` secret is set (free plan: 500,000 characters a month; free keys end in `:fx`)
3. Microsoft's free translator (the one the Edge browser uses)
4. Google's free translator
5. MyMemory (free, limited per day)

The free ones need no setup but can be refused from GitHub's servers. For translation you can count on,
add one key. Either is free at this site's volume.

- **DeepL (simplest):** sign up for DeepL API Free at deepl.com/pro-api, copy the key from your account.
- **Google Cloud:** at console.cloud.google.com create a project, enable Cloud Translation API, create an
  API key restricted to that API.

Then in the repo: **Settings → Secrets and variables → Actions → New repository secret**, name
`DEEPL_API_KEY` or `GOOGLE_TRANSLATE_API_KEY`, paste the key.

## When a source stops responding

For each source the updater tries, in order: its feed address, any `"alt"` addresses listed for it in
`sources.json`, the last address that worked, the feed address the site advertises on its home page, and
Google News results for that site. Settings shows how each source was read ("via Google News", "new feed
address") and why a source failed ("blocked", "feed removed", "site error"). When the log says a new feed
address was found, put it in `sources.json`.

## Keeping the schedule alive

GitHub switches off scheduled workflows after 60 days without a commit. On the 1st of each month
the workflow updates the date in `sitemap.xml` and commits it, which keeps the schedule running.

## Search engines

Each run, `build_site.py` writes the newest 40 stories into the page as real links, with structured data
(schema.org WebSite, CollectionPage and ItemList), a description built from the latest headlines,
`feed.xml` (RSS), `sitemap.xml`, an Apple touch icon, Safari's pinned-tab icon and a web app manifest.

Once, after the first deploy:
1. **Google Search Console** (search.google.com/search-console): add `onimugen.com`, verify it, submit
   `https://onimugen.com/sitemap.xml`. Safari on the Mac uses Google by default.
2. **Bing Webmaster Tools** (bing.com/webmasters): import from Google Search Console. This also covers DuckDuckGo.
3. Applebot (Spotlight and Siri suggestions on Mac, iPhone and iPad) is allowed by `robots.txt`; nothing to do.
