#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib import error, parse, request
import xml.etree.ElementTree as ET


LOGGER = logging.getLogger("eu_ai_act_scraper")
USER_AGENT = "eu-ai-act-markdown-scraper/1.0 (+https://artificialintelligenceact.eu)"
DEFAULT_SITEMAP = "https://artificialintelligenceact.eu/wp-sitemap.xml"
DEFAULT_OUTPUT_DIR = "markdown_db"
CONTENT_TYPES = {"article", "recital", "annex", "chapter", "section", "page", "post"}
SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "iframe", "form"}
BLOCK_TAGS = {
    "article",
    "blockquote",
    "div",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tr",
    "ul",
}
VOID_TAGS = {"br", "hr", "img", "meta", "link", "input"}


class ScraperError(Exception):
    pass


@dataclass
class ScrapeErrorRecord:
    url: str
    stage: str
    error_type: str
    message: str
    timestamp: str


@dataclass
class PageRecord:
    url: str
    title: str
    slug: str
    content_type: str
    language: str
    output_path: str
    source: str
    scraped_at: str
    published_at: str | None = None
    updated_at: str | None = None
    description: str | None = None
    word_count: int = 0


class HtmlNode:
    def __init__(self, tag: str, attrs: dict[str, str] | None = None, parent: "HtmlNode | None" = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.parent = parent
        self.children: list[HtmlNode | str] = []

    def add_child(self, child: "HtmlNode | str") -> None:
        self.children.append(child)

    def attr(self, key: str) -> str:
        return self.attrs.get(key, "")

    def classes(self) -> set[str]:
        return {item for item in self.attr("class").split() if item}


class HtmlTreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_attrs = {key.lower(): value or "" for key, value in attrs}
        node = HtmlNode(tag.lower(), normalized_attrs, self.stack[-1])
        self.stack[-1].add_child(node)
        if tag.lower() not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == lowered:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].add_child(data)

    def handle_comment(self, data: str) -> None:
        return


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def safe_slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower())
    return value.strip("-_") or "untitled"


def collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def text_content(node: HtmlNode | str) -> str:
    if isinstance(node, str):
        return collapse_whitespace(node)
    parts: list[str] = []
    for child in node.children:
        child_text = text_content(child)
        if child_text:
            parts.append(child_text)
    return collapse_whitespace(" ".join(parts))


def walk_nodes(node: HtmlNode) -> Iterable[HtmlNode]:
    yield node
    for child in node.children:
        if isinstance(child, HtmlNode):
            yield from walk_nodes(child)


def find_first(node: HtmlNode, predicate) -> HtmlNode | None:
    for child in walk_nodes(node):
        if predicate(child):
            return child
    return None


def find_all(node: HtmlNode, predicate) -> list[HtmlNode]:
    return [child for child in walk_nodes(node) if predicate(child)]


