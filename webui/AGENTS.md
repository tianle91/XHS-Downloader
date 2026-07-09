# Working on the Web UI

Developer notes for `webui/`. For what the thing *does*, read
[`README.md`](README.md). For the request/data flow in detail, read
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## The one rule

**All feature code lives inside `webui/`.** Nothing under `source/` is modified.
Where the engine lacks a hook, the Web UI sets the attribute on the live
instance at run time rather than patching the engine:

| Need                        | How it is done                                    |
| --------------------------- | ------------------------------------------------- |
| Capture progress logs       | `xhs.print.func = _LogCapture(job)`                |
| Choose the date format      | `xhs.explore.time_format = …` (not an `XHS(...)` param) |
| Send files to a link's folder | `xhs.download.folder = …`, retargeted per link   |

If you find yourself wanting to edit `source/`, look for an attribute to set
instead. The one exception outside this folder is `/Downloads/` in
`.gitignore` — the default download directory sits at the repo root.

## Layout

| File                  | Purpose                                              |
| --------------------- | ---------------------------------------------------- |
| `app.py`              | FastAPI app, job runner, `BatchOptions` request model |
| `index.html`          | The entire frontend: one page, one inline `<script>`  |
| `__main__.py`         | `python -m webui` entry point (uvicorn)               |
| `tests/test_options.py` | Backend unit tests (stdlib `unittest`)             |
| `tests/ui_harness.py` | Frontend harness — **macOS only**, see below          |

## How it works

1. The browser posts the links + options to `POST /api/jobs`, which starts a
   background job and returns a `job_id`.
2. The job configures a `source.XHS` engine instance and disables the shared
   "download history" DB (`download_record=False`), so the engine never skips
   anything — this UI decides what to skip itself, from the folders on disk.
3. The pasted text is split on whitespace and each token handled on its own,
   rather than passing the whole blob to `extract_links()` once. That call
   resolves `xhslink.com` short links through a redirect, and the folder must be
   named after the link the user *typed*, not the canonical URL it resolves to —
   so the pairing has to be kept. A token that resolves to nothing is not a
   link: it is logged and dropped from `job.total`, never counted as a failure.
4. For each link the engine's file destination is retargeted at
   `<download dir>/<folder_for_link(token)>`, and progress logs are captured. A
   link whose folder already holds files is skipped *before* it is resolved, so
   re-running a batch of short links issues no redirect requests at all.
5. A link that yields no work is recorded in the job's `failed_links` — as
   pasted, so retry re-submits what the user gave us. The browser polls this and
   offers to re-submit them as a fresh job. Its folder is removed if nothing was
   written, so the next run does not mistake it for finished.
6. The engine's own working directory is a throwaway temp dir — it only ever
   holds `ExploreData.db` — and is deleted when the job ends. Job records are
   dropped from memory after 1 hour.

Because the engine is a process-wide singleton with shared HTTP clients, jobs
are serialised with an `asyncio` lock.

## API

| Method | Path                        | Purpose                              |
| ------ | --------------------------- | ------------------------------------ |
| `GET`  | `/`                         | The web UI                           |
| `GET`  | `/api/fields`               | Field ids, date formats, download dir |
| `POST` | `/api/jobs`                 | Start a batch job → `{job_id}`       |
| `GET`  | `/api/jobs/{id}`            | Job status / progress / logs         |

There is no download endpoint: files are written to disk, not streamed to the
browser.

## Tests

```bash
uv run python -m unittest discover webui/tests   # backend, runs anywhere
uv run python webui/tests/ui_harness.py          # frontend, macOS only
```

`tests/test_options.py` covers `BatchOptions` — the boundary between the browser
and the engine: which options are accepted, and how they become `XHS(...)`
keyword arguments. Standard library only, no extra dependencies.

