# EU AI Act

A local compilation of the EU AI Act in Markdown, with a GitHub Pages reader for browsing the full text.

Live site:

- https://msidhartha7.github.io/eu-ai-act/

## What is in this repository

This repository contains a structured English compilation of the EU AI Act, organized as a markdown database and published as a static website.

Included:

- `113` articles
- `180` recitals
- `13` annexes
- `13` chapters
- `16` sections

All compiled content is stored under `markdown_db/`.

## Repository structure

```text
markdown_db/
  manifest.json
  errors.json
  en/
    article/
    recital/
    annex/
    chapter/
    section/

site/
  static/

scripts/
  build_github_pages.py
  scrape_eu_ai_act.py
```

## GitHub Pages reader

This repository ships with a static Act explorer that renders the compiled corpus as a minimal reading experience.

Features:

- Type-based navigation for articles, recitals, annexes, chapters, and sections
- Clean reader pages with previous/next navigation
- Static search index
- Responsive white-and-blue layout

Build locally:

```bash
python3 scripts/build_github_pages.py
```

Generated output:

- `site_dist/`

Deployment:

- GitHub Actions builds and deploys the site on pushes to `main`
- The published Pages site serves the compiled markdown corpus directly from this repository

## Data format

Each page in `markdown_db/` is stored as Markdown with YAML frontmatter.

Example fields:

- `title`
- `source_url`
- `slug`
- `content_type`
- `language`
- `scraped_at`
- `word_count`

The corpus inventory is recorded in:

- `markdown_db/manifest.json`

## Source and maintenance

This repository is maintained as a compiled local database of the EU AI Act and a published reader for that corpus.

The scraper remains in the repository as a maintenance utility for rebuilding or refreshing the dataset when needed, but the primary purpose of this repository is the compiled Act itself.
