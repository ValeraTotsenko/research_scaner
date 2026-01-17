#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Interactive Research Scanner Launcher
# ============================================================================
# Позволяет выбрать конфиг из папки configs/ и запустить сканер
# Usage: ./scripts/run_interactive.sh [--output /custom/output/path]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIGS_DIR="${PROJECT_ROOT}/configs"
DEFAULT_OUTPUT_DIR="${PROJECT_ROOT}/output"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Парсинг аргументов
OUTPUT_DIR="${DEFAULT_OUTPUT_DIR}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--output /path/to/output]"
      echo ""
      echo "Интерактивный запуск Research Scanner с выбором конфига"
      echo ""
      echo "Options:"
      echo "  --output DIR    Директория для output (default: ${DEFAULT_OUTPUT_DIR})"
      echo "  -h, --help      Показать эту справку"
      exit 0
      ;;
    *)
      echo -e "${RED}Unknown option: $1${NC}" >&2
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

# ============================================================================
# Функции
# ============================================================================

print_header() {
  echo ""
  echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
  echo -e "${BLUE}  Research Scanner - Interactive Launcher${NC}"
  echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
  echo ""
}

check_python() {
  local python_bin="${1:-python}"

  if ! command -v "$python_bin" &> /dev/null; then
    echo -e "${RED}Error: Python не найден${NC}" >&2
    return 1
  fi

  local version
  version=$("$python_bin" --version 2>&1 | awk '{print $2}')
  echo -e "${GREEN}✓${NC} Python version: ${version}"

  # Проверка модулей
  local missing_modules=()
  for module in yaml pydantic httpx; do
    if ! "$python_bin" -c "import ${module}" &> /dev/null; then
      missing_modules+=("$module")
    fi
  done

  if (( ${#missing_modules[@]} )); then
    echo -e "${RED}Error: Отсутствуют модули: ${missing_modules[*]}${NC}" >&2
    echo -e "${YELLOW}Запустите: pip install -e .${NC}"
    return 1
  fi

  echo -e "${GREEN}✓${NC} Все необходимые модули установлены"
  return 0
}

list_configs() {
  local configs=()

  if [[ ! -d "$CONFIGS_DIR" ]]; then
    echo -e "${RED}Error: Директория configs не найдена: ${CONFIGS_DIR}${NC}" >&2
    return 1
  fi

  while IFS= read -r -d '' config_file; do
    configs+=("$(basename "$config_file")")
  done < <(find "$CONFIGS_DIR" -maxdepth 1 -name "*.yaml" -print0 | sort -z)

  if (( ${#configs[@]} == 0 )); then
    echo -e "${RED}Error: Конфиги не найдены в ${CONFIGS_DIR}${NC}" >&2
    return 1
  fi

  echo "${configs[@]}"
  return 0
}

select_config() {
  local configs_array=("$@")

  echo ""
  echo -e "${YELLOW}Доступные конфигурации:${NC}"
  echo ""

  for i in "${!configs_array[@]}"; do
    local config="${configs_array[$i]}"
    local config_path="${CONFIGS_DIR}/${config}"

    # Попытка извлечь run_name из конфига
    local run_name=""
    if command -v yq &> /dev/null; then
      run_name=$(yq eval '.runtime.run_name' "$config_path" 2>/dev/null || echo "")
    elif command -v python &> /dev/null; then
      run_name=$(python -c "import yaml; print(yaml.safe_load(open('${config_path}'))['runtime']['run_name'])" 2>/dev/null || echo "")
    fi

    printf "  ${GREEN}%2d)${NC} %-30s" "$((i+1))" "$config"
    [[ -n "$run_name" ]] && printf "  [%s]" "$run_name"
    echo ""
  done

  echo ""
  echo -e "${YELLOW}Выберите конфиг (1-${#configs_array[@]}) или 'q' для выхода:${NC}"
  read -r selection

  if [[ "$selection" == "q" ]] || [[ "$selection" == "Q" ]]; then
    echo "Выход."
    exit 0
  fi

  if ! [[ "$selection" =~ ^[0-9]+$ ]] || (( selection < 1 || selection > ${#configs_array[@]} )); then
    echo -e "${RED}Error: Неверный выбор${NC}" >&2
    return 1
  fi

  echo "${configs_array[$((selection-1))]}"
  return 0
}

confirm_launch() {
  local config="$1"
  local config_path="${CONFIGS_DIR}/${config}"

  echo ""
  echo -e "${BLUE}────────────────────────────────────────────────────────────${NC}"
  echo -e "${GREEN}Выбранный конфиг:${NC} ${config}"
  echo -e "${GREEN}Путь к конфигу:${NC}   ${config_path}"
  echo -e "${GREEN}Output директория:${NC} ${OUTPUT_DIR}"
  echo -e "${BLUE}────────────────────────────────────────────────────────────${NC}"
  echo ""

  # Показать ключевые параметры из конфига
  if command -v python &> /dev/null; then
    echo -e "${YELLOW}Основные параметры:${NC}"
    python - <<PY
import yaml
import sys

try:
    with open('${config_path}') as f:
        cfg = yaml.safe_load(f)

    print(f"  Universe:          {cfg.get('universe', {}).get('quote_asset', 'N/A')}")
    print(f"  Min Volume 24h:    {cfg.get('universe', {}).get('min_quote_volume_24h', 'N/A'):,}")
    print(f"  Spread Duration:   {cfg.get('sampling', {}).get('spread', {}).get('duration_s', 'N/A')}s")
    print(f"  Depth Duration:    {cfg.get('sampling', {}).get('depth', {}).get('duration_s', 'N/A')}s")
    print(f"  Edge Min:          {cfg.get('thresholds', {}).get('edge_min_bps', 'N/A')} bps")
    print(f"  Total Timeout:     {cfg.get('pipeline', {}).get('total_timeout_s', 'N/A')}s")
except Exception as e:
    print(f"  (не удалось прочитать параметры: {e})", file=sys.stderr)
PY
    echo ""
  fi

  echo -e "${YELLOW}Запустить сканер? [Y/n]:${NC}"
  read -r confirm

  if [[ "$confirm" =~ ^[Nn]$ ]]; then
    echo "Отменено."
    exit 0
  fi
}

run_scanner() {
  local config="$1"
  local config_path="${CONFIGS_DIR}/${config}"
  local python_bin

  # Определяем Python
  if [[ -f "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    python_bin="${PROJECT_ROOT}/.venv/bin/python"
  else
    python_bin="python"
  fi

  echo ""
  echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
  echo -e "${GREEN}Запуск сканера...${NC}"
  echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
  echo ""

  # Создаем output директорию
  mkdir -p "$OUTPUT_DIR"

  # Запускаем сканер
  cd "$PROJECT_ROOT"

  echo -e "${YELLOW}Команда:${NC}"
  echo "  $python_bin -m scanner run --config \"$config_path\" --output \"$OUTPUT_DIR\""
  echo ""

  exec "$python_bin" -m scanner run --config "$config_path" --output "$OUTPUT_DIR"
}

# ============================================================================
# Main
# ============================================================================

main() {
  print_header

  # Проверка Python и модулей
  local python_bin
  if [[ -f "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    python_bin="${PROJECT_ROOT}/.venv/bin/python"
    echo -e "${GREEN}✓${NC} Найден venv: ${python_bin}"
  else
    python_bin="python"
    echo -e "${YELLOW}⚠${NC}  Используется системный Python"
  fi

  if ! check_python "$python_bin"; then
    exit 1
  fi

  echo ""

  # Получение списка конфигов
  local configs_output
  configs_output=$(list_configs)
  if [[ $? -ne 0 ]]; then
    exit 1
  fi

  # Преобразуем в массив
  IFS=' ' read -r -a configs_array <<< "$configs_output"

  # Выбор конфига
  local selected_config
  selected_config=$(select_config "${configs_array[@]}")
  if [[ $? -ne 0 ]] || [[ -z "$selected_config" ]]; then
    echo -e "${RED}Не удалось выбрать конфиг${NC}" >&2
    exit 1
  fi

  # Подтверждение и запуск
  confirm_launch "$selected_config"
  run_scanner "$selected_config"
}

main "$@"
