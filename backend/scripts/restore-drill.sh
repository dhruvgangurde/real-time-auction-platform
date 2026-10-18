#!/usr/bin/env bash
# OR-6: the restore drill, from the repository root on the dev stack (Git Bash on Windows):
#   bash backend/scripts/restore-drill.sh
# 1. Counts every table's rows, takes a backup with the daily backup's own code (`backup` in
#    backup.sh) into $BACKUP_DIR/drill, and counts again; the two counts must be equal (else it
#    retries, three times at most), so the dump is of one known state.
# 2. Restores the dump into a scratch database, auction_restore (pg_restore, the owner role), and
#    the images into a scratch folder.
# 3. Checks them (python -m app.cli restore-check, app/restore_check.py): rows per table, the six
#    ledger checks on the latest verified stress run, every image byte for byte.
# 4. Deletes the scratch database and folder, and writes the evidence page,
#    ops/evidence/restore-drill-<date>.md (published: counts, check values, digests only).
set -euo pipefail
export MSYS_NO_PATHCONV=1 # Git Bash: leave container paths alone

cd "$(dirname "$0")/../.."
DATE=$(date -u +%F)
OUT=ops/evidence/restore-drill-$DATE.md
HOST_BACKUPS=$(grep -E '^BACKUP_DIR=' .env 2>/dev/null | cut -d= -f2- || true)
HOST_BACKUPS=${HOST_BACKUPS:-../auction-private/backups}
HOST_BACKUPS=$(cd "$HOST_BACKUPS" && (pwd -W 2>/dev/null || pwd))

in_backup() { docker compose exec -T backup bash -c "$1"; }
PG='PGPASSWORD=$POSTGRES_PASSWORD; export PGPASSWORD PGHOST=postgres PGUSER=$POSTGRES_USER'
COUNT_SQL="SELECT tablename, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', schemaname, tablename), false, true, '')))[1]::text FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
counts() { in_backup "$PG; psql -d \$POSTGRES_DB -At -F \$'\\t' -c \"$COUNT_SQL\""; }
now() { date +%s.%N; }
since() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }

in_backup "rm -rf /backups/drill && mkdir -p /backups/drill"
for attempt in 1 2 3; do
  counts >"$HOST_BACKUPS/drill/counts-before.tsv"
  started=$(now)
  in_backup "BACKUP_LIB=1 BACKUP_DIR=/backups/drill; . /scripts/backup.sh; backup drill" >/dev/null
  dump_s=$(since "$started")
  counts >"$HOST_BACKUPS/drill/counts.tsv"
  if cmp -s "$HOST_BACKUPS/drill/counts-before.tsv" "$HOST_BACKUPS/drill/counts.tsv"; then break; fi
  echo "restore drill: rows changed during the dump (attempt $attempt); again" >&2
  [ "$attempt" = 3 ] && { echo "restore drill: the database never held still" >&2; exit 1; }
done

started=$(now)
in_backup "$PG; dropdb --if-exists auction_restore && createdb auction_restore && pg_restore --no-owner --exit-on-error -d auction_restore /backups/drill/auction-drill.dump"
restore_s=$(since "$started")
started=$(now)
in_backup "mkdir -p /backups/drill/media && tar -xzf /backups/drill/media-drill.tgz -C /backups/drill/media"
media_s=$(since "$started")

started=$(now)
status=0
mkdir -p ops/evidence
docker compose run --rm --no-deps -T -v "$HOST_BACKUPS:/backups" api python -m app.cli restore-check \
  --db auction_restore --counts /backups/drill/counts.tsv --media /backups/drill/media --date "$DATE" \
  --timings "dump=$dump_s,restore=$restore_s,media=$media_s" >"$OUT.partial" || status=$?
echo "check $(since "$started") s" >&2

in_backup "$PG; dropdb --if-exists auction_restore; rm -rf /backups/drill"
mv -f "$OUT.partial" "$OUT"
head -1 "$OUT"
echo "restore drill: evidence in $OUT" >&2
exit "$status"
