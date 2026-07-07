#!/usr/bin/env bash

if [[ -n "${OPENLIST_MOVER_RUNNER_LOADED:-}" ]]; then
  return 0
fi
OPENLIST_MOVER_RUNNER_LOADED=1

emit_process_outcome() {
  printf '%s' "$1"
}

resolve_process_context() {
  local file_path="$1"
  local allow_rename="${2:-true}"
  local actual_file_path="${file_path}"

  if [[ "${allow_rename}" == "true" && -f "${file_path}" && "${DRY_RUN}" != "true" && "${SANITIZE_EMOJI}" == "true" ]]; then
    actual_file_path=$(sanitize_file_name_in_place "${file_path}") || return 1
  fi

  local paths
  local src_dir dst_dir file_name
  paths=$(build_remote_paths "${actual_file_path}") || return 1
  src_dir=$(sed -n '1p' <<< "${paths}")
  dst_dir=$(sed -n '2p' <<< "${paths}")
  file_name=$(sed -n '3p' <<< "${paths}")
  printf '%s\n%s\n%s\n%s\n' "${actual_file_path}" "${src_dir}" "${dst_dir}" "${file_name}"
}

handle_pending_reconcile() {
  local token="$1"
  local file_path="$2"
  local src_dir="$3"
  local dst_dir="$4"
  local file_name="$5"
  local action

  action=$(reconcile_pending_file "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}")
  case "${action}" in
    "${ACTION_COMPLETED}") emit_process_outcome "${ACTION_COMPLETED}" ;;
    "${ACTION_WAIT}") emit_process_outcome "${ACTION_WAIT}" ;;
    "${ACTION_SKIP}") emit_process_outcome "${ACTION_SKIP}" ;;
    *) emit_process_outcome "${ACTION_CONTINUE}" ;;
  esac
}

handle_existing_task_attach() {
  local token="$1"
  local file_path="$2"
  local src_dir="$3"
  local dst_dir="$4"
  local file_name="$5"
  local attach_rc=1

  set +e
  attach_existing_undone_move_task "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}"
  attach_rc=$?
  set -e

  case "${attach_rc}" in
    0|2) emit_process_outcome "${ACTION_WAIT}" ;;
    *) emit_process_outcome "${ACTION_CONTINUE}" ;;
  esac
}

handle_existing_target() {
  local token="$1"
  local file_path="$2"
  local src_dir="$3"
  local dst_dir="$4"
  local file_name="$5"
  local cleanup_rc=0

  set +e
  try_cleanup_existing_target "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}"
  cleanup_rc=$?
  set -e

  case "${cleanup_rc}" in
    0) emit_process_outcome "${ACTION_COMPLETED}" ;;
    2) emit_process_outcome "${ACTION_WAIT}" ;;
    3) emit_process_outcome "${ACTION_SKIP}" ;;
    *) emit_process_outcome "${ACTION_CONTINUE}" ;;
  esac
}

handle_submitted_move() {
  local token="$1"
  local file_path="$2"
  local src_dir="$3"
  local dst_dir="$4"
  local file_name="$5"
  local move_rc=0

  set +e
  move_remote_file "${token}" "${src_dir}" "${dst_dir}" "${file_name}"
  move_rc=$?
  set -e

  if [[ "${move_rc}" -eq 0 ]]; then
    mark_file_pending "${file_path}" "${MOVE_LAST_TASK_ID}"
    if [[ -n "${MOVE_LAST_TASK_ID}" ]]; then
      log "已提交移动任务: file=${file_path}, task=${MOVE_LAST_TASK_ID}, dst=${dst_dir}/${file_name}"
    else
      log "已提交移动任务但未返回 task_id: file=${file_path}, dst=${dst_dir}/${file_name}"
    fi

    reset_remote_list_cache
    if [[ "${VERIFY_AFTER_MOVE}" == "true" ]] && verify_move_finished "${token}" "${src_dir}" "${dst_dir}" "${file_name}"; then
      finalize_move_file "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "移动任务已完成即时校验" "${ACTION_WAIT}"
      return 0
    fi

    emit_process_outcome "${ACTION_WAIT}"
    return 0
  fi

  if [[ "${move_rc}" -eq 2 ]]; then
    finalize_move_file "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "提交移动时目标端已存在同名文件" "${ACTION_WAIT}"
    return 0
  fi

  emit_process_outcome "${ACTION_ERROR}"
}

