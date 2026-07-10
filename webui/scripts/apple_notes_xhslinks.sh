#!/usr/bin/env bash
#
# apple_notes_xhslinks.sh
#
# Print every XiaoHongShu / RedNote short link (http://xhslink.com/...) found in
# your Apple Notes, one per line, deduplicated. The output is ready to paste
# straight into the XHS-Downloader batch Web UI links box.
#
# Usage:
#   ./apple_notes_xhslinks.sh                 # print links to the terminal
#   ./apple_notes_xhslinks.sh | pbcopy        # copy them to the clipboard
#   ./apple_notes_xhslinks.sh > links.txt     # save them to a file
#
# macOS only: it drives the Notes app through AppleScript (osascript). The first
# run pops up a permission prompt ("Terminal wants access to control Notes") —
# click OK. If you miss it, grant access under
# System Settings ▸ Privacy & Security ▸ Automation.
#
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Error: this script only runs on macOS (it needs the Notes app)." >&2
  exit 1
fi

# Dump the raw body (HTML) of every note across every account/folder, one note
# per line. AppleScript joins the list with a newline so a note's own newlines
# don't matter — we only care about the URLs inside.
notes_html="$(osascript <<'APPLESCRIPT'
tell application "Notes"
    set AppleScript's text item delimiters to linefeed
    set out to (body of every note) as text
    set AppleScript's text item delimiters to ""
    return out
end tell
APPLESCRIPT
)"

# Extract xhslink.com short links. The path stops at whitespace, quotes or angle
# brackets (note bodies are HTML, so links appear inside href="..." too). We then
# strip any trailing punctuation an editor may have glued on, and dedupe while
# preserving first-seen order.
printf '%s\n' "$notes_html" \
  | grep -Eo 'https?://xhslink\.com/[^"'"'"'<> ]+' \
  | sed -E 's/[.,;:)]+$//' \
  | awk '!seen[$0]++'
