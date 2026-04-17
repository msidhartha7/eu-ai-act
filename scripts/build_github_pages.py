#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib import parse


ROOT = Path(__file__).resolve().parent.parent
MARKDOWN_DB = ROOT / "markdown_db"
STATIC_DIR = ROOT / "site" / "static"
DEFAULT_OUTPUT = ROOT / "site_dist"
TYPE_ORDER = ["article", "recital", "chapter", "section", "annex"]
TYPE_LABELS = {
    "article": "Articles",
    "recital": "Recitals",
    "chapter": "Chapters",
    "section": "Sections",
    "annex": "Annexes",
}
SINGULAR_LABELS = {
    "article": "Article",
    "recital": "Recital",
    "chapter": "Chapter",
    "section": "Section",
    "annex": "Annex",
}
TYPE_SLUGS = {
    "article": "articles",
    "recital": "recitals",
    "chapter": "chapters",
    "section": "sections",
    "annex": "annexes",
}


@dataclass
class Page:
    title: str
    source_url: str
    slug: str
    content_type: str
    language: str
    output_path: str
    source: str
    scraped_at: str | None
    published_at: str | None
    updated_at: str | None
    description: str | None
    word_count: int
    body_markdown: str
    body_html: str
    excerpt: str
    display_number: str
    route: str
    disk_path: Path


def json_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def detect_repo_name() -> str:
    env_repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if env_repo and "/" in env_repo:
        return env_repo.split("/", 1)[1]

    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ROOT.name

    remote = result.stdout.strip()
    if not remote:
        return ROOT.name

    match = re.search(r"[:/]([^/]+?)(?:\.git)?$", remote)
    return match.group(1) if match else ROOT.name


def site_base_path() -> str:
    configured = os.getenv("SITE_BASE_PATH", "").strip()
    if configured:
        normalized = configured if configured.startswith("/") else f"/{configured}"
        return normalized.rstrip("/")
    return f"/{detect_repo_name()}".rstrip("/")


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def slug_sort_key(slug: str) -> tuple:
    parts = re.findall(r"\d+|[A-Za-z]+", slug)
    key = []
    for part in parts:
        key.append(int(part) if part.isdigit() else part.lower())
    return tuple(key)


def display_number(page: dict[str, object]) -> str:
    slug = str(page["slug"])
    title = str(page["title"])
    matches = re.findall(r"\d+", slug)
    if page["content_type"] == "section" and len(matches) >= 2:
        return ".".join(matches)
    if matches:
        return matches[-1]
    match = re.search(r"\b([IVXLCM]+)\b", title)
    if match:
        return match.group(1)
    return slug


def read_frontmatter(path: Path) -> tuple[dict[str, object], str]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise ValueError(f"{path} is missing frontmatter")

    parts = raw.split("\n---\n", 1)
    if len(parts) != 2:
        raise ValueError(f"{path} has invalid frontmatter")

    frontmatter_block, body = parts
    frontmatter: dict[str, object] = {}
    for line in frontmatter_block.splitlines()[1:]:
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        frontmatter[key] = json.loads(value)
    return frontmatter, body.lstrip()


def strip_duplicate_title_heading(body: str, title: str) -> str:
    lines = body.splitlines()
    if lines and lines[0].strip() == f"# {title}":
        return "\n".join(lines[1:]).lstrip()
    return body


def render_inline(markdown: str) -> str:
    rendered = escape(markdown)
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    rendered = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', rendered)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", rendered)
    return rendered


