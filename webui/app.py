"""XHS-Downloader Web UI.

A self-contained batch-download web interface that sits on top of the existing
``source.XHS`` engine. It accepts many links at once, exposes rich file naming
options, and downloads every work straight to a folder on disk.

Every link gets its own folder, named after the link itself, inside one shared
download directory. A link whose folder already holds files is skipped, so the
same batch can be re-run cheaply after fixing a few failures.

Everything related to this feature lives inside the ``webui`` folder; nothing
outside of it is modified. The engine (``source.XHS``) is imported and used
read-only.

Run with::

    python -m webui            # then open http://127.0.0.1:5557

"""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from json import dump
from os import getenv
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from source import XHS

# --------------------------------------------------------------------------- #
# Paths / constants
# --------------------------------------------------------------------------- #

HERE = Path(__file__).resolve().parent
INDEX_HTML = HERE.joinpath("index.html")
REPO_ROOT = HERE.parent

# Where finished files are written. Every batch writes into this one directory,
# so re-running a batch finds the folders it already created and skips them.
# Override with XHS_WEBUI_DOWNLOAD_DIR; a relative value is taken from the repo
# root, so the default is simply ``<repo>/Downloads``.
DOWNLOAD_DIR = Path(getenv("XHS_WEBUI_DOWNLOAD_DIR") or REPO_ROOT.joinpath("Downloads"))
if not DOWNLOAD_DIR.is_absolute():
    DOWNLOAD_DIR = REPO_ROOT.joinpath(DOWNLOAD_DIR)
DOWNLOAD_DIR = DOWNLOAD_DIR.resolve()

# A link's folder name is capped at this many characters. Long enough to keep
# the host and the work id, short enough to stay well inside PATH_MAX.
FOLDER_NAME_LENGTH = 80

# Written next to the media when the user asks for it; never counted as media.
METADATA_NAME = "metadata.json"

# The XHS engine is a singleton and keeps shared HTTP clients / SQLite handles,
# so only one job may touch it at a time.
ENGINE_LOCK = asyncio.Lock()

# Finished job records are dropped from memory after this long.
JOB_TTL_SECONDS = 60 * 60  # 1 hour

# Mapping between the friendly field ids used by the UI and the tokens the
# engine understands for ``name_format`` (the engine only accepts these exact
# Chinese tokens).
NAME_FIELDS: dict[str, str] = {
    "publish_time": "发布时间",
    "update_time": "最后更新时间",
    "author": "作者昵称",
    "author_id": "作者ID",
    "title": "作品标题",
    "description": "作品描述",
    "id": "作品ID",
    "type": "作品类型",
    "tags": "作品标签",
    "likes": "点赞数量",
    "collections": "收藏数量",
    "comments": "评论数量",
    "shares": "分享数量",
}

VALID_IMAGE_FORMATS = {"AUTO", "PNG", "WEBP", "JPEG", "HEIC", "AVIF"}
VALID_VIDEO_PREFERENCE = {"resolution", "bitrate", "size"}

# How the ``publish_time`` / ``update_time`` fields are rendered. The engine
# formats them with ``Explore.time_format`` (a strftime pattern) before they
# reach either the file name or metadata.json. Only this fixed set is offered:
# the value ends up in file names, so arbitrary patterns are not accepted.
DATE_FORMATS: dict[str, str] = {
    "datetime": "%Y-%m-%d_%H:%M:%S",
    "date": "%Y-%m-%d",
    "date_compact": "%Y%m%d",
    "datetime_compact": "%Y%m%d_%H%M%S",
    "date_dotted": "%Y.%m.%d",
    "month": "%Y-%m",
    "day_first": "%d-%m-%Y",
    "month_first": "%m-%d-%Y",
}
DEFAULT_DATE_FORMAT = "datetime"

# --------------------------------------------------------------------------- #
# Job state
# --------------------------------------------------------------------------- #


@dataclass
class Job:
    id: str
    status: str = "pending"  # pending | running | done | error
    total: int = 0
    done: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    current: str = ""
    logs: list[str] = field(default_factory=list)
    failed_links: list[str] = field(default_factory=list)
    error: str = ""
    output_dir: str = str(DOWNLOAD_DIR)
    file_count: int = 0
    size_bytes: int = 0
    created_at: float = field(default_factory=time.time)

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "success": self.success,
            "failed": self.failed,
            "skipped": self.skipped,
            "current": self.current,
            "logs": self.logs[-200:],
            "failed_links": self.failed_links,
            "error": self.error,
            "output_dir": self.output_dir,
            "file_count": self.file_count,
            "size_bytes": self.size_bytes,
        }


JOBS: dict[str, Job] = {}


class _LogCapture:
    """Duck-typed replacement for the engine's ``print`` sink.

    ``source.module.tools.logging`` calls ``func.write(text, scroll_end=...)``
    for any sink that is not the builtin ``print``, so we only need ``write``.
    """

    def __init__(self, job: Job) -> None:
        self.job = job

    def write(self, text, scroll_end: bool = True) -> None:  # noqa: D401,FBT001
        message = getattr(text, "plain", None) or str(text)
        message = message.strip()
        if message:
            self.job.logs.append(message)


