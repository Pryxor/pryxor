#!/usr/bin/env bash
#
# Pryxor — Backup from a running Docker container.
#
# Usage :
#   bash scripts/backup_docker.sh pryxor ./backups

set -euo pipefail

CONTAINER="${1:-pryxor}"
OUT_DIR="${2:-./backups}"

mkdir -p "$OUT_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
TMP_NAME="pryxor_backup_${TS}.sqlite3"
OUT_FILE="${OUT_DIR}/${TMP_NAME}.gz"

echo "▶ Dumping DB from container '$CONTAINER'..."
docker exec "$CONTAINER" sh -c "sqlite3 /data/pryxor_state.sqlite3 \"VACUUM INTO '/tmp/${TMP_NAME}'\""

echo "▶ Copying out..."
docker cp "${CONTAINER}:/tmp/${TMP_NAME}" "${OUT_DIR}/${TMP_NAME}"

echo "▶ Compressing..."
gzip -f "${OUT_DIR}/${TMP_NAME}"

echo "▶ Cleaning inside container..."
docker exec "$CONTAINER" rm -f "/tmp/${TMP_NAME}"

echo "✅ Backup: $OUT_FILE"