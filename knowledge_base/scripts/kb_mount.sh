#!/usr/bin/env bash
# Mount the external drive and the btrfs image that holds Postgres' data directory, then start the container.
# Run after every reboot / re-plug. Idempotent. The container refuses to start if the image is not mounted.
set -euo pipefail
cd "$(dirname "$0")/.."
HDD=$(grep '^KB_HDD=' .env | cut -d= -f2- | tr -d '"'); HDD=$(dirname "$HDD")   # .../security-kb -> drive root is its parent
IMG="$HDD/security-kb/pgdata.img"
if ! findmnt -rn "$HDD" >/dev/null; then udisksctl mount -b "$(findmnt -rn -o SOURCE --target "$HDD" 2>/dev/null || lsblk -rpno NAME,LABEL | awk '/My Passport/{print $1; exit}')" || true; fi
[ -f "$IMG" ] || { echo "image not found: $IMG (is the drive mounted?)" >&2; exit 1; }
if ! findmnt -rn /run/media/meh/kbdata >/dev/null; then
  dev=$(losetup -j "$IMG" -O NAME -n | head -1); [ -n "$dev" ] || dev=$(udisksctl loop-setup -f "$IMG" | sed -E 's/.* as (\/dev\/loop[0-9]+)\..*/\1/')
  findmnt -rn /run/media/meh/kbdata >/dev/null || udisksctl mount -b "$dev"
fi
# the image is btrfs: force zstd on every write (plain 'compress' gives up on a file after one incompressible write, and
# the vector codes are close to incompressible). Existing data reads fine without this; only new writes would land uncompressed.
findmnt -rno OPTIONS /run/media/meh/kbdata | grep -q compress-force || sudo -n mount -o remount,compress-force=zstd:3 /run/media/meh/kbdata \
  || echo "warning: could not enable zstd on /run/media/meh/kbdata; new writes will be stored uncompressed" >&2
docker compose up -d
# the data lives on a spinning disk: a cold vector/full-text index costs seconds per query, so load the indexes into RAM now
for i in $(seq 30); do docker exec security-kb pg_isready -q -U kb && break; sleep 1; done
# body_vec (codes, scanned on every vector query) and the keyword indexes go into shared buffers; the text heap only into the
# OS page cache, with its TOAST table (results read their rows from it; long tsv values live there and ts_rank_cd reads them)
docker exec security-kb psql -U kb -d securitykb -qAtc "SELECT pg_prewarm(r) FROM unnest(ARRAY['body_vec_granite','bodies_tsv_idx','term_df','term_df_word','entities','chunk_refs']) r; SELECT pg_prewarm(r, 'read') FROM unnest(ARRAY['bodies']) r; SELECT pg_prewarm(reltoastrelid, 'read') FROM pg_class WHERE relname = 'bodies'" >/dev/null && echo "indexes prewarmed"
