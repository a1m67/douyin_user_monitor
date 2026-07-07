#!/usr/bin/env bash
if [[ -n "${OPENLIST_MOVER_STATE_LOADED:-}" ]]; then
  return 0
fi
OPENLIST_MOVER_STATE_LOADED=1
PENDING_RECORD_FILE_PATH=""
PENDING_RECORD_TASK_ID=""
PENDING_RECORD_SUBMITTED_AT="0"
PENDING_RECORD_PROGRESS="0"
PENDING_RECORD_LAST_SEEN_AT="0"
PENDING_RECORD_STALE_CHECKS="0"

acquire_lock() {
  local lock_fd
  ensure_parent_dir "${LOCK_FILE}"
  exec {lock_fd}>"${LOCK_FILE}" || { log "无法创建锁文件: ${LOCK_FILE}"; exit 1; }
  if ! flock -n "${lock_fd}"; then
    log "已有实例在运行，跳过本轮"
    exit 0
  fi
}
lock_is_held() {
  (
    exec {lock_fd}>"${LOCK_FILE}" || exit 2
    flock -n "${lock_fd}" && exit 1
    exit 0
  )
  case "$?" in
    0) return 0 ;;
    1) return 1 ;;
    *) return 2 ;;
  esac
}
pending_line_file_path() {
  local line="$1"
  [[ "${line}" == *$'\t'* ]] && printf '%s' "${line%%$'\t'*}" || printf '%s' "${line}"
}
pending_parse_line() {
  local line="$1" file_path="" task_id="" submitted_at="0" last_progress="0" last_seen_at="0" stale_checks="0"
  if [[ "${line}" == *$'\t'* ]]; then
    IFS=$'\t' read -r file_path task_id submitted_at last_progress last_seen_at stale_checks <<< "${line}"
  else
    file_path="${line}"
  fi
  printf '%s\n%s\n%s\n%s\n%s\n%s\n' "${file_path}" "${task_id}" "${submitted_at:-0}" "${last_progress:-0}" "${last_seen_at:-0}" "${stale_checks:-0}"
}
pending_find_line() {
  local file_path="$1"
  [[ -f "${PENDING_FILE}" ]] || return 1
  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    if [[ "$(pending_line_file_path "${line}")" == "${file_path}" ]]; then
      printf '%s' "${line}"
      return 0
    fi
  done < "${PENDING_FILE}"
  return 1
}
pending_normalize_epoch() {
  local value="${1:-0}" fallback="${2:-0}"
  [[ "${value}" =~ ^[0-9]+$ ]] && (( value > 0 )) && printf '%s' "${value}" || printf '%s' "${fallback}"
}
pending_normalize_counter() {
  local value="${1:-0}"
  [[ "${value}" =~ ^[0-9]+$ ]] && (( value >= 0 )) && printf '%s' "${value}" || printf '0'
}
pending_load_line() {
  local line="$1" now_epoch="${2:-$(date +%s)}"
  local -a fields
  mapfile -t fields < <(pending_parse_line "${line}")
  PENDING_RECORD_FILE_PATH="${fields[0]}"
  PENDING_RECORD_TASK_ID="${fields[1]}"
  PENDING_RECORD_SUBMITTED_AT=$(pending_normalize_epoch "${fields[2]}" "${now_epoch}")
  PENDING_RECORD_PROGRESS="${fields[3]:-0}"
  PENDING_RECORD_LAST_SEEN_AT=$(pending_normalize_epoch "${fields[4]}" "${PENDING_RECORD_SUBMITTED_AT}")
  PENDING_RECORD_STALE_CHECKS=$(pending_normalize_counter "${fields[5]}")
}
pending_load_record() {
  local file_path="$1" now_epoch="${2:-$(date +%s)}" line
  line=$(pending_find_line "${file_path}") || return 1
  pending_load_line "${line}" "${now_epoch}"
}
pending_write_record() {
  local file_path="$1" task_id="$2" submitted_at="$3" last_progress="$4" last_seen_at="$5" stale_checks="$6"
  local new_record tmp_file replaced=0
  new_record="${file_path}"$'\t'"${task_id}"$'\t'"${submitted_at}"$'\t'"${last_progress}"$'\t'"${last_seen_at}"$'\t'"${stale_checks}"
  tmp_file=$(mktemp)
  if [[ -f "${PENDING_FILE}" ]]; then
    while IFS= read -r line; do
      [[ -z "${line}" ]] && continue
      if [[ "$(pending_line_file_path "${line}")" == "${file_path}" ]]; then
        if (( replaced == 0 )); then
          printf '%s\n' "${new_record}" >> "${tmp_file}"
          replaced=1
        fi
        continue
      fi
      printf '%s\n' "${line}" >> "${tmp_file}"
    done < "${PENDING_FILE}"
  fi
  (( replaced == 0 )) && printf '%s\n' "${new_record}" >> "${tmp_file}"
  mv "${tmp_file}" "${PENDING_FILE}"
}
is_file_pending() {
  local file_path="$1"
  pending_find_line "${file_path}" >/dev/null
}
mark_file_pending() {
  local file_path="$1" task_id="${2:-}" now_epoch
  now_epoch=$(date +%s)
  pending_write_record "${file_path}" "${task_id}" "${now_epoch}" "0" "${now_epoch}" "0"
}
update_pending_progress() {
  local file_path="$1" task_id="$2" submitted_at="$3" last_progress="$4" last_seen_at="$5" stale_checks="$6"
  pending_write_record "${file_path}" "${task_id}" "${submitted_at}" "${last_progress}" "${last_seen_at}" "${stale_checks}"
}
pending_mark_observed() {
  local file_path="$1" task_id="$2" submitted_at="$3" last_progress="$4" now_epoch="${5:-$(date +%s)}"
  pending_write_record "${file_path}" "${task_id}" "${submitted_at}" "${last_progress}" "${now_epoch}" "0"
}
pending_increment_stale() {
  local file_path="$1" task_id="$2" submitted_at="$3" last_progress="$4" last_seen_at="$5" stale_checks="$6"
  local next_stale=$((stale_checks + 1))
  pending_write_record "${file_path}" "${task_id}" "${submitted_at}" "${last_progress}" "${last_seen_at}" "${next_stale}"
  printf '%s' "${next_stale}"
}
unmark_file_pending() {
  local file_path="$1"
  [[ -f "${PENDING_FILE}" ]] || return 0
  local tmp_file
  tmp_file=$(mktemp)
  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    [[ "$(pending_line_file_path "${line}")" == "${file_path}" ]] && continue
    printf '%s\n' "${line}" >> "${tmp_file}"
  done < "${PENDING_FILE}"
  mv "${tmp_file}" "${PENDING_FILE}"
}
prune_pending_file() {
  [[ -f "${PENDING_FILE}" ]] || return 0
  local tmp_file
  tmp_file=$(mktemp)
  declare -A seen=()
  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    local file_path
    file_path=$(pending_line_file_path "${line}")
    [[ -z "${file_path}" || -n "${seen["${file_path}"]+x}" ]] && continue
    seen["${file_path}"]=1
    printf '%s\n' "${line}" >> "${tmp_file}"
  done < "${PENDING_FILE}"
  mv "${tmp_file}" "${PENDING_FILE}"
}
list_pending_paths() {
  [[ -f "${PENDING_FILE}" ]] || return 0
  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    local file_path
    file_path=$(pending_line_file_path "${line}")
    [[ -n "${file_path}" ]] && printf '%s\0' "${file_path}"
  done < "${PENDING_FILE}"
}
pending_record_count() {
  count_non_empty_lines "${PENDING_FILE}"
}
is_file_skipped() {
  local file_path="$1"
  [[ "${SKIP_CONFLICTING_FILES}" == "true" && -f "${SKIPPED_FILE}" ]] || return 1
  grep -Fx -- "${file_path}" "${SKIPPED_FILE}" >/dev/null 2>&1
}
mark_file_skipped() {
  local file_path="$1"
  [[ "${SKIP_CONFLICTING_FILES}" == "true" ]] || return 1
  is_file_skipped "${file_path}" && return 0
  printf '%s\n' "${file_path}" >> "${SKIPPED_FILE}"
}
prune_skipped_file() {
  [[ "${SKIP_CONFLICTING_FILES}" == "true" && -f "${SKIPPED_FILE}" ]] || return 0
  local tmp_file
  tmp_file=$(mktemp)
  declare -A seen=()
  while IFS= read -r line; do
    [[ -z "${line}" || -n "${seen["${line}"]+x}" ]] && continue
    seen["${line}"]=1
    [[ -f "${line}" ]] && printf '%s\n' "${line}" >> "${tmp_file}"
  done < "${SKIPPED_FILE}"
  mv "${tmp_file}" "${SKIPPED_FILE}"
}
skipped_record_count() {
  count_non_empty_lines "${SKIPPED_FILE}"
}
