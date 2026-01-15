#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "[uninstall_service] ERROR: run as root (sudo)." >&2
  exit 1
fi

systemctl stop 'research-scanner@*' >/dev/null 2>&1 || true
systemctl disable 'research-scanner@*' >/dev/null 2>&1 || true

rm -f /etc/systemd/system/research-scanner@.service
systemctl daemon-reload

echo "[uninstall_service] Removed unit file. Data/configs remain in /etc/research_scanner and /var/lib/research_scanner."
