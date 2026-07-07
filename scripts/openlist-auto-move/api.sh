#!/usr/bin/env bash

if [[ -n "${OPENLIST_MOVER_API_LOADED:-}" ]]; then
  return 0
fi
OPENLIST_MOVER_API_LOADED=1

MOVE_TASK_ID=""
MOVE_TASK_STATE=""
MOVE_TASK_STATUS=""
MOVE_TASK_PROGRESS="0"
MOVE_TASK_ERROR=""
MOVE_TASK_CACHE_UNDONE=""
MOVE_TASK_CACHE_DONE=""

get_token() {
  sqlite3 "${OPENLIST_DB}" "SELECT value FROM x_setting_items WHERE key='token' LIMIT 1;"
}

api_request() {
  local token="$1"
  local method="$2"
  local path="$3"
  local payload="${4:-}"
  local attempt=1

  while (( attempt <= API_RETRY_COUNT )); do
    local resp=""
    if [[ "${method}" == "POST" ]]; then
      if resp=$(curl -fsS -X POST "${API_URL}${path}" -H "Authorization: ${token}" -H 'Content-Type: application/json' -d "${payload}"); then
        printf '%s' "${resp}"
        return 0
      fi
    elif resp=$(curl -fsS -X GET "${API_URL}${path}" -H "Authorization: ${token}"); then
      printf '%s' "${resp}"
      return 0
    fi

    if (( attempt == API_RETRY_COUNT )); then
      break
    fi

    sleep $((API_RETRY_INTERVAL_SECONDS * attempt))
    attempt=$((attempt + 1))
  done

  return 1
}

api_post_json() {
  local token="$1"
  local path="$2"
  local payload="$3"
  api_request "${token}" "POST" "${path}" "${payload}"
}

api_get_json() {
  local token="$1"
  local path="$2"
  api_request "${token}" "GET" "${path}" ""
}

reset_move_task_cache() {
  MOVE_TASK_CACHE_UNDONE=""
  MOVE_TASK_CACHE_DONE=""
}

get_move_task_list_cached() {
  local token="$1"
  local endpoint="$2"

  case "${endpoint}" in
    "/api/task/move/undone")
      if [[ -z "${MOVE_TASK_CACHE_UNDONE}" ]]; then
        MOVE_TASK_CACHE_UNDONE=$(api_get_json "${token}" "${endpoint}") || return 1
      fi
      printf '%s' "${MOVE_TASK_CACHE_UNDONE}"
      ;;
    "/api/task/move/done")
      if [[ -z "${MOVE_TASK_CACHE_DONE}" ]]; then
        MOVE_TASK_CACHE_DONE=$(api_get_json "${token}" "${endpoint}") || return 1
      fi
      printf '%s' "${MOVE_TASK_CACHE_DONE}"
      ;;
    *)
      api_get_json "${token}" "${endpoint}"
      ;;
  esac
}

load_move_task_fields() {
  local task_json="$1"
  MOVE_TASK_ID=$(jq -r '.id // ""' <<< "${task_json}")
  MOVE_TASK_STATE=$(jq -r '.state // ""' <<< "${task_json}")
  MOVE_TASK_STATUS=$(jq -r '.status // ""' <<< "${task_json}")
  MOVE_TASK_PROGRESS=$(jq -r '.progress // 0' <<< "${task_json}")
  MOVE_TASK_ERROR=$(jq -r '.error // ""' <<< "${task_json}")
}

get_move_task_by_id() {
  local token="$1"
  local task_id="$2"
  local endpoint

  for endpoint in "/api/task/move/undone" "/api/task/move/done"; do
    local resp
    if ! resp=$(get_move_task_list_cached "${token}" "${endpoint}"); then
      return 2
    fi
    if [[ "$(jq -r '.code // 0' <<< "${resp}")" != "200" ]]; then
      continue
    fi

    local task_json
    task_json=$(jq -c --arg id "${task_id}" '.data // [] | map(select(.id == $id)) | .[0] // empty' <<< "${resp}")
    if [[ -n "${task_json}" && "${task_json}" != "null" ]]; then
      printf '%s' "${task_json}"
      return 0
    fi
  done

  return 1
}

path_root_name() {
  local path="$1"
  local trimmed="${path#/}"
  printf '%s' "${trimmed%%/*}"
}

