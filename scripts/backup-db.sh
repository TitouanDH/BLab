#!/bin/sh
# Dumps production's database (pg_dump, custom format) into ~/blab-backups/blab-<UTC time>.dump
# and keeps the newest 14. Runs daily from the user crontab on the server, from the production
# checkout, after the nightly Sweep has started (02:00 Paris):
#   0 3 * * * /home/titouan/BLab/scripts/backup-db.sh >> /home/titouan/backup-db.log 2>&1
# Titouan's PC copies the dumps with scripts/pull-backups.ps1; restoring one: README, "Deploy".
# A failed dump exits non-zero, logs FAILED, and leaves the earlier dumps untouched.
# Optional: BLAB_BACKUP_DIR (default ~/blab-backups) and BLAB_BACKUP_KEEP (default 14).

main() {
  set -u
  umask 077  # the dumps hold users and tokens
  cd "$(dirname "$0")/.." || fail "cannot cd to the checkout"
  dir=${BLAB_BACKUP_DIR:-$HOME/blab-backups}
  keep=${BLAB_BACKUP_KEEP:-14}
  case $keep in ''|0|*[!0-9]*) fail "BLAB_BACKUP_KEEP must be a positive number, not '$keep'" ;; esac
  mkdir -p "$dir" || fail "cannot create $dir"

  name=blab-$(date -u +%Y%m%dT%H%M%SZ).dump
  partial="$dir/.$name.partial"  # not blab-*.dump: a partial dump never counts as one
  # The database and user come from the db container's own environment, nothing is repeated here
  if ! docker compose exec -T db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$partial"; then
    rm -f "$partial"; fail "pg_dump failed"
  fi
  if [ ! -s "$partial" ]; then
    rm -f "$partial"; fail "pg_dump wrote nothing"
  fi
  mv "$partial" "$dir/$name" || { rm -f "$partial"; fail "cannot rename the dump"; }
  log "wrote $name ($(du -h "$dir/$name" | cut -f1))"

  # Names sort by time; remove all but the newest $keep
  count=$(ls "$dir" | grep -c '^blab-.*\.dump$')
  if [ "$count" -gt "$keep" ]; then
    ls "$dir" | grep '^blab-.*\.dump$' | sort | head -n "$((count - keep))" | while read -r old; do
      rm -f "$dir/$old" && log "removed $old"
    done
  fi
}

log() { echo "$(date -Is) [backup-db] $*"; }

fail() { log "FAILED: $*"; exit 1; }

main "$@"