# --------------------------------------------------------------------------- #
# Request model
# --------------------------------------------------------------------------- #


class BatchOptions(BaseModel):
    links: str = Field(..., description="Whitespace/newline separated XHS links")

    # File naming / layout. These apply *inside* a link's folder; the folder
    # itself is always named after the link.
    name_fields: list[str] = Field(default_factory=lambda: ["publish_time", "author", "title"])
    date_format: str = DEFAULT_DATE_FORMAT
    image_format: str = "JPEG"
    video_preference: str = "resolution"
    folder_mode: bool = False  # each work in its own sub-folder

    # Download toggles
    image_download: bool = True
    video_download: bool = True
    live_download: bool = False
    write_mtime: bool = False
    include_metadata: bool = False  # write metadata.json alongside the files

    # Network / auth
    cookie: str = ""
    proxy: str = ""

    # Re-download a link even if its folder already holds files.
    overwrite: bool = False

    @field_validator("name_fields")
    @classmethod
    def _validate_name_fields(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("Select at least one file-name field.")
        if unknown := [f for f in value if f not in NAME_FIELDS]:
            raise ValueError(f"Unknown file-name field(s): {', '.join(unknown)}")
        return value

    @field_validator("date_format")
    @classmethod
    def _validate_date_format(cls, value: str) -> str:
        if value not in DATE_FORMATS:
            raise ValueError(f"Unknown date format: {value}")
        return value

    def name_format(self) -> str:
        return " ".join(NAME_FIELDS[f] for f in self.name_fields)

    def time_format(self) -> str:
        """The strftime pattern the engine should render date fields with."""
        return DATE_FORMATS[self.date_format]

    def engine_kwargs(self, work_path: Path) -> dict:
        image_format = self.image_format.upper()
        if image_format not in VALID_IMAGE_FORMATS:
            image_format = "JPEG"
        preference = self.video_preference if self.video_preference in VALID_VIDEO_PREFERENCE else "resolution"
        return {
            # The engine's own folder is a throwaway: it is where ExploreData.db
            # lands. Media never goes here -- ``Download.folder`` is retargeted
            # at each link's folder before it runs. See _run_job.
            "work_path": str(work_path),
            "folder_name": "engine",
            "name_format": self.name_format(),
            "image_format": image_format,
            "video_preference": preference,
            "folder_mode": self.folder_mode,
            # Each link already has its own folder, so grouping by author inside
            # it would only ever add one redundant level.
            "author_archive": False,
            "image_download": self.image_download,
            "video_download": self.video_download,
            "live_download": self.live_download,
            "write_mtime": self.write_mtime,
            "cookie": self.cookie.strip(),
            "proxy": self.proxy.strip() or None,
            # Web-UI specific: never skip based on the shared history DB (this
            # UI skips on the presence of a link's folder instead) and never
            # persist per-work data.
            "download_record": False,
            "record_data": False,
            "language": "en_US",
            "script_server": False,
        }


# --------------------------------------------------------------------------- #
# Core job runner
# --------------------------------------------------------------------------- #


def folder_for_link(link: str) -> str:
    """The folder name a link downloads into.

    ``link`` is the link *as pasted*, never the one the engine resolves it to:
    a ``xhslink.com`` short link redirects to a canonical ``discovery/item``
    URL, and naming the folder after that would leave the user hunting for a
    work id they never typed.

    The link itself, minus the noise: the scheme and ``www.`` carry nothing, and
    the query string is dropped because a work's ``xsec_token`` is dated -- two
    copies of the same link pasted a day apart must still map to one folder.
    Whatever survives is reduced to a single safe path segment.
    """
    parts = urlsplit(link if "//" in link else f"//{link}")
    host = parts.netloc.removeprefix("www.")
    stem = f"{host}{parts.path}".strip("/")
    name = re.sub(r"[^\w.-]+", "_", stem, flags=re.UNICODE)
    # Separators are already gone, so no run of dots can traverse anywhere --
    # but a segment literally containing ".." has no business existing either.
    name = re.sub(r"\.{2,}", ".", name).strip("._")
    return name[:FOLDER_NAME_LENGTH] or "link"


def _media_files(folder: Path) -> list[Path]:
    """Everything a user would call a download: media, not our metadata.json."""
    if not folder.is_dir():
        return []
    return [p for p in folder.rglob("*") if p.is_file() and p.name != METADATA_NAME]


async def _run_job(job: Job, options: BatchOptions) -> None:
    async with ENGINE_LOCK:
        job.status = "running"
        # The engine writes its ExploreData.db under ``work_path``; media does
        # not go here. Throwaway, so the download folder stays free of DB files.
        work_path = Path(tempfile.mkdtemp(prefix="xhs_webui_"))
        try:
            DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            async with XHS(**options.engine_kwargs(work_path)) as xhs:
                xhs.print.func = _LogCapture(job)
                # Neither of these is an XHS(...) parameter, so both are applied
                # to the live instance. ``time_format`` drives the date fields;
                # ``download.folder`` is the directory files land in, and is
                # retargeted per link below.
                xhs.explore.time_format = options.time_format()

                # One token at a time, rather than one ``extract_links`` call over
                # the whole blob: that call resolves short links, and we need to
                # keep each pasted link paired with what it resolves to so the
                # folder can be named after what the user actually typed.
                pasted = options.links.split()
                job.total = len(pasted)

                for token in pasted:
                    job.current = token
                    folder = DOWNLOAD_DIR.joinpath(folder_for_link(token))

                    # Checked before resolving, so re-running a batch of short
                    # links costs no redirect requests at all.
                    if _media_files(folder) and not options.overwrite:
                        job.skipped += 1
                        job.logs.append(f"Skipping {token}: {folder.name} already has files")
                        job.done += 1
                        continue

                    resolved = await xhs.extract_links(token)
                    if not resolved:
                        # Not a link. Prose pasted alongside the URLs is normal,
                        # so drop it from the denominator rather than fail it.
                        job.total -= 1
                        job.logs.append(f"Ignoring {token}: not a XiaoHongShu link")
                        continue

                    folder.mkdir(parents=True, exist_ok=True)
                    xhs.download.folder = folder
                    try:
                        result = await xhs.extract(resolved[0], True, None, True)
                    except Exception as exc:  # noqa: BLE001 - surface to the user
                        job.logs.append(f"Error processing {token}: {exc!r}")
                        result = []

                    valid = [item for item in (result or []) if item and item.get("作品ID")]
                    if valid:
                        job.success += 1
                        if options.include_metadata:
                            _write_metadata(folder, valid)
                        files = _media_files(folder)
                        job.file_count += len(files)
                        job.size_bytes += sum(p.stat().st_size for p in files)
                    else:
                        # The pasted link, not the resolved one: retry re-submits
                        # exactly what the user gave us.
                        job.failed += 1
                        job.failed_links.append(token)
                        _discard_if_no_media(folder)
                    job.done += 1

                job.current = ""

            if not job.total:
                job.status = "error"
                job.error = "No valid XiaoHongShu links were found in the input."
                return

            if not job.success and not job.skipped:
                job.status = "error"
                job.error = (
                    "Nothing was downloaded. The links may be invalid/expired, "
                    "or every enabled media type was filtered out. A Cookie can "
                    "help with restricted or high-resolution content."
                )
                return

            job.status = "done"
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"Unexpected error: {exc!r}"
        finally:
            shutil.rmtree(work_path, ignore_errors=True)
            _cleanup_expired()


def _discard_if_no_media(folder: Path) -> None:
    """A link that downloaded nothing must not leave a folder behind.

    Otherwise the next run would find it and skip the link it never fetched.
    ``folder_mode`` can leave empty per-work subdirectories, so an ``rmdir`` of
    the top level is not enough.
    """
    if not _media_files(folder):
        shutil.rmtree(folder, ignore_errors=True)


def _write_metadata(folder: Path, data: list[dict]) -> None:
    """Persist a trimmed metadata.json next to the downloaded files."""
    folder.mkdir(parents=True, exist_ok=True)
    trimmed = []
    for item in data:
        entry = {k: v for k, v in item.items() if k not in {"下载地址", "动图地址"}}
        trimmed.append(entry)
    with folder.joinpath(METADATA_NAME).open("w", encoding="utf-8") as f:
        dump(trimmed, f, ensure_ascii=False, indent=2, default=str)


def _cleanup_expired() -> None:
    """Drop stale job records. The downloaded files themselves are the user's."""
    now = time.time()
    for job_id, job in list(JOBS.items()):
        if now - job.created_at > JOB_TTL_SECONDS:
            JOBS.pop(job_id, None)


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #

app = FastAPI(title="XHS-Downloader Web UI", docs_url=None, redoc_url=None)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


@app.get("/api/fields")
async def fields() -> JSONResponse:
    return JSONResponse(
        {
            "name_fields": list(NAME_FIELDS.keys()),
            "date_formats": DATE_FORMATS,
            "image_formats": sorted(VALID_IMAGE_FORMATS),
            "video_preferences": sorted(VALID_VIDEO_PREFERENCE),
            "download_dir": str(DOWNLOAD_DIR),
        }
    )


@app.post("/api/jobs")
async def create_job(options: BatchOptions) -> JSONResponse:
    if not options.links.strip():
        raise HTTPException(status_code=400, detail="Please provide at least one link.")
    job = Job(id=uuid4().hex)
    JOBS[job.id] = job
    asyncio.create_task(_run_job(job, options))
    return JSONResponse({"job_id": job.id})


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str) -> JSONResponse:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired.")
    return JSONResponse(job.public())
