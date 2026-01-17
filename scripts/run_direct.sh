#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Direct Research Scanner Launcher
# ============================================================================
# Прямой запуск сканера с указанием конфига
# Usage: ./scripts/run_direct.sh <config_name> [--output /path]
#
# Examples:
#   ./scripts/run_direct.sh smoke
#   ./scripts/run_direct.sh fast_smoke_sanity --output /tmp/output
#   ./scripts/run_direct.sh strict

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIGS_DIR="${PROJECT_ROOT}/configs"
DEFAULT_OUTPUT_DIR="${PROJECT_ROOT}/output"

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ============================================================================
# Парсинг аргументов
# ============================================================================

if [[ $# -eq 0 ]]; then
  echo -e "${RED}Error: Не указан конфиг${NC}" >&2
  echo ""
  echo "Usage: $0 <config_name> [--output /path]"
  echo ""
  echo "Available configs:"
  if [[ -d "$CONFIGS_DIR" ]]; then
    for config in "$CONFIGS_DIR"/*.yaml; do
      [[ -f "$config" ]] && echo "  - $(basename "$config" .yaml)"
    done
  fi
  echo ""
  echo "Examples:"
  echo "  $0 smoke"
  echo "  $0 fast_smoke_sanity --output /tmp/output"
  echo "  $0 strict"
  exit 1
fi

CONFIG_NAME="$1"
shift

OUTPUT_DIR="${DEFAULT_OUTPUT_DIR}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    *)
      echo -e "${RED}Unknown option: $1${NC}" >&2
      exit 1
      ;;
  esac
done

# ============================================================================
# Валидация
# ============================================================================

# Добавляем .yaml если не указан
if [[ ! "$CONFIG_NAME" =~ \.yaml$ ]]; then
  CONFIG_NAME="${CONFIG_NAME}.yaml"
fi

CONFIG_PATH="${CONFIGS_DIR}/${CONFIG_NAME}"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo -e "${RED}Error: Конфиг не найден: ${CONFIG_PATH}${NC}" >&2
  echo ""
  echo "Available configs:"
  if [[ -d "$CONFIGS_DIR" ]]; then
    for config in "$CONFIGS_DIR"/*.yaml; do
      [[ -f "$config" ]] && echo "  - $(basename "$config" .yaml)"
    done
  fi
  exit 1
fi

# Определяем Python
if [[ -f "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
else
  PYTHON_BIN="python"
fi

if ! command -v "$PYTHON_BIN" &> /dev/null; then
  echo -e "${RED}Error: Python не найден${NC}" >&2
  exit 1
fi

# ============================================================================
# Запуск
# ============================================================================

echo ""
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}Research Scanner - Direct Launch${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${GREEN}Config:${NC}  ${CONFIG_NAME} (${CONFIG_PATH})"
echo -e "${GREEN}Output:${NC}  ${OUTPUT_DIR}"
echo -e "${GREEN}Python:${NC}  ${PYTHON_BIN}"
echo ""

# Создаем output директорию
mkdir -p "$OUTPUT_DIR"

# Переходим в project root
cd "$PROJECT_ROOT"

# Запускаем
echo -e "${YELLOW}Starting scanner...${NC}"
echo ""

exec "$PYTHON_BIN" -m scanner run --config "$CONFIG_PATH" --output "$OUTPUT_DIR"
