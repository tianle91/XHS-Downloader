# XHS-Downloader · Batch Web UI

A self-contained web interface for **batch downloading** XiaoHongShu / RedNote
works with rich file naming options. Files are written **straight to a folder on
disk** — one folder per link, all inside a single download directory.

It is inspired by tools like [dlbunny](https://dlbunny.com/en/xhs) and is built
directly on top of the existing `source.XHS` engine — no engine code is
modified. **All feature code lives inside this `webui/` folder.**

![Web UI screenshot](screenshot.png)

Building on it? See [`AGENTS.md`](AGENTS.md).

## Features

- **Batch input** — paste many links at once (spaces or new lines). Supports
  `explore`, `discovery/item`, `user/profile` and `xhslink.com` short links.
  Invalid text is ignored automatically.
- **A folder per link** — every link downloads into its own folder, named after
  the link **exactly as you pasted it**, inside one shared download directory
  (`<repo>/Downloads` by default). The scheme, `www.` and the query string are
  stripped, so `https://www.xiaohongshu.com/explore/65a1b2c3?xsec_token=…`
  becomes `xiaohongshu.com_explore_65a1b2c3`, and `http://xhslink.com/o/3Gx5N7WOIHi`
  becomes `xhslink.com_o_3Gx5N7WOIHi` — a short link is *not* renamed to the
  canonical URL it redirects to.
- **Already-downloaded links are skipped** — because a link maps to a stable
  folder name (the dated `xsec_token` is ignored), re-running a batch only
  fetches what is missing. Tick **Re-download links already saved** to force it.
- **Partial failures are recoverable** — links that could not be downloaded are
  listed when the job ends, with a **Retry failed links** button that re-runs
  only those. A link that downloads nothing leaves no folder behind, so it is
  never mistaken for "already done".
- **File name builder** — click fields (publish time, author, title, likes,
  tags, …) in the order you want them; an example file name updates live. Click
  a field again to remove it; clear them all to start a new order from scratch.
- **Date format** — render the publish/update time fields as
  `2024-01-31_18:30:45`, `2024-01-31`, `20240131`, `2024.01.31`, `2024-01`,
  `31-01-2024`, … It applies to file names and `metadata.json`; file mtimes are
  always the exact publish timestamp.
- **Format control** — choose image format (JPEG / PNG / WEBP / AUTO / HEIC /
  AVIF) and video quality preference (resolution / bitrate / size).
- **Media toggles** — enable/disable images, videos and live photos
  independently.
- **Extras** — put each work in its own sub-folder, write the publish date to
  file mtimes, and optionally include a `metadata.json` describing every work.
- **Advanced** — optional Cookie (for restricted / higher-resolution content)
  and proxy.
- **Remembered settings** — every option above is saved in your browser and
  restored next time. The pasted links are not (their `xsec_token` goes stale).
  *Reset to defaults* in the page footer clears them.
- **Live progress** — per-work progress bar, saved/skipped/failed counts and a
  live log.

## Running

From the repository root:

```bash
uv run python -m webui
```

`uv run` installs/syncs the project dependencies into the virtual environment on
first use, so no separate install step is needed. If you manage the environment
yourself (`pip install -r requirements.txt`), plain `python -m webui` works too.

Then open <http://127.0.0.1:5557>.

> **That's the only command you need.** The Web UI runs the `XHS` engine
> **in-process**, so you do **not** have to start `uv run python main.py api`
> (the `:5556` REST server) or any other mode first.

## Where files go

Everything lands in one directory, `<repo>/Downloads` unless you say otherwise:

```
Downloads/
├── xiaohongshu.com_explore_65a1b2c3/
│   ├── 2024-01-31_18.30.45_Alice_Autumn-in-Kyoto.jpg
│   └── metadata.json                     # only with "Include metadata.json"
└── xhslink.com_o_3Gx5N7WOIHi/
    └── 2024-02-02_09.15.00_Bob_Ramen.mp4
```

The **file name format** options apply *inside* each folder. The folder name
itself always comes from the link you pasted, so you can find a work again by
searching for the link you copied.

Text that is not a link is ignored, so you can paste links with prose around
them.

Re-running a batch skips any link whose folder already holds files, so you can
paste the same list again and only fetch what is missing. **Re-download links
already saved** overrides that; it writes new files alongside whatever is
already in the folder rather than emptying it first.

## Configuration

| Variable                 | Default             | Description             |
| ------------------------ | ------------------- | ----------------------- |
| `XHS_WEBUI_HOST`         | `127.0.0.1`         | Bind host               |
| `XHS_WEBUI_PORT`         | `5557`              | Bind port               |
| `XHS_WEBUI_DOWNLOAD_DIR` | `<repo>/Downloads`  | Where files are written |

`XHS_WEBUI_DOWNLOAD_DIR` may be absolute, or relative to the repository root.
The resolved path is shown at the top of the page.

> The default host `127.0.0.1` keeps the server local-only. Set
> `XHS_WEBUI_HOST=0.0.0.0` if you intentionally want to expose it on your
> network — note that the download directory is chosen by the *server*, never by
> the browser, so a remote client cannot pick where files land.

## Your settings, and your Cookie

The form is remembered **in your browser only**. The server keeps no per-user
state, so nothing is shared between browsers and there is no settings file to
back up. Your `settings.json` is never read or written, and the Web UI cannot
mark works as "already downloaded" for the TUI/CLI.

> **The Cookie is saved too**, verbatim. If you copied it while logged in it
> contains `web_session` — your XiaoHongShu login — and it will sit in browser
> storage for any script on the origin to read. High-resolution video does
> **not** require a logged-in account, so a cookie copied from a logged-out
> session is enough for most users. Use *Reset to defaults* to clear it.

## Notes & limitations

- A Cookie is not required, but improves reliability and unlocks
  high-resolution video. Without one, some works may return low-resolution
  files or fail with `403`.
- **Retries happen at two levels.** The engine already retries each HTTP request
  and each file download internally (`max_retry`, default 5, so up to six
  attempts) — the Web UI inherits that and does not change it. *Retry failed
  links* is the outer level: it re-runs whole links that still failed after the
  engine gave up, which is what you want for an expired `xsec_token` or a `403`
  you have since fixed by pasting a Cookie. Retrying a genuinely dead link will
  simply fail again.
- XHS links carry a dated `xsec_token`; use freshly-copied links for best
  results.
- Jobs run one at a time. The engine is a process-wide singleton with shared
  HTTP clients, so concurrent jobs would trample each other; they queue instead.
- For personal, authorised use only. Respect XiaoHongShu's terms and the
  original creators' rights.
