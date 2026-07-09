# Web UI Architecture

This document explains how the `webui/` batch downloader is built and how it
plugs into the rest of XHS-Downloader. For the user-facing feature list see
[`README.md`](README.md).

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
| `app.py`       | FastAPI app, job model, job runner, ZIP packaging, engine wiring  |
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

The Web UI is a sibling front-end. It adds batching + ZIP packaging on top of
the engine; it does not change the engine.

## Request / data flow

```
Browser                         webui/app.py                         source.XHS
  │                                  │                                    │
  │  POST /api/jobs  ───────────────►│                                    │
  │   {links, formatting options}    │  create Job, spawn task            │
  │◄──────────  {job_id}             │                                    │
  │                                  │  async with ENGINE_LOCK:           │
  │                                  │    mkdtemp() → work_path           │
  │                                  │    XHS(**engine_kwargs) ──────────►│  __init__ (new Manager,
  │                                  │    xhs.print.func = LogCapture     │            HTTP clients)
  │                                  │    links = extract_links(text) ───►│  regex parse
  │  GET /api/jobs/{id}  (poll 1s)   │    for link in links:              │
  │◄──── {status, done/total, logs}  │      extract(link, download=True)─►│  fetch → Download files
  │                                  │      job.done += 1                 │      into work_path/<folder>
  │                                  │    (optional) write metadata.json  │
  │                                  │  zip (exclude engine *.db)         │
  │                                  │  rmtree(work_path)                 │
  │  GET /api/jobs/{id}              │                                    │
  │◄──── {ready:true, file_count}    │                                    │
  │  GET /api/jobs/{id}/download ───►│  FileResponse(zip) ───────────────►│
  │◄──────────  <folder>.zip         │                                    │
```

## Key integration points in `app.py`

- **`from source import XHS`** — the only coupling to the rest of the project.
  Everything else is standard library + FastAPI/uvicorn (already project deps).
- **`BatchOptions.engine_kwargs(work_path)`** — translates the browser form into
  the exact keyword arguments of `XHS.__init__`. This is the single place that
  maps UI concepts to engine parameters. It hard-codes the isolation policy:
  `download_record=False`, `record_data=False`, `script_server=False`,
  `language="en_US"`, and a per-job temporary `work_path`.
- **`NAME_FIELDS`** — maps friendly UI field ids to the engine's `name_format`
  tokens (which the engine validates against `Manager.NAME_KEYS`). The UI never
  sends raw tokens, so it cannot produce an invalid format.
- **`DATE_FORMATS`** — the date/time presets offered for the publish-time and
  update-time fields. `time_format` is not an `XHS(...)` parameter, so the
  chosen strftime pattern is assigned to `xhs.explore.time_format` on the live
  engine instance once the job starts. Only this fixed set is accepted, because
  the rendered value lands in file names. File mtimes are unaffected — the
  engine takes those from the raw `时间戳` value, not the formatted string.
- **Settings persistence lives entirely in the browser.** `index.html` writes the
  form to `localStorage` under `xhs-webui-settings-v1` on every change and
  restores it on load; the server is stateless and never sees it. Values are
  validated against the current `AVAILABLE_FIELDS` / `CHOICES` on restore, so an
  entry written by an older build is ignored rather than applied. The pasted
  links are excluded — their `xsec_token` is dated.
- **`_LogCapture`** — a duck-typed sink assigned to `xhs.print.func`. The
  engine's `source.module.tools.logging` calls `func.write(text, scroll_end=…)`
  for any non-`print` sink, so capturing progress needs only a `write()` method.
  This is how live logs reach the browser without touching engine code.
- **`ENGINE_DB_FILES`** — the engine's `DataRecorder` opens
  `ExploreData.db` in the download folder on `__aenter__` even when
  `record_data=False`. These bookkeeping DBs are excluded from the ZIP and from
  the "was anything actually downloaded?" check.

## Concurrency model

`XHS` is a process-wide singleton (`__new__` caches the instance) that owns
shared `httpx.AsyncClient`s and SQLite handles. Two jobs running concurrently
would reconfigure and close each other's clients. The runner therefore holds a
module-level `asyncio.Lock` (`ENGINE_LOCK`) for the entire duration of a job:
the HTTP layer stays single-session, while users can still queue any number of
jobs (each job's status is tracked independently in the `JOBS` dict).

## Lifecycle & cleanup

- Each job downloads into `mkdtemp(prefix="xhs_webui_")`; that directory is
  `rmtree`'d immediately after zipping.
- Finished ZIPs live in `<tempdir>/xhs_webui_zips/<job_id>.zip` and are removed
  after `JOB_TTL_SECONDS` (1 hour), swept on every new job.
- **Downloaded media never lands under the project tree** — it goes to the
  per-job temp dir and is deleted after zipping.
- The engine still opens its shared bookkeeping DBs in `Volume/`
  (`ExploreID.db`, `MappingData.db`) on start-up, exactly as the TUI/API/MCP/CLI
  do. With `download_record=False` and no mapping data, the Web UI writes **no
  rows** to them — they are created empty and carry no state between jobs.
- The engine's shared `Volume/Temp` scratch is used for in-flight chunks; a
  partial file may remain there if a download is interrupted (this is engine
  behaviour, identical across all modes).
