# XHS-Downloader · Batch Web UI

A self-contained web interface for **batch downloading** XiaoHongShu / RedNote
works with rich file & folder formatting options. Everything is packed into a
single **ZIP file** that the user downloads from the browser.

It is inspired by tools like [dlbunny](https://dlbunny.com/en/xhs) and is built
directly on top of the existing `source.XHS` engine — no engine code is
modified. **All feature code lives inside this `webui/` folder.**

![Web UI screenshot](screenshot.png)

## Features

- **Batch input** — paste many links at once (spaces or new lines). Supports
  `explore`, `discovery/item`, `user/profile` and `xhslink.com` short links.
  Invalid text is ignored automatically.
- **One ZIP download** — every downloaded work is packed into a single ZIP,
  named after your root folder.
- **File name builder** — click fields (publish time, author, title, likes,
  tags, …) in the order you want them; the file-name format updates live.
- **Folder organisation** — optionally put each work in its own sub-folder
  and/or group works by author.
- **Format control** — choose image format (JPEG / PNG / WEBP / AUTO / HEIC /
  AVIF) and video quality preference (resolution / bitrate / size).
- **Media toggles** — enable/disable images, videos and live photos
  independently.
- **Extras** — write the publish date to file mtimes, and optionally include a
  `metadata.json` describing every work.
- **Advanced** — optional Cookie (for restricted / higher-resolution content)
  and proxy.
- **Live progress** — per-work progress bar, success/fail counts and a live log.

## Running

From the repository root, after installing the project dependencies
(`uv sync` or `pip install -r requirements.txt`):

```bash
python -m webui
```

Then open <http://127.0.0.1:5557>.

Configuration via environment variables:

| Variable          | Default     | Description        |
| ----------------- | ----------- | ------------------ |
| `XHS_WEBUI_HOST`  | `127.0.0.1` | Bind host          |
| `XHS_WEBUI_PORT`  | `5557`      | Bind port          |

> The default host `127.0.0.1` keeps the server local-only. Set
> `XHS_WEBUI_HOST=0.0.0.0` if you intentionally want to expose it on your
> network.

## How it works

1. The browser posts the links + options to `POST /api/jobs`, which starts a
   background job and returns a `job_id`.
2. The job configures a `source.XHS` engine instance with a **unique temporary
   working directory**, disables the shared "download history" DB
   (`download_record=False`) so nothing is ever skipped, and downloads every
   link, capturing progress logs.
3. When finished, the working folder is zipped (engine bookkeeping DBs
   excluded) and served from `GET /api/jobs/{id}/download`.
4. The temporary working folder is deleted immediately after zipping; finished
   ZIPs are cleaned up automatically after 1 hour.

Because the engine is a process-wide singleton with shared HTTP clients, jobs
are serialised with an `asyncio` lock — multiple users can queue jobs, and they
run one after another.

## API

| Method | Path                        | Purpose                          |
| ------ | --------------------------- | -------------------------------- |
| `GET`  | `/`                         | The web UI                       |
| `GET`  | `/api/fields`               | Available formatting field ids   |
| `POST` | `/api/jobs`                 | Start a batch job → `{job_id}`   |
| `GET`  | `/api/jobs/{id}`            | Job status / progress / logs     |
| `GET`  | `/api/jobs/{id}/download`   | Download the resulting ZIP       |

## Notes & limitations

- A Cookie is not required, but improves reliability and unlocks
  high-resolution video. Without one, some works may return low-resolution
  files or fail with `403`.
- XHS links carry a dated `xsec_token`; use freshly-copied links for best
  results.
- For personal, authorised use only. Respect XiaoHongShu's terms and the
  original creators' rights.
