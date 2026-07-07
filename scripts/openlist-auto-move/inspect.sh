#!/usr/bin/env bash

if [[ -n "${OPENLIST_MOVER_INSPECT_LOADED:-}" ]]; then
  return 0
fi
OPENLIST_MOVER_INSPECT_LOADED=1

print_kv() {
  local key="$1"
  local value="$2"
  printf '%-18s %s\n' "${key}" "${value}"
}

print_pending_records() {
  local now_epoch
  now_epoch=$(date +%s)

  printf 'pending 文件: %s\n' "${PENDING_FILE}"
  printf '配置隔离: %s\n' "$(describe_state_isolation)"
  printf 'stale 阈值: %ss / %s 次\n' "${PENDING_STALE_SECONDS}" "${PENDING_MAX_STALE_CHECKS}"

  if [[ ! -s "${PENDING_FILE}" ]]; then
    printf 'pending 为空\n'
    return 0
  fi

  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    pending_load_line "${line}" "${now_epoch}"

    local age_seconds=0
    local stale_age=0
    local exists_now="false"
    if [[ -f "${PENDING_RECORD_FILE_PATH}" ]]; then
      exists_now="true"
    fi
    age_seconds=$((now_epoch - PENDING_RECORD_SUBMITTED_AT))
    stale_age=$((now_epoch - PENDING_RECORD_LAST_SEEN_AT))

    printf -- '- %s\n' "${PENDING_RECORD_FILE_PATH}"
    printf '  local_exists=%s task=%s progress=%s submitted=%s age=%ss last_seen=%s stale_age=%ss stale_checks=%s\n' \
      "${exists_now}" \
      "${PENDING_RECORD_TASK_ID:--}" \
      "${PENDING_RECORD_PROGRESS:-0}" \
      "$(format_epoch "${PENDING_RECORD_SUBMITTED_AT}")" \
      "${age_seconds}" \
      "$(format_epoch "${PENDING_RECORD_LAST_SEEN_AT}")" \
      "${stale_age}" \
      "${PENDING_RECORD_STALE_CHECKS:-0}"
  done < "${PENDING_FILE}"
}

print_skipped_records() {
  printf 'skipped 文件: %s\n' "${SKIPPED_FILE}"
  printf '配置隔离: %s\n' "$(describe_state_isolation)"

  if [[ "${SKIP_CONFLICTING_FILES}" != "true" ]]; then
    printf '当前配置未启用 skipped 机制\n'
    return 0
  fi
  if [[ ! -s "${SKIPPED_FILE}" ]]; then
    printf 'skipped 为空\n'
    return 0
  fi

  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    printf -- '- %s\n' "${line}"
  done < "${SKIPPED_FILE}"
}

