# Web UI Architecture

This document explains how the `webui/` batch downloader is built and how it
plugs into the rest of XHS-Downloader. For the user-facing feature list see
[`README.md`](README.md); for day-to-day contributor notes see
[`AGENTS.md`](AGENTS.md).

## Design goals

1. **Reuse the engine, don't fork it.** Every download goes through the same
   `source.application.app.XHS` engine the TUI/API/MCP/CLI use, so there is a
   single source of truth for link parsing, formatting and downloading.
2. **Zero blast radius.** Running the Web UI must not change any persisted state
   the other modes rely on (`settings.json`, `Volume/Download`, the history DB).
3. **Self-contained.** All feature code lives in `webui/`; nothing outside the
   folder is modified.

## Files

| File           | Responsibility                                                    |
| -------------- | ----------------------------------------------------------------- |
| `app.py`       | FastAPI app, job model, job runner, engine wiring                 |
| `index.html`   | Single-page frontend (vanilla JS, polls job status)               |
| `__main__.py`  | `python -m webui` entry point (uvicorn)                           |
| `__init__.py`  | Package marker (intentionally does **not** re-export `app`)       |

> **Note on `__init__.py`:** the FastAPI object is named `app` and lives in
> `webui/app.py`. Re-exporting it from `__init__.py` (`from .app import app`)
> would make the package attribute `webui.app` point at the FastAPI object and
> **shadow the `webui.app` submodule**. It is therefore intentionally not
> re-exported, so `webui.app` always resolves to the module.

## Where it sits in XHS-Downloader

```
                         source.application.app.XHS   (the engine)
                          extract_links()  extract()
                          Download · Image · Video · Html · Recorder
                                        ▲
        ┌───────────────┬──────────────┼───────────────┬────────────────┐
        │               │              │               │                │
   TUI (Textual)   API (FastAPI)   MCP server      CLI (click)     Web UI  ← this folder
   python main.py  main.py api     main.py mcp     main.py <args>  python -m webui
```

The Web UI is a sibling front-end. It adds batching, per-link folders and a
skip-what-exists policy on top of the engine; it does not change the engine.

## Request / data flow

```
Browser                         webui/app.py                         source.XHS
  │                                  │                                    │
  │  POST /api/jobs  ───────────────►│                                    │
  │   {links, naming options}        │  create Job, spawn task            │
  │◄──────────  {job_id}             │                                    │
  │                                  │  async with ENGINE_LOCK:           │
  │                                  │    mkdtemp() → work_path (DB only) │
  │                                  │    XHS(**engine_kwargs) ──────────►│  __init__ (new Manager,
  │                                  │    xhs.print.func = LogCapture     │            HTTP clients)
  │                                  │    xhs.explore.time_format = …     │
  │                                  │    links = extract_links(text) ───►│  regex parse
  │  GET /api/jobs/{id}  (poll 1s)   │    for link in links:              │
  │◄──── {status, done/total, logs,  │      folder = DOWNLOAD_DIR /       │
  │       skipped, failed_links}     │               folder_for_link(link)│
  │                                  │      if folder has media: skip     │
  │                                  │      xhs.download.folder = folder  │
  │                                  │      extract(link, download=True)─►│  fetch → Download files
  │                                  │      job.done += 1                 │      into folder
  │                                  │      (optional) metadata.json      │
  │                                  │      else: discard empty folder    │
  │                                  │  rmtree(work_path)                 │
  │  GET /api/jobs/{id}              │                                    │
  │◄──── {status:done, file_count,   │                                    │
  │       output_dir}                │                                    │
```

Files are written to disk, so there is no download endpoint — the browser only
ever learns *where* they went.

## Key integration points in `app.py`

- **`from source import XHS`** — the only coupling to the rest of the project.
  Everything else is standard library + FastAPI/uvicorn (already project deps).
- **`BatchOptions.engine_kwargs(work_path)`** — translates the browser form into
  the exact keyword arguments of `XHS.__init__`. This is the single place that
  maps UI concepts to engine parameters. It hard-codes the isolation policy:
  `download_record=False`, `record_data=False`, `author_archive=False`,
  `script_server=False`, `language="en_US"`, and a per-job temporary
  `work_path`. Note the engine's `folder_name` is a throwaway (`"engine"`) —
  media never lands there, see `folder_for_link` below.
