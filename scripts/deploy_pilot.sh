#!/bin/sh
# Deploy the pilot. Reads names from the environment and does not print values.
set -eu
cd "$(dirname "$0")/.."

missing=0
for name in \
  CLOUDFLARE_API_TOKEN \
  CLOUDFLARE_ACCOUNT_ID \
  DATABASE_URL \
  ADMIN_EMAILS \
  CLOUDFLARE_ACCESS_TEAM_DOMAIN \
  CLOUDFLARE_ACCESS_AUD \
  BENEFITIQ_SESSION_SECRET
do
  if [ -z "$(printenv "$name" 2>/dev/null || true)" ]; then
    echo "missing $name" >&2
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  exit 1
fi

site="$(printenv TURNSTILE_SITE_KEY 2>/dev/null || true)"
secret="$(printenv TURNSTILE_SECRET_KEY 2>/dev/null || true)"
if [ -n "$site" ] && [ -z "$secret" ]; then
  echo "missing TURNSTILE_SECRET_KEY" >&2
  exit 1
fi
if [ -z "$site" ] && [ -n "$secret" ]; then
  echo "missing TURNSTILE_SITE_KEY" >&2
  exit 1
fi

if [ ! -d node_modules/wrangler ]; then
  npm install
fi

put_secret() {
  name="$1"
  value="$(printenv "$name" 2>/dev/null || true)"
  if [ -z "$value" ]; then
    return 0
  fi
  printf '%s' "$value" | npx wrangler secret put "$name" >/dev/null
  echo "set secret $name"
}

put_secret DATABASE_URL
put_secret ADMIN_EMAILS
put_secret CLOUDFLARE_ACCESS_TEAM_DOMAIN
put_secret CLOUDFLARE_ACCESS_AUD
put_secret BENEFITIQ_SESSION_SECRET
put_secret TURNSTILE_SITE_KEY
put_secret TURNSTILE_SECRET_KEY
put_secret ACCESS_NOTIFY_WEBHOOK_URL
put_secret OPENAI_API_KEY

npx wrangler deploy
