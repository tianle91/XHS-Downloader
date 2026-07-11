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
  which only exists on a Mac. `bash` and `perl` are both preinstalled; nothing
  needs installing.
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

It walks your Notes **account by account and folder by folder** (into subfolders
too), reading each note's HTML body and skipping any it can't open (a locked
note, or one on an IMAP/Exchange account). It deliberately does **not** use the
app-wide note list, because that also returns trashed notes; walking real folders
and skipping the **Recently Deleted** folder is what keeps already-deleted notes
from resurfacing. From the bodies it reads, it extracts anything matching
`https?://xhslink.com/…` — whether the link is visible text or hidden inside an
`href="…"` attribute — trims trailing ASCII punctuation, and removes duplicates
while keeping first-seen order. Without `--delete` it never modifies your notes;
it only reads them.

The link body uses the **same terminator set as the engine's `SHORT` pattern**
(`source/application/app.py`): it stops at whitespace, quotes, angle brackets,
`` \ ^ ` { | } `` and CJK punctuation `，。；！？、【】《》`. That last part matters
for XHS shares — a plain-text link glued to Chinese text like
`…/6RRY1UzhcbG，看笔记` stops at the fullwidth comma instead of swallowing it. The
extraction uses `perl` with UTF-8 I/O so those multibyte terminators match
reliably (BSD `grep` bracket expressions are unreliable for multibyte input).

> One deliberate difference from the engine: the scheme is **required** here
> (`https?://`), whereas the engine also accepts a bare `xhslink.com/…`. Notes
> always store XHS shares with the scheme, and requiring it avoids matching
> `xhslink.com` embedded in another host's path (e.g. `foo.com/xhslink.com/…`).

> A locked note stays skipped until you unlock it in the Notes app; run the
> script again afterwards to pick up any links inside it.

> The Recently Deleted folder is matched **by name**. English and Simplified /
> Traditional Chinese are built in; if your Mac uses another language and you see
> trashed notes coming back, run `--list-folders` (below) to find the exact
> folder name and add it to `TRASH_NAMES` near the top of the script.

### Troubleshooting: what's being scanned

If the output looks wrong — nothing found, or notes you deleted still showing up
— run:

```bash
./apple_notes_xhslinks.sh --list-folders
```

It prints each account and its folder tree with per-folder note counts, and
marks the folder(s) treated as *Recently Deleted*:

```
ACCOUNT: iCloud
  Notes (128 notes)
  Travel (14 notes)
  Recently Deleted (37 notes)   <- skipped (Recently Deleted)
```

If your Recently Deleted folder is **not** marked as skipped, its name isn't in
`TRASH_NAMES` yet — add it and re-run.

### Tests

The AppleScript that reads Notes needs a real Mac, but the link-extraction
pipeline is exposed through an internal `--extract` mode that filters **stdin**
only (no Notes access), so it runs anywhere:

```bash
echo 'see http://xhslink.com/o/6RRY1UzhcbG.' | ./apple_notes_xhslinks.sh --extract
# -> http://xhslink.com/o/6RRY1UzhcbG
```

[`../tests/test_apple_notes_script.py`](../tests/test_apple_notes_script.py)
pipes sample note HTML through that mode to pin down the matching behaviour
(href vs. plain text, trailing punctuation, dedup, ignoring non-xhslink URLs).
Run it with the rest of the Web UI suite from the repository root:

```bash
uv run python -m unittest discover webui/tests
```
