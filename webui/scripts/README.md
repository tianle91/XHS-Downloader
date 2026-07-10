# webui/scripts

Small helper scripts for feeding links into the [batch Web UI](../README.md).

## `apple_notes_xhslinks.sh`

Print every XiaoHongShu / RedNote short link (`http://xhslink.com/...`) stored in
your **Apple Notes**, one per line and deduplicated, ready to paste into the Web
UI's links box.

A common workflow is to share a XiaoHongShu post to Apple Notes from your iPhone
("Share ▸ Notes"), which drops a `http://xhslink.com/o/…` link into a note. This
script collects all of those in one go so you can batch-download them.

### Requirements

- **macOS** — the script drives the Notes app through AppleScript (`osascript`),
  which only exists on a Mac. `bash`, `grep`, `sed` and `awk` are all built in;
  nothing needs installing.
- Your notes must be readable in the **Notes** app on this Mac (any account or
  folder counts).

### Usage

From this folder:

```bash
./apple_notes_xhslinks.sh                 # print the links to the terminal
./apple_notes_xhslinks.sh | pbcopy        # copy them straight to the clipboard
./apple_notes_xhslinks.sh > links.txt     # save them to a file
```

The **first run** shows a macOS prompt — *"Terminal wants access to control
Notes"* — click **OK**. If you dismiss it by accident, re-enable access under
**System Settings ▸ Privacy & Security ▸ Automation**.

### Then, in the Web UI

1. Start the Web UI from the repository root: `uv run python -m webui`, then open
   <http://127.0.0.1:5557>.
2. Paste the script's output into the **links** box (it accepts many links
   separated by spaces or new lines) and start the batch.

The Web UI ignores any non-link text, dedupes, and skips links already
downloaded, so pasting the whole list repeatedly is safe.

### How it works

It asks Notes for the HTML body of every note, then extracts anything matching
`https?://xhslink.com/…` — whether the link is visible text or hidden inside an
`href="…"` attribute — trims trailing punctuation, and removes duplicates while
keeping first-seen order. It never modifies your notes; it only reads them.
