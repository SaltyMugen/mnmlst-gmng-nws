# OniMugen+

Gaming news, leaks and rumours from 45+ sites in one compact feed. Styled after the Poden+ app.

A GitHub Action runs every 10 minutes. It tests the code, fetches the feeds, translates Japanese
headlines, groups stories that several sites covered, saves each site's icon, then publishes to Pages.

## Files you might edit

| File | What it controls |
|---|---|
| `sources.json` | The feeds. Add `"tags": ["playstation"]` for platform-only sites, `"lang": "ja"` for Japanese ones, `"alt": ["https://..."]` for backup feed addresses, `"enabled": false` to switch one off. |
| `config.json` | Tag rules (which words mean PlayStation, Xbox, Nintendo, PC or Rumour), blocked headlines, Trending settings, and names that shouldn't group unrelated stories. |

Everything else (`index.html`, `style.css`, `script.js`, `fetch_news.py`, `tests.py`) is the site and the updater.

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
