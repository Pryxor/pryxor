#!/usr/bin/env bash
#
# Pryxor — SQLite restore script.
#
# Restaure une DB sauvegardée par `backup.sh`.
#
# ⚠️ Pryxor doit être ARRÊTÉ avant de lancer ce script.
#
# Usage :
#   bash scripts/restore.sh /backup/pryxor_backup_20260918T120000Z.sqlite3.gz
#
# Exit codes :
#   0 = succès
#   1 = erreur

set -euo pipefail

# --- Defaults ---------------------------------------------------------
STATE_PATH="${PRYXOR_STATE_PATH:-./pryxor_state.sqlite3}"

# --- Args -------------------------------------------------------------
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <backup-file> [--state <path>]" >&2
    exit 1
fi

BACKUP_FILE="$1"
shift

while [[ $# -gt 0 ]]; do
    case "$1" in
        --state) STATE_PATH="$2"; shift 2 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

# --- Vérifications ----------------------------------------------------
if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "❌ Backup file not found: $BACKUP_FILE" >&2
    exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "❌ sqlite3 CLI not found." >&2
    exit 1
fi

# --- Confirmation -----------------------------------------------------
echo "⚠️  This will REPLACE $STATE_PATH with:"
echo "     $BACKUP_FILE"
echo ""
printf "Type 'yes' to continue: "
read -r confirmation
if [[ "$confirmation" != "yes" ]]; then
    echo "Aborted."
    exit 0
fi

# --- Décompression si nécessaire --------------------------------------
WORK_FILE="$BACKUP_FILE"
TMP_FILE=""
if [[ "$BACKUP_FILE" == *.gz ]]; then
    TMP_FILE="$(mktemp -t pryxor_restore_XXXXXX.sqlite3)"
    echo "▶ Decompressing..."
    gunzip -c "$BACKUP_FILE" > "$TMP_FILE"
    WORK_FILE="$TMP_FILE"
fi

# --- Validation : est-ce une DB Pryxor ? ------------------------------
echo "▶ Validating backup..."
if ! sqlite3 "$WORK_FILE" "PRAGMA integrity_check;" | grep -q "^ok$"; then
    echo "❌ Backup file is not a valid SQLite database." >&2
    [[ -n "$TMP_FILE" ]] && rm -f "$TMP_FILE"
    exit 1
fi

TABLES=$(sqlite3 "$WORK_FILE" "SELECT name FROM sqlite_master WHERE type='table';")
for required in holds audit_events outbox_events; do
    if ! echo "$TABLES" | grep -q "^${required}$"; then
        echo "❌ Backup is missing required table: $required" >&2
        [[ -n "$TMP_FILE" ]] && rm -f "$TMP_FILE"
        exit 1
    fi
done

# --- Sauvegarde de l'ancienne DB (au cas où) --------------------------
if [[ -f "$STATE_PATH" ]]; then
    OLD="${STATE_PATH}.before_restore_$(date -u +%Y%m%dT%H%M%SZ)"
    echo "▶ Saving current state to: $OLD"
    mv "$STATE_PATH" "$OLD"
    # Nettoyer aussi les fichiers WAL/SHM de l'ancienne DB
    rm -f "${STATE_PATH}-wal" "${STATE_PATH}-shm"
fi

# --- Restauration -----------------------------------------------------
echo "▶ Restoring..."
cp "$WORK_FILE" "$STATE_PATH"

# --- Nettoyage --------------------------------------------------------
[[ -n "$TMP_FILE" ]] && rm -f "$TMP_FILE"

echo ""
echo "✅ Restore complete: $STATE_PATH"
echo "   You can now start Pryxor."
exit 0