#!/usr/bin/env bash

if [[ -n "${OPENLIST_MOVER_DRYRUN_LOADED:-}" ]]; then
  return 0
fi
OPENLIST_MOVER_DRYRUN_LOADED=1

DRY_RUN_COMPLETED_COUNT=0
DRY_RUN_WAIT_COUNT=0
DRY_RUN_CONTINUE_COUNT=0
DRY_RUN_SKIP_COUNT=0
DRY_RUN_UNKNOWN_COUNT=0
DRY_RUN_PLANNED_COUNT=0

get_dry_run_token() {
  local token="" token_rc=0
  if [[ ! -f "${OPENLIST_DB}" ]]; then
    log "DRY_RUN: OpenList 数据库不存在，远端分支预测已降级为本地计划"
    printf '%s' "${token}"
    return 0
  fi

  set +e
  token=$(get_token 2>/dev/null)
  token_rc=$?
  set -e
  if [[ "${token_rc}" -ne 0 || -z "${token}" ]]; then
    log "DRY_RUN: 无法读取 token，远端分支预测已降级为本地计划"
    printf '%s' ""
    return 0
  fi

  printf '%s' "${token}"
}

reset_dry_run_prediction_counts() {
  DRY_RUN_COMPLETED_COUNT=0
  DRY_RUN_WAIT_COUNT=0
  DRY_RUN_CONTINUE_COUNT=0
  DRY_RUN_SKIP_COUNT=0
  DRY_RUN_UNKNOWN_COUNT=0
  DRY_RUN_PLANNED_COUNT=0
}

dry_run_override_mutators() {
  log() { :; }
  mark_file_pending() { :; }
  unmark_file_pending() { :; }
  mark_file_skipped() { :; }
  pending_write_record() { :; }
  pending_mark_observed() { :; }
  pending_increment_stale() { printf '%s' "$((PENDING_RECORD_STALE_CHECKS + 1))"; }
  rm() { :; }
}

dry_run_capture_reconcile_action() {
  local token="$1" file_path="$2" src_dir="$3" dst_dir="$4" file_name="$5"
  (
    dry_run_override_mutators
    reconcile_pending_file "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}"
  )
}

dry_run_capture_attach_rc() {
  local token="$1" file_path="$2" src_dir="$3" dst_dir="$4" file_name="$5"
  (
    dry_run_override_mutators
    local rc=1
    set +e
    attach_existing_undone_move_task "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}"
    rc=$?
    set -e
    printf '%s' "${rc}"
  )
}

dry_run_capture_target_rc() {
  local token="$1" file_path="$2" src_dir="$3" dst_dir="$4" file_name="$5"
  (
    dry_run_override_mutators
    local rc=1
    set +e
    try_cleanup_existing_target "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}"
    rc=$?
    set -e
    printf '%s' "${rc}"
  )
}

dry_run_predict_action() {
  local token="$1" file_pending="$2" file_path="$3" src_dir="$4" dst_dir="$5" file_name="$6"
  local action="${ACTION_UNKNOWN}"
  local path_label="local-only"

  if [[ -n "${token}" ]]; then
    if [[ "${file_pending}" == "true" ]]; then
      action=$(dry_run_capture_reconcile_action "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}")
      path_label="pending-reconcile"
    else
      local attach_rc
      attach_rc=$(dry_run_capture_attach_rc "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}")
      if [[ "${attach_rc}" == "0" || "${attach_rc}" == "2" ]]; then
        action="${ACTION_WAIT}"
        path_label="existing-task-attach"
      else
        local target_rc
        target_rc=$(dry_run_capture_target_rc "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}")
        case "${target_rc}" in
          0) action="${ACTION_COMPLETED}" ; path_label="target-finalize" ;;
          2) action="${ACTION_WAIT}" ; path_label="target-finalize" ;;
          3) action="${ACTION_SKIP}" ; path_label="target-finalize" ;;
          *) action="${ACTION_CONTINUE}" ; path_label="submit-move" ;;
        esac
      fi
    fi
  elif [[ "${file_pending}" == "true" ]]; then
    path_label="pending-reconcile"
  else
    path_label="submit-move"
  fi

  log "DRY_RUN: file=${file_path}, pending=${file_pending}, dst=${dst_dir}/${file_name}, predicted_action=${action}, path=${path_label}, remote=$([[ -n "${token}" ]] && printf 'enabled' || printf 'disabled')"
  printf '%s' "${action}"
}
