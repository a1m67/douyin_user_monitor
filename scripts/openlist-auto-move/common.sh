#!/usr/bin/env bash

if [[ -n "${OPENLIST_MOVER_COMMON_LOADED:-}" ]]; then
  return 0
fi
OPENLIST_MOVER_COMMON_LOADED=1

OPENLIST_MOVER_PROJECT_DIR="${OPENLIST_MOVER_PROJECT_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
DEFAULT_LOCK_FILE="${OPENLIST_MOVER_PROJECT_DIR}/runtime/.openlist-auto-move.lock"
DEFAULT_PENDING_FILE="${OPENLIST_MOVER_PROJECT_DIR}/runtime/.openlist-auto-move.pending"
DEFAULT_SKIPPED_FILE="${OPENLIST_MOVER_PROJECT_DIR}/runtime/.openlist-auto-move.skipped"

CONFIG_FILE="${OPENLIST_MOVER_CONFIG:-${DEFAULT_CONFIG_FILE}}"
LOCK_FILE="${LOCK_FILE:-${DEFAULT_LOCK_FILE}}"
PENDING_FILE="${PENDING_FILE:-${DEFAULT_PENDING_FILE}}"

API_URL="http://127.0.0.1:5244"
OPENLIST_DB="/opt/openlist/data/data.db"
SOURCE_ROOT="${OPENLIST_MOVER_PROJECT_DIR}/Videos"
SOURCE_MOUNT="/bgo"
TARGET_BASE="/d2/Downloads"
STRIP_PREFIX_DIR=""
MIN_AGE_SECONDS=120
DRY_RUN="false"
SANITIZE_EMOJI="false"
FILE_PATTERN="*.mp4"
PROCESS_MOVED_COUNT=0
PROCESS_ERROR_COUNT=0
VERIFY_RETRIES=6
VERIFY_INTERVAL_SECONDS=3
API_RETRY_COUNT=4
API_RETRY_INTERVAL_SECONDS=2
REMOTE_LIST_PAGE_SIZE=1000
PENDING_STALE_SECONDS=1800
PENDING_MAX_STALE_CHECKS=20
PENDING_PROGRESS_EPSILON="0.0001"
MAX_PENDING_TASKS=1
SKIP_CONFLICTING_FILES="false"
SIZE_MISMATCH_TOLERANCE_BYTES=0
SKIPPED_FILE="${SKIPPED_FILE:-${DEFAULT_SKIPPED_FILE}}"
MOVE_LAST_TASK_ID=""
PROCESS_SKIPPED_COUNT=0
PROCESS_SUBMITTED_COUNT=0
MOVE_BATCH_SIZE=1
MAX_NEW_FILES_PER_RUN=0
MAX_RECONCILE_FILES_PER_RUN=0
VERIFY_AFTER_MOVE="true"
REMOTE_LIST_CACHE_ENABLED="false"
REMOTE_LIST_REFRESH="true"

ACTION_COMPLETED="completed"
ACTION_WAIT="wait"
ACTION_CONTINUE="continue"
ACTION_SKIP="skip"
ACTION_ERROR="error"
ACTION_UNKNOWN="unknown"

COMMAND="run"
COMMAND_TARGET=""
CLI_FORCE_DRY_RUN="false"
CONFIG_LOADED="false"
CONFIG_EXISTS="false"

log() {
  printf '[openlist-mover] %s\n' "$*" >&2
}

