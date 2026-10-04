#!/bin/bash

# ===== APEX elevation preamble :: Hardened, observable, idempotent, safely-concurrent automation: bash scripts pass shellcheck clean under strict mode with trapped error/cleanup, full CLI ergonomics, structured logging, dependency preflight, single-instance locking, and disciplined exit codes; Python scripts use argparse + logging + type hints + pathlib + list-form subprocess + context managers + narrow exception handling + explicit exit codes. This is the fallback "make any ordinary script production-grade" domain. =====
# >>> APEX[DT-BASH-STRICT-HEADER] Canonical strict-mode bash header: portable shebang via env, capital -E so traps inherit into functions/subshells, errexit/nounset/pipefail, safe IFS, and immutable program metadata. Paste directly under any new or hardened bash script. >>>
#
# <one-line description>
#
set -Eeuo pipefail
IFS=$'\n\t'

# --- program metadata ---------------------------------------------------
readonly PROG="${0##*/}"
readonly VERSION="1.0.0"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"

# --- exit codes ---------------------------------------------------------
readonly E_OK=0 E_USAGE=2 E_DEP=3 E_PERM=4 E_LOCK=5 E_RUNTIME=1

# --- runtime flags (defaults) -------------------------------------------
DRY_RUN=false
VERBOSE=false
LOG_LEVEL="INFO"
# <<< APEX[DT-BASH-STRICT-HEADER] <<<

# >>> APEX[DT-BASH-LOG-TRAP-SCAFFOLD] Structured leveled logger writing timestamped lines to stderr, an optional run() wrapper honoring --dry-run, a die() helper, and the ERR/EXIT/signal trap set with cleanup. Provides BASH-ERR-EXIT-TRAP, BASH-SIGNAL-HANDLING, LOG-STRUCTURED-LEVELS, CLI-DRY-RUN, and EXIT-CODE-DISCIPLINE in one block. >>>
# --- logging ------------------------------------------------------------
_level_num(){ case "$1" in DEBUG) echo 10;; INFO) echo 20;; WARN) echo 30;; ERROR) echo 40;; *) echo 20;; esac; }
log(){
  local level="$1"; shift
  (( $(_level_num "$level") < $(_level_num "$LOG_LEVEL") )) && return 0
  printf '%s [%s] %s: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$PROG" "$*" >&2
}
die(){ local code="$1"; shift; log ERROR "$*"; exit "$code"; }

# --- side-effect wrapper (honors --dry-run) -----------------------------
run(){
  if [[ "$DRY_RUN" == true ]]; then log INFO "DRY-RUN: $*"; return 0; fi
  "$@"
}

# --- cleanup + traps ----------------------------------------------------
cleanup(){
  local rc=$?
  [[ -n "${TMPDIR_RUN:-}" && -d "${TMPDIR_RUN:-}" ]] && rm -rf -- "$TMPDIR_RUN"
  [[ -n "${LOCKDIR:-}" && -d "${LOCKDIR:-}" ]] && rmdir -- "$LOCKDIR" 2>/dev/null || true
  return $rc
}
err_trap(){ local line="$1" code="$2"; log ERROR "failed at line ${line} (exit ${code})"; }
on_signal(){ local sig="$1"; log WARN "received SIG${sig}; aborting"; exit $((128 + $(kill -l "$sig") )); }

trap 'err_trap "${LINENO}" "$?"' ERR
trap cleanup EXIT
trap 'on_signal INT'  INT
trap 'on_signal TERM' TERM
trap 'on_signal HUP'  HUP
# <<< APEX[DT-BASH-LOG-TRAP-SCAFFOLD] <<<

# >>> APEX[DT-BASH-ARGPARSE-SKELETON] Argument parser supporting GNU-style long flags and short flags, both --opt value and --opt=value forms, --help/--version/--dry-run/--verbose/--quiet, positional collection, unknown-flag rejection, and a quoted-here-doc usage(). Implements BASH-ARG-PARSING, CLI-HELP-USAGE, CLI-VERSION-FLAG, CLI-DRY-RUN, CLI-VERBOSE-QUIET. >>>
usage(){
  cat <<'EOF'
Usage: PROG [OPTIONS] [ARGS...]

  Short description of what the script does.

Options:
  -h, --help        Show this help and exit
      --version     Print version and exit
  -n, --dry-run     Show actions without performing them
  -v, --verbose     Enable debug logging
  -q, --quiet       Only log warnings and errors

Exit codes:
  0 success  2 usage  3 missing dependency  4 permission  5 lock held
EOF
}

POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)    usage; exit 0 ;;
    --version)    printf '%s %s\n' "$PROG" "$VERSION"; exit 0 ;;
    -n|--dry-run) DRY_RUN=true; shift ;;
    -v|--verbose) VERBOSE=true; LOG_LEVEL="DEBUG"; shift ;;
    -q|--quiet)   LOG_LEVEL="WARN"; shift ;;
    --opt=*)      OPT="${1#*=}"; shift ;;
    --opt)        OPT="${2:?--opt requires a value}"; shift 2 ;;
    --)           shift; POSITIONAL+=("$@"); break ;;
    -*)           usage >&2; die "$E_USAGE" "unknown option: $1" ;;
    *)            POSITIONAL+=("$1"); shift ;;
  esac
done
set -- "${POSITIONAL[@]:-}"
# <<< APEX[DT-BASH-ARGPARSE-SKELETON] <<<

# >>> APEX[DT-BASH-DESTRUCTIVE-GUARD] Confirmation + dry-run guard for destructive operations (rm -rf, mkfs, dd, truncate). safe_rm() refuses empty/'/'/'~' targets, honors --dry-run, and prompts unless --yes was given. Fires when the script performs irreversible deletes/writes (controls IDEMPOTENCY, CLI-DRY-RUN, BASH-ROOT-GUARD). >>>
# --- destructive-operation guard ---------------------------------------
# Wrap irreversible deletes. Refuses to remove '', '/', '/*', or a bare
# $HOME; no-ops under --dry-run; requires confirmation unless ASSUME_YES.
ASSUME_YES="${ASSUME_YES:-false}"
confirm(){
  [[ "$ASSUME_YES" == true ]] && return 0
  local reply; printf '%s [y/N] ' "$1" >&2; read -r reply
  [[ "$reply" =~ ^[Yy]$ ]]
}
safe_rm(){
  local target="${1:?safe_rm: target required}"
  case "$target" in
    ""|/|/\*|"$HOME"|"$HOME/") die "${E_USAGE:-2}" "refusing to remove unsafe path: '$target'" ;;
  esac
  if [[ "${DRY_RUN:-false}" == true ]]; then log INFO "DRY-RUN: rm -rf -- $target"; return 0; fi
  confirm "remove '$target' recursively?" || { log WARN "skipped: $target"; return 0; }
  rm -rf -- "$target"
}
# <<< APEX[DT-BASH-DESTRUCTIVE-GUARD] <<<
# ===== end APEX preamble =====

# nightly backup
SRC=/var/www
DEST=/backups/www-$(date +%F).tar.gz
tar czf $DEST $SRC
rm -f /backups/www-old.tar.gz
echo backup done
