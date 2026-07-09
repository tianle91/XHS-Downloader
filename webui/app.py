"""XHS-Downloader Web UI.

A self-contained batch-download web interface that sits on top of the existing
``source.XHS`` engine. It accepts many links at once, exposes rich file/folder
formatting options, downloads every work and packs the whole result into a
single ZIP file the user can download from the browser.

Everything related to this feature lives inside the ``webui`` folder; nothing
outside of it is modified. The engine (``source.XHS``) is imported and used
read-only.

Run with::

    python -m webui            # then open http://127.0.0.1:5557

"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from json import dump
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from source import XHS

# --------------------------------------------------------------------------- #
# Paths / constants
# --------------------------------------------------------------------------- #

HERE = Path(__file__).resolve().parent
INDEX_HTML = HERE.joinpath("index.html")

# Persistent place to keep finished ZIP files until they are downloaded.
ZIP_DIR = Path(tempfile.gettempdir()).joinpath("xhs_webui_zips")
ZIP_DIR.mkdir(exist_ok=True)

# The XHS engine is a singleton and keeps shared HTTP clients / SQLite handles,
# so only one job may touch it at a time.
ENGINE_LOCK = asyncio.Lock()

# Finished jobs are kept for this long before their ZIP is cleaned up.
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

# Bookkeeping SQLite files the engine may create inside the download folder.
# They are never useful to the end user, so they are excluded from the ZIP and
# ignored when deciding whether anything was actually downloaded.
ENGINE_DB_FILES = {"ExploreData.db", "ExploreID.db", "MappingData.db"}


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
    current: str = ""
    logs: list[str] = field(default_factory=list)
    error: str = ""
    zip_path: Path | None = None
    zip_name: str = ""
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
            "current": self.current,
            "logs": self.logs[-200:],
            "error": self.error,
            "file_count": self.file_count,
            "size_bytes": self.size_bytes,
            "zip_name": self.zip_name,
            "ready": self.status == "done" and self.zip_path is not None,
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

    # File / folder formatting options
    folder_name: str = "Download"
    name_fields: list[str] = Field(default_factory=lambda: ["publish_time", "author", "title"])
    date_format: str = DEFAULT_DATE_FORMAT
    image_format: str = "JPEG"
    video_preference: str = "resolution"
    folder_mode: bool = False  # each work in its own sub-folder
    author_archive: bool = False  # group each author's works in a sub-folder

    # Download toggles
    image_download: bool = True
    video_download: bool = True
    live_download: bool = False
    write_mtime: bool = False
    include_metadata: bool = False  # write metadata.json alongside the files

    # Network / auth
    cookie: str = ""
    proxy: str = ""

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
        folder_name = self.folder_name.strip() or "Download"
        return {
            "work_path": str(work_path),
            "folder_name": folder_name,
            "name_format": self.name_format(),
            "image_format": image_format,
            "video_preference": preference,
            "folder_mode": self.folder_mode,
            "author_archive": self.author_archive,
            "image_download": self.image_download,
            "video_download": self.video_download,
            "live_download": self.live_download,
            "write_mtime": self.write_mtime,
            "cookie": self.cookie.strip(),
            "proxy": self.proxy.strip() or None,
            # Web-UI specific: never skip based on the shared history DB and
            # never persist per-work data unless the user explicitly asks.
            "download_record": False,
            "record_data": False,
            "language": "en_US",
            "script_server": False,
        }


# --------------------------------------------------------------------------- #
# Core job runner
# --------------------------------------------------------------------------- #


def _zip_directory(source_dir: Path, zip_path: Path) -> tuple[int, int]:
    """Zip everything under ``source_dir`` (minus engine DBs); return counts."""
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(source_dir.rglob("*")):
            if item.is_file() and item.name not in ENGINE_DB_FILES:
                zf.write(item, item.relative_to(source_dir))
                count += 1
    return count, zip_path.stat().st_size if zip_path.exists() else 0


async def _run_job(job: Job, options: BatchOptions) -> None:
    async with ENGINE_LOCK:
        job.status = "running"
        work_path = Path(tempfile.mkdtemp(prefix="xhs_webui_"))
        engine_folder: Path | None = None
        collected: list[dict] = []
        try:
            async with XHS(**options.engine_kwargs(work_path)) as xhs:
                xhs.print.func = _LogCapture(job)
                # ``time_format`` is not an XHS(...) parameter, so the chosen
                # date format is applied to the extractor instance directly. It
                # drives both the name fields and metadata.json; file mtimes are
                # unaffected (they come from the raw timestamp).
                xhs.explore.time_format = options.time_format()
                engine_folder = Path(xhs.manager.folder)

                links = await xhs.extract_links(options.links)
                if not links:
                    job.status = "error"
                    job.error = "No valid XiaoHongShu links were found in the input."
                    return

                job.total = len(links)
                for i, link in enumerate(links):
                    job.current = link
                    try:
                        result = await xhs.extract(link, True, None, True)
                    except Exception as exc:  # noqa: BLE001 - surface to the user
                        job.failed += 1
                        job.logs.append(f"Error processing {link}: {exc!r}")
                        result = []
                    valid = [item for item in (result or []) if item and item.get("作品ID")]
                    if valid:
                        job.success += 1
                        collected.extend(valid)
                    else:
                        job.failed += 1
                    job.done = i + 1

                job.current = ""

                if options.include_metadata and collected:
                    _write_metadata(engine_folder, collected)

            # ---- packaging (engine context closed) ---- #
            media = (
                [
                    p
                    for p in engine_folder.rglob("*")
                    if p.is_file() and p.name not in ENGINE_DB_FILES and p.name != "metadata.json"
                ]
                if engine_folder
                else []
            )
            if not media:
                job.status = "error"
                job.error = (
                    "Nothing was downloaded. The links may be invalid/expired, "
                    "or every enabled media type was filtered out. A Cookie can "
                    "help with restricted or high-resolution content."
                )
                return

            zip_name = _safe_zip_name(options.folder_name)
            zip_path = ZIP_DIR.joinpath(f"{job.id}.zip")
            count, size = await asyncio.to_thread(_zip_directory, engine_folder, zip_path)

            job.zip_path = zip_path
            job.zip_name = zip_name
            job.file_count = count
            job.size_bytes = size
            job.status = "done"
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"Unexpected error: {exc!r}"
        finally:
            shutil.rmtree(work_path, ignore_errors=True)
            _cleanup_expired()


def _write_metadata(folder: Path, data: list[dict]) -> None:
    """Persist a trimmed metadata.json next to the downloaded files."""
    folder.mkdir(parents=True, exist_ok=True)
    trimmed = []
    for item in data:
        entry = {k: v for k, v in item.items() if k not in {"下载地址", "动图地址"}}
        trimmed.append(entry)
    with folder.joinpath("metadata.json").open("w", encoding="utf-8") as f:
        dump(trimmed, f, ensure_ascii=False, indent=2, default=str)


def _safe_zip_name(folder_name: str) -> str:
    base = "".join(c for c in folder_name.strip() if c.isalnum() or c in "-_ ").strip()
    base = base.replace(" ", "-") or "XHS-Download"
    return f"{base}.zip"


def _cleanup_expired() -> None:
    now = time.time()
    for job_id, job in list(JOBS.items()):
        if now - job.created_at > JOB_TTL_SECONDS:
            if job.zip_path and job.zip_path.exists():
                job.zip_path.unlink(missing_ok=True)
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


@app.get("/api/jobs/{job_id}/download")
async def job_download(job_id: str) -> FileResponse:
    job = JOBS.get(job_id)
    if not job or job.status != "done" or not job.zip_path or not job.zip_path.exists():
        raise HTTPException(status_code=404, detail="Result not ready or expired.")
    return FileResponse(job.zip_path, media_type="application/zip", filename=job.zip_name)
