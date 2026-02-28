#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env"

API_URL="${BASE_URL:-https://notifications.osmosis.page}/api/notifications/"

TITLE="${1:?Usage: $0 <title> [message] [priority] [source]}"
MESSAGE="${2:-}"
PRIORITY="${3:-medium}"
SOURCE="${4:-}"

BODY=$(jq -n \
  --arg title "$TITLE" \
  --arg message "$MESSAGE" \
  --arg priority "$PRIORITY" \
  --arg source "$SOURCE" \
  '{title: $title, priority: $priority}
   + (if $message != "" then {message: $message} else {} end)
   + (if $source != "" then {source: $source} else {} end)')

curl -s -X POST "$API_URL" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$BODY" | jq .
