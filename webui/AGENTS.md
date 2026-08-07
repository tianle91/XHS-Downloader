# Working on the Web UI

Developer notes for `webui/`. For what the thing *does*, read
[`README.md`](README.md). For the request/data flow in detail, read
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## The two rules

### 1. Stay inside `webui/`

**All feature code lives inside `webui/`.** Nothing under `source/` is modified.
Where the engine lacks a hook, the Web UI sets the attribute on the live
instance at run time rather than patching the engine:

| Need                          | How it is done                                                          |
| ----------------------------- | ----------------------------------------------------------------------- |
| Capture progress logs         | `xhs.print.func = _LogCapture(job)`                                     |
| Choose the date format        | `xhs.explore.time_format = …` (not an `XHS(...)` param)                 |
| Send files to a link's folder | `xhs.downloader.folder = …`, retargeted per link                        |
| Decide if a token is a link   | `looks_like_xhs_link` → `XHS.SHORT` / `LINK_*` / `SHARE_*` / `USER_*`  |
| Soft-404 `error_code` in logs | `_unresolved_link_detail` → `request_url` **only** when `SHORT` matches |

If you find yourself wanting to edit `source/`, look for an attribute to set
instead. The one exception outside this folder is `/Downloads/` in
`.gitignore` — the default download directory sits at the repo root.

### 2. Reuse the engine as much as possible

The Web UI is a **front-end** on `source.application.app.XHS`, not a parallel
downloader. Prefer calling into the engine over re-implementing what it already
knows:

| Do in the engine / via its API              | Do in the Web UI                                      |
| ------------------------------------------- | ----------------------------------------------------- |
| Link recognition (`SHORT` / `LINK_*` / …)   | Job accounting, folders, Retry UI                     |
| Short-link redirect + note resolution       | Classify empty `extract_links` results (prose vs fail)|
| Fetching and writing media (`extract`)      | Present soft-404 `error_code` in logs                 |
| `name_format` tokens, image/video options   | Map friendly UI ids → those same engine tokens        |

**Smell test:** if you are about to add a regex, HTTP client, HTML parser, or
download path that the engine already has, stop — call or reference the engine
instead. A parallel copy will drift the first time `source/` changes.

Concrete don'ts that follow from this:

- **Do not re-encode engine URL knowledge** in a host substring or home-grown
  regex. Use `XHS.SHORT` / `LINK_*` / `SHARE_*` / `USER_*`.
- **Do not resolve or download outside `extract_links` / `extract`.** Diagnostic
  `request_url` is allowed only to read a soft-404 `error_code` after a short
  link already failed, and only when `SHORT` matches.