process_one_file() {
  local token="$1"
  local file_path="$2"
  local file_pending="false"
  local -a context

  if is_file_pending "${file_path}"; then
    file_pending="true"
  fi
  mapfile -t context < <(resolve_process_context "${file_path}" "$([[ "${file_pending}" == "true" ]] && printf 'false' || printf 'true')") || {
    emit_process_outcome "${ACTION_ERROR}"
    return 0
  }

  local actual_file_path="${context[0]}"
  local src_dir="${context[1]}"
  local dst_dir="${context[2]}"
  local file_name="${context[3]}"

  if [[ "${DRY_RUN}" == "true" ]]; then
    dry_run_predict_action "${token}" "${file_pending}" "${actual_file_path}" "${src_dir}" "${dst_dir}" "${file_name}"
    return 0
  fi

  local action="${ACTION_CONTINUE}"
  if [[ "${file_pending}" != "true" ]] && is_file_pending "${actual_file_path}"; then
    file_pending="true"
  fi
  if [[ "${file_pending}" == "true" ]]; then
    action=$(handle_pending_reconcile "${token}" "${actual_file_path}" "${src_dir}" "${dst_dir}" "${file_name}")
  else
    action=$(handle_existing_task_attach "${token}" "${actual_file_path}" "${src_dir}" "${dst_dir}" "${file_name}")
  fi

  case "${action}" in
    "${ACTION_COMPLETED}"|"${ACTION_WAIT}"|"${ACTION_SKIP}"|"${ACTION_ERROR}")
      emit_process_outcome "${action}"
      return 0
      ;;
  esac

  ensure_remote_dir "${token}" "${dst_dir}" || {
    emit_process_outcome "${ACTION_ERROR}"
    return 0
  }

  action=$(handle_existing_target "${token}" "${actual_file_path}" "${src_dir}" "${dst_dir}" "${file_name}")
  case "${action}" in
    "${ACTION_COMPLETED}"|"${ACTION_WAIT}"|"${ACTION_SKIP}"|"${ACTION_ERROR}")
      emit_process_outcome "${action}"
      return 0
      ;;
  esac

  handle_submitted_move "${token}" "${actual_file_path}" "${src_dir}" "${dst_dir}" "${file_name}"
}

process_files() {
  if batch_mode_enabled; then
    process_files_batched "$@"
  else
    process_files_legacy "$@"
  fi
}

command_run() {
  prepare_env
  trap cleanup_remote_list_cache EXIT
  if [[ "${DRY_RUN}" != "true" ]]; then
    acquire_lock
    prune_pending_file
    prune_skipped_file
  fi

  local token=""
  if [[ "${DRY_RUN}" != "true" ]]; then
    token=$(get_token)
    if [[ -z "${token}" ]]; then
      log "无法从 OpenList 数据库读取 token"
      exit 1
    fi
  else
    token=$(get_dry_run_token)
  fi

  local run_epoch
  run_epoch=$(date +%s)
  process_files "${token}" "${run_epoch}"

  if [[ "${DRY_RUN}" == "true" ]]; then
    log "DRY_RUN 完成，计划处理 ${DRY_RUN_PLANNED_COUNT} 个文件；预测 completed=${DRY_RUN_COMPLETED_COUNT}, wait=${DRY_RUN_WAIT_COUNT}, continue=${DRY_RUN_CONTINUE_COUNT}, skip=${DRY_RUN_SKIP_COUNT}, unknown=${DRY_RUN_UNKNOWN_COUNT}"
  else
    log "完成，本轮完成收口 ${PROCESS_MOVED_COUNT} 个文件，提交 ${PROCESS_SUBMITTED_COUNT} 个文件，跳过 ${PROCESS_SKIPPED_COUNT} 个文件，失败 ${PROCESS_ERROR_COUNT} 个文件"
  fi
  if [[ "${PROCESS_ERROR_COUNT}" -gt 0 ]]; then
    exit 1
  fi
}
