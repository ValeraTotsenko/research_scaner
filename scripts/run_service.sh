#!/usr/bin/env bash
set -euo pipefail

instance="${1:-}"
if [[ -z "$instance" ]]; then
  echo "[research-scanner] ERROR: missing instance name (expected %i)." >&2
  exit 2
fi

scanner_home="${RESEARCH_SCANNER_HOME:-/opt/research_scanner}"
config_root="${RESEARCH_SCANNER_CONFIG_ROOT:-/etc/research_scanner/configs}"
output_root="${RESEARCH_SCANNER_OUTPUT_ROOT:-/var/lib/research_scanner/output}"
python_bin="${RESEARCH_SCANNER_PYTHON:-${scanner_home}/.venv/bin/python}"

config_path="${config_root}/${instance}.yaml"
output_dir="${output_root}/${instance}"

if [[ ! -f "$config_path" ]]; then
  echo "[research-scanner] ERROR: config not found: ${config_path}" >&2
  exit 3
fi

if [[ ! -x "$python_bin" ]]; then
  echo "[research-scanner] ERROR: python not found or not executable: ${python_bin}" >&2
  echo "[research-scanner] Hint: create the venv at ${scanner_home}/.venv." >&2
  exit 4
fi

missing_modules=()
for module in yaml pydantic httpx; do
  if ! "$python_bin" - <<PY
import importlib.util
import sys
sys.exit(0 if importlib.util.find_spec("${module}") else 1)
PY
  then
    missing_modules+=("$module")
  fi
done

if (( ${#missing_modules[@]} )); then
  echo "[research-scanner] ERROR: missing Python modules in ${python_bin}: ${missing_modules[*]}" >&2
  echo "[research-scanner] Hint: run scripts/install_service.sh to install dependencies." >&2
  exit 6
fi

mkdir -p "$output_dir"

if [[ ! -w "$output_dir" ]]; then
  echo "[research-scanner] ERROR: output directory is not writable: ${output_dir}" >&2
  exit 5
fi

cd "$scanner_home"

echo "[research-scanner] Starting instance=${instance} config=${config_path} output=${output_dir}"
exec "$python_bin" -m scanner run --config "$config_path" --output "$output_dir"
