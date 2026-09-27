#!/usr/bin/env bash
#
# Pryxor — SQLite backup script.
#
# Creates a consistent copy of the database via `VACUUM INTO`.
# Works even if Pryxor is running (WAL mode enabled).
#
# Usage:
#   bash scripts/backup.sh
#   bash scripts/backup.sh --state /data/pryxor_state.sqlite3 --out /backup
#   bash scripts/backup.sh --keep 30
#
# Exit codes:
#   0 = success
#   1 = error (DB missing, VACUUM failed, etc.)

set -euo pipefail

# --- Defaults ---------------------------------------------------------
STATE_PATH="${PRYXOR_STATE_PATH:-./pryxor_state.sqlite3}"
OUT_DIR="${PRYXOR_BACKUP_DIR:-./backups}"
KEEP="${PRYXOR_BACKUP_KEEP:-14}"
COMPRESS=true

# --- Parse args -------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --state)   STATE_PATH="$2"; shift 2 ;;
        --out)     OUT_DIR="$2"; shift 2 ;;
        --keep)    KEEP="$2"; shift 2 ;;
        --no-gzip) COMPRESS=false; shift ;;
        -h|--help)
            sed -n '2,15p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

# --- Verifications ----------------------------------------------------
if [[ ! -f "$STATE_PATH" ]]; then
    echo "❌ State DB not found: $STATE_PATH" >&2
    exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "❌ sqlite3 CLI not found. Install it (apt install sqlite3 / brew install sqlite)." >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

# --- File names -------------------------------------------------------
# Granularity: seconds + PID. Two runs in the same second never collide.
TIMESTAMP="$(date -u +"%Y%m%dT%H%M%S")"
SUFFIX="$$"
BACKUP="${OUT_DIR}/pryxor_backup_${TIMESTAMP}_${SUFFIX}.sqlite3"

if [[ "$COMPRESS" == "true" ]]; then
    FINAL_FILE="${BACKUP}.gz"
else
    FINAL_FILE="${BACKUP}"
fi

# --- Backup (VACUUM INTO) --------------------------------------------
echo "▶ Backing up $STATE_PATH → $BACKUP"

# VACUUM INTO refuses to overwrite an existing file.
rm -f "$BACKUP"

sqlite3 "$STATE_PATH" "VACUUM INTO '$BACKUP';"

if [[ ! -f "$BACKUP" ]]; then
    echo "❌ VACUUM INTO failed (no output file)." >&2
    exit 1
fi

SIZE_BEFORE="$(du -h "$BACKUP" | cut -f1)"
echo "  ✅ Snapshot: $SIZE_BEFORE"

# --- Optional compression --------------------------------------------
if [[ "$COMPRESS" == "true" ]]; then
    gzip -f "$BACKUP"
    SIZE_AFTER="$(du -h "$FINAL_FILE" | cut -f1)"
    echo "  ✅ Compressed: $SIZE_AFTER"
fi

# --- Rotation ---------------------------------------------------------
if [[ "$KEEP" -gt 0 ]]; then
    echo "▶ Rotating (keeping last $KEEP backups)"
    # -t sorts by mtime descending, newest first. Skip the first $KEEP,
    # delete the rest. Compatible with GNU coreutils and BSD ls.
    mapfile -t ALL < <(ls -1t "${OUT_DIR}"/pryxor_backup_*.sqlite3* 2>/dev/null || true)
    if (( ${#ALL[@]} > KEEP )); then
        for old in "${ALL[@]:KEEP}"; do
            rm -f -- "$old"
            echo "  🗑  Removed: $old"
        done
    fi
fi

echo ""
echo "✅ Backup complete: $FINAL_FILE"
exit 0