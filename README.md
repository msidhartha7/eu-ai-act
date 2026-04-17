# EU AI Act Markdown Scraper

This repository contains a standalone Python scraper that exports EU AI Act site content into a markdown-backed database.

## What it does

- Crawls the `artificialintelligenceact.eu` sitemap.
- Filters content by type such as `article`, `recital`, `annex`, `chapter`, `section`, `page`, or `post`.
- Converts the main page body into markdown.
- Writes one markdown file per page with YAML frontmatter.
- Generates `manifest.json` and `errors.json` so failed pages are recorded instead of stopping the run.

## Usage

Scrape the English article pages into `markdown_db/`:

```bash
python3 scripts/scrape_eu_ai_act.py
```

Scrape articles, recitals, and annexes including translated pages:

```bash
python3 scripts/scrape_eu_ai_act.py --include article recital annex --all-languages
```

Use the existing exported link list as an extra source of URLs:

```bash
python3 scripts/scrape_eu_ai_act.py \
  --seed-json artificialintelligenceact.eu_.2026-04-16T15_23_52.390Z.json
```

Run a small test batch:

```bash
python3 scripts/scrape_eu_ai_act.py --limit 5 --verbose
```

## Output layout

Generated files are written under:

```text
markdown_db/
  manifest.json
  errors.json
  en/
    article/
      article-1.md
```

The exact layout depends on page language and content type.

## Error handling

The scraper includes:

- Retry and backoff for HTTP fetch failures.
- Structured error recording for sitemap discovery and page scraping.
- Filesystem write failure handling.
- Empty-content detection, so bad parses are flagged instead of silently written.

## Notes

- The script uses only the Python standard library.
- By default it scrapes English `article` pages only.
- If the target site changes its HTML structure, the markdown extraction heuristics may need adjustment.
