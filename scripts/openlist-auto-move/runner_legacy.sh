#!/usr/bin/env bash

if [[ -n "${OPENLIST_MOVER_RUNNER_LEGACY_LOADED:-}" ]]; then
  return 0
fi
OPENLIST_MOVER_RUNNER_LEGACY_LOADED=1
LEGACY_PENDING_LIMIT=1
LEGACY_UNLIMITED_PENDING="false"

list_candidate_files() {
  declare -A seen=()
  while IFS= read -r -d '' file_path; do
    [[ -n "${file_path}" ]] || continue
    if [[ -n "${seen["${file_path}"]+x}" ]]; then
      continue
    fi
    seen["${file_path}"]=1
    printf '%s\0' "${file_path}"
  done < <(list_source_files; list_pending_paths)
}

process_files_legacy() {
  local token="$1"
  local run_epoch="$2"
  local pending_count=0
  local limit_logged="false"

  PROCESS_MOVED_COUNT=0
  PROCESS_ERROR_COUNT=0
  PROCESS_SKIPPED_COUNT=0
  PROCESS_SUBMITTED_COUNT=0
  reset_dry_run_prediction_counts

  normalize_legacy_pending_limit
  pending_count=$(pending_record_count)
  log_legacy_pending_count "${pending_count}"

  while IFS= read -r -d '' file_path; do
    local file_pending="false"
    local action

    if is_file_pending "${file_path}"; then
      file_pending="true"
    elif ! should_process_file "${file_path}" "${run_epoch}"; then
      continue
    fi

    pending_count=$(pending_record_count)
    if legacy_pending_limit_blocks_new_file "${file_pending}" "${pending_count}"; then
      if [[ "${limit_logged}" != "true" ]]; then
        log "进行中的移动任务达到上限（${pending_count}/${LEGACY_PENDING_LIMIT}），本轮只继续 reconcile 现有 pending"
        limit_logged="true"
      fi
      continue
    fi
    limit_logged="false"

    action=$(process_one_file "${token}" "${file_path}")
    if [[ "${DRY_RUN}" == "true" ]]; then
      record_legacy_dry_run_action "${action}"
      continue
    fi

    record_legacy_process_action "${action}"
  done < <(list_candidate_files)
}

normalize_legacy_pending_limit() {
  LEGACY_PENDING_LIMIT="${MAX_PENDING_TASKS}"
  LEGACY_UNLIMITED_PENDING="false"
  if ! [[ "${LEGACY_PENDING_LIMIT}" =~ ^[0-9]+$ ]]; then
    log "MAX_PENDING_TASKS 非法，回退为 1: ${LEGACY_PENDING_LIMIT}"
    LEGACY_PENDING_LIMIT=1
  elif (( LEGACY_PENDING_LIMIT == 0 )); then
    LEGACY_UNLIMITED_PENDING="true"
  fi
}

log_legacy_pending_count() {
  local pending_count="$1"
  (( pending_count > 0 )) || return 0
  if [[ "${LEGACY_UNLIMITED_PENDING}" == "true" ]]; then
    log "检测到 ${pending_count} 个 pending，本轮不设并发上限，优先对账后继续提交新任务"
    return 0
  fi
  log "检测到 ${pending_count} 个 pending，本轮允许最多 ${LEGACY_PENDING_LIMIT} 个并发移动任务"
}

legacy_pending_limit_blocks_new_file() {
  local file_pending="$1"
  local pending_count="$2"
  [[ "${file_pending}" != "true" ]] \
    && [[ "${LEGACY_UNLIMITED_PENDING}" != "true" ]] \
    && (( pending_count >= LEGACY_PENDING_LIMIT ))
}

record_legacy_dry_run_action() {
  local action="$1"
  DRY_RUN_PLANNED_COUNT=$((DRY_RUN_PLANNED_COUNT + 1))
  case "${action}" in
    "${ACTION_COMPLETED}") DRY_RUN_COMPLETED_COUNT=$((DRY_RUN_COMPLETED_COUNT + 1)) ;;
    "${ACTION_WAIT}") DRY_RUN_WAIT_COUNT=$((DRY_RUN_WAIT_COUNT + 1)) ;;
    "${ACTION_CONTINUE}") DRY_RUN_CONTINUE_COUNT=$((DRY_RUN_CONTINUE_COUNT + 1)) ;;
    "${ACTION_SKIP}") DRY_RUN_SKIP_COUNT=$((DRY_RUN_SKIP_COUNT + 1)) ;;
    *) DRY_RUN_UNKNOWN_COUNT=$((DRY_RUN_UNKNOWN_COUNT + 1)) ;;
  esac
}

record_legacy_process_action() {
  local action="$1"
  case "${action}" in
    "${ACTION_COMPLETED}") PROCESS_MOVED_COUNT=$((PROCESS_MOVED_COUNT + 1)) ;;
    "${ACTION_SKIP}") PROCESS_SKIPPED_COUNT=$((PROCESS_SKIPPED_COUNT + 1)) ;;
    "${ACTION_WAIT}") ;;
    *) PROCESS_ERROR_COUNT=$((PROCESS_ERROR_COUNT + 1)) ;;
  esac
}
