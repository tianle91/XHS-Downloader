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

from webui.app import DATE_FORMATS, DEFAULT_DATE_FORMAT, NAME_FIELDS, BatchOptions


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

    def test_blank_folder_name_falls_back(self) -> None:
        kwargs = BatchOptions(links="x", folder_name="   ").engine_kwargs(Path("/tmp/work"))
        self.assertEqual(kwargs["folder_name"], "Download")

    def test_blank_proxy_becomes_none(self) -> None:
        kwargs = BatchOptions(links="x", proxy="  ").engine_kwargs(Path("/tmp/work"))
        self.assertIsNone(kwargs["proxy"])


if __name__ == "__main__":
    unittest.main()
