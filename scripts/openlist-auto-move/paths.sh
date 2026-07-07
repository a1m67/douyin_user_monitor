#!/usr/bin/env bash

if [[ -n "${OPENLIST_MOVER_PATHS_LOADED:-}" ]]; then
  return 0
fi
OPENLIST_MOVER_PATHS_LOADED=1

sanitize_component() {
  local text="$1"
  local sanitized
  sanitized=$(printf '%s' "${text}" | perl -CSDA -Mutf8 -pe 's/\p{Extended_Pictographic}//g; s/[\x{FE0F}\x{200D}]//g;')
  sanitized=$(printf '%s' "${sanitized}" | sed -E 's/[[:space:]]+/ /g; s/^[[:space:]]+//; s/[[:space:]]+$//')
  printf '%s' "${sanitized}"
}

sanitize_relative_dir() {
  local relative_dir="$1"
  if [[ "${relative_dir}" == "." ]]; then
    printf '.'
    return 0
  fi

  local result=""
  local component
  IFS='/' read -r -a components <<< "${relative_dir}"
  for component in "${components[@]}"; do
    local cleaned
    cleaned=$(sanitize_component "${component}")
    if [[ -z "${cleaned}" ]]; then
      log "目录名去除 emoji 后为空: ${component}"
      return 1
    fi
    if [[ -z "${result}" ]]; then
      result="${cleaned}"
    else
      result="${result}/${cleaned}"
    fi
  done

  printf '%s' "${result}"
}

sanitize_file_name_in_place() {
  local file_path="$1"
  local dir_path
  local base_name
  local sanitized_name
  local target_path

  dir_path=$(dirname "${file_path}")
  base_name=$(basename "${file_path}")
  sanitized_name=$(sanitize_component "${base_name}")

  if [[ -z "${sanitized_name}" ]]; then
    log "文件名去除 emoji 后为空: ${file_path}"
    return 1
  fi

  if [[ "${sanitized_name}" == "${base_name}" ]]; then
    printf '%s' "${file_path}"
    return 0
  fi

  target_path="${dir_path}/${sanitized_name}"
  if [[ -e "${target_path}" ]]; then
    log "重命名冲突，目标已存在: ${target_path}"
    return 1
  fi

  mv "${file_path}" "${target_path}"
  log "已重命名文件: ${file_path} -> ${target_path}"
  printf '%s' "${target_path}"
}

predict_sanitized_file_path() {
  local file_path="$1"
  local dir_path
  local base_name
  local sanitized_name

  dir_path=$(dirname "${file_path}")
  base_name=$(basename "${file_path}")
  sanitized_name=$(sanitize_component "${base_name}")

  if [[ -z "${sanitized_name}" ]]; then
    return 1
  fi

  if [[ "${sanitized_name}" == "${base_name}" ]]; then
    printf '%s' "${file_path}"
    return 0
  fi

  printf '%s/%s' "${dir_path}" "${sanitized_name}"
}

should_process_file() {
  local file_path="$1"
  local run_epoch="$2"
  local file_epoch

  if ! file_epoch=$(stat -c '%Y' "${file_path}" 2>/dev/null); then
    log "文件已不存在，跳过: ${file_path}"
    return 1
  fi

  if is_file_skipped "${file_path}"; then
    return 1
  fi

  if [[ "${file_epoch}" -gt "${run_epoch}" ]]; then
    return 1
  fi

  if (( run_epoch - file_epoch < MIN_AGE_SECONDS )); then
    return 1
  fi

  return 0
}

build_remote_paths() {
  local file_path="$1"
  local relative_path="${file_path#${SOURCE_ROOT}/}"
  if [[ -n "${STRIP_PREFIX_DIR}" ]]; then
    case "${relative_path}" in
      "${STRIP_PREFIX_DIR}/"*) relative_path="${relative_path#${STRIP_PREFIX_DIR}/}" ;;
    esac
  fi

  local source_path="${SOURCE_MOUNT}/${relative_path}"
  local relative_dir
  relative_dir=$(dirname "${relative_path}")

  local effective_relative_dir="${relative_dir}"
  if [[ "${SANITIZE_EMOJI}" == "true" ]]; then
    effective_relative_dir=$(sanitize_relative_dir "${relative_dir}") || return 1
  fi

  local dst_dir="${TARGET_BASE}"
  if [[ "${effective_relative_dir}" != "." ]]; then
    dst_dir="${TARGET_BASE}/${effective_relative_dir}"
  fi

  local src_dir
  local file_name
  src_dir=$(dirname "${source_path}")
  file_name=$(basename "${source_path}")
  printf '%s\n%s\n%s\n' "${src_dir}" "${dst_dir}" "${file_name}"
}

list_source_files() {
  if [[ "${FILE_PATTERN}" == "*" ]]; then
    find "${SOURCE_ROOT}" -type f -print0
    return 0
  fi

  find "${SOURCE_ROOT}" -type f -name "${FILE_PATTERN}" -print0
}