def node_score(node: HtmlNode) -> int:
    attrs_blob = " ".join([node.attr("id"), node.attr("class"), node.attr("role"), node.attr("itemprop")]).lower()
    text_len = len(text_content(node))
    score = min(text_len // 200, 30)

    if node.tag in {"main", "article"}:
        score += 40
    if "main" in attrs_blob or "content" in attrs_blob or "entry" in attrs_blob or "post" in attrs_blob:
        score += 20
    if "sidebar" in attrs_blob or "menu" in attrs_blob or "nav" in attrs_blob or "footer" in attrs_blob:
        score -= 30

    paragraphs = len(find_all(node, lambda item: item.tag == "p" and len(text_content(item)) > 40))
    headings = len(find_all(node, lambda item: item.tag in {"h1", "h2", "h3"}))
    score += paragraphs * 2
    score += headings
    return score


def select_content_root(root: HtmlNode) -> HtmlNode:
    candidates = []
    for node in walk_nodes(root):
        if node.tag in SKIP_TAGS:
            continue
        if node.tag in {"main", "article", "section", "div"}:
            candidates.append(node)

    if not candidates:
        return root

    best = max(candidates, key=node_score)
    return best if node_score(best) > 0 else root


def render_inline(node: HtmlNode | str) -> str:
    if isinstance(node, str):
        return collapse_whitespace(node)

    if node.tag in SKIP_TAGS:
        return ""

    if node.tag == "br":
        return "  \n"
    if node.tag == "code":
        value = collapse_whitespace("".join(render_inline(child) for child in node.children))
        return f"`{value}`" if value else ""
    if node.tag in {"strong", "b"}:
        value = collapse_whitespace("".join(render_inline(child) for child in node.children))
        return f"**{value}**" if value else ""
    if node.tag in {"em", "i"}:
        value = collapse_whitespace("".join(render_inline(child) for child in node.children))
        return f"*{value}*" if value else ""
    if node.tag == "a":
        href = node.attr("href").strip()
        label = collapse_whitespace("".join(render_inline(child) for child in node.children)) or href
        if href:
            return f"[{label}]({href})"
        return label

    parts = [render_inline(child) for child in node.children]
    return collapse_whitespace(" ".join(part for part in parts if part))


def render_table(node: HtmlNode) -> str:
    rows = []
    for tr in find_all(node, lambda item: item.tag == "tr"):
        cells = [collapse_whitespace(render_inline(cell)) for cell in tr.children if isinstance(cell, HtmlNode) and cell.tag in {"th", "td"}]
        if cells:
            rows.append(cells)

    if not rows:
        return ""

    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    separator = ["---"] * width
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in normalized[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_blocks(node: HtmlNode | str, indent: int = 0) -> list[str]:
    if isinstance(node, str):
        text = collapse_whitespace(node)
        return [text] if text else []

    if node.tag in SKIP_TAGS:
        return []

    if node.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(node.tag[1])
        text = collapse_whitespace("".join(render_inline(child) for child in node.children))
        return [("#" * level) + f" {text}"] if text else []

    if node.tag == "p":
        text = collapse_whitespace("".join(render_inline(child) for child in node.children))
        return [text] if text else []

    if node.tag == "blockquote":
        lines = []
        for child in node.children:
            for line in render_blocks(child, indent=indent):
                if line:
                    lines.append(f"> {line}")
        return lines

    if node.tag == "pre":
        content = "".join(text_content(child) if isinstance(child, HtmlNode) else child for child in node.children).strip("\n")
        return [f"```\n{content}\n```"] if content else []

    if node.tag == "ul":
        lines: list[str] = []
        for li in [child for child in node.children if isinstance(child, HtmlNode) and child.tag == "li"]:
            item_text = collapse_whitespace(" ".join(render_inline(grandchild) for grandchild in li.children))
            if item_text:
                lines.append(("  " * indent) + f"- {item_text}")
        return lines

    if node.tag == "ol":
        lines = []
        index = 1
        for li in [child for child in node.children if isinstance(child, HtmlNode) and child.tag == "li"]:
            item_text = collapse_whitespace(" ".join(render_inline(grandchild) for grandchild in li.children))
            if item_text:
                lines.append(("  " * indent) + f"{index}. {item_text}")
                index += 1
        return lines

    if node.tag == "table":
        table = render_table(node)
        return [table] if table else []

    if node.tag == "hr":
        return ["---"]

    lines: list[str] = []
    for child in node.children:
        child_lines = render_blocks(child, indent=indent + 1 if node.tag in {"ul", "ol"} else indent)
        lines.extend(child_lines)
    return lines


def clean_markdown(lines: list[str]) -> str:
    cleaned: list[str] = []
    previous_blank = False
    for line in lines:
        value = line.strip()
        is_blank = not value
        if is_blank and previous_blank:
            continue
        cleaned.append(value)
        previous_blank = is_blank
    return "\n\n".join(cleaned).strip()


def normalize_body_markdown(title: str, content_type: str, body_markdown: str) -> str:
    title_heading = f"# {title}"
    marker = f"\n{title_heading}\n"

    if "## Table of contents" in body_markdown and marker in body_markdown:
        body_markdown = body_markdown.split(marker, 1)[1].strip()

    if body_markdown.startswith("[←Back to index]"):
        parts = body_markdown.split("\n\n", 1)
        body_markdown = parts[1].strip() if len(parts) == 2 else ""

    if body_markdown.startswith(title_heading):
        body_markdown = body_markdown[len(title_heading):].strip()

    note_prefix = "**NOTE:**"
    if body_markdown.startswith(note_prefix):
        parts = body_markdown.split("\n\n", 1)
        body_markdown = parts[1].strip() if len(parts) == 2 else ""

    if content_type == "article":
        article_start = re.search(r"(?m)^1\.\s", body_markdown)
        if article_start:
            body_markdown = body_markdown[article_start.start():].strip()

    footer_markers = [
        "\n\nNext\n",
        "\n\nSuitable Recitals",
        "\n\n**Feedback**",
        "\n\n## Receive EU AI Act updates",
        "\n\n© Future of Life Institute",
        "\n\n←\n",
        "\n\nPrevious\n",
    ]
    footer_positions = [body_markdown.find(marker) for marker in footer_markers if marker in body_markdown]
    if footer_positions:
        body_markdown = body_markdown[: min(footer_positions)].strip()

    return body_markdown.strip()


def parse_html_document(html: str) -> HtmlNode:
    parser = HtmlTreeBuilder()
    parser.feed(html)
    parser.close()
    return parser.root


def extract_title(root: HtmlNode) -> str:
    for selector in [
        lambda node: node.tag == "meta" and node.attr("property") == "og:title",
        lambda node: node.tag == "meta" and node.attr("name") == "twitter:title",
        lambda node: node.tag == "title",
        lambda node: node.tag == "h1",
    ]:
        match = find_first(root, selector)
        if not match:
            continue
        if match.tag == "meta":
            title = collapse_whitespace(match.attr("content"))
        else:
            title = text_content(match)
        if title:
            return re.sub(r"\s*\|\s*EU Artificial Intelligence Act\s*$", "", title).strip()
    return "Untitled"


def extract_meta_content(root: HtmlNode, attr_key: str, attr_value: str) -> str | None:
    match = find_first(root, lambda node: node.tag == "meta" and node.attr(attr_key) == attr_value)
    if match:
        content = collapse_whitespace(match.attr("content"))
        return content or None
    return None


def extract_datetime(root: HtmlNode) -> tuple[str | None, str | None]:
    published = extract_meta_content(root, "property", "article:published_time")
    updated = extract_meta_content(root, "property", "article:modified_time")
    if published or updated:
        return published, updated

    time_nodes = find_all(root, lambda node: node.tag == "time" and node.attr("datetime"))
    values = [collapse_whitespace(node.attr("datetime")) for node in time_nodes if collapse_whitespace(node.attr("datetime"))]
    if not values:
        return None, None
    return values[0], values[1] if len(values) > 1 else values[0]


def classify_url(url: str) -> tuple[str | None, str, str]:
    parsed = parse.urlparse(url)
    segments = [segment for segment in parsed.path.strip("/").split("/") if segment]

    if not segments:
        return "page", "en", "home"

    language = "en"
    if len(segments) > 1 and re.fullmatch(r"[a-z]{2}", segments[0]):
        language = segments[0]
        segments = segments[1:]

    first = segments[0]
    if first in {"wp-sitemap.xml", "feed"} or first.startswith("wp-sitemap"):
        return None, language, safe_slug(parsed.path)

    content_type = "page"
    if first in CONTENT_TYPES:
        content_type = first
    elif first == "article":
        content_type = "article"
    elif first == "recital":
        content_type = "recital"
    elif first == "annex":
        content_type = "annex"
    elif first == "chapter":
        content_type = "chapter"
    elif first == "section":
        content_type = "section"
    elif first in {"category", "tag", "author"}:
        return None, language, safe_slug(parsed.path)
    else:
        content_type = "page"

    slug = safe_slug("-".join(segments))
    return content_type, language, slug


def should_keep_url(url: str, include_types: set[str], include_all_languages: bool) -> bool:
    content_type, language, _ = classify_url(url)
    if not content_type:
        return False
    if content_type not in include_types:
        return False
    if not include_all_languages and language != "en":
        return False
    return True


def fetch_text(url: str, timeout: float, retries: int, backoff_seconds: float) -> str:
    last_error: Exception | None = None
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(1, retries + 1):
        try:
            req = request.Request(url, headers=headers)
            with request.urlopen(req, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read()
                return body.decode(charset, errors="replace")
        except (error.HTTPError, error.URLError, TimeoutError, UnicodeDecodeError, OSError) as exc:
            last_error = exc
            LOGGER.warning("Fetch attempt %s/%s failed for %s: %s", attempt, retries, url, exc)
            if attempt < retries:
                time.sleep(backoff_seconds * attempt)

    raise ScraperError(f"Failed to fetch {url}: {last_error}")


def parse_sitemap(xml_text: str) -> tuple[list[str], list[str]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ScraperError(f"Invalid sitemap XML: {exc}") from exc

    tag_name = root.tag.split("}")[-1]
    if tag_name == "sitemapindex":
        nested = [element.text.strip() for element in root.findall(".//{*}loc") if element.text]
        return nested, []
    if tag_name == "urlset":
        urls = [element.text.strip() for element in root.findall(".//{*}loc") if element.text]
        return [], urls

    raise ScraperError(f"Unsupported sitemap root tag: {root.tag}")


def discover_urls(sitemap_url: str, timeout: float, retries: int, backoff_seconds: float) -> tuple[set[str], list[ScrapeErrorRecord]]:
    to_visit = [sitemap_url]
    visited: set[str] = set()
    page_urls: set[str] = set()
    errors: list[ScrapeErrorRecord] = []

    while to_visit:
        current = to_visit.pop()
        if current in visited:
            continue

        visited.add(current)
        try:
            xml_text = fetch_text(current, timeout=timeout, retries=retries, backoff_seconds=backoff_seconds)
            nested_sitemaps, urls = parse_sitemap(xml_text)
            to_visit.extend(nested_sitemaps)
            page_urls.update(urls)
        except Exception as exc:
            errors.append(
                ScrapeErrorRecord(
                    url=current,
                    stage="discover",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    timestamp=utc_now(),
                )
            )

    return page_urls, errors


def yaml_line(key: str, value: str | int | None) -> str:
    if value is None:
        return f"{key}: null"
    return f"{key}: {json.dumps(value, ensure_ascii=False)}"


def markdown_frontmatter(record: PageRecord) -> str:
    lines = [
        "---",
        yaml_line("title", record.title),
        yaml_line("source_url", record.url),
        yaml_line("slug", record.slug),
        yaml_line("content_type", record.content_type),
        yaml_line("language", record.language),
        yaml_line("source", record.source),
        yaml_line("scraped_at", record.scraped_at),
        yaml_line("published_at", record.published_at),
        yaml_line("updated_at", record.updated_at),
        yaml_line("description", record.description),
        yaml_line("word_count", record.word_count),
        "---",
        "",
    ]
    return "\n".join(lines)


def build_markdown(title: str, description: str | None, body_markdown: str) -> str:
    lines = [f"# {title}", ""]
    if description:
        lines.extend([description, ""])
    lines.append(body_markdown.strip())
    lines.append("")
    return "\n".join(lines)


def scrape_page(url: str, timeout: float, retries: int, backoff_seconds: float, source: str) -> tuple[PageRecord, str]:
    html = fetch_text(url, timeout=timeout, retries=retries, backoff_seconds=backoff_seconds)
    root = parse_html_document(html)
    content_root = select_content_root(root)

    title = extract_title(root)
    description = extract_meta_content(root, "name", "description")
    published_at, updated_at = extract_datetime(root)
    content_type, language, slug = classify_url(url)
    if not content_type:
        raise ScraperError(f"URL is not a supported content page: {url}")

    lines = render_blocks(content_root)
    body_markdown = normalize_body_markdown(title, content_type, clean_markdown(lines))
    if not body_markdown:
        raise ScraperError(f"Empty extracted markdown body for {url}")

    word_count = len(body_markdown.split())
    record = PageRecord(
        url=url,
        title=title,
        slug=slug,
        content_type=content_type,
        language=language,
        output_path="",
        source=source,
        scraped_at=utc_now(),
        published_at=published_at,
        updated_at=updated_at,
        description=description,
        word_count=word_count,
    )
    return record, body_markdown


def write_text_file(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(contents, encoding="utf-8")
    except OSError as exc:
        raise ScraperError(f"Failed to write {path}: {exc}") from exc


def write_json_file(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ScraperError(f"Failed to write {path}: {exc}") from exc


def load_urls_from_json(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScraperError(f"Failed to read JSON seed file {path}: {exc}") from exc

    links = payload.get("links")
    if not isinstance(links, list):
        raise ScraperError(f"Seed JSON file {path} does not contain a top-level 'links' array")

    urls: list[str] = []
    for entry in links:
        if isinstance(entry, dict) and isinstance(entry.get("url"), str):
            urls.append(entry["url"])
    return urls


def load_urls_from_file(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ScraperError(f"Failed to read URL file {path}: {exc}") from exc

    urls = []
    for line in lines:
        value = line.strip()
        if value and not value.startswith("#"):
            urls.append(value)
    return urls


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape EU AI Act content into a markdown database.")
    parser.add_argument("--sitemap-url", default=DEFAULT_SITEMAP, help="Root sitemap URL to crawl.")
    parser.add_argument("--seed-json", type=Path, help="Optional JSON file with a top-level 'links' array.")
    parser.add_argument("--url-file", type=Path, help="Optional text file with one URL per line.")
    parser.add_argument("--output-dir", type=Path, default=Path(DEFAULT_OUTPUT_DIR), help="Directory to write markdown database into.")
    parser.add_argument(
        "--include",
        nargs="+",
        default=["article"],
        choices=sorted(CONTENT_TYPES),
        help="Content types to scrape. Defaults to article.",
    )
    parser.add_argument(
        "--all-languages",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Include translated paths like /fr/article/25. Defaults to English-only.",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="Per-request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=3, help="Number of HTTP retries per request.")
    parser.add_argument("--backoff-seconds", type=float, default=1.5, help="Backoff multiplier between retries.")
    parser.add_argument("--limit", type=int, help="Optional page limit for test runs.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    parser.add_argument(
        "--skip-sitemap",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Skip sitemap discovery and use only URLs provided via --seed-json or --url-file.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    include_types = set(args.include)
    output_dir: Path = args.output_dir.resolve()
    manifest_path = output_dir / "manifest.json"
    errors_path = output_dir / "errors.json"

    discovered_urls: set[str] = set()
    errors: list[ScrapeErrorRecord] = []
    if not args.skip_sitemap:
        LOGGER.info("Discovering URLs from %s", args.sitemap_url)
        discovered_urls, errors = discover_urls(
            sitemap_url=args.sitemap_url,
            timeout=args.timeout,
            retries=args.retries,
            backoff_seconds=args.backoff_seconds,
        )

    sources: dict[str, str] = {url: "sitemap" for url in discovered_urls}

    if args.seed_json:
        LOGGER.info("Loading additional URLs from %s", args.seed_json)
        for url in load_urls_from_json(args.seed_json):
            sources.setdefault(url, "seed_json")

    if args.url_file:
        LOGGER.info("Loading additional URLs from %s", args.url_file)
        for url in load_urls_from_file(args.url_file):
            sources.setdefault(url, "url_file")

    candidate_urls = sorted(
        url
        for url in sources
        if should_keep_url(url, include_types=include_types, include_all_languages=args.all_languages)
    )

    if args.limit is not None:
        candidate_urls = candidate_urls[: args.limit]

    LOGGER.info("Selected %s candidate URLs", len(candidate_urls))

    records: list[PageRecord] = []
    for index, url in enumerate(candidate_urls, start=1):
        LOGGER.info("Scraping %s/%s: %s", index, len(candidate_urls), url)
        try:
            record, body_markdown = scrape_page(
                url,
                timeout=args.timeout,
                retries=args.retries,
                backoff_seconds=args.backoff_seconds,
                source=sources[url],
            )
            relative_path = Path(record.language) / record.content_type / f"{record.slug}.md"
            destination = output_dir / relative_path
            record.output_path = str(relative_path)
            markdown = markdown_frontmatter(record) + build_markdown(record.title, record.description, body_markdown)
            write_text_file(destination, markdown)
            records.append(record)
        except Exception as exc:
            LOGGER.error("Failed to scrape %s: %s", url, exc)
            errors.append(
                ScrapeErrorRecord(
                    url=url,
                    stage="scrape",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    timestamp=utc_now(),
                )
            )

    manifest = {
        "generated_at": utc_now(),
        "output_dir": str(output_dir),
        "filters": {
            "include": sorted(include_types),
            "all_languages": args.all_languages,
            "limit": args.limit,
        },
        "stats": {
            "discovered_urls": len(discovered_urls),
            "selected_urls": len(candidate_urls),
            "written_pages": len(records),
            "error_count": len(errors),
        },
        "pages": [asdict(record) for record in records],
    }

    write_json_file(manifest_path, manifest)
    write_json_file(errors_path, [asdict(item) for item in errors])

    LOGGER.info("Wrote %s pages to %s", len(records), output_dir)
    LOGGER.info("Manifest: %s", manifest_path)
    LOGGER.info("Errors: %s", errors_path)
    return 0 if records else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
