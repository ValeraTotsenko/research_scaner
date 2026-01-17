#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Sync Configs to Systemd
# ============================================================================
# Копирует конфиги из репозитория в systemd config директорию
# Usage: sudo ./scripts/sync_configs_to_systemd.sh

if [[ "$EUID" -ne 0 ]]; then
  echo "ERROR: run as root (sudo)" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIGS_SRC="${REPO_ROOT}/configs"
CONFIGS_DST="/etc/research_scanner/configs"

echo "================================================"
echo "Sync configs to systemd"
echo "================================================"
echo ""
echo "Source:      ${CONFIGS_SRC}"
echo "Destination: ${CONFIGS_DST}"
echo ""

# Проверка существования source
if [[ ! -d "$CONFIGS_SRC" ]]; then
  echo "ERROR: Source configs directory not found: ${CONFIGS_SRC}" >&2
  exit 1
fi

# Создание destination если не существует
if [[ ! -d "$CONFIGS_DST" ]]; then
  echo "Creating destination directory: ${CONFIGS_DST}"
  mkdir -p "$CONFIGS_DST"

  # Установка владельца (если пользователь scanner существует)
  if id -u scanner >/dev/null 2>&1; then
    chown scanner:scanner "$CONFIGS_DST"
  fi

  chmod 750 "$CONFIGS_DST"
fi

# Список конфигов для копирования
configs_found=0
echo "Available configs:"
for config in "$CONFIGS_SRC"/*.yaml; do
  if [[ -f "$config" ]]; then
    echo "  - $(basename "$config")"
    ((configs_found++))
  fi
done

if (( configs_found == 0 )); then
  echo ""
  echo "ERROR: No config files found in ${CONFIGS_SRC}" >&2
  exit 1
fi

echo ""
read -p "Copy all configs to ${CONFIGS_DST}? [y/N]: " confirm

if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  echo "Cancelled."
  exit 0
fi

echo ""
echo "Copying configs..."

copied=0
for config in "$CONFIGS_SRC"/*.yaml; do
  if [[ -f "$config" ]]; then
    config_name="$(basename "$config")"
    dest_path="${CONFIGS_DST}/${config_name}"

    echo "  Copying ${config_name}..."
    install -m 0640 "$config" "$dest_path"

    # Установка владельца (если пользователь scanner существует)
    if id -u scanner >/dev/null 2>&1; then
      chown scanner:scanner "$dest_path"
    fi

    ((copied++))
  fi
done

echo ""
echo "================================================"
echo "Done! Copied ${copied} config(s)."
echo "================================================"
echo ""
echo "You can now start systemd services:"
echo ""
for config in "$CONFIGS_SRC"/*.yaml; do
  if [[ -f "$config" ]]; then
    config_name="$(basename "$config" .yaml)"
    echo "  sudo systemctl start research-scanner@${config_name}"
  fi
done
echo ""
