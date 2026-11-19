#!/bin/sh
# local: docker compose boots Postgres, loads the stub corpus, and serves.
# serve: the Cloudflare container only serves. It refuses to start without DATABASE_URL.
set -eu

mode="${1:-serve}"

require_name() {
  if [ -z "$(printenv "$1" 2>/dev/null || true)" ]; then
    echo "missing $1" >&2
    exit 1
  fi
}

if [ "$mode" = "local" ]; then
  python -m benefitiq.cli wait-db
  python -m benefitiq.cli init-db
  python -m benefitiq.cli ingest --manifest corpus/stub/manifest.yaml
  python -m benefitiq.cli ingest --manifest corpus/office/manifest.yaml
  python -m benefitiq.cli ingest --manifest corpus/payers/manifest.yaml
  exec uvicorn benefitiq.main:app --host 0.0.0.0 --port 8000 --no-access-log
fi

if [ "$mode" != "serve" ]; then
  echo "unknown mode" >&2
  exit 1
fi

require_name DATABASE_URL
if [ "${BENEFITIQ_ACCESS_ENFORCE:-}" = "1" ]; then
  require_name ADMIN_EMAILS
  require_name CLOUDFLARE_ACCESS_TEAM_DOMAIN
  require_name CLOUDFLARE_ACCESS_AUD
  require_name BENEFITIQ_SESSION_SECRET
  if [ -n "${TURNSTILE_SITE_KEY:-}" ] && [ -z "${TURNSTILE_SECRET_KEY:-}" ]; then
    echo "missing TURNSTILE_SECRET_KEY" >&2
    exit 1
  fi
  if [ -z "${TURNSTILE_SITE_KEY:-}" ] && [ -n "${TURNSTILE_SECRET_KEY:-}" ]; then
    echo "missing TURNSTILE_SITE_KEY" >&2
    exit 1
  fi
fi

exec uvicorn benefitiq.main:app --host 0.0.0.0 --port 8000 --no-access-log
