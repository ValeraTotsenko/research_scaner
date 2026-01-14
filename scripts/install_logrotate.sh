#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install_logrotate.sh [--output-dir PATH] [--keep-days N] [--size SIZE]

Installs logrotate rules for research_scanner logs into /etc/logrotate.d/research_scanner.
Defaults:
  --output-dir  /var/log/research_scanner
  --keep-days   7
  --size        100M
USAGE
}

OUTPUT_DIR="/var/log/research_scanner"
KEEP_DAYS="7"
ROTATE_SIZE="100M"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --keep-days)
      KEEP_DAYS="$2"
      shift 2
      ;;
    --size)
      ROTATE_SIZE="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ! "$KEEP_DAYS" =~ ^[0-9]+$ ]]; then
  echo "--keep-days must be a non-negative integer" >&2
  exit 1
fi

if [[ -z "$OUTPUT_DIR" ]]; then
  echo "--output-dir is required" >&2
  exit 1
fi

LOGROTATE_PATH="/etc/logrotate.d/research_scanner"
LOGROTATE_CONTENT=$(cat <<LOGROTATE_EOF
$OUTPUT_DIR/run_*/logs.jsonl {
  daily
  size $ROTATE_SIZE
  rotate $KEEP_DAYS
  compress
  missingok
  notifempty
  copytruncate
}
LOGROTATE_EOF
)

if [[ "$EUID" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
  echo "$LOGROTATE_CONTENT" | sudo tee "$LOGROTATE_PATH" >/dev/null
else
  echo "$LOGROTATE_CONTENT" > "$LOGROTATE_PATH"
fi

echo "Installed logrotate config at $LOGROTATE_PATH"
