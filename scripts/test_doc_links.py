# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("links", Path(__file__).with_name("check-doc-links.py"))
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)


class LinkTests(unittest.TestCase):
    def test_parser_ignores_code_and_supports_reference_links(self):
        text = "[ok][target]\n\n[target]: docs/a.md\n\n`[not](missing.md)`\n\n```\n[x](bad)\n```"
        self.assertEqual(list(links.targets(text)), ["docs/a.md"])

    def test_missing_traversal_and_heading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "README.md"
            source.write_text("# A heading\n# A heading\n")
            self.assertIsNone(links.check_link(root, source, "#a-heading"))
            self.assertIsNone(links.check_link(root, source, "#a-heading-1"))
            self.assertIsNotNone(links.check_link(root, source, "#missing"))
            self.assertIsNotNone(links.check_link(root, source, "missing.md"))
            self.assertIsNotNone(links.check_link(root, source, "../outside"))
            self.assertIsNone(links.check_link(root, source, "https://example.com/document"))
