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

### Deleting notes after export

Pass `--delete` to clean up once you've grabbed the links — it moves every note
that contained an `xhslink.com` link to Notes' **Recently Deleted** folder:

```bash
./apple_notes_xhslinks.sh --delete        # print links, then confirm and trash
./apple_notes_xhslinks.sh --delete --yes  # ...skipping the confirmation prompt
```

- The links are **always printed first**, so the same run still gives you
  everything to paste into the Web UI before anything is removed.
- Deletion is **recoverable**: notes go to *Recently Deleted* and stay there for
  ~30 days — this is not a permanent delete. Empty that folder yourself if you
  want them gone for good.
- Without `--yes` you get a `Move N note(s)…? [y/N]` prompt; anything other than
  `y` aborts and deletes nothing.
- Status messages (the confirmation, counts) go to **stderr**, so
  `./apple_notes_xhslinks.sh --delete --yes | pbcopy` still copies only the links.

### Then, in the Web UI

1. Start the Web UI from the repository root: `uv run python -m webui`, then open
   <http://127.0.0.1:5557>.
2. Paste the script's output into the **links** box (it accepts many links
   separated by spaces or new lines) and start the batch.

The Web UI ignores any non-link text, dedupes, and skips links already
downloaded, so pasting the whole list repeatedly is safe.

### How it works

It walks your notes one at a time and reads the HTML body of each, skipping any
it can't open (a locked note, or one on an IMAP/Exchange account) and any note in
the **Recently Deleted** folder — so already-trashed notes never resurface. From
that it extracts anything matching `https?://xhslink.com/…` — whether the link is
visible text or hidden inside an `href="…"` attribute — trims trailing
punctuation, and removes duplicates while keeping first-seen order. Without
`--delete` it never modifies your notes; it only reads them.

> A locked note stays skipped until you unlock it in the Notes app; run the
> script again afterwards to pick up any links inside it.

> The Recently Deleted folder is matched by name. If your Mac's language isn't
> English or Chinese, add your locale's folder name to `TRASH_NAMES` near the top
> of the script so those notes stay excluded.
