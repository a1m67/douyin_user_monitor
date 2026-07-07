#!/usr/bin/env bash

if [[ -n "${OPENLIST_MOVER_RECONCILE_LOADED:-}" ]]; then
  return 0
fi
OPENLIST_MOVER_RECONCILE_LOADED=1

emit_reconcile_action() {
  printf '%s' "$1"
}

pending_stale_age() {
  local now_epoch="$1"
  local last_seen_at="$2"
  printf '%s' "$((now_epoch - last_seen_at))"
}

pending_should_reset() {
  local stale_age="$1"
  local stale_checks="$2"
  (( stale_age >= PENDING_STALE_SECONDS || stale_checks >= PENDING_MAX_STALE_CHECKS ))
}

reconcile_wait_or_reset() {
  local file_path="$1"
  local task_id="$2"
  local submitted_at="$3"
  local progress="$4"
  local last_seen_at="$5"
  local stale_checks="$6"
  local now_epoch="$7"
  local message="$8"
  local next_stale
  local stale_age

  next_stale=$(pending_increment_stale "${file_path}" "${task_id}" "${submitted_at}" "${progress}" "${last_seen_at}" "${stale_checks}")
  stale_age=$(pending_stale_age "${now_epoch}" "${last_seen_at}")

  if pending_should_reset "${stale_age}" "${next_stale}"; then
    unmark_file_pending "${file_path}"
    log "${message}，达到 stale 阈值，清理 pending 后允许重试: file=${file_path}, task=${task_id:--}, stale_age=${stale_age}s, checks=${next_stale}"
    emit_reconcile_action "${ACTION_CONTINUE}"
    return 0
  fi

  log "${message}，继续等待: file=${file_path}, task=${task_id:--}, stale_age=${stale_age}s, checks=${next_stale}"
  emit_reconcile_action "${ACTION_WAIT}"
}

reconcile_attach_result() {
  local attach_rc="$1"
  case "${attach_rc}" in
    0|2) emit_reconcile_action "${ACTION_WAIT}" ;;
    *) emit_reconcile_action "${ACTION_CONTINUE}" ;;
  esac
}

reconcile_finalize_action() {
  local token="$1"
  local file_path="$2"
  local src_dir="$3"
  local dst_dir="$4"
  local file_name="$5"
  local reason="$6"
  local missing_target_action="${7:-${ACTION_WAIT}}"
  finalize_move_file "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "${reason}" "${missing_target_action}"
}

reconcile_failed_task() {
  local token="$1"
  local file_path="$2"
  local src_dir="$3"
  local dst_dir="$4"
  local file_name="$5"
  local reason="$6"
  local now_epoch="$7"
  local attach_rc=1

  set +e
  attach_existing_undone_move_task "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "${PENDING_RECORD_TASK_ID}"
  attach_rc=$?
  set -e

  if [[ "${attach_rc}" -eq 0 ]]; then
    emit_reconcile_action "${ACTION_WAIT}"
    return 0
  fi
  if [[ "${attach_rc}" -eq 2 ]]; then
    reconcile_wait_or_reset \
      "${file_path}" \
      "${PENDING_RECORD_TASK_ID}" \
      "${PENDING_RECORD_SUBMITTED_AT}" \
      "${PENDING_RECORD_PROGRESS}" \
      "${PENDING_RECORD_LAST_SEEN_AT}" \
      "${PENDING_RECORD_STALE_CHECKS}" \
      "${now_epoch}" \
      "查询同名未完成任务失败"
    return 0
  fi

  unmark_file_pending "${file_path}"
  log "${reason}，准备重试: file=${file_path}, task=${PENDING_RECORD_TASK_ID:--}, state=${MOVE_TASK_STATE:--}, progress=${MOVE_TASK_PROGRESS:-0}, status=${MOVE_TASK_STATUS:--}, error=${MOVE_TASK_ERROR:--}"
  emit_reconcile_action "${ACTION_CONTINUE}"
}

reconcile_running_task() {
  local token="$1"
  local file_path="$2"
  local src_dir="$3"
  local dst_dir="$4"
  local file_name="$5"
  local now_epoch="$6"
  local next_stale
  local stale_age

  if float_gt "${MOVE_TASK_PROGRESS}" "${PENDING_RECORD_PROGRESS}"; then
    pending_mark_observed "${file_path}" "${PENDING_RECORD_TASK_ID}" "${PENDING_RECORD_SUBMITTED_AT}" "${MOVE_TASK_PROGRESS}" "${now_epoch}"
    log "移动任务进行中: file=${file_path}, task=${PENDING_RECORD_TASK_ID}, state=${MOVE_TASK_STATE}, progress=${MOVE_TASK_PROGRESS}, status=${MOVE_TASK_STATUS}"
    emit_reconcile_action "${ACTION_WAIT}"
    return 0
  fi

  next_stale=$(pending_increment_stale "${file_path}" "${PENDING_RECORD_TASK_ID}" "${PENDING_RECORD_SUBMITTED_AT}" "${PENDING_RECORD_PROGRESS}" "${PENDING_RECORD_LAST_SEEN_AT}" "${PENDING_RECORD_STALE_CHECKS}")
  stale_age=$(pending_stale_age "${now_epoch}" "${PENDING_RECORD_LAST_SEEN_AT}")
  if ! pending_should_reset "${stale_age}" "${next_stale}"; then
    log "文件已提交移动，等待目标端完成: file=${file_path}, task=${PENDING_RECORD_TASK_ID}, state=${MOVE_TASK_STATE}, progress=${MOVE_TASK_PROGRESS}, stale_age=${stale_age}s, checks=${next_stale}"
    emit_reconcile_action "${ACTION_WAIT}"
    return 0
  fi

  local attach_rc=1
  set +e
  attach_existing_undone_move_task "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "${PENDING_RECORD_TASK_ID}"
  attach_rc=$?
  set -e

  if [[ "${attach_rc}" -eq 0 || "${attach_rc}" -eq 2 ]]; then
    emit_reconcile_action "${ACTION_WAIT}"
    return 0
  fi

  unmark_file_pending "${file_path}"
  log "移动任务长时间无进展，重置 pending 后重试: file=${file_path}, task=${PENDING_RECORD_TASK_ID}, progress=${MOVE_TASK_PROGRESS}, stale_age=${stale_age}s, checks=${next_stale}"
  emit_reconcile_action "${ACTION_CONTINUE}"
}

