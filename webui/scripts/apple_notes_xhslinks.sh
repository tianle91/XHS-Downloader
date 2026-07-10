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
LIST_FOLDERS=0
for arg in "$@"; do
  case "$arg" in
    -d|--delete)   DELETE=1 ;;
    -y|--yes)      ASSUME_YES=1 ;;
    --extract)     EXTRACT=1 ;;        # internal: filter stdin only, no Notes access
    --list-folders) LIST_FOLDERS=1 ;;  # diagnostic: print the account/folder tree
    -h|--help)     usage; exit 0 ;;
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
# name here if it isn't listed and you see already-trashed notes coming back;
# run `--list-folders` to see the exact folder names on this Mac.
readonly TRASH_NAMES='{"Recently Deleted", "最近删除", "最近刪除"}'

# --list-folders: print the account/folder tree with note counts, marking the
# folder(s) treated as Recently Deleted. A diagnostic to confirm what is and
# isn't being scanned.
if [[ "$LIST_FOLDERS" -eq 1 ]]; then
  osascript <<APPLESCRIPT
tell application "Notes"
    set trashNames to ${TRASH_NAMES}
    set out to ""
    repeat with acct in accounts
        set out to out & "ACCOUNT: " & (name of acct) & linefeed
        try
            repeat with f in folders of acct
                set out to out & my listFolder(f, trashNames, "  ")
            end repeat
        end try
    end repeat
    return out
end tell

on listFolder(theFolder, trashNames, indent)
    set acc to ""
    tell application "Notes"
        set fname to "?"
        try
            set fname to name of theFolder
        end try
        set nc to -1
        try
            set nc to count of notes of theFolder
        end try
        set mark to ""
        if fname is in trashNames then set mark to "   <- skipped (Recently Deleted)"
        set acc to indent & fname & " (" & nc & " notes)" & mark & linefeed
        try
            repeat with sub in folders of theFolder
                set acc to acc & my listFolder(sub, trashNames, indent & "  ")
            end repeat
        end try
    end tell
    return acc
end listFolder
APPLESCRIPT
  exit 0
fi

# Dump the raw body (HTML) of every note, one per line. We walk accounts →
# folders → subfolders and read each note's body inside its own `try`, rather
# than coercing `body of every note` in one shot: that keeps a single unreadable
# note (locked, empty, or from an IMAP/Exchange account) from aborting the whole
# run with an Apple Events error (e.g. -1741). Walking real folders — instead of
# the app-level `notes`, which also returns trashed notes — is what excludes the
# Recently Deleted folder: a trashed note lives only there, so skipping that
# folder by name drops it structurally, with no dependency on a per-note
# `container` lookup (which can fail).
notes_html="$(osascript <<APPLESCRIPT
tell application "Notes"
    set trashNames to ${TRASH_NAMES}
    set out to ""
    repeat with acct in accounts
        try
            repeat with f in folders of acct
                set out to out & my dumpFolder(f, trashNames)
            end repeat
        end try
    end repeat
    return out
end tell

on dumpFolder(theFolder, trashNames)
    set acc to ""
    tell application "Notes"
        try
            if (name of theFolder) is in trashNames then return ""
        end try
        try
            repeat with n in notes of theFolder
                try
                    set acc to acc & (body of n) & linefeed
                end try
            end repeat
        end try
        try
            repeat with sub in folders of theFolder
                set acc to acc & my dumpFolder(sub, trashNames)
            end repeat
        end try
    end tell
    return acc
end dumpFolder
APPLESCRIPT
)"

# Extract the xhslink.com short links from the collected note HTML.
links="$(printf '%s\n' "$notes_html" | extract_links)"

if [[ -n "$links" ]]; then
  printf '%s\n' "$links"
else
  # Nothing matched — help distinguish "no access" from "no links". Diagnostics
  # go to stderr so they never pollute a piped/redirected link list.
  {
    echo "No http://xhslink.com/... links found."
    if [[ -z "$notes_html" ]]; then
      echo "  No note content was read at all — likely an access problem. Grant"
      echo "  control under System Settings ▸ Privacy & Security ▸ Automation"
      echo "  (Terminal → Notes), make sure the Notes app is open and finished"
      echo "  syncing, then re-run. Use --list-folders to see what is visible."
    else
      echo "  Notes were read, but none contained an xhslink.com link (locked"
      echo "  notes and the Recently Deleted folder are skipped)."
    fi
  } >&2
fi

[[ "$DELETE" -eq 1 ]] || exit 0

# ---- Deletion --------------------------------------------------------------
# Collect the ids of the notes to trash: those that mention xhslink.com, walking
# real folders so the Recently Deleted folder is excluded (same reasoning as the
# body dump above). Referencing notes by id (not list index) keeps deletion
# stable even as the collection shrinks.
ids_raw="$(osascript <<APPLESCRIPT
tell application "Notes"
    set trashNames to ${TRASH_NAMES}
    set out to ""
    repeat with acct in accounts
        try
            repeat with f in folders of acct
                set out to out & my collectFolder(f, trashNames)
            end repeat
        end try
    end repeat
    return out
end tell

on collectFolder(theFolder, trashNames)
    set acc to ""
    tell application "Notes"
        try
            if (name of theFolder) is in trashNames then return ""
        end try
        try
            repeat with n in notes of theFolder
                try
                    if (body of n) contains "xhslink.com" then
                        set acc to acc & (id of n) & linefeed
                    end if
                end try
            end repeat
        end try
        try
            repeat with sub in folders of theFolder
                set acc to acc & my collectFolder(sub, trashNames)
            end repeat
        end try
    end tell
    return acc
end collectFolder
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

# Delete by id, passing the ids as arguments. The AppleScript is written to a
# real temp file rather than piped in: osascript cannot reliably read its script
# from /dev/stdin (a heredoc), which fails with "I/O error (bummers)".
delete_script="$(mktemp "${TMPDIR:-/tmp}/xhslink_delete.XXXXXX")"
trap 'rm -f "$delete_script"' EXIT
cat >"$delete_script" <<'APPLESCRIPT'
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

deleted="$(osascript "$delete_script" "${ids[@]}")"

echo "Moved ${deleted} note(s) to Recently Deleted (recoverable for ~30 days)." >&2
