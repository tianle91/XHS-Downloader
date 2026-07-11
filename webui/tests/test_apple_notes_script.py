"""Tests for the Apple Notes link-extraction script.

Stdlib only — run from the repository root with::

    uv run python -m unittest discover webui/tests

``scripts/apple_notes_xhslinks.sh`` reads Apple Notes on a Mac (via AppleScript)
and prints the ``xhslink.com`` short links they contain. The AppleScript half
needs a real Mac and cannot run here, but the link-extraction pipeline — the
only part carrying logic — is exposed through the script's ``--extract`` mode,
which filters **stdin** and touches nothing macOS-specific. These tests pipe
sample note HTML through it and pin the matching behaviour down.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apple_notes_xhslinks.sh"


@unittest.skipUnless(shutil.which("bash"), "bash is required to run the script")
class ExtractLinksTest(unittest.TestCase):
    """Exercise ``apple_notes_xhslinks.sh --extract`` as a text filter."""

    def extract(self, text: str) -> list[str]:
        """Run the script's --extract filter over ``text`` and return the links."""
        result = subprocess.run(
            ["bash", str(SCRIPT), "--extract"],
            input=text,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"non-zero exit; stderr={result.stderr!r}",
        )
        self.assertEqual(result.stderr, "", msg="--extract must not write to stderr")
        return result.stdout.splitlines()

    def test_plain_text_link(self) -> None:
        self.assertEqual(
            self.extract("check this http://xhslink.com/o/6RRY1UzhcbG out"),
            ["http://xhslink.com/o/6RRY1UzhcbG"],
        )

    def test_link_inside_href_attribute(self) -> None:
        # Note bodies are HTML; the link lives in the href, not the visible text.
        html = '<div><a href="http://xhslink.com/o/6RRY1UzhcbG">看笔记</a></div>'
        self.assertEqual(self.extract(html), ["http://xhslink.com/o/6RRY1UzhcbG"])

    def test_https_is_matched_too(self) -> None:
        self.assertEqual(
            self.extract("https://xhslink.com/a/AbCd123"),
            ["https://xhslink.com/a/AbCd123"],
        )

    def test_trailing_punctuation_is_stripped(self) -> None:
        for suffix in (".", ",", ";", ":", ")"):
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    self.extract(f"link http://xhslink.com/o/6RRY1UzhcbG{suffix}"),
                    ["http://xhslink.com/o/6RRY1UzhcbG"],
                )

    def test_cjk_punctuation_terminates_the_link(self) -> None:
        # A plain-text link glued to Chinese text must stop at the CJK
        # punctuation, matching the engine's terminator set — not swallow it.
        self.assertEqual(
            self.extract("看这个 http://xhslink.com/o/6RRY1UzhcbG，很好看。"),
            ["http://xhslink.com/o/6RRY1UzhcbG"],
        )
        self.assertEqual(
            self.extract("【http://xhslink.com/o/6RRY1UzhcbG】"),
            ["http://xhslink.com/o/6RRY1UzhcbG"],
        )

    def test_duplicates_are_removed_keeping_first_seen_order(self) -> None:
        text = (
            'first <a href="http://xhslink.com/o/AAA">a</a> '
            "then http://xhslink.com/o/BBB "
            "and http://xhslink.com/o/AAA again"
        )
        self.assertEqual(
            self.extract(text),
            ["http://xhslink.com/o/AAA", "http://xhslink.com/o/BBB"],
        )

    def test_non_xhslink_urls_are_ignored(self) -> None:
        text = (
            "https://www.xiaohongshu.com/explore/65a1b2c3 "
            "https://example.com/xhslink.com/o/nope "  # host is example.com
            "http://xhslink.com/o/KEEP"
        )
        self.assertEqual(self.extract(text), ["http://xhslink.com/o/KEEP"])

    def test_multiple_links_across_lines(self) -> None:
        text = "http://xhslink.com/o/AAA\nsome prose\nhttp://xhslink.com/o/BBB\n"
        self.assertEqual(
            self.extract(text),
            ["http://xhslink.com/o/AAA", "http://xhslink.com/o/BBB"],
        )

    def test_empty_input_yields_no_output(self) -> None:
        self.assertEqual(self.extract(""), [])

    def test_text_without_links_yields_no_output(self) -> None:
        self.assertEqual(self.extract("just a note with no links at all"), [])


if __name__ == "__main__":
    unittest.main()
