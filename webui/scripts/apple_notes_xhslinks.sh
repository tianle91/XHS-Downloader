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

# Core link-extraction pipeline: read text (note HTML) on stdin and print the
# xhslink.com short links it contains, one per line, deduped in first-seen
# order. The path stops at whitespace, quotes or angle brackets (note bodies are
# HTML, so links appear inside href="..." too); any trailing punctuation an
# editor glued on is stripped. `|| true` keeps a no-match `grep` from tripping
# `set -o pipefail`. Kept as one function with a single regex so the matching
# behaviour has one source of truth — exercised without a Mac via `--extract`
# (see webui/tests/test_apple_notes_script.py).
extract_links() {
  grep -Eo 'https?://xhslink\.com/[^"'"'"'<> ]+' \
    | sed -E 's/[.,;:)]+$//' \
    | awk '!seen[$0]++' || true
}

DELETE=0
ASSUME_YES=0
EXTRACT=0
for arg in "$@"; do
  case "$arg" in
    -d|--delete)  DELETE=1 ;;
    -y|--yes)     ASSUME_YES=1 ;;
    --extract)    EXTRACT=1 ;;   # internal: filter stdin only, no Notes access
    -h|--help)    usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; echo >&2; usage >&2; exit 2 ;;
  esac
done

# --extract runs the pure text pipeline over stdin and stops, so the extraction
# logic is testable on any platform. It deliberately sits before the macOS check.
if [[ "$EXTRACT" -eq 1 ]]; then
  extract_links
  exit 0
fi

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Error: this script only runs on macOS (it needs the Notes app)." >&2
  exit 1
fi

# Localised names of the "Recently Deleted" folder we skip. Add your locale's
# name here if it isn't listed and you see already-trashed notes coming back.
readonly TRASH_NAMES='{"Recently Deleted", "最近删除", "最近刪除"}'

# Dump the raw body (HTML) of every note across every account/folder, one note
# per line, skipping the Recently Deleted folder. We loop note-by-note (rather
# than coercing `body of every note` in one shot) so a single unreadable note —
# locked, empty, or from an IMAP/Exchange account — is skipped instead of
# aborting the whole run with an Apple Events error (e.g. -1741). The folder name
# is read in its own `try`: if that lookup fails we default to "" (treated as
# not-trash) and still keep the note, rather than dropping it.
notes_html="$(osascript <<APPLESCRIPT
tell application "Notes"
    set trashNames to ${TRASH_NAMES}
    set out to ""
    repeat with n in notes
        set fname to ""
        try
            set fname to name of container of n
        end try
        if fname is not in trashNames then
            try
                set out to out & (body of n) & linefeed
            end try
        end if
    end repeat
    return out
end tell
APPLESCRIPT
)"

# Extract the xhslink.com short links from the collected note HTML.
links="$(printf '%s\n' "$notes_html" | extract_links)"

if [[ -n "$links" ]]; then
  printf '%s\n' "$links"
else
  # Nothing matched — help distinguish "no access" from "no links". Diagnostics
  # go to stderr so they never pollute a piped/redirected link list.
  note_count="$(osascript -e 'tell application "Notes" to return count of notes' 2>/dev/null || echo '?')"
  {
    echo "No http://xhslink.com/... links found."
    echo "  Notes visible to the script: ${note_count}"
    if [[ "$note_count" == "0" || "$note_count" == "?" ]]; then
      echo "  That looks like an access problem. Grant control under"
      echo "  System Settings ▸ Privacy & Security ▸ Automation (Terminal → Notes),"
      echo "  make sure the Notes app is open and finished syncing, then re-run."
    else
      echo "  Access is fine, but none of those notes contained an xhslink.com link"
      echo "  (locked notes and the Recently Deleted folder are skipped)."
    fi
  } >&2
fi

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
        set fname to ""
        try
            set fname to name of container of n
        end try
        if fname is not in trashNames then
            try
                if (body of n) contains "xhslink.com" then
                    set out to out & (id of n) & linefeed
                end if
            end try
        end if
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