reconcile_task_json() {
  local token="$1"
  local file_path="$2"
  local src_dir="$3"
  local dst_dir="$4"
  local file_name="$5"
  local now_epoch="$6"
  local task_json="$7"

  load_move_task_fields "${task_json}"
  log "对账任务状态: file=${file_path}, task=${MOVE_TASK_ID:--}, state=${MOVE_TASK_STATE:--}, progress=${MOVE_TASK_PROGRESS:-0}, status=${MOVE_TASK_STATUS:--}, error=${MOVE_TASK_ERROR:--}"

  case "${MOVE_TASK_STATE}" in
    2)
      reconcile_finalize_action "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "移动任务已完成" "${ACTION_WAIT}"
      ;;
    3|4|5|6|7|8|9)
      reconcile_failed_task "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "移动任务失败" "${now_epoch}"
      ;;
    *)
      if [[ -n "${MOVE_TASK_ERROR}" ]]; then
        reconcile_failed_task "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "移动任务出现错误" "${now_epoch}"
      else
        reconcile_running_task "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "${now_epoch}"
      fi
      ;;
  esac
}

reconcile_without_task() {
  local token="$1"
  local file_path="$2"
  local src_dir="$3"
  local dst_dir="$4"
  local file_name="$5"
  local now_epoch="$6"
  local attach_rc=1

  set +e
  attach_existing_undone_move_task "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "${PENDING_RECORD_TASK_ID}"
  attach_rc=$?
  set -e

  if [[ "${attach_rc}" -eq 0 ]]; then
    reconcile_attach_result "${attach_rc}"
    return 0
  fi
  if [[ "${attach_rc}" -eq 2 ]]; then
    reconcile_wait_or_reset \
      "${file_path}" \
      "${PENDING_RECORD_TASK_ID}" \
      "${PENDING_RECORD_SUBMITTED_AT}" \
      "${PENDING_RECORD_PROGRESS}" \
      "${PENDING_RECORD_LAST_SEEN_AT}" \
      "${PENDING_RECORD_STALE_CHECKS}" \
      "${now_epoch}" \
      "查询同名未完成任务失败"
    return 0
  fi

  local action
  action=$(reconcile_finalize_action "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "pending 无可用任务，检查是否已完成" "${ACTION_CONTINUE}")
  if [[ "${action}" != "${ACTION_CONTINUE}" ]]; then
    emit_reconcile_action "${action}"
    return 0
  fi

  if [[ -f "${file_path}" ]]; then
    unmark_file_pending "${file_path}"
    log "pending 记录已失效，准备重新提交移动: ${file_path}"
    emit_reconcile_action "${ACTION_CONTINUE}"
    return 0
  fi

  reconcile_wait_or_reset \
    "${file_path}" \
    "${PENDING_RECORD_TASK_ID}" \
    "${PENDING_RECORD_SUBMITTED_AT}" \
    "${PENDING_RECORD_PROGRESS}" \
    "${PENDING_RECORD_LAST_SEEN_AT}" \
    "${PENDING_RECORD_STALE_CHECKS}" \
    "${now_epoch}" \
    "本地源文件已不存在且未找到可附着任务"
}

reconcile_pending_file() {
  local token="$1"
  local file_path="$2"
  local src_dir="$3"
  local dst_dir="$4"
  local file_name="$5"
  local now_epoch
  local task_json=""
  local task_rc=0

  now_epoch=$(date +%s)
  if ! pending_load_record "${file_path}" "${now_epoch}"; then
    emit_reconcile_action "${ACTION_CONTINUE}"
    return 0
  fi

  if [[ -n "${PENDING_RECORD_TASK_ID}" ]]; then
    set +e
    task_json=$(get_move_task_by_id "${token}" "${PENDING_RECORD_TASK_ID}")
    task_rc=$?
    set -e

    case "${task_rc}" in
      0)
        reconcile_task_json "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "${now_epoch}" "${task_json}"
        return 0
        ;;
      2)
        reconcile_wait_or_reset \
          "${file_path}" \
          "${PENDING_RECORD_TASK_ID}" \
          "${PENDING_RECORD_SUBMITTED_AT}" \
          "${PENDING_RECORD_PROGRESS}" \
          "${PENDING_RECORD_LAST_SEEN_AT}" \
          "${PENDING_RECORD_STALE_CHECKS}" \
          "${now_epoch}" \
          "连续读取任务状态失败"
        return 0
        ;;
    esac
  fi

  reconcile_without_task "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}" "${now_epoch}"
}