def render_markdown(markdown: str) -> str:
    lines = markdown.splitlines()
    html_lines: list[str] = []
    paragraph: list[str] = []
    list_stack: list[str] = []
    in_code = False
    code_lines: list[str] = []
    table_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = normalize_whitespace(" ".join(item.strip() for item in paragraph))
            html_lines.append(f"<p>{render_inline(text)}</p>")
            paragraph = []

    def flush_lists() -> None:
        nonlocal list_stack
        while list_stack:
            html_lines.append(f"</{list_stack.pop()}>")

    def flush_table() -> None:
        nonlocal table_lines
        if not table_lines:
            return
        rows = []
        for line in table_lines:
            stripped = line.strip().strip("|")
            if not stripped:
                continue
            cells = [render_inline(cell.strip()) for cell in stripped.split("|")]
            rows.append(cells)
        if len(rows) >= 2 and all(set(cell) <= {"-"} for cell in rows[1]):
            header = rows[0]
            body_rows = rows[2:]
            html_lines.append("<table><thead><tr>")
            html_lines.extend(f"<th>{cell}</th>" for cell in header)
            html_lines.append("</tr></thead><tbody>")
            for row in body_rows:
                html_lines.append("<tr>")
                html_lines.extend(f"<td>{cell}</td>" for cell in row)
                html_lines.append("</tr>")
            html_lines.append("</tbody></table>")
        else:
            html_lines.extend(f"<p>{render_inline(line)}</p>" for line in table_lines)
        table_lines = []

    for line in lines:
        stripped = line.rstrip()

        if in_code:
            if stripped.startswith("```"):
                html_lines.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                code_lines.append(line)
            continue

        if stripped.startswith("```"):
            flush_paragraph()
            flush_lists()
            flush_table()
            in_code = True
            code_lines = []
            continue

        if not stripped:
            flush_paragraph()
            flush_lists()
            flush_table()
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            flush_lists()
            table_lines.append(stripped)
            continue

        flush_table()

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_paragraph()
            flush_lists()
            level = len(heading.group(1))
            html_lines.append(f"<h{level}>{render_inline(heading.group(2).strip())}</h{level}>")
            continue

        blockquote = re.match(r"^>\s?(.*)$", stripped)
        if blockquote:
            flush_paragraph()
            flush_lists()
            html_lines.append(f"<blockquote><p>{render_inline(blockquote.group(1).strip())}</p></blockquote>")
            continue

        unordered = re.match(r"^- (.*)$", stripped)
        ordered = re.match(r"^\d+\. (.*)$", stripped)
        if unordered or ordered:
            flush_paragraph()
            tag = "ul" if unordered else "ol"
            if not list_stack or list_stack[-1] != tag:
                flush_lists()
                html_lines.append(f"<{tag}>")
                list_stack.append(tag)
            item = unordered.group(1) if unordered else ordered.group(1)
            html_lines.append(f"<li>{render_inline(item.strip())}</li>")
            continue

        paragraph.append(stripped)

    flush_paragraph()
    flush_lists()
    flush_table()

    return "\n".join(html_lines)


def page_route(content_type: str, slug: str) -> str:
    return f"{TYPE_SLUGS[content_type]}/{slug}/"


def page_output_dir(output_dir: Path, route: str) -> Path:
    return output_dir / Path(route)


def relative_href(current_route: str, target_route: str) -> str:
    current_dir = Path(current_route)
    target_dir = Path(target_route)
    href = os.path.relpath(target_dir.as_posix(), start=current_dir.as_posix())
    if target_route.endswith("/") and href not in {".", "./"} and not href.endswith("/"):
        href += "/"
    return href


def asset_href(current_route: str, asset_name: str) -> str:
    return os.path.relpath((Path("assets") / asset_name).as_posix(), start=Path(current_route).as_posix())


def internal_route_from_href(href: str) -> str | None:
    parsed = parse.urlparse(href)
    if parsed.scheme and parsed.netloc not in {"artificialintelligenceact.eu", "www.artificialintelligenceact.eu"}:
        return None

    segments = [segment for segment in parsed.path.strip("/").split("/") if segment]
    if len(segments) > 1 and re.fullmatch(r"[a-z]{2}", segments[0]):
        segments = segments[1:]

    if len(segments) < 2:
        return None

    content_type = segments[0]
    if content_type not in TYPE_SLUGS:
        return None

    slug = segments[1]
    if not slug.startswith(f"{content_type}-"):
        slug = f"{content_type}-{slug}"

    route = page_route(content_type, slug)
    if parsed.fragment:
        return f"{route}#{parsed.fragment}"
    return route


def normalize_internal_href(current_route: str, href: str) -> str:
    route = internal_route_from_href(href)
    if not route:
        return href

    target_route, _, fragment = route.partition("#")
    normalized = relative_href(current_route, target_route)
    if fragment:
        return f"{normalized}#{fragment}"
    return normalized


def rewrite_internal_links(body_html: str, current_route: str) -> str:
    def replace_href(match: re.Match[str]) -> str:
        original_href = html.unescape(match.group(1))
        normalized_href = normalize_internal_href(current_route, original_href)
        if normalized_href == original_href:
            return match.group(0)
        return f'href="{escape(normalized_href)}"'

    return re.sub(r'href="([^"]+)"', replace_href, body_html)