- **Do not fork option semantics.** Browser controls map onto real `XHS(...)`
  kwargs / live attributes; inventing Web UI–only meanings for the same names
  will disagree with TUI/CLI.

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
   so the pairing has to be kept. When `extract_links` returns nothing:
   - **Prose** (does not match any engine URL regex) is logged and dropped from
     `job.total`.
   - **A token the engine would have tried** (`looks_like_xhs_link`, which
     reuses `XHS.SHORT` / `LINK_*` / `SHARE_*` / `USER_*`) is counted as
     **failed** and appended to `failed_links` for Retry.
   - **Short-link soft-404s** may call `request_url` once more *only* when
     `XHS.SHORT` matches, so the log can show `error_code` /
     `error_msg`. Non-short unresolved shapes get a generic Failed reason —
     do not re-fetch them (that would double an `@retry`'d request). Download
     resolution stays with `extract_links` / `extract`.
4. For each link the engine's file destination is retargeted at
   `<download dir>/<folder_for_link(token)>`, and progress logs are captured. A
   link whose folder already holds files is skipped *before* it is resolved, so
   re-running a batch of short links issues no redirect requests at all.
5. A link that yields no work — or that matched an engine URL shape but could
   not be resolved — is recorded in the job's `failed_links` as pasted, so retry
   re-submits what the user gave us. The browser polls this and offers to
   re-submit them as a fresh job. Its folder is removed if nothing was written,
   so the next run does not mistake it for finished.
6. The engine's own working directory is a throwaway temp dir — it only ever
   holds `ExploreData.db` — and is deleted when the job ends. Job records are
   dropped from memory after 1 hour.

Because the engine is a process-wide singleton with shared HTTP clients, jobs
are serialised with an `asyncio` lock.

### Things to be careful of

- **Hold a reference to the job task.** `asyncio` keeps only a weak reference to
  a task, so a fire-and-forget `create_task` can be collected mid-`await` and
  the browser polls a job that never finishes. `RUNNING_TASKS` keeps it alive.
- **Only evict finished jobs.** `_cleanup_expired` checks `Job.finished()` as
  well as the TTL: a batch running longer than an hour would otherwise be swept
  by the next job's cleanup and start 404-ing at the browser.
- **Validate at the boundary, not at use.** Every enum on `BatchOptions` rejects
  unknown values with a `field_validator`, so `engine_kwargs` only maps names.
  Do not reintroduce a silent fallback: it turns a client's typo into a
  wrong-format download.
- **Reuse the engine; do not fork its logic.** Before adding URL matching,
  HTTP, parsing, or download behaviour in `webui/`, check whether `XHS` (or a
  live attribute on the instance) already does it. Prefer calling that over a
  parallel copy that will drift.
- **Reuse engine URL regexes; do not invent a host matcher.** A looser
  `xiaohongshu.com` substring diverges from what `extract_links` accepts
  (explore / discovery/item / user/profile / xhslink only).
- **Gate any diagnostic `request_url` on `XHS.SHORT`.** `extract_links` already
  followed the short-link redirect and discarded the soft-404 URL;
  re-fetching is solely to read `error_code` for the log. Ungated re-fetches
  double an `@retry`'d HTTP loop on every failed link.
- **Some filesystem work is still synchronous** inside `_run_job` —
  `mkdtemp`, `rmtree`, `_write_metadata`, and the `_has_media` short-circuit.
  They are small. The one walk that is not, `_folder_stats` (`rglob` + `stat`
  per file), runs in `asyncio.to_thread` so it does not stall status polls. If
  you add another whole-tree walk, put it on a thread too.

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
and calls the identical pipeline the other modes use. Batching, per-link
folders, skip-what-exists, and the Retry UI are Web UI concerns; link
recognition, resolution, and downloading are not.

```
webui/app.py
   └─ from source import XHS
        XHS(**engine_kwargs)          # same constructor the TUI/API/MCP/CLI call
        └─ xhs.extract_links(text)    # same link parsing (explore/item/user/xhslink)
        └─ xhs.extract(link, ...)     # same Download / Image / Video / Html modules
```

### What it shares with the other modes

- **The engine and most options.** `name_format`, `image_format`,
  `video_preference`, `image/video/live_download`, `write_mtime`, `cookie`,
  `proxy` are the exact `XHS(...)` parameters documented in the project
  README's *配置文件 / Settings* table.
- **One work per link.** Every pattern `extract_links()` matches is a single
  work — `user/profile/<id>/<note id>` is a note viewed through its author's
  profile, not an account listing, and a bare profile URL matches nothing.
  (`source/application/user_posted.py`, which would crawl an account's posts,
  is not imported anywhere.) So `extract()` never returns more than one work,
  and `folder_mode` / `author_archive` are pinned off in `engine_kwargs`: each
  would nest exactly one redundant directory inside the link's own folder.
- **The `name_format` field tokens.** The UI's friendly ids (`title`, `author`,
  `likes`, …) map to the same Chinese tokens the engine expects, via
  `NAME_FIELDS` in `app.py`. A format built in the UI behaves identically to one
  set in `settings.json`.
- **Link parsing and download logic.** No copies or re-implementations — the UI
  reuses `extract_links()` and `extract()` verbatim, so any engine fix or new
  supported link type is picked up automatically. URL-*shape* checks for empty
  `extract_links` results also go through the engine's class regexes
  (`looks_like_xhs_link`), not a Web UI–owned host matcher.
  `resolve_failure_detail` is presentation-only (parse `error_code` /
  `error_msg` from a soft-404 URL for the job log); it does not drive downloads.

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