normalize_input_path() {
  local input_path="$1"
  if [[ "${input_path}" == /* ]]; then
    printf '%s' "${input_path}"
    return 0
  fi

  printf '%s/%s' "$(pwd)" "${input_path}"
}

command_status() {
  load_config

  local lock_state="未知"
  local skipped_count="0"
  if lock_is_held; then
    lock_state="运行中"
  else
    case "$?" in
      1) lock_state="空闲" ;;
      *) lock_state="无法判断" ;;
    esac
  fi
  if [[ "${SKIP_CONFLICTING_FILES}" == "true" ]]; then
    skipped_count=$(skipped_record_count)
  fi

  print_kv "命令" "${COMMAND}"
  print_kv "配置文件" "${CONFIG_FILE}"
  print_kv "配置存在" "${CONFIG_EXISTS}"
  print_kv "配置隔离" "$(describe_state_isolation)"
  print_kv "源目录" "${SOURCE_ROOT}"
  print_kv "源挂载" "${SOURCE_MOUNT}"
  print_kv "目标目录" "${TARGET_BASE}"
  print_kv "文件匹配" "${FILE_PATTERN}"
  print_kv "最小静置秒数" "${MIN_AGE_SECONDS}"
  print_kv "并发上限" "${MAX_PENDING_TASKS}"
  print_kv "批量大小" "${MOVE_BATCH_SIZE}"
  print_kv "每轮新文件上限" "${MAX_NEW_FILES_PER_RUN}"
  print_kv "每轮对账上限" "${MAX_RECONCILE_FILES_PER_RUN}"
  print_kv "提交后即时校验" "${VERIFY_AFTER_MOVE}"
  print_kv "远端列表缓存" "${REMOTE_LIST_CACHE_ENABLED}"
  print_kv "远端列表刷新" "${REMOTE_LIST_REFRESH}"
  print_kv "stale 秒数" "${PENDING_STALE_SECONDS}"
  print_kv "stale 次数" "${PENDING_MAX_STALE_CHECKS}"
  print_kv "冲突跳过" "${SKIP_CONFLICTING_FILES}"
  print_kv "大小容差" "${SIZE_MISMATCH_TOLERANCE_BYTES}"
  print_kv "DRY_RUN" "${DRY_RUN}"
  print_kv "锁状态" "${lock_state}"
  print_kv "锁文件" "${LOCK_FILE}"
  print_kv "pending 文件" "${PENDING_FILE}"
  print_kv "pending 数量" "$(pending_record_count)"
  print_kv "pending 更新时间" "$(format_file_mtime "${PENDING_FILE}")"
  print_kv "skipped 文件" "${SKIPPED_FILE}"
  print_kv "skipped 数量" "${skipped_count}"
  print_kv "skipped 更新时间" "$(format_file_mtime "${SKIPPED_FILE}")"
  print_kv "完成态收口" "目标端可确认存在时执行本地清理并清除 pending"
}

command_explain() {
  load_config
  if [[ -z "${COMMAND_TARGET}" ]]; then
    die "explain 需要一个文件路径参数"
  fi

  local original_path planned_path within_source="false"
  local exists_now="false" age_seconds="-"
  original_path=$(normalize_input_path "${COMMAND_TARGET}")
  planned_path="${original_path}"

  if [[ "${SANITIZE_EMOJI}" == "true" ]]; then
    planned_path=$(predict_sanitized_file_path "${original_path}" 2>/dev/null || printf '%s' "${original_path}")
  fi
  case "${planned_path}" in
    "${SOURCE_ROOT}"/*) within_source="true" ;;
  esac

  if [[ -e "${original_path}" ]]; then
    exists_now="true"
    local file_epoch
    file_epoch=$(stat -c '%Y' "${original_path}" 2>/dev/null || printf '0')
    if [[ "${file_epoch}" =~ ^[0-9]+$ ]] && (( file_epoch > 0 )); then
      age_seconds=$(( $(date +%s) - file_epoch ))
    fi
  fi

  print_kv "原始路径" "${original_path}"
  print_kv "计划路径" "${planned_path}"
  print_kv "文件存在" "${exists_now}"
  print_kv "位于源目录" "${within_source}"
  print_kv "年龄秒数" "${age_seconds}"
  print_kv "已在 pending" "$(is_file_pending "${planned_path}" && printf 'true' || printf 'false')"
  print_kv "已在 skipped" "$(is_file_skipped "${planned_path}" && printf 'true' || printf 'false')"
  print_kv "配置隔离" "$(describe_state_isolation)"
  print_kv "完成态收口" "目标端可确认存在时删除本地源文件并清除 pending"

  if [[ "${within_source}" != "true" ]]; then
    print_kv "结论" "不在 SOURCE_ROOT 下，脚本不会处理"
    return 0
  fi

  local remote_paths src_dir dst_dir file_name
  remote_paths=$(build_remote_paths "${planned_path}") || {
    print_kv "结论" "远端路径推导失败"
    return 1
  }
  src_dir=$(sed -n '1p' <<< "${remote_paths}")
  dst_dir=$(sed -n '2p' <<< "${remote_paths}")
  file_name=$(sed -n '3p' <<< "${remote_paths}")

  print_kv "远端源目录" "${src_dir}"
  print_kv "远端目标目录" "${dst_dir}"
  print_kv "远端文件名" "${file_name}"

  if [[ "${exists_now}" != "true" ]]; then
    print_kv "结论" "文件当前不存在；若仍在 pending，会继续走对账/完成态收口逻辑"
    return 0
  fi

  if should_process_file "${original_path}" "$(date +%s)"; then
    print_kv "结论" "满足当前扫描条件"
  else
    print_kv "结论" "当前不会处理，通常是太新、已跳过或文件状态已变化"
  fi
}