def load_pages() -> tuple[list[Page], dict[str, object]]:
    manifest = json.loads((MARKDOWN_DB / "manifest.json").read_text(encoding="utf-8"))
    pages: list[Page] = []

    for item in manifest["pages"]:
        disk_path = MARKDOWN_DB / item["output_path"]
        frontmatter, body = read_frontmatter(disk_path)
        body = strip_duplicate_title_heading(body, str(frontmatter["title"]))
        route = page_route(str(frontmatter["content_type"]), str(frontmatter["slug"]))
        body_html = render_markdown(body)
        body_html = rewrite_internal_links(body_html, route)
        excerpt = normalize_whitespace(strip_tags(body_html))[:220].strip()
        if excerpt and len(excerpt) == 220:
            excerpt = excerpt.rstrip(".;, ") + "…"
        pages.append(
            Page(
                title=str(frontmatter["title"]),
                source_url=str(frontmatter["source_url"]),
                slug=str(frontmatter["slug"]),
                content_type=str(frontmatter["content_type"]),
                language=str(frontmatter["language"]),
                output_path=str(item["output_path"]),
                source=str(frontmatter["source"]),
                scraped_at=frontmatter.get("scraped_at"),
                published_at=frontmatter.get("published_at"),
                updated_at=frontmatter.get("updated_at"),
                description=frontmatter.get("description"),
                word_count=int(frontmatter.get("word_count") or 0),
                body_markdown=body,
                body_html=body_html,
                excerpt=excerpt,
                display_number=display_number(frontmatter),
                route=route,
                disk_path=disk_path,
            )
        )

    pages.sort(key=lambda page: (TYPE_ORDER.index(page.content_type), slug_sort_key(page.slug)))
    return pages, manifest


def groups_for_pages(pages: Iterable[Page]) -> dict[str, list[Page]]:
    grouped: dict[str, list[Page]] = defaultdict(list)
    for page in pages:
        grouped[page.content_type].append(page)
    return grouped


def layout(
    *,
    title: str,
    description: str,
    route: str,
    body_html: str,
    nav_html: str,
    base_path: str,
    page_class: str,
) -> str:
    css_href = asset_href(route, "styles.css")
    js_href = asset_href(route, "app.js")
    home_href = relative_href(route, "")
    return f"""<!doctype html>
<html lang="en" data-site-base="{escape(base_path)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <meta name="description" content="{escape(description)}">
  <link rel="stylesheet" href="{escape(css_href)}">
</head>
<body class="{escape(page_class)}">
  <div class="site-shell">
    <aside class="site-sidebar" id="sidebar">
      <div class="sidebar-top">
        <a class="brand" href="{escape(home_href)}">
          <span class="brand-kicker">EU AI Act</span>
          <span class="brand-title">Act Explorer</span>
        </a>
        <button class="sidebar-close" type="button" data-close-sidebar aria-label="Close navigation">×</button>
      </div>
      {nav_html}
    </aside>
    <div class="site-backdrop" data-close-sidebar></div>
    <div class="site-main">
      <header class="site-header">
        <button class="nav-toggle" type="button" data-open-sidebar aria-label="Open navigation">Menu</button>
        <a class="header-home" href="{escape(home_href)}">EU AI Act</a>
        <label class="search-shell" for="site-search">
          <span class="search-label">Search</span>
          <input id="site-search" class="search-input" type="search" placeholder="Search articles, recitals, annexes">
          <div class="search-results" id="search-results" hidden></div>
        </label>
      </header>
      {body_html}
    </div>
  </div>
  <script src="{escape(js_href)}" defer></script>
</body>
</html>
"""


def nav_markup(groups: dict[str, list[Page]], current_route: str) -> str:
    sections = ['<nav class="sidebar-nav" aria-label="EU AI Act content">']
    for content_type in TYPE_ORDER:
        pages = groups[content_type]
        sections.append('<section class="nav-group">')
        sections.append(
            f'<div class="nav-group-header"><a href="{escape(relative_href(current_route, TYPE_SLUGS[content_type] + "/"))}">{TYPE_LABELS[content_type]}</a></div>'
        )
        sections.append('<ul class="nav-list">')
        for page in pages:
            href = relative_href(current_route, page.route)
            active = ' class="is-active"' if page.route == current_route else ""
            sections.append(
                f'<li{active}><a href="{escape(href)}"><span class="nav-number">{escape(page.display_number)}</span><span class="nav-title">{escape(page.title)}</span></a></li>'
            )
        sections.append("</ul>")
        sections.append("</section>")
    sections.append("</nav>")
    return "\n".join(sections)


