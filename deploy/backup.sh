#!/usr/bin/env bash
# Nightly SQLite backup with rotation. Runs on the VPS via systemd timer.
#
# Strategy:
#   - sqlite3 .backup gives a consistent snapshot even with live WAL writes
#   - compress with zstd (~5× smaller than raw)
#   - keep last 30 days locally in /opt/aerocrawl/backups/
#   - daily backups named YYYY-MM-DD.db.zst; duplicates within a day overwrite
#
# Optional: if RCLONE_REMOTE env is set, also pushes to that remote
# (e.g., hetzner-storage:aerocrawl-backups/). Gracefully no-ops if not set.

set -euo pipefail

DB_PATH="/opt/aerocrawl/data/aerocrawl.db"
BACKUP_DIR="/opt/aerocrawl/backups"
RETAIN_DAYS=30
RCLONE_REMOTE="${RCLONE_REMOTE:-}"

mkdir -p "$BACKUP_DIR"

if [[ ! -f "$DB_PATH" ]]; then
  echo "ERROR: $DB_PATH not found — service may never have initialized DB"
  exit 1
fi

TODAY=$(date -u +%F)
TMP_DB="/tmp/aerocrawl-backup-$$.db"
OUT="$BACKUP_DIR/$TODAY.db.zst"

# sqlite3 isn't installed on VPS; use Python's sqlite3 .backup via a one-liner
python3 - <<PY
import sqlite3, sys
src = sqlite3.connect("$DB_PATH")
dst = sqlite3.connect("$TMP_DB")
with dst: src.backup(dst)
src.close(); dst.close()
PY

# zstd is installed as a dep of the V3 Python stack; shell fallback to gzip if not
if command -v zstd >/dev/null 2>&1; then
  zstd -q -19 --rm -o "$OUT" "$TMP_DB"
else
  gzip -9 "$TMP_DB"
  OUT="$BACKUP_DIR/$TODAY.db.gz"
  mv "${TMP_DB}.gz" "$OUT"
fi

echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"

# Rotation: delete backups older than RETAIN_DAYS
find "$BACKUP_DIR" -name '*.db.zst' -o -name '*.db.gz' | while read -r f; do
  # Filename is YYYY-MM-DD — extract and compare
  name=$(basename "$f")
  date_part="${name%.db.*}"
  if [[ -n "$date_part" ]]; then
    file_epoch=$(date -u -d "$date_part" +%s 2>/dev/null || echo 0)
    cutoff_epoch=$(date -u -d "-$RETAIN_DAYS days" +%s)
    if (( file_epoch > 0 && file_epoch < cutoff_epoch )); then
      echo "Removing old backup: $f"
      rm -f "$f"
    fi
  fi
done

# Optional off-host copy
if [[ -n "$RCLONE_REMOTE" && $(command -v rclone) ]]; then
  echo "Pushing to $RCLONE_REMOTE"
  rclone copy "$OUT" "$RCLONE_REMOTE" --transfers=1 --checkers=1 2>&1 | tail -3 || true
fi

echo "Backup done."