`tests/ui_harness.py` is deliberately **not** a `unittest` module, so
`unittest discover` will not pick it up and fail on Linux or CI. There is no
build step and no Node dependency; rather than add one, it extracts the inline
`<script>` from `index.html` and runs *that real code* against a hand-written
DOM stub under JavaScriptCore (`osascript -l JavaScript`, macOS only). It boots
the page four times — fresh, reopened with saved settings, seeded with stale
settings from an older build, and with a job that finished with failures.

Because the DOM is stubbed, it checks behaviour (state, wiring, persistence),
never rendering. If you change an element `id` in `index.html`, add it to the
stub's id list or the harness will fail with a null dereference.

## Integration with XHS-Downloader

XHS-Downloader is really **one engine with several front-ends**. The engine is
`source.application.app.XHS`; `main.py` dispatches to the different front-ends:

| Command                         | Front-end       | Serves                     |
| ------------------------------- | --------------- | -------------------------- |
| `uv run python main.py`         | TUI (Textual)   | terminal app               |
| `uv run python main.py api`     | FastAPI REST    | `:5556/xhs/detail`         |
| `uv run python main.py mcp`     | MCP server      | `:5556/mcp/`               |
| `uv run python main.py <args>`  | CLI (click)     | terminal                   |
| **`uv run python -m webui`**    | **Web UI**      | **`:5557` (this folder)**  |

The Web UI is **just another consumer of the same engine** — it imports `XHS`
and calls the identical pipeline the other modes use:

```
webui/app.py
   └─ from source import XHS
        XHS(**engine_kwargs)          # same constructor the TUI/API/MCP/CLI call
        └─ xhs.extract_links(text)    # same link parsing (explore/item/user/xhslink)
        └─ xhs.extract(link, ...)     # same Download / Image / Video / Html modules
```

### What it shares with the other modes

- **The engine and most options.** `name_format`, `image_format`,
  `video_preference`, `folder_mode`, `image/video/live_download`, `write_mtime`,
  `cookie`, `proxy` are the exact `XHS(...)` parameters documented in the
  project README's *配置文件 / Settings* table.
- **The `name_format` field tokens.** The UI's friendly ids (`title`, `author`,
  `likes`, …) map to the same Chinese tokens the engine expects, via
  `NAME_FIELDS` in `app.py`. A format built in the UI behaves identically to one
  set in `settings.json`.
- **Link parsing and download logic.** No copies or re-implementations — the UI
  reuses `extract_links()` and `extract()` verbatim, so any engine fix or new
  supported link type is picked up automatically.

### What it deliberately does *not* share (isolation)

This is what keeps the Web UI from interfering with TUI/CLI usage:

| Concern                | Other modes                          | Web UI                                                    |
| ---------------------- | ------------------------------------ | --------------------------------------------------------- |
| Settings source        | `Volume/settings.json`               | the browser's `localStorage` (never reads/writes `settings.json`) |
| Download location      | `Volume/Download`                    | `<repo>/Downloads`, a folder per link (`XHS_WEBUI_DOWNLOAD_DIR`) |
| Skipping known works   | `Volume/ExploreID.db` (`download_record`) | disabled — skips on the presence of a link's folder instead |
| Metadata DB            | `Volume/.../ExploreData.db` (`record_data`) | disabled — optional `metadata.json` per folder instead |
| Date format            | `Explore.time_format` (`%Y-%m-%d_%H:%M:%S`) | chosen per job, set on the engine instance at run time |
| Concurrency            | one session per process              | jobs serialised with an `asyncio` lock (engine is a singleton) |

### Optionally wiring it into `main.py`

To keep every feature in a single folder, the Web UI ships as a standalone
`python -m webui` entry point and does **not** modify `main.py`. If you later
want a `uv run python main.py web` subcommand, it is a small, self-contained
addition (the dispatcher in `main.py` already branches on `argv[1]`):

```python
# in main.py, in the __main__ block alongside the api / mcp branches.
# webui.__main__.main() is synchronous (it calls uvicorn.run itself),
# so it does not need asyncio.run():
elif argv[1].upper() == "WEB":
    from webui.__main__ import main as run_web
    run_web()
```