def page_chrome(page: Page, groups: dict[str, list[Page]]) -> str:
    siblings = groups[page.content_type]
    index = siblings.index(page)
    previous_page = siblings[index - 1] if index > 0 else None
    next_page = siblings[index + 1] if index + 1 < len(siblings) else None
    type_listing_route = f"{TYPE_SLUGS[page.content_type]}/"

    breadcrumb = (
        f'<nav class="breadcrumbs" aria-label="Breadcrumbs">'
        f'<a href="{escape(relative_href(page.route, ""))}">Home</a>'
        f'<span>/</span>'
        f'<a href="{escape(relative_href(page.route, type_listing_route))}">{TYPE_LABELS[page.content_type]}</a>'
        f'<span>/</span>'
        f'<span>{escape(page.title)}</span>'
        f"</nav>"
    )

    metadata = (
        '<div class="page-meta">'
        f'<span>{SINGULAR_LABELS[page.content_type]} {escape(page.display_number)}</span>'
        f'<span>{page.word_count} words</span>'
        f'<a href="{escape(page.source_url)}" target="_blank" rel="noreferrer">Original source</a>'
        "</div>"
    )

    pager_parts = ['<div class="pager">']
    if previous_page:
        pager_parts.append(
            f'<a class="pager-link" href="{escape(relative_href(page.route, previous_page.route))}"><span class="pager-label">Previous</span><span>{escape(previous_page.title)}</span></a>'
        )
    else:
        pager_parts.append('<span class="pager-spacer"></span>')
    if next_page:
        pager_parts.append(
            f'<a class="pager-link pager-link-next" href="{escape(relative_href(page.route, next_page.route))}"><span class="pager-label">Next</span><span>{escape(next_page.title)}</span></a>'
        )
    pager_parts.append("</div>")

    return f"""
<main class="content-layout">
  {breadcrumb}
  <article class="reader-card">
    <header class="reader-header">
      <p class="reader-kicker">{escape(SINGULAR_LABELS[page.content_type])}</p>
      <h1>{escape(page.title)}</h1>
      {metadata}
    </header>
    <div class="reader-body">
      {page.body_html}
    </div>
  </article>
  {''.join(pager_parts)}
</main>
"""


def listing_page(title: str, intro: str, current_route: str, pages: list[Page], groups: dict[str, list[Page]]) -> str:
    cards = []
    for page in pages:
        href = relative_href(current_route, page.route)
        cards.append(
            f"""
<a class="page-card" href="{escape(href)}">
  <div class="page-card-meta">
    <span>{escape(SINGULAR_LABELS[page.content_type])} {escape(page.display_number)}</span>
    <span>{page.word_count} words</span>
  </div>
  <h2>{escape(page.title)}</h2>
  <p>{escape(page.excerpt or "Read the full text.")}</p>
</a>
"""
        )
    return f"""
<main class="content-layout listing-layout">
  <section class="hero-card">
    <p class="eyebrow">Browse the corpus</p>
    <h1>{escape(title)}</h1>
    <p>{escape(intro)}</p>
  </section>
  <section class="cards-grid">
    {''.join(cards)}
  </section>
</main>
"""


def homepage(current_route: str, manifest: dict[str, object], groups: dict[str, list[Page]]) -> str:
    stats = manifest["stats"]["by_content_type"]
    highlights = []
    for content_type in TYPE_ORDER:
        page = groups[content_type][0]
        href = relative_href(current_route, page.route)
        listing_href = relative_href(current_route, f"{TYPE_SLUGS[content_type]}/")
        highlights.append(
            f"""
<section class="homepage-panel">
  <div class="homepage-panel-top">
    <span class="panel-label">{TYPE_LABELS[content_type]}</span>
    <span class="panel-count">{stats[content_type]}</span>
  </div>
  <h2>{escape(page.title)}</h2>
  <p>{escape(page.excerpt or "Open the corpus.")}</p>
  <div class="panel-actions">
    <a class="panel-link" href="{escape(href)}">Open first</a>
    <a class="panel-link panel-link-secondary" href="{escape(listing_href)}">Browse all</a>
  </div>
</section>
"""
        )

    stat_cards = []
    for content_type in TYPE_ORDER:
        href = relative_href(current_route, f"{TYPE_SLUGS[content_type]}/")
        stat_cards.append(
            f'<a class="stat-card" href="{escape(href)}"><span class="stat-number">{stats[content_type]}</span><span class="stat-label">{TYPE_LABELS[content_type]}</span></a>'
        )

    article_href = relative_href(current_route, "articles/")
    return f"""
<main class="content-layout home-layout">
  <section class="hero-card hero-card-home">
    <p class="eyebrow">GitHub Pages edition</p>
    <h1>Read the EU AI Act in a clean, navigable format.</h1>
    <p class="hero-copy">A static explorer for the full English legal corpus: articles, recitals, chapters, sections, and annexes, rendered from the local markdown database.</p>
    <div class="hero-actions">
      <a class="button button-primary" href="{escape(article_href)}">Start with Articles</a>
      <a class="button button-secondary" href="#corpus-stats">See corpus structure</a>
    </div>
  </section>
  <section class="stats-row" id="corpus-stats">
    {''.join(stat_cards)}
  </section>
  <section class="info-strip">
    <div>
      <span class="strip-label">Corpus size</span>
      <strong>{manifest["stats"]["written_pages"]} pages</strong>
    </div>
    <div>
      <span class="strip-label">Language</span>
      <strong>English</strong>
    </div>
    <div>
      <span class="strip-label">Source</span>
      <strong>Static markdown database</strong>
    </div>
  </section>
  <section class="cards-grid cards-grid-home">
    {''.join(highlights)}
  </section>
</main>
"""


