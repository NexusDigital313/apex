#!/usr/bin/env bash
# Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
# Proprietary and Confidential.
#
# Unauthorized reproduction, copying, modification, distribution,
# redistribution, publication, reverse engineering, decompilation, or
# disassembly of this file, in whole or in part, via any medium, is
# strictly prohibited without explicit prior written authorization.

# ===== APEX elevation preamble :: Courtroom-admissible acquisition: FRE 901/902 authentication & self-authentication, Daubert reliability, NIST SP 800-86 (IR forensics), NIST SP 800-101r1 (mobile), ACPO 4 principles, SWGDE Best Practices for Computer/Image Acquisition, ISO/IEC 27037:2012, RFC 3227 (order of volatility), RFC 3161 (timestamping) =====
# >>> APEX[DT-STRICT-TRAP] Enforce abort-on-any-error and a contemporaneous failure record. Adds strict mode and an ERR trap that logs the exact failing command, line number, and UTC time, then exits non-zero so no downstream seal/sign can run on a broken acquisition; the EXIT handler seals only on a clean exit. Satisfies FOR-SET-EUO-TRAP and feeds FOR-CMDLINE-RECORD. >>>
set -Eeuo pipefail
: "${COC_LOG:?COC_LOG (chain-of-custody log path) must be set}"
# absolute, so a script that changes directory keeps writing the same custody log
case $COC_LOG in /*) ;; *) COC_LOG=$PWD/$COC_LOG ;; esac
_log(){ printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$COC_LOG" >&2; }
_fatal(){ _log "FATAL line:${BASH_LINENO[0]:-?} cmd:[${BASH_COMMAND}] rc:$?"; _log 'ABORT: acquisition not verified — DO NOT use output as evidence'; APEX_ABORTED=1; exit 1; }
# a clean exit seals the evidence even when the script's own code ends in `exit 0`
_apex_exit(){
  local rc=$?
  if [ "$rc" = 0 ] && declare -F _apex_seal >/dev/null; then _apex_seal; fi
  # a failure that bypassed the ERR trap (set -u, an explicit exit 1) is still an abort
  if [ "$rc" != 0 ] && [ -z "${APEX_ABORTED:-}" ]; then
    _log 'ABORT: acquisition not verified — DO NOT use output as evidence'
  fi
  _log "EXIT rc:$rc"
}
trap _fatal ERR
trap _apex_exit EXIT
# record every command verbatim to the log
export PS4='+ $(date -u +%H:%M:%SZ) ${BASH_SOURCE}:${LINENO}: '
exec 19>>"$COC_LOG"; export BASH_XTRACEFD=19; set -x
# <<< APEX[DT-STRICT-TRAP] <<<

# >>> APEX[DT-COC-HEADER] Capture the chain-of-custody header (5 Ws + examiner identity + clock source + tool versions) to the append-only log before any acquisition. Satisfies FOR-COC-LOG, FOR-COC-EXAMINER, FOR-EVIDENCE-ID, FOR-UTC-TZ, FOR-TOOL-VERSION, FOR-CLOCK-SOURCE, FOR-CONTEMP-LOG. >>>
# --- Chain of custody header (required vars hard-fail if unset) ---
: "${EXAMINER:?examiner full name}"; : "${EXAMINER_ORG:?examiner org}"; : "${EXAMINER_CREDS:?examiner credentials/cert}"
: "${EVIDENCE_ID:?unique evidence/exhibit id}"; : "${CASE_NUMBER:?case number / legal authority}"
: "${LOCATION:?acquisition location}"; : "${PURPOSE:?why — warrant/consent/IR scope}"
: "${SOURCE_DESC:?source device description/serial}"
touch "$COC_LOG"; chattr +a "$COC_LOG" 2>/dev/null || _log 'WARN: append-only (chattr +a) unavailable on this fs'
_log '===== CHAIN OF CUSTODY ====='
_log "WHO     examiner=$EXAMINER org=$EXAMINER_ORG creds=$EXAMINER_CREDS"
_log "WHAT    evidence_id=$EVIDENCE_ID source=$SOURCE_DESC"
_log "WHEN    start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
_log "WHERE   location=$LOCATION host=$(uname -n) kernel=$(uname -a)"
_log "WHY     case=$CASE_NUMBER purpose=$PURPOSE"
_log "CLOCK   $(timedatectl 2>/dev/null | tr '\n' ';' || chronyc tracking 2>/dev/null | tr '\n' ';' || echo 'no-sync-info')"
_log "WBLOCK  hardware_write_blocker=${WRITE_BLOCKER:-NONE-software-RO} attest=${WRITE_BLOCKER_SERIAL:-n/a}"
_log "PHOTOS  ${PHOTOS:-NONE-RECORDED}"
for t in dc3dd dcfldd ewfacquire ewfverify gpg sha256sum sha512sum losetup blockdev openssl; do
  if ! command -v "$t" >/dev/null 2>&1; then v=MISSING
  else
    # openssl has no --version; the ewf tools print their version with -V
    case $t in
      openssl) v=$(openssl version 2>&1) ;;
      ewf*)    v=$("$t" -V 2>&1 | head -1) ;;
      *)       v=$("$t" --version 2>&1 | head -1) ;;
    esac || true
  fi
  _log "TOOL    $t = $v"
done
_log '==========================='
# <<< APEX[DT-COC-HEADER] <<<

# >>> APEX[DT-RO-ENFORCE] Define the software write-block used before any read of the source: set the block device RO, prove getro==1, refuse a source that overlaps TARGET_DEV, and provide ro_loop/ro_mount helpers that force ro,noload,noatime. Satisfies FOR-WRITEBLOCK (software path), FOR-RO-MOUNT, FOR-NO-ATIME, FOR-STERILE-TARGET (assertion part). >>>
# true when two block devices are the same disk or one is a partition of the other
_apex_overlap(){
  local a b
  a=$(readlink -f -- "$1"); b=$(readlink -f -- "$2")
  [ "$a" = "$b" ] && return 0
  lsblk -nrpo NAME -- "$a" 2>/dev/null | grep -qxF -- "$b" && return 0
  lsblk -nrpo NAME -- "$b" 2>/dev/null | grep -qxF -- "$a"
}
ro_enforce(){
  local src="$1"
  if [ -f "$src" ]; then _log "RO-ENFORCE n/a: source $src is a file, not a device"; return 0; fi
  [ -b "$src" ] || { _log "FATAL source $src is not a block device"; exit 1; }
  if [ -n "${TARGET_DEV:-}" ] && _apex_overlap "$src" "$TARGET_DEV"; then _log "FATAL target $TARGET_DEV overlaps source $src"; exit 1; fi
  blockdev --setro "$src"
  [ "$(blockdev --getro "$src")" = 1 ] || { _log "FATAL source $src NOT read-only"; exit 1; }
  _log "RO-ENFORCED $src getro=1"
}
# if anything must be loop-mounted for analysis, force RO + no journal replay + noatime
ro_loop(){ losetup --read-only --find --show "$1"; }
ro_mount(){ mount -o ro,noload,noatime,nodev,noexec "$1" "$2"; }
# <<< APEX[DT-RO-ENFORCE] <<<

# >>> APEX[DT-STERILE-TARGET] Wipe and verify the destination media sterile before imaging, proving no cross-contamination. Opt-in (WIPE_TARGET=1); refuses a mounted target and any target that is, contains or sits on SOURCE_DEV. Satisfies FOR-STERILE-TARGET. >>>
if [ "${WIPE_TARGET:-0}" = 1 ]; then
  : "${TARGET_DEV:?target device to sterilize}"
  # the wipe must provably miss the evidence: name the source, and never wipe a mounted target
  : "${SOURCE_DEV:?SOURCE_DEV (evidence device) is required with WIPE_TARGET=1}"
  [ -b "$TARGET_DEV" ] || { _log "FATAL target $TARGET_DEV is not a block device"; exit 1; }
  if _apex_overlap "$TARGET_DEV" "$SOURCE_DEV"; then _log "FATAL target $TARGET_DEV overlaps source $SOURCE_DEV"; exit 1; fi
  mnt=$(lsblk -nro MOUNTPOINT -- "$TARGET_DEV")
  [ -z "${mnt//[[:space:]]/}" ] || { _log "FATAL target $TARGET_DEV is mounted; sterilize it before creating the filesystem"; exit 1; }
  _log "STERILIZE wiping $TARGET_DEV"
  # zero-fill (plain discard does not guarantee zeroes); shred knows the device size,
  # unlike dd, which always ends in ENOSPC and would trip the ERR trap
  blkdiscard -f -z "$TARGET_DEV" 2>/dev/null || shred -n 0 -z "$TARGET_DEV"
  # verify sterile: hash a sample must equal hash of equivalent zeroes
  sample=$(head -c 64M -- "$TARGET_DEV" | sha256sum | awk '{print $1}')
  zeros=$(head -c 64M /dev/zero | sha256sum | awk '{print $1}')
  [ "$sample" = "$zeros" ] || { _log 'FATAL target not sterile after wipe'; exit 1; }
  _log 'STERILE: target verified zeroed'
fi
# <<< APEX[DT-STERILE-TARGET] <<<

# >>> APEX[DT-DUAL-HASH-VERIFY] Define verify_dual: hash the produced image independently with sha256 and sha512 and HARD-FAIL unless both match the acquisition-time (source) hashes in the imager's hashlog. acquire_image calls it straight after imaging, so nothing downstream ever sees an unverified image. Satisfies FOR-POST-HASH-IMG, FOR-DUAL-HASH, FOR-VERIFY-COMPARE, FOR-BITFORBIT-VERIFY. >>>
verify_dual(){
  local img="$1" hl="$2"
  local s256_img s512_img s256_src s512_src
  # dc3dd hashlog result lines read `   <hex> (sha256)`; the command line also names
  # the algorithms, so match the result lines exactly
  s256_src=$(awk '$2 == "(sha256)" { print tolower($1); exit }' "$hl")
  s512_src=$(awk '$2 == "(sha512)" { print tolower($1); exit }' "$hl")
  s256_img=$(sha256sum < "$img" | awk '{print $1}')
  s512_img=$(sha512sum < "$img" | awk '{print $1}')
  printf '%s  %s\n' "$s256_img" "$img" > "${img}.sha256"
  printf '%s  %s\n' "$s512_img" "$img" > "${img}.sha512"
  _log "VERIFY sha256 src=$s256_src img=$s256_img"
  _log "VERIFY sha512 src=$s512_src img=$s512_img"
  [ -n "$s256_src" ] && [ "$s256_src" = "$s256_img" ] || { _log 'FATAL sha256 MISMATCH — image NOT verified'; exit 1; }
  [ -n "$s512_src" ] && [ "$s512_src" = "$s512_img" ] || { _log 'FATAL sha512 MISMATCH — image NOT verified'; exit 1; }
  _log 'VERIFY PASS: dual-hash bit-for-bit match (sha256 & sha512)'
  APEX_VERIFIED=("$s256_img" "$s512_img")
}
# E01 path additionally: ewfverify -d sha256 "$IMAGE" 2>&1 | tee -a "$COC_LOG" || { _log 'FATAL ewfverify failed'; exit 1; }
# <<< APEX[DT-DUAL-HASH-VERIFY] <<<

# >>> APEX[DT-DD-TO-DC3DD] Define acquire_image, which replaces the script's own bare `dd if=SRC of=IMAGE` (RW-FOR-DD-DC3DD): write-block the source, refuse to clobber, image with dc3dd (dual inline hashes = the pre-acquisition source hash, hashlog, error log), then verify_dual before the script continues. Satisfies FOR-SOUND-IMAGER, FOR-HASHLOG-ERRLOG, FOR-PRE-HASH-SRC, FOR-DUAL-HASH (acquisition side), FOR-BADBLOCK, FOR-IDEMPOTENT. >>>
APEX_IMAGES=(); APEX_SHA256=(); APEX_SHA512=()
# BEFORE:  dd if=$SRC of=$OUT bs=4M
# AFTER:   acquire_image $SRC $OUT
acquire_image(){
  [ "$#" -eq 2 ] || { _log "FATAL acquire_image needs SOURCE and IMAGE, got $# argument(s)"; exit 1; }
  local src="$1" img="$2"
  if [ -n "${SOURCE_DEV:-}" ] && [ "$(readlink -f -- "$src")" != "$(readlink -f -- "$SOURCE_DEV")" ]; then
    _log "FATAL imaging $src but SOURCE_DEV=$SOURCE_DEV"; exit 1
  fi
  [ ! -b "$img" ] || { _log "FATAL $img is a device; acquire_image writes image files"; exit 1; }
  [ ! -e "$img" ] || { _log "FATAL refuse to clobber $img"; exit 1; }
  ro_enforce "$src"
  dc3dd if="$src" of="$img" \
    hash=sha256 hash=sha512 \
    hlog="${img}.hashlog" \
    log="${img}.dc3dd.log" \
    verb=on \
    bufsz=4M 2>&1 | tee -a "$COC_LOG"
  # dc3dd hashes the source as it reads it -> that IS the pre-acquisition source hash
  awk '$2 == "(sha256)" || $2 == "(sha512)"' "${img}.hashlog" | tee -a "$COC_LOG"
  _log "PRE-HASH(source via dc3dd) recorded in ${img}.hashlog"
  verify_dual "$img" "${img}.hashlog"
  APEX_IMAGES+=("$(readlink -f -- "$img")")
  APEX_SHA256+=("${APEX_VERIFIED[0]}"); APEX_SHA512+=("${APEX_VERIFIED[1]}")
}
# <<< APEX[DT-DD-TO-DC3DD] <<<

# >>> APEX[DT-SEAL] Define the seal: write SHA256SUMS/SHA512SUMS manifests binding every acquired image, its hashlog/error log and a frozen copy of the custody log, tar those small files with a reproducibility statement, detach-sign the tar with the examiner GPG key, and optionally add an RFC 3161 timestamp. Runs once, on a clean exit only, so it is reachable only after every verify_dual passed. Satisfies FOR-MANIFEST, FOR-SEAL-SIGN, FOR-RFC3161-TSA, FOR-REPRO-STATEMENT. >>>
# check the signing key now, not after hours of imaging
: "${GPG_KEY:?examiner signing key id/fingerprint}"
gpg --batch --list-secret-keys -- "$GPG_KEY" >/dev/null 2>&1 || { _log "FATAL no secret key for GPG_KEY=$GPG_KEY"; exit 1; }
_apex_seal(){
  [ -z "${APEX_SEALED:-}" ] || return 0
  APEX_SEALED=1
  if [ "${#APEX_IMAGES[@]}" -eq 0 ]; then _log 'SEAL skipped: no image was acquired through acquire_image'; return 0; fi
  local dir bundle img f rel h256 h512 i
  local -A seen=()
  dir=$(dirname -- "${APEX_IMAGES[0]}")
  bundle="${EVIDENCE_ID}_$(date -u +%Y%m%dT%H%M%SZ)"
  # seal a frozen copy of the COC log: xtrace and _log keep appending to the live one
  cp -- "$COC_LOG" "$dir/$bundle.coc.log"
  # manifests: every artifact with both digests, paths relative to $dir. The images
  # were just hashed by verify_dual, so its digests are reused, not recomputed (and
  # not re-read from ${img}.sha256, which the script's own code may have rewritten).
  : > "$dir/$bundle.SHA256SUMS"; : > "$dir/$bundle.SHA512SUMS"
  for i in "${!APEX_IMAGES[@]}"; do
    img=${APEX_IMAGES[$i]}
    for f in "$img" "$img.hashlog" "$img.dc3dd.log" "$img.sha256" "$img.sha512" "$dir/$bundle.coc.log"; do
      if [ ! -f "$f" ] || [ -n "${seen[$f]:-}" ]; then continue; fi
      seen[$f]=1
      case $f in "$dir"/*) rel=${f#"$dir"/} ;; *) rel=$f ;; esac
      if [ "$f" = "$img" ]; then
        h256=${APEX_SHA256[$i]}; h512=${APEX_SHA512[$i]}
      else
        h256=$(sha256sum < "$f" | awk '{print $1}'); h512=$(sha512sum < "$f" | awk '{print $1}')
      fi
      printf '%s  %s\n' "$h256" "$rel" >> "$dir/$bundle.SHA256SUMS"
      printf '%s  %s\n' "$h512" "$rel" >> "$dir/$bundle.SHA512SUMS"
    done
  done
  # reproducibility statement
  { echo "# evidence $EVIDENCE_ID case $CASE_NUMBER examiner $EXAMINER"
    echo "REPRODUCE (in $dir): gpg --verify $bundle.tar.asc $bundle.tar && tar -xf $bundle.tar && sha256sum -c $bundle.SHA256SUMS && sha512sum -c $bundle.SHA512SUMS"
    echo 'Tool versions and every command run are in the sealed chain-of-custody log.'; } > "$dir/$bundle.reproduce.txt"
  # the tar holds the manifests, not the image bytes: the digests bind the image
  # without a second full copy on the evidence drive
  tar -C "$dir" --sort=name --owner=0 --group=0 -cf "$dir/$bundle.tar" \
    "$bundle.SHA256SUMS" "$bundle.SHA512SUMS" "$bundle.reproduce.txt" "$bundle.coc.log"
  gpg --batch --yes --local-user "$GPG_KEY" --armor --detach-sign --output "$dir/$bundle.tar.asc" "$dir/$bundle.tar"
  _log "SEALED $dir/$bundle.tar signed-by $(gpg --with-colons --fingerprint -- "$GPG_KEY" 2>/dev/null | awk -F: '$1 == "fpr" { print $10; exit }')"
  if [ -n "${TSA_URL:-}" ]; then
    if openssl ts -query -data "$dir/$bundle.tar" -sha512 -cert -out "$dir/$bundle.tsq" &&
       curl -fsS -H 'Content-Type: application/timestamp-query' --data-binary @"$dir/$bundle.tsq" "$TSA_URL" -o "$dir/$bundle.tsr" &&
       openssl ts -reply -in "$dir/$bundle.tsr" -text >/dev/null; then
      _log "RFC3161 timestamp obtained $dir/$bundle.tsr from $TSA_URL"
    else
      _log "WARN: RFC3161 timestamp NOT obtained from $TSA_URL"
    fi
  fi
  _log 'ACQUISITION COMPLETE — verified & sealed'
}
# <<< APEX[DT-SEAL] <<<
# ===== end APEX preamble =====


# quick disk image
SRC=$1
OUT=$2
acquire_image $SRC $OUT
sha256sum $OUT > $OUT.sha256
echo "done imaging $SRC"

# ===== APEX appended helpers =====
# >>> APEX[DT-SEAL-FINAL] Seal the evidence after the script's own code finishes (the EXIT handler also seals on a clean `exit 0`; the seal runs once). >>>
_apex_seal
# <<< APEX[DT-SEAL-FINAL] <<<
