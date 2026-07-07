#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly SCRIPT_BASENAME="$(basename "${BASH_SOURCE[0]}")"
readonly DEFAULT_CONFIG_FILE="${SCRIPT_DIR}/openlist-auto-move.conf"
readonly LIB_DIR="${SCRIPT_DIR}/openlist-auto-move"
export OPENLIST_MOVER_PROJECT_ROOT="${PROJECT_ROOT}"

load_lib() {
  local lib_name="$1"
  local lib_path="${LIB_DIR}/${lib_name}"
  if [[ ! -f "${lib_path}" ]]; then
    printf '[openlist-mover] 缺少库文件: %s\n' "${lib_path}" >&2
    exit 1
  fi

  # shellcheck disable=SC1090
  source "${lib_path}"
}

load_lib common.sh
load_lib state.sh
load_lib api.sh
load_lib remote_cache.sh
load_lib remote.sh
load_lib paths.sh
load_lib reconcile.sh
load_lib dryrun.sh
load_lib runner.sh
load_lib runner_legacy.sh
load_lib runner_batch.sh
load_lib inspect.sh
load_lib commands.sh

main() {
  parse_args "$@"
  dispatch_command
}

main "$@"