def search_index(pages: list[Page], base_path: str) -> list[dict[str, object]]:
    items = []
    for page in pages:
        items.append(
            {
                "title": page.title,
                "content_type": page.content_type,
                "type_label": SINGULAR_LABELS[page.content_type],
                "display_number": page.display_number,
                "excerpt": page.excerpt,
                "url": f"{base_path}/{page.route}".replace("//", "/"),
                "search_text": normalize_whitespace(f"{page.title} {page.content_type} {page.excerpt} {strip_tags(page.body_html)}").lower(),
            }
        )
    return items


def write_page(output_dir: Path, route: str, content: str) -> None:
    destination = page_output_dir(output_dir, route)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "index.html").write_text(content, encoding="utf-8")


def copy_assets(output_dir: Path) -> None:
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for asset in STATIC_DIR.iterdir():
        shutil.copy2(asset, assets_dir / asset.name)


def build(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pages, manifest = load_pages()
    groups = groups_for_pages(pages)
    base_path = site_base_path()
    copy_assets(output_dir)

    home_nav = nav_markup(groups, "")
    write_page(
        output_dir,
        "",
        layout(
            title="EU AI Act Explorer",
            description="A clean GitHub Pages explorer for the EU AI Act legal corpus.",
            route="",
            body_html=homepage("", manifest, groups),
            nav_html=home_nav,
            base_path=base_path,
            page_class="page-home",
        ),
    )

    for content_type in TYPE_ORDER:
        route = f"{TYPE_SLUGS[content_type]}/"
        nav_html = nav_markup(groups, route)
        write_page(
            output_dir,
            route,
            layout(
                title=f"{TYPE_LABELS[content_type]} | EU AI Act Explorer",
                description=f"Browse all {TYPE_LABELS[content_type].lower()} in the EU AI Act corpus.",
                route=route,
                body_html=listing_page(
                    TYPE_LABELS[content_type],
                    f"Browse all {TYPE_LABELS[content_type].lower()} in numerical order.",
                    route,
                    groups[content_type],
                    groups,
                ),
                nav_html=nav_html,
                base_path=base_path,
                page_class="page-listing",
            ),
        )

    for page in pages:
        nav_html = nav_markup(groups, page.route)
        write_page(
            output_dir,
            page.route,
            layout(
                title=f"{page.title} | EU AI Act Explorer",
                description=page.excerpt or f"Read {page.title} in the EU AI Act Explorer.",
                route=page.route,
                body_html=page_chrome(page, groups),
                nav_html=nav_html,
                base_path=base_path,
                page_class="page-reader",
            ),
        )

    not_found = layout(
        title="Not Found | EU AI Act Explorer",
        description="The requested EU AI Act page could not be found.",
        route="",
        body_html="""
<main class="content-layout">
  <section class="hero-card">
    <p class="eyebrow">404</p>
    <h1>That page does not exist.</h1>
    <p>The requested route could not be resolved. Use the explorer navigation or return to the homepage.</p>
    <div class="hero-actions">
      <a class="button button-primary" href="./">Return home</a>
      <a class="button button-secondary" href="./articles/">Browse articles</a>
    </div>
  </section>
</main>
""",
        nav_html=nav_markup(groups, ""),
        base_path=base_path,
        page_class="page-reader",
    )
    (output_dir / "404.html").write_text(not_found, encoding="utf-8")

    (output_dir / "search-index.json").write_text(
        json.dumps(search_index(pages, base_path), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "site-manifest.json").write_text(
        json.dumps(
            {
                "title": "EU AI Act Explorer",
                "base_path": base_path,
                "stats": manifest["stats"],
                "types": {content_type: len(groups[content_type]) for content_type in TYPE_ORDER},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the GitHub Pages EU AI Act explorer.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help="Directory for generated site output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build(args.output_dir.resolve())
    print(f"Built GitHub Pages site at {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
