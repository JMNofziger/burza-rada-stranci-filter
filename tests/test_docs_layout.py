from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DocsLayoutTests(unittest.TestCase):
    def test_readme_is_end_user_and_short(self):
        readme = (ROOT / "README.md").read_text()
        self.assertLessEqual(len(readme.splitlines()), 40)
        self.assertIn("jmnofziger.github.io/burza-rada-stranci-filter", readme)
        self.assertIn("product/METHOD.md", readme)
        self.assertIn("product/OPERATIONS.md", readme)
        self.assertNotIn("workflow_dispatch", readme)
        self.assertNotIn("detail_batch_size", readme)
        self.assertNotIn("btnTrazilica", readme)

    def test_product_docs_are_not_on_pages(self):
        self.assertTrue((ROOT / "product" / "METHOD.md").is_file())
        self.assertTrue((ROOT / "product" / "OPERATIONS.md").is_file())
        self.assertTrue((ROOT / "product" / "README.md").is_file())
        self.assertFalse((ROOT / "docs" / "METHOD.md").exists())
        self.assertFalse((ROOT / "docs" / "OPERATIONS.md").exists())
        self.assertFalse((ROOT / "docs" / "method.html").exists())
        pages = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
        self.assertIn("path: docs", pages)
        self.assertNotIn("path: product", pages)
