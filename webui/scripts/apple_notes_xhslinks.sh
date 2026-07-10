#!/usr/bin/env bash
#
# apple_notes_xhslinks.sh
#
# Print every XiaoHongShu / RedNote short link (http://xhslink.com/...) found in
# your Apple Notes, one per line, deduplicated. The output is ready to paste
# straight into the XHS-Downloader batch Web UI links box.
#
# Notes in the "Recently Deleted" folder are ignored, so already-trashed notes
# never come back.
#
# Usage:
#   ./apple_notes_xhslinks.sh                 # print links to the terminal
#   ./apple_notes_xhslinks.sh | pbcopy        # copy them to the clipboard
#   ./apple_notes_xhslinks.sh > links.txt     # save them to a file
#   ./apple_notes_xhslinks.sh --delete        # print links, then trash the
#                                             #   notes that contained them
#   ./apple_notes_xhslinks.sh --delete --yes  # ...without the confirmation
#
# --delete moves each matched note to Notes' "Recently Deleted" folder, where it
# stays recoverable for ~30 days — it is not a permanent delete. The links are
# always printed *before* anything is deleted, so a single run still captures
# them.
#
# macOS only: it drives the Notes app through AppleScript (osascript). The first
# run pops up a permission prompt ("Terminal wants access to control Notes") —
# click OK. If you miss it, grant access under
# System Settings ▸ Privacy & Security ▸ Automation.
#
set -euo pipefail

usage() {
  sed -n '3,29p' "$0" | sed 's/^# \{0,1\}//'
}

DELETE=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    -d|--delete) DELETE=1 ;;
    -y|--yes)    ASSUME_YES=1 ;;
    -h|--help)   usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; echo >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Error: this script only runs on macOS (it needs the Notes app)." >&2
  exit 1
fi

# Localised names of the "Recently Deleted" folder we skip. Add your locale's
# name here if it isn't listed and you see already-trashed notes coming back.
readonly TRASH_NAMES='{"Recently Deleted", "最近删除", "最近刪除"}'

# Dump the raw body (HTML) of every note across every account/folder, one note
# per line, skipping the Recently Deleted folder. We loop note-by-note (rather
# than coercing `body of every note` in one shot) and wrap each read in `try`,
# so a single unreadable note — locked, empty, or from an IMAP/Exchange account
# — is skipped instead of aborting the whole run with an Apple Events error
# (e.g. -1741).
notes_html="$(osascript <<APPLESCRIPT
tell application "Notes"
    set trashNames to ${TRASH_NAMES}
    set out to ""
    repeat with n in notes
        try
            if (name of container of n) is not in trashNames then
                set out to out & (body of n) & linefeed
            end if
        end try
    end repeat
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

[[ "$DELETE" -eq 1 ]] || exit 0

# ---- Deletion --------------------------------------------------------------
# Collect the ids of the notes to trash: those that mention xhslink.com and are
# not already in Recently Deleted. Referencing notes by id (not list index)
# keeps deletion stable even as the collection shrinks.
ids_raw="$(osascript <<APPLESCRIPT
tell application "Notes"
    set trashNames to ${TRASH_NAMES}
    set out to ""
    repeat with n in notes
        try
            if (name of container of n) is not in trashNames then
                if (body of n) contains "xhslink.com" then
                    set out to out & (id of n) & linefeed
                end if
            end if
        end try
    end repeat
    return out
end tell
APPLESCRIPT
)"

ids=()
while IFS= read -r line; do
  [[ -n "$line" ]] && ids+=("$line")
done <<< "$ids_raw"

if [[ "${#ids[@]}" -eq 0 ]]; then
  echo "No notes contained xhslink.com links; nothing to delete." >&2
  exit 0
fi

if [[ "$ASSUME_YES" -ne 1 ]]; then
  if [[ ! -e /dev/tty ]]; then
    echo "Refusing to delete without a terminal to confirm on; re-run with --yes." >&2
    exit 1
  fi
  printf 'Move %d note(s) containing xhslink.com links to Recently Deleted? [y/N] ' \
    "${#ids[@]}" >&2
  read -r reply </dev/tty || reply=""
  case "$reply" in
    y|Y|yes|Yes|YES) ;;
    *) echo "Aborted; no notes were deleted." >&2; exit 0 ;;
  esac
fi

deleted="$(osascript /dev/stdin "${ids[@]}" <<'APPLESCRIPT'
on run argv
    tell application "Notes"
        set c to 0
        repeat with theID in argv
            try
                delete note id (theID as text)
                set c to c + 1
            end try
        end repeat
        return c
    end tell
end run
APPLESCRIPT
)"

echo "Moved ${deleted} note(s) to Recently Deleted (recoverable for ~30 days)." >&2
