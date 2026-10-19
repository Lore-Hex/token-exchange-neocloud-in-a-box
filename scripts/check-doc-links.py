#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate local Markdown targets, offline; remote links are source citations."""

from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt


def targets(text):
    for token in MarkdownIt().parse(text):
        for child in token.children or []:
            if child.type == "link_open":
                yield child.attrGet("href")
            elif child.type == "image":
                yield child.attrGet("src")


def anchors(text):
    result = set()
    counts = {}
    tokens = MarkdownIt().parse(text)
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open":
            continue
        heading = tokens[index + 1]
        plain = "".join(t.content for t in heading.children or [] if t.type in ("text", "code_inline"))
        slug = re.sub(r"[^\w\- ]", "", plain.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(f"{slug}-{count}" if count else slug)
    return result


def check_link(root, source, target):
    url = urlsplit(target)
    if url.scheme or url.netloc:
        return None
    path = (source.parent / unquote(url.path)).resolve() if url.path else source.resolve()
    if not path.is_relative_to(root.resolve()):
        return "target escapes the repository"
    if not path.exists():
        return "target does not exist"
    if url.fragment and path.suffix == ".md" and unquote(url.fragment) not in anchors(path.read_text()):
        return "heading does not exist"
    return None


def main():
    root = Path(__file__).resolve().parent.parent
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "*.md"], cwd=root
    ).decode().split("\0")
    failures = []
    for name in sorted(set(files) - {""}):
        source = root / name
        for target in targets(source.read_text()):
            if error := check_link(root, source, target):
                failures.append(f"{name}: {target}: {error}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("Local Markdown paths and heading links passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
