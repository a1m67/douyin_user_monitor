#!/usr/bin/env bash

if [[ -n "${OPENLIST_MOVER_RUNNER_BATCH_LOADED:-}" ]]; then
  return 0
fi
OPENLIST_MOVER_RUNNER_BATCH_LOADED=1

normalize_nonnegative_limit() {
  local value="$1"
  local fallback="$2"
  [[ "${value}" =~ ^[0-9]+$ ]] && printf '%s' "${value}" || printf '%s' "${fallback}"
}

batch_mode_enabled() {
  [[ "${DRY_RUN}" == "true" ]] && return 1
  (( $(normalize_nonnegative_limit "${MOVE_BATCH_SIZE}" "1") > 1 )) && return 0
  [[ "${VERIFY_AFTER_MOVE}" != "true" ]] && return 0
  (( $(normalize_nonnegative_limit "${MAX_NEW_FILES_PER_RUN}" "0") > 0 )) && return 0
  (( $(normalize_nonnegative_limit "${MAX_RECONCILE_FILES_PER_RUN}" "0") > 0 )) && return 0
  [[ "${REMOTE_LIST_CACHE_ENABLED}" == "true" ]]
}

record_process_action() {
  local action="$1"
  case "${action}" in
    "${ACTION_COMPLETED}") PROCESS_MOVED_COUNT=$((PROCESS_MOVED_COUNT + 1)) ;;
    "${ACTION_SKIP}") PROCESS_SKIPPED_COUNT=$((PROCESS_SKIPPED_COUNT + 1)) ;;
    "${ACTION_WAIT}") ;;
    *) PROCESS_ERROR_COUNT=$((PROCESS_ERROR_COUNT + 1)) ;;
  esac
}

process_pending_files_batched() {
  local token="$1"
  local max_reconcile processed=0 action
  max_reconcile=$(normalize_nonnegative_limit "${MAX_RECONCILE_FILES_PER_RUN}" "0")

  while IFS= read -r -d '' file_path; do
    if (( max_reconcile > 0 && processed >= max_reconcile )); then
      log "pending 对账达到本轮上限: ${processed}/${max_reconcile}"
      return 0
    fi
    action=$(process_one_file "${token}" "${file_path}")
    record_process_action "${action}"
    processed=$((processed + 1))
  done < <(list_pending_paths)
}

can_submit_more_new_files() {
  local pending_limit="$1"
  local pending_count="$2"
  local new_count="$3"
  local max_new="$4"

  (( max_new > 0 && new_count >= max_new )) && return 1
  (( pending_limit > 0 && pending_count + new_count >= pending_limit )) && return 1
  return 0
}

init_batch_buffers() {
  declare -gA BATCH_FILES=()
  declare -gA BATCH_NAMES=()
  declare -gA BATCH_SRC_DIRS=()
  declare -gA BATCH_DST_DIRS=()
  declare -gA BATCH_COUNTS=()
  declare -gA BATCH_TARGET_READY=()
  declare -ga BATCH_KEYS=()
}

batch_add_file() {
  local key="$1" file_path="$2" src_dir="$3" dst_dir="$4" file_name="$5"
  local count="${BATCH_COUNTS["${key}"]:-0}"
  if (( count == 0 )); then
    BATCH_KEYS+=("${key}")
    BATCH_SRC_DIRS["${key}"]="${src_dir}"
    BATCH_DST_DIRS["${key}"]="${dst_dir}"
  fi
  BATCH_FILES["${key}"]+="${file_path}"$'\n'
  BATCH_NAMES["${key}"]+="${file_name}"$'\n'
  BATCH_COUNTS["${key}"]=$((count + 1))
}

batch_names_json() {
  local key="$1"
  printf '%s' "${BATCH_NAMES["${key}"]}" | jq -Rsc 'split("\n") | map(select(length > 0))'
}

mark_batch_pending() {
  local key="$1" task_id="$2" file_path
  while IFS= read -r file_path; do
    [[ -n "${file_path}" ]] && mark_file_pending "${file_path}" "${task_id}"
  done <<< "${BATCH_FILES["${key}"]}"
  return 0
}

process_batch_fallback() {
  local token="$1" key="$2" file_path action
  while IFS= read -r file_path; do
    [[ -n "${file_path}" ]] || continue
    action=$(process_one_file "${token}" "${file_path}")
    record_process_action "${action}"
  done <<< "${BATCH_FILES["${key}"]}"
  return 0
}

clear_batch() {
  local key="$1"
  BATCH_FILES["${key}"]=""
  BATCH_NAMES["${key}"]=""
  BATCH_SRC_DIRS["${key}"]=""
  BATCH_DST_DIRS["${key}"]=""
  BATCH_COUNTS["${key}"]=0
}

ensure_batch_target_dir() {
  local token="$1" dst_dir="$2"
  if [[ -n "${BATCH_TARGET_READY["${dst_dir}"]+x}" ]]; then
    return 0
  fi
  ensure_remote_dir "${token}" "${dst_dir}" || return 1
  BATCH_TARGET_READY["${dst_dir}"]=1
}

precheck_batch_candidate_target() {
  local token="$1" file_path="$2" src_dir="$3" dst_dir="$4" file_name="$5"
  local action
  action=$(handle_existing_target "${token}" "${file_path}" "${src_dir}" "${dst_dir}" "${file_name}")
  case "${action}" in
    "${ACTION_CONTINUE}") return 0 ;;
    "${ACTION_COMPLETED}"|"${ACTION_WAIT}"|"${ACTION_SKIP}"|"${ACTION_ERROR}")
      record_process_action "${action}"
      return 1
      ;;
    *)
      PROCESS_ERROR_COUNT=$((PROCESS_ERROR_COUNT + 1))
      return 1
      ;;
  esac
}

