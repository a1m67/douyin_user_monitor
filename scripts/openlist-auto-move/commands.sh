#!/usr/bin/env bash

if [[ -n "${OPENLIST_MOVER_COMMANDS_LOADED:-}" ]]; then
  return 0
fi
OPENLIST_MOVER_COMMANDS_LOADED=1

doctor_check_cmd() {
  local name="$1"
  if has_cmd "${name}"; then
    printf '[OK] 命令可用: %s\n' "${name}"
    return 0
  fi

  printf '[FAIL] 缺少命令: %s\n' "${name}"
  return 1
}

doctor_check_path_exists() {
  local label="$1"
  local path="$2"
  local kind="$3"

  if [[ "${kind}" == "dir" && -d "${path}" ]]; then
    printf '[OK] %s存在: %s\n' "${label}" "${path}"
    return 0
  fi
  if [[ "${kind}" == "file" && -f "${path}" ]]; then
    printf '[OK] %s存在: %s\n' "${label}" "${path}"
    return 0
  fi

  printf '[FAIL] %s不存在: %s\n' "${label}" "${path}"
  return 1
}

command_doctor() {
  load_config

  local failures=0
  local cmd token="" resp="" api_rc=0
  local -a required_cmds=(sqlite3 jq curl find sed grep awk mktemp flock stat date)
  if [[ "${REMOTE_LIST_CACHE_ENABLED}" == "true" ]]; then
    required_cmds+=(sha256sum)
  fi
  if [[ "${SANITIZE_EMOJI}" == "true" ]]; then
    required_cmds+=(perl)
  fi

  for cmd in "${required_cmds[@]}"; do
    if ! doctor_check_cmd "${cmd}"; then
      failures=$((failures + 1))
    fi
  done

  if ! doctor_check_path_exists "源目录" "${SOURCE_ROOT}" "dir"; then
    failures=$((failures + 1))
  fi
  if ! doctor_check_path_exists "OpenList 数据库" "${OPENLIST_DB}" "file"; then
    failures=$((failures + 1))
  fi

  ensure_parent_dir "${LOCK_FILE}"
  ensure_parent_dir "${PENDING_FILE}"
  ensure_parent_dir "${SKIPPED_FILE}"

  printf '[INFO] 配置文件: %s (exists=%s)\n' "${CONFIG_FILE}" "${CONFIG_EXISTS}"
  printf '[INFO] 配置隔离: %s\n' "$(describe_state_isolation)"
  printf '[INFO] API 地址: %s\n' "${API_URL}"
  printf '[INFO] pending 文件: %s\n' "${PENDING_FILE}"
  printf '[INFO] skipped 文件: %s\n' "${SKIPPED_FILE}"
  printf '[INFO] 完成态收口: 目标端可确认存在时，执行本地清理并清除 pending\n'
  printf '[INFO] 批量配置: batch=%s max_new=%s max_reconcile=%s verify_after_move=%s remote_cache=%s remote_refresh=%s\n' \
    "${MOVE_BATCH_SIZE}" \
    "${MAX_NEW_FILES_PER_RUN}" \
    "${MAX_RECONCILE_FILES_PER_RUN}" \
    "${VERIFY_AFTER_MOVE}" \
    "${REMOTE_LIST_CACHE_ENABLED}" \
    "${REMOTE_LIST_REFRESH}"
  printf '[INFO] 冲突策略: skip=%s tolerance=%s\n' "${SKIP_CONFLICTING_FILES}" "${SIZE_MISMATCH_TOLERANCE_BYTES}"

  if has_cmd sqlite3 && [[ -f "${OPENLIST_DB}" ]]; then
    token=$(get_token 2>/dev/null || true)
    if [[ -n "${token}" ]]; then
      printf '[OK] 已从数据库读取 token\n'
    else
      printf '[FAIL] 无法从数据库读取 token\n'
      failures=$((failures + 1))
    fi
  fi

  if has_cmd curl && has_cmd jq && [[ -n "${token}" ]]; then
    local original_retry_count="${API_RETRY_COUNT}"
    local original_retry_interval="${API_RETRY_INTERVAL_SECONDS}"
    API_RETRY_COUNT=1
    API_RETRY_INTERVAL_SECONDS=1
    set +e
    resp=$(api_get_json "${token}" "/api/task/move/undone")
    api_rc=$?
    set -e
    API_RETRY_COUNT="${original_retry_count}"
    API_RETRY_INTERVAL_SECONDS="${original_retry_interval}"

    if [[ "${api_rc}" -eq 0 && "$(jq -r '.code // 0' <<< "${resp}")" == "200" ]]; then
      printf '[OK] OpenList API 可访问，任务查询正常\n'
    else
      printf '[FAIL] OpenList API 查询失败: %s\n' "${API_URL}"
      failures=$((failures + 1))
    fi
  fi

  if (( failures > 0 )); then
    printf '[RESULT] doctor 失败项: %s\n' "${failures}"
    return 1
  fi

  printf '[RESULT] doctor 通过\n'
}

dispatch_command() {
  case "${COMMAND}" in
    run) command_run ;;
    status) command_status ;;
    list-pending)
      load_config
      print_pending_records
      ;;
    list-skipped)
      load_config
      print_skipped_records
      ;;
    explain) command_explain ;;
    doctor) command_doctor ;;
    help) print_usage ;;
    *) die "不支持的命令: ${COMMAND}" ;;
  esac
}
