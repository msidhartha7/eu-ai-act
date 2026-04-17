import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_github_pages.py"


spec = importlib.util.spec_from_file_location("build_github_pages", MODULE_PATH)
build_github_pages = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = build_github_pages
spec.loader.exec_module(build_github_pages)


class BuildGithubPagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pages, _ = build_github_pages.load_pages()
        cls.pages_by_slug = {page.slug: page for page in cls.pages}

    def test_rewrites_root_relative_recital_links_in_markdown_body(self) -> None:
        article_6 = self.pages_by_slug["article-6"]

        self.assertIn('href="../../recitals/recital-47/"', article_6.body_html)
        self.assertIn('href="../../recitals/recital-50/"', article_6.body_html)
        self.assertIn('href="../../recitals/recital-51/"', article_6.body_html)

    def test_rewrites_absolute_internal_links_in_markdown_body(self) -> None:
        article_6 = self.pages_by_slug["article-6"]
        article_3 = self.pages_by_slug["article-3"]

        self.assertIn('href="../../annexes/annex-3/"', article_6.body_html)
        self.assertIn('href="../article-49/"', article_6.body_html)
        self.assertIn('href="../../chapters/chapter-3/"', article_3.body_html)


if __name__ == "__main__":
    unittest.main()