- **`NAME_FIELDS`** — maps friendly UI field ids to the engine's `name_format`
  tokens (which the engine validates against `Manager.NAME_KEYS`). The UI never
  sends raw tokens, so it cannot produce an invalid format.
- **`DATE_FORMATS`** — the date/time presets offered for the publish-time and
  update-time fields. `time_format` is not an `XHS(...)` parameter, so the
  chosen strftime pattern is assigned to `xhs.explore.time_format` on the live
  engine instance once the job starts. Only this fixed set is accepted, because
  the rendered value lands in file names. File mtimes are unaffected — the
  engine takes those from the raw `时间戳` value, not the formatted string.
- **`folder_for_link(link)`** — each link downloads into
  `DOWNLOAD_DIR/<folder_for_link(link)>`. `Download` captures `manager.folder`
  at construction, so `xhs.download.folder` is reassigned before each link;
  the engine itself is untouched. The name drops the scheme, `www.` and the
  query string — the `xsec_token` is dated, so keeping it would give the same
  work a new folder every day and defeat the skip check — then reduces what is
  left to one safe path segment. The engine's own folder stays in the temp
  `work_path`, which is why `ExploreData.db` never appears in `Downloads/`.
- **Skip policy.** A link whose folder already contains a media file is skipped
  without a request (`overwrite` forces it). Symmetrically, a link that writes
  nothing has its folder removed — otherwise the next run would see the empty
  directory and skip a link it never fetched.
- **`Job.failed_links`** — links that produced no work, whether `extract()`
  raised or simply returned nothing. Exposed through `GET /api/jobs/{id}` so the
  browser can offer to re-submit them as a new job. This is a level above the
  engine's own `max_retry`, which has already been exhausted by this point.
- **Settings persistence lives entirely in the browser.** `index.html` writes the
  form to `localStorage` under `xhs-webui-settings-v2` on every change and
  restores it on load; the server is stateless and never sees it. Values are
  validated against the current `AVAILABLE_FIELDS` / `CHOICES` on restore, so an
  entry written by an older build is ignored rather than applied. The pasted
  links are excluded — their `xsec_token` is dated.
- **`_LogCapture`** — a duck-typed sink assigned to `xhs.print.func`. The
  engine's `source.module.tools.logging` calls `func.write(text, scroll_end=…)`
  for any non-`print` sink, so capturing progress needs only a `write()` method.
  This is how live logs reach the browser without touching engine code.
- **`_media_files(folder)`** — everything a user would call a download, i.e.
  every file except our own `metadata.json`. It answers both "has this link
  already been fetched?" and "did this link produce anything?".

## Concurrency model

`XHS` is a process-wide singleton (`__new__` caches the instance) that owns
shared `httpx.AsyncClient`s and SQLite handles. Two jobs running concurrently
would reconfigure and close each other's clients. The runner therefore holds a
module-level `asyncio.Lock` (`ENGINE_LOCK`) for the entire duration of a job:
the HTTP layer stays single-session, while users can still queue any number of
jobs (each job's status is tracked independently in the `JOBS` dict).

## Lifecycle & cleanup

- Each job gets a `mkdtemp(prefix="xhs_webui_")` for the engine's `Manager`,
  which is where `ExploreData.db` lands. No media goes there; it is `rmtree`'d
  when the job ends.
- **Downloaded media persists**, in `DOWNLOAD_DIR` (`<repo>/Downloads` by
  default, or `XHS_WEBUI_DOWNLOAD_DIR`). It belongs to the user and is never
  cleaned up. The default is git-ignored.
- Job records are dropped from the `JOBS` dict after `JOB_TTL_SECONDS`
  (1 hour), swept on every new job.
- The engine still opens its shared bookkeeping DBs in `Volume/`
  (`ExploreID.db`, `MappingData.db`) on start-up, exactly as the TUI/API/MCP/CLI
  do. With `download_record=False` and no mapping data, the Web UI writes **no
  rows** to them — they are created empty and carry no state between jobs.
- The engine's shared `Volume/Temp` scratch is used for in-flight chunks; a
  partial file may remain there if a download is interrupted (this is engine
  behaviour, identical across all modes).
