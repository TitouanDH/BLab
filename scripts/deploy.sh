#!/bin/sh
# Redeploys this checkout when its branch moved on GitHub. Runs every minute from cron on
# the server, once per checkout (production = main, pre-prod = dev):
#   * * * * * /home/titouan/BLab/scripts/deploy.sh >> /home/titouan/deploy-production.log 2>&1
# Settings come from the .env next to docker-compose.yml (see .env.example). The outcome is
# posted to GitHub as commit status "deploy/<BLAB_STAGE>" when GITHUB_STATUS_TOKEN is set.
# A commit is attempted once; to retry a failed deploy, push a fix (or delete .deploy-attempted).

# Everything lives in main() so the shell has read the whole script before `git merge`
# can replace this file.
main() {
  set -u
  cd "$(dirname "$0")/.." || exit 1
  exec 9>.deploy.lock
  flock -n 9 || exit 0  # previous run still deploying

  set -a; . ./.env; set +a
  branch=$(git rev-parse --abbrev-ref HEAD)
  git fetch -q origin "$branch" || { log "git fetch failed"; exit 1; }
  target=$(git rev-parse "origin/$branch")
  [ "$target" = "$(cat .deploy-attempted 2>/dev/null)" ] && exit 0
  echo "$target" > .deploy-attempted

  log "deploying $branch at $target"
  status pending "Deploying"
  if ! git merge -q --ff-only "$target"; then
    log "fast-forward failed"; status failure "git pull failed (local changes on the server?)"; exit 1
  fi
  export GIT_COMMIT="$target"
  if ! docker compose up -d --build --remove-orphans; then
    log "compose failed"; status failure "docker compose up failed"; exit 1
  fi

  # Healthy once Django answers through nginx, which means its migrations ran
  code=000
  for _ in $(seq 1 24); do
    code=$(curl -sk -o /dev/null -w '%{http_code}' "$HEALTH_URL")
    case $code in
      2*|3*|4*) log "healthy (HTTP $code)"; status success "Deployed"; exit 0 ;;
    esac
    sleep 5
  done
  log "unhealthy (HTTP $code)"; status failure "Not healthy after 2 minutes (HTTP $code)"; exit 1
}

log() { echo "$(date -Is) [${BLAB_STAGE:-?}] $*"; }

# status <pending|success|failure> <description>
status() {
  [ -n "${GITHUB_STATUS_TOKEN:-}" ] || return 0
  curl -sS -o /dev/null -X POST \
    -H "Authorization: Bearer $GITHUB_STATUS_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/TitouanDH/BLab/statuses/$target" \
    -d "{\"state\":\"$1\",\"context\":\"deploy/$BLAB_STAGE\",\"description\":\"$2\"}" \
    || log "could not post commit status"
}

main "$@"
