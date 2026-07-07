import os
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_bash(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["REPO_ROOT"] = str(REPO_ROOT)
    env["TMP_ROOT"] = str(tmp_path)
    return subprocess.run(
        ["bash", "-c", textwrap.dedent(script)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def test_batch_submission_groups_same_remote_dirs(tmp_path: Path) -> None:
    result = run_bash(
        r'''
        set -euo pipefail
        SCRIPT_DIR="${REPO_ROOT}/scripts"
        SCRIPT_BASENAME="openlist-auto-move-mp4.sh"
        DEFAULT_CONFIG_FILE="${SCRIPT_DIR}/openlist-auto-move.conf"
        OPENLIST_MOVER_PROJECT_ROOT="${TMP_ROOT}/project"
        mkdir -p "${OPENLIST_MOVER_PROJECT_ROOT}/runtime"

        source "${SCRIPT_DIR}/openlist-auto-move/common.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/state.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/api.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/remote_cache.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/remote.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/paths.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/reconcile.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/dryrun.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/runner.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/runner_legacy.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/runner_batch.sh"

        SOURCE_ROOT="${TMP_ROOT}/source"
        SOURCE_MOUNT="/qbt/source"
        TARGET_BASE="/kuake/target"
        PENDING_FILE="${TMP_ROOT}/runtime/pending"
        SKIPPED_FILE="${TMP_ROOT}/runtime/skipped"
        MOVE_CALLS_FILE="${TMP_ROOT}/move-calls"
        mkdir -p "${SOURCE_ROOT}/acct" "${TMP_ROOT}/runtime"
        printf a > "${SOURCE_ROOT}/acct/1.jpg"
        printf b > "${SOURCE_ROOT}/acct/2.jpg"
        printf c > "${SOURCE_ROOT}/acct/3.jpg"

        FILE_PATTERN="*"
        MIN_AGE_SECONDS=0
        MOVE_BATCH_SIZE=2
        MAX_PENDING_TASKS=2000
        MAX_NEW_FILES_PER_RUN=500
        MAX_RECONCILE_FILES_PER_RUN=1000
        VERIFY_AFTER_MOVE="false"
        REMOTE_LIST_CACHE_ENABLED="true"

        api_post_json() {
          local path="$2" payload="$3"
          case "${path}" in
            "/api/fs/mkdir")
              printf '{"code":200}'
              ;;
            "/api/fs/list")
              jq -cn '{code:200,data:{total:0,content:[]}}'
              ;;
            "/api/fs/move")
              jq -r '.names | length' <<< "${payload}" >> "${MOVE_CALLS_FILE}"
              local call_id
              call_id=$(wc -l < "${MOVE_CALLS_FILE}")
              jq -cn --arg id "task-${call_id}" '{code:200,data:{tasks:[{id:$id}]}}'
              ;;
            *)
              printf 'unexpected api_post_json path: %s\n' "${path}" >&2
              return 1
              ;;
          esac
        }

        process_files_batched "token" "$(date +%s)"

        call_sizes=$(paste -sd, "${MOVE_CALLS_FILE}")
        pending_count=$(wc -l < "${PENDING_FILE}")
        task_count=$(cut -f2 "${PENDING_FILE}" | sort -u | wc -l)
        [[ "${call_sizes}" == "2,1" ]]
        [[ "${pending_count}" == "3" ]]
        [[ "${task_count}" == "2" ]]
        printf 'batch call_sizes=%s pending=%s tasks=%s\n' "${call_sizes}" "${pending_count}" "${task_count}"
        ''',
        tmp_path,
    )
    assert "batch call_sizes=2,1 pending=3 tasks=2" in result.stdout


def test_batch_existing_same_size_target_is_not_submitted(tmp_path: Path) -> None:
    result = run_bash(
        r'''
        set -euo pipefail
        SCRIPT_DIR="${REPO_ROOT}/scripts"
        SCRIPT_BASENAME="openlist-auto-move-mp4.sh"
        DEFAULT_CONFIG_FILE="${SCRIPT_DIR}/openlist-auto-move.conf"
        OPENLIST_MOVER_PROJECT_ROOT="${TMP_ROOT}/project"
        mkdir -p "${OPENLIST_MOVER_PROJECT_ROOT}/runtime"

        source "${SCRIPT_DIR}/openlist-auto-move/common.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/state.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/api.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/remote_cache.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/remote.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/paths.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/reconcile.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/dryrun.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/runner.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/runner_legacy.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/runner_batch.sh"

        SOURCE_ROOT="${TMP_ROOT}/source"
        SOURCE_MOUNT="/qbt/source"
        TARGET_BASE="/kuake/target"
        PENDING_FILE="${TMP_ROOT}/runtime/pending"
        SKIPPED_FILE="${TMP_ROOT}/runtime/skipped"
        MOVE_NAMES_FILE="${TMP_ROOT}/move-names"
        mkdir -p "${SOURCE_ROOT}/acct" "${TMP_ROOT}/runtime"
        printf a > "${SOURCE_ROOT}/acct/a.mp4"
        printf b > "${SOURCE_ROOT}/acct/b.mp4"

        FILE_PATTERN="*"
        MIN_AGE_SECONDS=0
        MOVE_BATCH_SIZE=50
        MAX_PENDING_TASKS=2000
        MAX_NEW_FILES_PER_RUN=500
        MAX_RECONCILE_FILES_PER_RUN=1000
        VERIFY_AFTER_MOVE="false"
        REMOTE_LIST_CACHE_ENABLED="true"
        SKIP_CONFLICTING_FILES="true"

        api_post_json() {
          local path="$2" payload="$3"
          case "${path}" in
            "/api/fs/mkdir")
              printf '{"code":200}'
              ;;
            "/api/fs/list")
              jq -cn '{code:200,data:{total:1,content:[{name:"a.mp4",size:1}]}}'
              ;;
            "/api/fs/move")
              jq -r '.names[]' <<< "${payload}" > "${MOVE_NAMES_FILE}"
              jq -cn '{code:200,data:{tasks:[{id:"task-new"}]}}'
              ;;
            *)
              printf 'unexpected api_post_json path: %s\n' "${path}" >&2
              return 1
              ;;
          esac
        }

        process_files_batched "token" "$(date +%s)"

        [[ ! -e "${SOURCE_ROOT}/acct/a.mp4" ]]
        [[ -e "${SOURCE_ROOT}/acct/b.mp4" ]]
        [[ "$(cat "${MOVE_NAMES_FILE}")" == "b.mp4" ]]
        [[ "$(wc -l < "${PENDING_FILE}")" == "1" ]]
        grep -F "${SOURCE_ROOT}/acct/b.mp4" "${PENDING_FILE}" >/dev/null
        printf 'same-size existing skipped submit names=%s\n' "$(cat "${MOVE_NAMES_FILE}")"
        ''',
        tmp_path,
    )
    assert "same-size existing skipped submit names=b.mp4" in result.stdout


def test_batch_existing_size_mismatch_is_skipped_not_submitted(tmp_path: Path) -> None:
    result = run_bash(
        r'''
        set -euo pipefail
        SCRIPT_DIR="${REPO_ROOT}/scripts"
        SCRIPT_BASENAME="openlist-auto-move-mp4.sh"
        DEFAULT_CONFIG_FILE="${SCRIPT_DIR}/openlist-auto-move.conf"
        OPENLIST_MOVER_PROJECT_ROOT="${TMP_ROOT}/project"
        mkdir -p "${OPENLIST_MOVER_PROJECT_ROOT}/runtime"

        source "${SCRIPT_DIR}/openlist-auto-move/common.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/state.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/api.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/remote_cache.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/remote.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/paths.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/reconcile.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/dryrun.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/runner.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/runner_legacy.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/runner_batch.sh"

        SOURCE_ROOT="${TMP_ROOT}/source"
        SOURCE_MOUNT="/qbt/source"
        TARGET_BASE="/kuake/target"
        PENDING_FILE="${TMP_ROOT}/runtime/pending"
        SKIPPED_FILE="${TMP_ROOT}/runtime/skipped"
        MOVE_NAMES_FILE="${TMP_ROOT}/move-names"
        mkdir -p "${SOURCE_ROOT}/acct" "${TMP_ROOT}/runtime"
        printf aaa > "${SOURCE_ROOT}/acct/a.mp4"
        printf b > "${SOURCE_ROOT}/acct/b.mp4"

        FILE_PATTERN="*"
        MIN_AGE_SECONDS=0
        MOVE_BATCH_SIZE=50
        MAX_PENDING_TASKS=2000
        MAX_NEW_FILES_PER_RUN=500
        MAX_RECONCILE_FILES_PER_RUN=1000
        VERIFY_AFTER_MOVE="false"
        REMOTE_LIST_CACHE_ENABLED="true"
        SKIP_CONFLICTING_FILES="true"
        SIZE_MISMATCH_TOLERANCE_BYTES=0

        api_post_json() {
          local path="$2" payload="$3"
          case "${path}" in
            "/api/fs/mkdir")
              printf '{"code":200}'
              ;;
            "/api/fs/list")
              jq -cn '{code:200,data:{total:1,content:[{name:"a.mp4",size:1}]}}'
              ;;
            "/api/fs/move")
              jq -r '.names[]' <<< "${payload}" > "${MOVE_NAMES_FILE}"
              jq -cn '{code:200,data:{tasks:[{id:"task-new"}]}}'
              ;;
            *)
              printf 'unexpected api_post_json path: %s\n' "${path}" >&2
              return 1
              ;;
          esac
        }

        process_files_batched "token" "$(date +%s)"

        [[ -e "${SOURCE_ROOT}/acct/a.mp4" ]]
        grep -Fx "${SOURCE_ROOT}/acct/a.mp4" "${SKIPPED_FILE}" >/dev/null
        [[ "$(cat "${MOVE_NAMES_FILE}")" == "b.mp4" ]]
        [[ "$(wc -l < "${PENDING_FILE}")" == "1" ]]
        printf 'mismatch skipped submit names=%s skipped=%s\n' "$(cat "${MOVE_NAMES_FILE}")" "$(wc -l < "${SKIPPED_FILE}")"
        ''',
        tmp_path,
    )
    assert "mismatch skipped submit names=b.mp4 skipped=1" in result.stdout


def test_remote_list_cache_survives_subshell_calls(tmp_path: Path) -> None:
    result = run_bash(
        r'''
        set -euo pipefail
        SCRIPT_DIR="${REPO_ROOT}/scripts"
        SCRIPT_BASENAME="openlist-auto-move-mp4.sh"
        DEFAULT_CONFIG_FILE="${SCRIPT_DIR}/openlist-auto-move.conf"
        OPENLIST_MOVER_PROJECT_ROOT="${TMP_ROOT}/project"
        mkdir -p "${OPENLIST_MOVER_PROJECT_ROOT}/runtime"

        source "${SCRIPT_DIR}/openlist-auto-move/common.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/state.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/api.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/remote_cache.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/remote.sh"

        REMOTE_LIST_CACHE_ENABLED="true"
        REMOTE_LIST_REFRESH="true"
        REMOTE_LIST_PAGE_SIZE=1000
        LIST_CALLS_FILE="${TMP_ROOT}/list-calls"

        api_post_json() {
          local path="$2"
          [[ "${path}" == "/api/fs/list" ]] || return 1
          printf x >> "${LIST_CALLS_FILE}"
          jq -cn '{code:200,data:{total:2,content:[{name:"a.jpg",size:1},{name:"b.jpg",size:2}]}}'
        }

        reset_remote_list_cache
        (file_exists_in_dir "token" "/dst" "a.jpg")
        (file_exists_in_dir "token" "/dst" "b.jpg")
        calls=$(wc -c < "${LIST_CALLS_FILE}")
        [[ "${calls}" == "1" ]]
        cleanup_remote_list_cache
        printf 'remote-list calls=%s\n' "${calls}"
        ''',
        tmp_path,
    )
    assert "remote-list calls=1" in result.stdout


def test_missing_remote_entry_returns_status_under_errexit(tmp_path: Path) -> None:
    result = run_bash(
        r'''
        set -euo pipefail
        SCRIPT_DIR="${REPO_ROOT}/scripts"
        SCRIPT_BASENAME="openlist-auto-move-mp4.sh"
        DEFAULT_CONFIG_FILE="${SCRIPT_DIR}/openlist-auto-move.conf"
        OPENLIST_MOVER_PROJECT_ROOT="${TMP_ROOT}/project"
        mkdir -p "${OPENLIST_MOVER_PROJECT_ROOT}/runtime"

        source "${SCRIPT_DIR}/openlist-auto-move/common.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/state.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/api.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/remote_cache.sh"
        source "${SCRIPT_DIR}/openlist-auto-move/remote.sh"

        REMOTE_LIST_CACHE_ENABLED="true"
        REMOTE_LIST_REFRESH="true"
        REMOTE_LIST_PAGE_SIZE=1000

        api_post_json() {
          local path="$2"
          [[ "${path}" == "/api/fs/list" ]] || return 1
          jq -cn '{code:200,data:{total:0,content:[]}}'
        }

        probe_like_finalize() {
          set +e
          file_exists_in_dir "token" "/missing" "absent.mp4"
          local rc=$?
          set -e
          printf '%s' "${rc}"
        }

        rc="$(probe_like_finalize)"
        [[ "${rc}" == "1" ]]
        cleanup_remote_list_cache
        printf 'missing rc=%s\n' "${rc}"
        ''',
        tmp_path,
    )
    assert "missing rc=1" in result.stdout