die() {
  log "$*"
  exit 2
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

require_cmd() {
  local cmd="$1"
  if ! has_cmd "${cmd}"; then
    log "缺少命令: ${cmd}"
    exit 1
  fi
}

ensure_parent_dir() {
  local target="$1"
  mkdir -p "$(dirname "${target}")"
}

describe_state_isolation() {
  local mode="共享默认状态文件"
  if [[ "${LOCK_FILE}" != "${DEFAULT_LOCK_FILE}" ]] \
    || [[ "${PENDING_FILE}" != "${DEFAULT_PENDING_FILE}" ]] \
    || [[ "${SKIPPED_FILE}" != "${DEFAULT_SKIPPED_FILE}" ]]; then
    mode="独立状态文件"
  fi

  printf '%s' "${mode}"
}

count_non_empty_lines() {
  local file_path="$1"
  if [[ ! -f "${file_path}" ]]; then
    printf '0'
    return 0
  fi

  awk 'NF { count += 1 } END { print count + 0 }' "${file_path}"
}

format_epoch() {
  local epoch="${1:-0}"
  if [[ -z "${epoch}" || "${epoch}" == "0" ]]; then
    printf '-'
    return 0
  fi

  date -d "@${epoch}" '+%F %T' 2>/dev/null || printf '%s' "${epoch}"
}

format_file_mtime() {
  local file_path="$1"
  if [[ ! -e "${file_path}" ]]; then
    printf '-'
    return 0
  fi

  stat -c '%y' "${file_path}" 2>/dev/null || printf '存在'
}

apply_cli_overrides() {
  if [[ "${CLI_FORCE_DRY_RUN}" == "true" ]]; then
    DRY_RUN="true"
  fi
}

load_config() {
  if [[ "${CONFIG_LOADED}" == "true" ]]; then
    return 0
  fi

  if [[ -f "${CONFIG_FILE}" ]]; then
    CONFIG_EXISTS="true"
    # shellcheck disable=SC1090
    source "${CONFIG_FILE}"
  fi

  apply_cli_overrides
  CONFIG_LOADED="true"
}

prepare_env() {
  load_config

  require_cmd sqlite3
  require_cmd jq
  require_cmd curl
  require_cmd find
  require_cmd sed
  require_cmd grep
  require_cmd awk
  require_cmd mktemp
  require_cmd flock
  require_cmd stat
  require_cmd date

  if [[ "${REMOTE_LIST_CACHE_ENABLED}" == "true" ]]; then
    require_cmd sha256sum
  fi
  if [[ "${SANITIZE_EMOJI}" == "true" ]]; then
    require_cmd perl
  fi

  ensure_parent_dir "${LOCK_FILE}"
  ensure_parent_dir "${PENDING_FILE}"
  ensure_parent_dir "${SKIPPED_FILE}"

  if [[ ! -d "${SOURCE_ROOT}" ]]; then
    log "源目录不存在: ${SOURCE_ROOT}"
    exit 1
  fi
}

parse_args() {
  COMMAND="run"

  while (( $# > 0 )); do
    case "$1" in
      run|status|list-pending|pending|list-skipped|skipped|doctor|explain|plan|help)
        COMMAND="$1"
        ;;
      -h|--help)
        COMMAND="help"
        ;;
      --config)
        shift
        [[ $# -gt 0 ]] || die "--config 需要一个路径参数"
        CONFIG_FILE="$1"
        ;;
      --config=*)
        CONFIG_FILE="${1#*=}"
        ;;
      --dry-run)
        CLI_FORCE_DRY_RUN="true"
        ;;
      --)
        shift
        [[ $# -eq 0 ]] || die "存在未识别的位置参数: $*"
        break
        ;;
      *)
        if [[ -n "${COMMAND_TARGET}" ]]; then
          die "存在多余的位置参数: $1"
        fi
        COMMAND_TARGET="$1"
        ;;
    esac
    shift
  done

  case "${COMMAND}" in
    pending) COMMAND="list-pending" ;;
    skipped) COMMAND="list-skipped" ;;
    plan) COMMAND="explain" ;;
  esac

  if [[ "${COMMAND}" != "explain" && -n "${COMMAND_TARGET}" ]]; then
    die "命令 ${COMMAND} 不接受位置参数: ${COMMAND_TARGET}"
  fi
}

print_usage() {
  cat <<EOF
用法:
  ${SCRIPT_BASENAME} [命令] [选项]

命令:
  run            执行一轮移动任务（默认）
  status         查看当前配置、锁、pending、skipped 状态
  list-pending   列出 pending 文件中的待跟踪任务
  list-skipped   列出 skipped 文件中的冲突任务
  doctor         检查依赖、配置、数据库和 OpenList API 连通性
  explain PATH   解释某个文件当前会如何映射、是否会被处理
  help           显示本帮助

选项:
  --config PATH  指定配置文件
  --dry-run      强制使用 dry-run 模式
  -h, --help     显示帮助

示例:
  ${SCRIPT_BASENAME}
  ${SCRIPT_BASENAME} status
  ${SCRIPT_BASENAME} list-pending --config ${SCRIPT_DIR}/openlist-auto-move-douyin-monitor.conf
  ${SCRIPT_BASENAME} explain "${SOURCE_ROOT}/抖音/主播A/demo.mp4"
  ${SCRIPT_BASENAME} doctor --dry-run
EOF
}