path_relative_to_root() {
  local path="$1"
  local trimmed="${path#/}"
  if [[ "${trimmed}" == */* ]]; then
    printf '/%s' "${trimmed#*/}"
    return 0
  fi

  printf '/'
}

build_move_task_name() {
  local src_dir="$1"
  local dst_dir="$2"
  local file_name="$3"
  local src_root src_rel dst_root dst_rel src_file

  src_root=$(path_root_name "${src_dir}")
  src_rel=$(path_relative_to_root "${src_dir}")
  dst_root=$(path_root_name "${dst_dir}")
  dst_rel=$(path_relative_to_root "${dst_dir}")
  src_file=$([[ "${src_rel}" == "/" ]] && printf '/%s' "${file_name}" || printf '%s/%s' "${src_rel}" "${file_name}")
  printf 'move [/%s](%s) to [/%s](%s)' "${src_root}" "${src_file}" "${dst_root}" "${dst_rel}"
}

find_matching_undone_move_task() {
  local token="$1"
  local src_dir="$2"
  local dst_dir="$3"
  local file_name="$4"
  local expected_name
  expected_name=$(build_move_task_name "${src_dir}" "${dst_dir}" "${file_name}")

  local resp
  resp=$(api_get_json "${token}" "/api/task/move/undone") || return 2
  if [[ "$(jq -r '.code // 0' <<< "${resp}")" != "200" ]]; then
    return 2
  fi

  local task_json
  task_json=$(jq -c --arg name "${expected_name}" '
    .data // []
    | map(select(.name == $name))
    | if length == 0 then empty else max_by((if (.state // 0) == 1 then 1000000 else 0 end) + (.progress // 0)) end
  ' <<< "${resp}")

  if [[ -n "${task_json}" && "${task_json}" != "null" ]]; then
    printf '%s' "${task_json}"
    return 0
  fi

  return 1
}

attach_existing_undone_move_task() {
  local token="$1"
  local file_path="$2"
  local src_dir="$3"
  local dst_dir="$4"
  local file_name="$5"
  local previous_task_id="${6:-}"
  local task_json="" find_rc=0 now_epoch

  task_json=$(find_matching_undone_move_task "${token}" "${src_dir}" "${dst_dir}" "${file_name}")
  find_rc=$?

  if [[ "${find_rc}" -eq 2 ]]; then
    log "查询同名未完成任务失败，暂不重新提交: ${file_path}"
    return 2
  fi
  if [[ "${find_rc}" -ne 0 || -z "${task_json}" ]]; then
    return 1
  fi

  load_move_task_fields "${task_json}"
  now_epoch=$(date +%s)
  pending_mark_observed "${file_path}" "${MOVE_TASK_ID}" "${now_epoch}" "${MOVE_TASK_PROGRESS}" "${now_epoch}"

  if [[ -n "${previous_task_id}" && "${MOVE_TASK_ID}" != "${previous_task_id}" ]]; then
    log "检测到同名未完成任务，切换跟踪: file=${file_path}, old_task=${previous_task_id}, task=${MOVE_TASK_ID}, state=${MOVE_TASK_STATE}, progress=${MOVE_TASK_PROGRESS}, status=${MOVE_TASK_STATUS}"
  else
    log "检测到同名未完成任务，继续等待: file=${file_path}, task=${MOVE_TASK_ID}, state=${MOVE_TASK_STATE}, progress=${MOVE_TASK_PROGRESS}, status=${MOVE_TASK_STATUS}"
  fi
  return 0
}

ensure_remote_dir() {
  local token="$1"
  local dst_dir="$2"
  local payload
  payload=$(jq -cn --arg path "${dst_dir}" '{path:$path}')

  local resp
  resp=$(api_post_json "${token}" "/api/fs/mkdir" "${payload}")
  if [[ "$(jq -r '.code // 0' <<< "${resp}")" != "200" ]]; then
    log "创建目录失败: ${dst_dir} ($(jq -r '.message // "unknown error"' <<< "${resp}"))"
    return 1
  fi
}

move_remote_file() {
  local token="$1"
  local src_dir="$2"
  local dst_dir="$3"
  local file_name="$4"
  local names_json
  names_json=$(jq -cn --arg n "${file_name}" '[$n]')

  move_remote_files "${token}" "${src_dir}" "${dst_dir}" "${names_json}"
}

move_remote_files() {
  local token="$1"
  local src_dir="$2"
  local dst_dir="$3"
  local names_json="$4"
  MOVE_LAST_TASK_ID=""

  local payload
  payload=$(jq -cn --arg s "${src_dir}" --arg d "${dst_dir}" --argjson names "${names_json}" '{src_dir:$s,dst_dir:$d,names:$names}')

  local resp
  resp=$(api_post_json "${token}" "/api/fs/move" "${payload}")
  reset_move_task_cache

  local code
  code=$(jq -r '.code // 0' <<< "${resp}")
  if [[ "${code}" == "200" ]]; then
    MOVE_LAST_TASK_ID=$(jq -r '.data.tasks[0].id // ""' <<< "${resp}")
    return 0
  fi

  local msg
  msg=$(jq -r '.message // "unknown error"' <<< "${resp}")
  if [[ "${msg}" == *"exists"* ]]; then
    log "目标已存在同名文件，批次未提交: src=${src_dir}, dst=${dst_dir}"
    return 2
  fi

  log "移动失败: src=${src_dir}, dst=${dst_dir} (${msg})"
  return 1
}

float_gt() {
  local left="$1"
  local right="$2"
  awk -v l="${left}" -v r="${right}" -v eps="${PENDING_PROGRESS_EPSILON}" 'BEGIN { exit !(l > r + eps) }'
}
