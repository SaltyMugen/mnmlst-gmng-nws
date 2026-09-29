# OniMugen+

Gaming news, leaks and rumours from 45+ sites in one compact feed. Styled after the Poden+ app.

A GitHub Action runs every 10 minutes. It tests the code, fetches the feeds, translates Japanese
headlines, groups stories that several sites covered, saves each site's icon, then publishes to Pages.

## Files you might edit

| File | What it controls |
|---|---|
| `sources.json` | The feeds. Add `"tags": ["playstation"]` for platform-only sites, `"lang": "ja"` for Japanese ones, `"enabled": false` to switch one off. |
| `config.json` | Tag rules (which words mean PlayStation, Xbox, Nintendo, PC or Rumour), blocked headlines, Trending settings, and names that shouldn't group unrelated stories. |

Everything else (`index.html`, `style.css`, `script.js`, `fetch_news.py`, `tests.py`) is the site and the updater.

## Publishing by upload

1. **Settings → Pages → Source: GitHub Actions**, custom domain `onimugen.com`.
2. **Add file → Upload files**: drag in every file except the `.github` folder. Commit.
3. The workflow file: open `.github/workflows/update.yml` in the repo, click ✏️, paste the new contents, commit.
4. Watch **Actions → Update feed**. If a test fails, the live site is left as it was.

## Dependable translation (optional, recommended)

Without a key the updater uses Google's public translation address, which Google sometimes blocks.
With a key it uses Google Cloud Translation. The first 500,000 characters each month are free,
and this site uses far less because each headline is translated once.

1. At console.cloud.google.com create a project, enable **Cloud Translation API**, and create an **API key**
   (restrict it to the Cloud Translation API).
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret**,
   name `GOOGLE_TRANSLATE_API_KEY`, paste the key.

The site shows a notice when translation fails, and the Settings sheet lists any site that isn't responding.

## Keeping the schedule alive

GitHub switches off scheduled workflows after 60 days without a commit. On the 1st of each month
the workflow updates the date in `sitemap.xml` and commits it, which keeps the schedule running.