flush_batch() {
  local token="$1" key="$2" count src_dir dst_dir names_json move_rc=0
  count="${BATCH_COUNTS["${key}"]:-0}"
  (( count > 0 )) || return 0
  src_dir="${BATCH_SRC_DIRS["${key}"]}"
  dst_dir="${BATCH_DST_DIRS["${key}"]}"
  names_json=$(batch_names_json "${key}")

  ensure_remote_dir "${token}" "${dst_dir}" || { PROCESS_ERROR_COUNT=$((PROCESS_ERROR_COUNT + count)); clear_batch "${key}"; return 0; }
  set +e
  move_remote_files "${token}" "${src_dir}" "${dst_dir}" "${names_json}"
  move_rc=$?
  set -e

  reset_remote_list_cache
  if [[ "${move_rc}" -eq 0 ]]; then
    mark_batch_pending "${key}" "${MOVE_LAST_TASK_ID}"
    PROCESS_SUBMITTED_COUNT=$((PROCESS_SUBMITTED_COUNT + count))
    log "已提交批量移动任务: count=${count}, task=${MOVE_LAST_TASK_ID:--}, src=${src_dir}, dst=${dst_dir}"
  elif [[ "${move_rc}" -eq 2 ]]; then
    log "批量移动遇到同名冲突，回退逐文件处理: count=${count}, src=${src_dir}, dst=${dst_dir}"
    process_batch_fallback "${token}" "${key}"
  else
    PROCESS_ERROR_COUNT=$((PROCESS_ERROR_COUNT + count))
  fi
  clear_batch "${key}"
}

flush_all_batches() {
  local token="$1" key
  for key in "${BATCH_KEYS[@]}"; do
    flush_batch "${token}" "${key}"
  done
}

submit_new_files_batched() {
  local token="$1" run_epoch="$2" batch_size pending_limit max_new
  local pending_count new_count=0 limit_logged="false"
  batch_size=$(normalize_nonnegative_limit "${MOVE_BATCH_SIZE}" "1")
  (( batch_size > 0 )) || batch_size=1
  pending_limit=$(normalize_nonnegative_limit "${MAX_PENDING_TASKS}" "1")
  max_new=$(normalize_nonnegative_limit "${MAX_NEW_FILES_PER_RUN}" "0")
  pending_count=$(pending_record_count)
  init_batch_buffers

  while IFS= read -r -d '' file_path; do
    if ! can_submit_more_new_files "${pending_limit}" "${pending_count}" "${new_count}" "${max_new}"; then
      [[ "${limit_logged}" == "true" ]] || log "新任务提交达到本轮上限: new=${new_count}, pending=${pending_count}, max_new=${max_new}, pending_limit=${pending_limit}"
      limit_logged="true"
      break
    fi
    SUBMIT_CANDIDATE_ADDED=0
    submit_candidate_file "${token}" "${run_epoch}" "${file_path}" "${batch_size}" || true
    new_count=$((new_count + SUBMIT_CANDIDATE_ADDED))
  done < <(list_source_files)
  flush_all_batches "${token}"
}

submit_candidate_file() {
  local token="$1" run_epoch="$2" file_path="$3" batch_size="$4"
  local -a context
  local key
  SUBMIT_CANDIDATE_ADDED=0

  is_file_pending "${file_path}" && return 0
  should_process_file "${file_path}" "${run_epoch}" || return 0
  mapfile -t context < <(resolve_process_context "${file_path}" "true") || { PROCESS_ERROR_COUNT=$((PROCESS_ERROR_COUNT + 1)); return 0; }
  is_file_pending "${context[0]}" && return 0
  ensure_batch_target_dir "${token}" "${context[2]}" || { PROCESS_ERROR_COUNT=$((PROCESS_ERROR_COUNT + 1)); return 0; }
  precheck_batch_candidate_target "${token}" "${context[0]}" "${context[1]}" "${context[2]}" "${context[3]}" || return 0
  key="${context[1]}"$'\t'"${context[2]}"
  batch_add_file "${key}" "${context[0]}" "${context[1]}" "${context[2]}" "${context[3]}"
  SUBMIT_CANDIDATE_ADDED=1
  if (( ${BATCH_COUNTS["${key}"]} >= batch_size )); then
    flush_batch "${token}" "${key}"
  fi
}

process_files_batched() {
  local token="$1"
  local run_epoch="$2"
  PROCESS_MOVED_COUNT=0
  PROCESS_ERROR_COUNT=0
  PROCESS_SKIPPED_COUNT=0
  PROCESS_SUBMITTED_COUNT=0
  reset_dry_run_prediction_counts
  reset_move_task_cache
  reset_remote_list_cache

  log "启用批量模式: batch_size=${MOVE_BATCH_SIZE}, max_new=${MAX_NEW_FILES_PER_RUN}, max_reconcile=${MAX_RECONCILE_FILES_PER_RUN}, verify_after_move=${VERIFY_AFTER_MOVE}, remote_cache=${REMOTE_LIST_CACHE_ENABLED}"
  process_pending_files_batched "${token}"
  submit_new_files_batched "${token}" "${run_epoch}"
}
