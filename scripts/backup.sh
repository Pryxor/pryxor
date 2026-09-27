#!/usr/bin/env bash
#
# Pryxor — SQLite backup script.
#
# Crée une copie cohérente de la base via `VACUUM INTO`.
# Fonctionne même si Pryxor tourne (WAL mode activé).
#
# Usage :
#   bash scripts/backup.sh
#   bash scripts/backup.sh --state /data/pryxor_state.sqlite3 --out /backup
#   bash scripts/backup.sh --keep 30
#
# Exit codes :
#   0 = succès
#   1 = erreur (DB absente, VACUUM échoué, etc.)

set -euo pipefail

# --- Defaults ---------------------------------------------------------
STATE_PATH="${PRYXOR_STATE_PATH:-./pryxor_state.sqlite3}"
OUT_DIR="${PRYXOR_BACKUP_DIR:-./backups}"
KEEP="${PRYXOR_BACKUP_KEEP:-14}"   # nombre de backups à conserver
COMPRESS=true

# --- Parse args -------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --state)     STATE_PATH="$2"; shift 2 ;;
        --out)       OUT_DIR="$2"; shift 2 ;;
        --keep)      KEEP="$2"; shift 2 ;;
        --no-gzip)   COMPRESS=false; shift ;;
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

# --- Vérifications ----------------------------------------------------
if [[ ! -f "$STATE_PATH" ]]; then
    echo "❌ State DB not found: $STATE_PATH" >&2
    exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "❌ sqlite3 CLI not found. Install it (apt install sqlite3 / brew install sqlite)." >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

# --- Noms de fichiers -------------------------------------------------
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BASE_NAME="pryxor_backup_${TIMESTAMP}"
BACKUP_FILE="${OUT_DIR}/${BASE_NAME}.sqlite3"
if [[ "$COMPRESS" == "true" ]]; then
    FINAL_FILE="${BACKUP_FILE}.gz"
else
    FINAL_FILE="${BACKUP_FILE}"
fi

# --- Backup (VACUUM INTO — cohérent même sous charge) -----------------
echo "▶ Backing up $STATE_PATH → $BACKUP_FILE"

# Supprime un éventuel fichier préexistant (VACUUM INTO refuse d'écraser)
rm -f "$BACKUP_FILE"

sqlite3 "$STATE_PATH" "VACUUM INTO '$BACKUP_FILE';"

if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "❌ VACUUM INTO failed (no output file)." >&2
    exit 1
fi

SIZE_BEFORE="$(du -h "$BACKUP_FILE" | cut -f1)"
echo "  ✅ Snapshot: $SIZE_BEFORE"

# --- Compression optionnelle ------------------------------------------
if [[ "$COMPRESS" == "true" ]]; then
    gzip -f "$BACKUP_FILE"
    SIZE_AFTER="$(du -h "$FINAL_FILE" | cut -f1)"
    echo "  ✅ Compressed: $SIZE_AFTER"
fi

# --- Rotation (conserve les N plus récents) ---------------------------
if [[ "$KEEP" -gt 0 ]]; then
    echo "▶ Rotating (keeping last $KEEP backups)"
    # Trie par date (nom), supprime les plus anciens
    ls -1t "${OUT_DIR}"/pryxor_backup_*.sqlite3* 2>/dev/null \
        | tail -n "+$((KEEP + 1))" \
        | while read -r old; do
            rm -f "$old"
            echo "  🗑  Removed: $old"
        done
fi

echo ""
echo "✅ Backup complete: $FINAL_FILE"
exit 0