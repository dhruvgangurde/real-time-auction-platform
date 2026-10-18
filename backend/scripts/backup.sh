#!/usr/bin/env bash
# OR-6: a daily logical backup of the database, kept seven days, plus the uploaded images.
# Runs in the compose service `backup` (the postgres:16 image, so pg_dump matches the server):
# at start and then every hour it takes today's backup unless one exists, then deletes the files
# dated more than six days before today. "Daily" is one backup per UTC calendar day while the
# stack runs, which holds on a laptop that is off at night. Each file is written under a hidden
# partial name and renamed when whole, so an interrupted run never leaves something that looks
# like a backup. Files: $BACKUP_DIR/auction-YYYY-MM-DD.dump (pg_dump -Fc) and media-YYYY-MM-DD.tgz.
# BACKUP_LIB=1 only defines the functions (tests/ops/test_backup_script.py).
set -euo pipefail
umask 077 # a dump holds every account and its password hash: readable by its owner only

BACKUP_DIR=${BACKUP_DIR:-/backups}
KEEP_DAYS=7
INTERVAL_S=${BACKUP_INTERVAL_S:-3600}

today() { date -u +%F; }

# taken DAY: true when a whole database dump of DAY exists
taken() { [ -s "$BACKUP_DIR/auction-$1.dump" ]; }

# prune TODAY: delete dated backups older than the KEEP_DAYS days ending TODAY, and partials
prune() {
  local oldest name day
  oldest=$(date -u -d "$1 - $((KEEP_DAYS - 1)) days" +%F)
  for path in "$BACKUP_DIR"/auction-*.dump "$BACKUP_DIR"/media-*.tgz; do
    [ -e "$path" ] || continue
    name=$(basename "$path")
    day=${name#*-}
    day=${day%%.*}
    [[ $day =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || continue # not a dated backup: not ours
    if [[ $day < $oldest ]]; then rm -f -- "$path"; fi
  done
  rm -f -- "$BACKUP_DIR"/.*.partial
}

# backup DAY: the database dump and the media archive of DAY, each renamed when whole
backup() {
  local day=$1
  PGPASSWORD=$POSTGRES_PASSWORD pg_dump -h "${PGHOST:-postgres}" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -Fc -f "$BACKUP_DIR/.auction-$day.dump.partial"
  mv -f "$BACKUP_DIR/.auction-$day.dump.partial" "$BACKUP_DIR/auction-$day.dump"
  tar -czf "$BACKUP_DIR/.media-$day.tgz.partial" -C /media .
  mv -f "$BACKUP_DIR/.media-$day.tgz.partial" "$BACKUP_DIR/media-$day.tgz"
  echo "backup: $(ls -l "$BACKUP_DIR/auction-$day.dump" "$BACKUP_DIR/media-$day.tgz" | awk '{print $5, $9}' | tr '\n' ' ')"
}

once() {
  local day
  day=$(today)
  if taken "$day"; then echo "backup: $day already taken"; else backup "$day"; fi
  prune "$day"
  echo "backup: kept $(ls "$BACKUP_DIR" | grep -cE '^auction-[0-9-]{10}\.dump$') daily dumps"
}

if [ "${BACKUP_LIB:-0}" != 1 ]; then
  mkdir -p "$BACKUP_DIR"
  if [ "${BACKUP_ONCE:-0}" = 1 ]; then
    once
  else
    while true; do
      once || echo "backup: failed; retrying in ${INTERVAL_S} s" >&2
      sleep "$INTERVAL_S"
    done
  fi
fi
