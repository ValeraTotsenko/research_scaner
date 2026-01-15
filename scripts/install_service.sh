#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "[install_service] ERROR: run as root (sudo)." >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
unit_src="${repo_root}/deploy/systemd/research-scanner@.service"
env_src="${repo_root}/deploy/systemd/research-scanner.env"
configs_src="${repo_root}/configs"

scanner_home="/opt/research_scanner"
config_root="/etc/research_scanner/configs"
output_root="/var/lib/research_scanner/output"
bootstrap_python="${RESEARCH_SCANNER_BOOTSTRAP_PYTHON:-python3}"

if ! id -u scanner >/dev/null 2>&1; then
  useradd --system --home /var/lib/research_scanner --shell /usr/sbin/nologin scanner
fi

install -d -m 0750 -o scanner -g scanner /var/lib/research_scanner
install -d -m 0750 -o scanner -g scanner "$output_root"
install -d -m 0750 -o scanner -g scanner /etc/research_scanner
install -d -m 0750 -o scanner -g scanner "$config_root"
install -d -m 0755 "$scanner_home"

rsync -a --delete \
  --exclude '.git' \
  --exclude '.venv' \
  "${repo_root}/" "${scanner_home}/"
chown -R scanner:scanner "$scanner_home"

venv_path="${scanner_home}/.venv"
if [[ ! -x "${venv_path}/bin/python" ]]; then
  if ! command -v "$bootstrap_python" >/dev/null 2>&1; then
    echo "[install_service] ERROR: ${bootstrap_python} not found to create venv." >&2
    exit 2
  fi
  echo "[install_service] Creating venv at ${venv_path}"
  runuser -u scanner -- "$bootstrap_python" -m venv "$venv_path"
fi

runuser -u scanner -- "${venv_path}/bin/pip" install -U pip
runuser -u scanner -- "${venv_path}/bin/pip" install -e "$scanner_home"

install -m 0644 "$unit_src" /etc/systemd/system/research-scanner@.service

if [[ ! -f /etc/research_scanner/research-scanner.env ]]; then
  install -m 0640 -o scanner -g scanner "$env_src" /etc/research_scanner/research-scanner.env
fi

if [[ -d "$configs_src" ]]; then
  install -m 0640 -o scanner -g scanner "$configs_src"/*.yaml "$config_root"/ 2>/dev/null || true
fi

systemctl daemon-reload

echo "[install_service] Done. Example commands:"
echo "  systemctl start research-scanner@smoke"
echo "  systemctl status research-scanner@smoke"
echo "  journalctl -u research-scanner@smoke -f"
