"""Tests for the Web UI's request model.

Stdlib only — run from the repository root with::

    uv run python -m unittest discover webui/tests

``BatchOptions`` is the boundary between the browser and the engine: it decides
which options are accepted and how they are translated into ``XHS(...)`` keyword
arguments. These tests pin that translation down.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from pydantic import ValidationError

from webui.app import (
    DATE_FORMATS,
    DEFAULT_DATE_FORMAT,
    FOLDER_NAME_LENGTH,
    NAME_FIELDS,
    BatchOptions,
    folder_for_link,
)


class NameFieldsTest(unittest.TestCase):
    def test_defaults_to_publish_time_author_title(self) -> None:
        options = BatchOptions(links="x")
        self.assertEqual(options.name_fields, ["publish_time", "author", "title"])
        self.assertEqual(options.name_format(), "发布时间 作者昵称 作品标题")

    def test_maps_ui_ids_to_engine_tokens_in_click_order(self) -> None:
        options = BatchOptions(links="x", name_fields=["title", "likes", "publish_time"])
        self.assertEqual(options.name_format(), "作品标题 点赞数量 发布时间")

    def test_every_ui_id_maps_to_a_token(self) -> None:
        options = BatchOptions(links="x", name_fields=list(NAME_FIELDS))
        self.assertEqual(options.name_format().split(), list(NAME_FIELDS.values()))

    def test_rejects_an_empty_selection(self) -> None:
        # The UI disables Start instead of sending this, but an API client can.
        with self.assertRaises(ValidationError) as caught:
            BatchOptions(links="x", name_fields=[])
        self.assertIn("at least one", str(caught.exception))

    def test_rejects_unknown_fields_rather_than_dropping_them(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            BatchOptions(links="x", name_fields=["title", "no_such_field"])
        self.assertIn("no_such_field", str(caught.exception))


class DateFormatTest(unittest.TestCase):
    def test_default_matches_the_engine(self) -> None:
        options = BatchOptions(links="x")
        self.assertEqual(options.date_format, DEFAULT_DATE_FORMAT)
        self.assertEqual(options.time_format(), "%Y-%m-%d_%H:%M:%S")

    def test_preset_resolves_to_a_strftime_pattern(self) -> None:
        options = BatchOptions(links="x", date_format="date_compact")
        self.assertEqual(options.time_format(), "%Y%m%d")

    def test_rejects_a_raw_strftime_pattern(self) -> None:
        # Only presets are accepted: the rendered value lands in a file name, so
        # a pattern such as %Y/%m/%d must not be able to inject a path separator.
        with self.assertRaises(ValidationError):
            BatchOptions(links="x", date_format="%Y/%m/%d")

    def test_no_preset_can_produce_a_path_separator(self) -> None:
        for name, pattern in DATE_FORMATS.items():
            with self.subTest(preset=name):
                self.assertNotIn("/", pattern)
                self.assertNotIn("\\", pattern)


class EngineKwargsTest(unittest.TestCase):
    def test_isolation_policy_is_hard_coded(self) -> None:
        kwargs = BatchOptions(links="x").engine_kwargs(Path("/tmp/work"))
        self.assertFalse(kwargs["download_record"])  # never skip already-seen works
        self.assertFalse(kwargs["record_data"])  # never write the metadata DB
        self.assertFalse(kwargs["script_server"])
        self.assertEqual(kwargs["work_path"], "/tmp/work")

    def test_time_format_is_not_an_engine_kwarg(self) -> None:
        # It is applied to xhs.explore at run time instead; see _run_job.
        self.assertNotIn("time_format", BatchOptions(links="x").engine_kwargs(Path("/tmp/work")))

    def test_falls_back_on_an_unusable_image_format(self) -> None:
        kwargs = BatchOptions(links="x", image_format="gif").engine_kwargs(Path("/tmp/work"))
        self.assertEqual(kwargs["image_format"], "JPEG")

    def test_upper_cases_a_valid_image_format(self) -> None:
        kwargs = BatchOptions(links="x", image_format="webp").engine_kwargs(Path("/tmp/work"))
        self.assertEqual(kwargs["image_format"], "WEBP")

    def test_author_archive_is_off(self) -> None:
        # Each link already has its own folder; grouping by author inside it
        # would only add a redundant level.
        self.assertFalse(BatchOptions(links="x").engine_kwargs(Path("/tmp/work"))["author_archive"])

    def test_engine_folder_is_not_the_download_folder(self) -> None:
        # The engine's folder holds ExploreData.db and lives in a temp dir;
        # media is redirected per link via xhs.download.folder. See _run_job.
        kwargs = BatchOptions(links="x").engine_kwargs(Path("/tmp/work"))
        self.assertEqual(kwargs["folder_name"], "engine")

    def test_blank_proxy_becomes_none(self) -> None:
        kwargs = BatchOptions(links="x", proxy="  ").engine_kwargs(Path("/tmp/work"))
        self.assertIsNone(kwargs["proxy"])


class FolderForLinkTest(unittest.TestCase):
    WORK = "https://www.xiaohongshu.com/explore/65a1b2c3"

    def test_derives_the_folder_from_the_link(self) -> None:
        self.assertEqual(folder_for_link(self.WORK), "xiaohongshu.com_explore_65a1b2c3")

    def test_short_links_keep_their_host(self) -> None:
        self.assertEqual(folder_for_link("https://xhslink.com/a/AbC123"), "xhslink.com_a_AbC123")

    def test_xsec_token_does_not_change_the_folder(self) -> None:
        # The token is dated. The same work pasted a day later must land in the
        # folder it already has, or the skip check would never fire.
        tokened = f"{self.WORK}?xsec_token=ABC123&source=web"
        self.assertEqual(folder_for_link(tokened), folder_for_link(self.WORK))

    def test_scheme_and_www_are_noise(self) -> None:
        for variant in (
            "http://www.xiaohongshu.com/explore/65a1b2c3",
            "https://xiaohongshu.com/explore/65a1b2c3",
            "www.xiaohongshu.com/explore/65a1b2c3",
        ):
            with self.subTest(variant=variant):
                self.assertEqual(folder_for_link(variant), folder_for_link(self.WORK))

    def test_different_works_get_different_folders(self) -> None:
        other = "https://www.xiaohongshu.com/explore/99z9z9z9"
        self.assertNotEqual(folder_for_link(other), folder_for_link(self.WORK))

    def test_result_is_a_single_safe_path_segment(self) -> None:
        for hostile in ("http://../../etc/passwd", "https://a/../../b", "https://a/b\\c"):
            with self.subTest(link=hostile):
                name = folder_for_link(hostile)
                self.assertNotIn("/", name)
                self.assertNotIn("\\", name)
                self.assertNotIn("..", name)
                self.assertFalse(name.startswith("."))

    def test_name_is_bounded(self) -> None:
        name = folder_for_link("https://xiaohongshu.com/" + "a" * 500)
        self.assertLessEqual(len(name), FOLDER_NAME_LENGTH)

    def test_never_empty(self) -> None:
        self.assertTrue(folder_for_link("https://"))
        self.assertTrue(folder_for_link("///"))


if __name__ == "__main__":
    unittest.main()
