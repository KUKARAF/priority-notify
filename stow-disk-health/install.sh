#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/disk-health-check"
ENV_FILE="$CONFIG_DIR/.env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON_ENTRY="0 9 * * 0 $HOME/.local/bin/disk-health-check"

# Create config directory
mkdir -p "$CONFIG_DIR"

# Copy .env.example if .env doesn't exist yet
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE — edit it to set your TOKEN."
else
  echo "$ENV_FILE already exists, skipping."
fi

# Add cron entry if not already present
if crontab -l 2>/dev/null | grep -qF "disk-health-check"; then
  echo "Cron entry already exists, skipping."
else
  (crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -
  echo "Added weekly cron entry (Sundays at 9:00 AM)."
fi

echo "Done. Make sure smartmontools, jq, and curl are installed."
