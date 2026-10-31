#!/usr/bin/env bash
# pg_dump -Fc to the HDD. Fails clearly if the drive is not mounted.
set -euo pipefail
cd "$(dirname "$0")/.."
HDD=$(grep '^KB_HDD=' .env | cut -d= -f2- | tr -d '"')
[ -d "$HDD" ] || { echo "HDD not mounted at: $HDD" >&2; exit 1; }
mkdir -p "$HDD/backups"
out="$HDD/backups/securitykb_$(date +%Y%m%d_%H%M%S).dump"
docker exec security-kb pg_dump -U kb -Fc securitykb > "$out"
ls -lh "$out"
