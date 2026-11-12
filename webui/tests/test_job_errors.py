"""Download failures should explain recovery and preserve batch accounting."""

import tempfile
import unittest
from json import JSONDecodeError
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from webui.app import BatchOptions, Job, _run_job, folder_for_link


class JobErrorsTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_note_json_is_retryable_and_batch_continues(self):
        links = [
            "https://www.rednote.com/discovery/item/first",
            "https://www.rednote.com/discovery/item/second",
        ]
        engine = MagicMock()
        engine.extract_links = AsyncMock(side_effect=[[url] for url in links])
        engine.extract = AsyncMock(
            side_effect=[
                JSONDecodeError("Expecting value", "", 0),
                [{"作品ID": "second"}],
            ]
        )
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=engine)
        context.__aexit__ = AsyncMock(return_value=False)
        job = Job(id="invalid-json-test")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("webui.app.DOWNLOAD_DIR", root),
                patch("webui.app.XHS", return_value=context),
            ):
                await _run_job(job, BatchOptions(links="\n".join(links)))
            self.assertFalse(root.joinpath(folder_for_link(links[0])).exists())

        self.assertEqual(job.status, "done")
        self.assertEqual((job.total, job.done, job.failed, job.success), (2, 2, 1, 1))
        self.assertEqual(job.failed_links, [links[0]])
        message = "\n".join(job.logs)
        self.assertIn("embedded note data", message)
        self.assertIn("login/verification", message)
        self.assertIn("Cookie", message)
        self.assertIn("JSONDecodeError", message)
        self.assertEqual(engine.extract.await_count, 2)
