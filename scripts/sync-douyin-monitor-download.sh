#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_ROOT="${SOURCE_ROOT:-/root/douyin_user_monitor/download}"
STAGING_ROOT="${STAGING_ROOT:-/root/qbt/downloads/douyin_user_monitor/download}"
LOCK_FILE="${LOCK_FILE:-${PROJECT_ROOT}/runtime/.sync-douyin-monitor-download.lock}"
MIN_AGE_SECONDS="${MIN_AGE_SECONDS:-30}"

SYNC_MOVED_COUNT=0
SYNC_SKIPPED_COUNT=0
SYNC_ERROR_COUNT=0

log() {
  printf '[douyin-sync] %s\n' "$*" >&2
}

acquire_lock() {
  local lock_fd
  exec {lock_fd}>"${LOCK_FILE}" || {
    log "无法创建锁文件: ${LOCK_FILE}"
    exit 1
  }

  if ! flock -n "${lock_fd}"; then
    log "已有同步实例在运行，跳过本轮"
    exit 0
  fi
}

prepare_env() {
  command -v find >/dev/null 2>&1 || { log "缺少命令: find"; exit 1; }
  command -v flock >/dev/null 2>&1 || { log "缺少命令: flock"; exit 1; }
  command -v stat >/dev/null 2>&1 || { log "缺少命令: stat"; exit 1; }
  command -v mv >/dev/null 2>&1 || { log "缺少命令: mv"; exit 1; }

  mkdir -p "${SOURCE_ROOT}"
  mkdir -p "${STAGING_ROOT}"
}

sync_files() {
  local run_epoch
  run_epoch=$(date +%s)
  SYNC_MOVED_COUNT=0
  SYNC_SKIPPED_COUNT=0
  SYNC_ERROR_COUNT=0

  while IFS= read -r -d '' src_file; do
    local src_epoch
    if ! src_epoch=$(stat -c '%Y' "${src_file}" 2>/dev/null); then
      continue
    fi
    if (( run_epoch - src_epoch < MIN_AGE_SECONDS )); then
      SYNC_SKIPPED_COUNT=$((SYNC_SKIPPED_COUNT + 1))
      continue
    fi

    local relative_path="${src_file#${SOURCE_ROOT}/}"
    local dst_file="${STAGING_ROOT}/${relative_path}"
    mkdir -p "$(dirname "${dst_file}")"

    if mv -n -- "${src_file}" "${dst_file}"; then
      SYNC_MOVED_COUNT=$((SYNC_MOVED_COUNT + 1))
    else
      SYNC_ERROR_COUNT=$((SYNC_ERROR_COUNT + 1))
      log "移动到 staging 失败: ${src_file}"
    fi
  done < <(find "${SOURCE_ROOT}" -type f -print0)

  find "${SOURCE_ROOT}" -type d -empty -delete || true
}

main() {
  prepare_env
  acquire_lock
  log "开始同步到 staging: source=${SOURCE_ROOT}, staging=${STAGING_ROOT}, min_age=${MIN_AGE_SECONDS}s"
  sync_files
  log "完成，本轮同步 ${SYNC_MOVED_COUNT} 个文件，跳过 ${SYNC_SKIPPED_COUNT} 个过新文件，失败 ${SYNC_ERROR_COUNT} 个文件"
  if [[ "${SYNC_ERROR_COUNT}" -gt 0 ]]; then
    exit 1
  fi
}

main "$@"
