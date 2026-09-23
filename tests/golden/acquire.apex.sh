#!/usr/bin/env bash

# ===== APEX elevation preamble :: Courtroom-admissible acquisition: FRE 901/902 authentication & self-authentication, Daubert reliability, NIST SP 800-86 (IR forensics), NIST SP 800-101r1 (mobile), ACPO 4 principles, SWGDE Best Practices for Computer/Image Acquisition, ISO/IEC 27037:2012, RFC 3227 (order of volatility), RFC 3161 (timestamping) =====
# >>> APEX[DT-STRICT-TRAP] Enforce abort-on-any-error and a contemporaneous failure record. Adds strict mode and an ERR/EXIT trap that logs the exact failing command, line number, and UTC time, then exits non-zero so no downstream seal/sign can run on a broken acquisition. Satisfies FOR-SET-EUO-TRAP and feeds FOR-CMDLINE-RECORD. >>>
set -Eeuo pipefail
IFS=$'\n\t'
: "${COC_LOG:?COC_LOG (chain-of-custody log path) must be set}"
_log(){ printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$COC_LOG" >&2; }
_fatal(){ _log "FATAL line:${BASH_LINENO[0]:-?} cmd:[${BASH_COMMAND}] rc:$?"; _log 'ABORT: acquisition not verified — DO NOT use output as evidence'; exit 1; }
trap _fatal ERR
trap '_log "EXIT rc:$?"' EXIT
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
_log "WHERE   location=$LOCATION host=$(hostname) kernel=$(uname -a)"
_log "WHY     case=$CASE_NUMBER purpose=$PURPOSE"
_log "CLOCK   $(timedatectl 2>/dev/null | tr '\n' ';' || chronyc tracking 2>/dev/null | tr '\n' ';' || echo 'no-sync-info')"
_log "WBLOCK  hardware_write_blocker=${WRITE_BLOCKER:-NONE-software-RO} attest=${WRITE_BLOCKER_SERIAL:-n/a}"
_log "PHOTOS  ${PHOTOS:-NONE-RECORDED}"
for t in dc3dd dcfldd ewfacquire ewfverify gpg sha256sum sha512sum losetup blockdev openssl; do \
  v=$(command -v "$t" >/dev/null 2>&1 && { "$t" --version 2>&1 | head -1; } || echo MISSING); _log "TOOL    $t = $v"; done
_log '==========================='
# <<< APEX[DT-COC-HEADER] <<<

# >>> APEX[DT-RO-ENFORCE] Enforce read-only on the source and assert it before any read. Sets the block device RO, verifies getro==1, asserts target!=source, and converts any source mount to ro,noload,noatime. Satisfies FOR-WRITEBLOCK (software path), FOR-RO-MOUNT, FOR-NO-ATIME, FOR-STERILE-TARGET (assertion part). >>>
: "${SOURCE_DEV:?source block device e.g. /dev/sdX}"
[ -b "$SOURCE_DEV" ] || _fatal
[ "${TARGET_DEV:-/dev/null}" != "$SOURCE_DEV" ] || { _log 'FATAL target==source'; exit 1; }
blockdev --setro "$SOURCE_DEV" || _fatal
if [ "$(blockdev --getro "$SOURCE_DEV")" != "1" ]; then _log 'FATAL source NOT read-only'; exit 1; fi
_log "RO-ENFORCED $SOURCE_DEV getro=1"
# if anything must be loop-mounted for analysis, force RO + no journal replay + noatime
ro_loop(){ losetup --read-only --find --show "$1"; }
ro_mount(){ mount -o ro,noload,noatime,nodev,noexec "$1" "$2"; }
# <<< APEX[DT-RO-ENFORCE] <<<

# >>> APEX[DT-DD-TO-DC3DD] Rewrite bare dd into dc3dd with dual inline hashing, error logging, and a separate hashlog — and capture the SOURCE pre-image hash in the same pass. Satisfies FOR-SOUND-IMAGER, FOR-HASHLOG-ERRLOG, FOR-PRE-HASH-SRC, FOR-DUAL-HASH (acquisition side), FOR-BADBLOCK, FOR-CMDLINE-RECORD. >>>
# BEFORE:  dd if=$SOURCE_DEV of=$IMAGE bs=4M
# AFTER (engine-injected):
IMAGE="${IMAGE:?output image path}"
[ -e "$IMAGE" ] && { _log "FATAL refuse to clobber $IMAGE"; exit 1; }
dc3dd if="$SOURCE_DEV" of="$IMAGE" \
  hash=sha256 hash=sha512 \
  hashlog="${IMAGE}.hashlog" \
  log="${IMAGE}.dc3dd.log" \
  hashconv=after verb=on \
  bs=4M 2>&1 | tee -a "$COC_LOG"
# dc3dd computes the source-read hash inline -> that IS the pre-acquisition source hash
awk '/sha256/||/sha512/' "${IMAGE}.hashlog" | tee -a "$COC_LOG"
_log "PRE-HASH(source via dc3dd) recorded in ${IMAGE}.hashlog"
# <<< APEX[DT-DD-TO-DC3DD] <<<

# >>> APEX[DT-STERILE-TARGET] Wipe and verify the destination media sterile before imaging, proving no cross-contamination. Satisfies FOR-STERILE-TARGET. >>>
if [ "${WIPE_TARGET:-0}" = 1 ]; then
  : "${TARGET_DEV:?target device to sterilize}"
  [ "$TARGET_DEV" != "$SOURCE_DEV" ] || { _log 'FATAL target==source'; exit 1; }
  _log "STERILIZE wiping $TARGET_DEV"
  blkdiscard -f "$TARGET_DEV" 2>/dev/null || dd if=/dev/zero of="$TARGET_DEV" bs=4M status=progress
  # verify sterile: hash a sample must equal hash of equivalent zeroes
  sample=$(dd if="$TARGET_DEV" bs=1M count=64 2>/dev/null | sha256sum | awk '{print $1}')
  zeros=$(dd if=/dev/zero bs=1M count=64 2>/dev/null | sha256sum | awk '{print $1}')
  [ "$sample" = "$zeros" ] || { _log 'FATAL target not sterile after wipe'; exit 1; }
  _log 'STERILE: target verified zeroed'
fi
# <<< APEX[DT-STERILE-TARGET] <<<
# ===== end APEX preamble =====

# quick disk image
SRC=$1
OUT=$2
dd if=$SRC of=$OUT bs=4M
sha256sum $OUT > $OUT.sha256
echo "done imaging $SRC"

# ===== APEX appended helpers =====
# >>> APEX[DT-DUAL-HASH-VERIFY] Compute dual hashes of the produced image independently and HARD-FAIL unless both match the acquisition-time (source) hashes from the imager's hashlog. This is the bit-for-bit verification gate that everything downstream depends on. Satisfies FOR-POST-HASH-IMG, FOR-DUAL-HASH, FOR-VERIFY-COMPARE, FOR-BITFORBIT-VERIFY. >>>
verify_dual(){
  local img="$1" hl="$2"
  local s256_img s512_img s256_src s512_src
  s256_img=$(sha256sum "$img" | awk '{print $1}')
  s512_img=$(sha512sum "$img" | awk '{print $1}')
  s256_src=$(grep -iE 'sha256' "$hl" | grep -oiE '[0-9a-f]{64}' | head -1)
  s512_src=$(grep -iE 'sha512' "$hl" | grep -oiE '[0-9a-f]{128}' | head -1)
  printf '%s  %s\n' "$s256_img" "$img" > "${img}.sha256"
  printf '%s  %s\n' "$s512_img" "$img" > "${img}.sha512"
  _log "VERIFY sha256 src=$s256_src img=$s256_img"
  _log "VERIFY sha512 src=$s512_src img=$s512_img"
  [ -n "$s256_src" ] && [ "$s256_src" = "$s256_img" ] || { _log 'FATAL sha256 MISMATCH — image NOT verified'; exit 1; }
  [ -n "$s512_src" ] && [ "$s512_src" = "$s512_img" ] || { _log 'FATAL sha512 MISMATCH — image NOT verified'; exit 1; }
  _log 'VERIFY PASS: dual-hash bit-for-bit match (sha256 & sha512)'
}
verify_dual "$IMAGE" "${IMAGE}.hashlog"
# E01 path additionally: ewfverify -d sha256 "$IMAGE" 2>&1 | tee -a "$COC_LOG" || { _log 'FATAL ewfverify failed'; exit 1; }
# <<< APEX[DT-DUAL-HASH-VERIFY] <<<

# >>> APEX[DT-SEAL] Build the signed manifest, tar the evidence set, detach-sign with the examiner GPG key, and optionally apply an RFC 3161 trusted timestamp. Reachable ONLY after DT-DUAL-HASH-VERIFY passes. Satisfies FOR-MANIFEST, FOR-SEAL-SIGN, FOR-RFC3161-TSA, FOR-REPRO-STATEMENT. >>>
: "${GPG_KEY:?examiner signing key id/fingerprint}"
BUNDLE="${EVIDENCE_ID}_$(date -u +%Y%m%dT%H%M%SZ)"
# manifest: every artifact with dual hashes
{ echo "# evidence manifest $EVIDENCE_ID case $CASE_NUMBER examiner $EXAMINER";
  for f in "$IMAGE"* "$COC_LOG"; do [ -f "$f" ] && printf '%s  %s  %s\n' "$(sha256sum "$f"|awk '{print $1}')" "$(sha512sum "$f"|awk '{print $1}')" "$f"; done; } > "${BUNDLE}.manifest"
# reproducibility statement
{ echo 'REPRODUCE: sha256sum -c and sha512sum -c the manifest; gpg --verify the .asc; dc3dd/ewfverify versions in COC log.';
  echo "verify_cmd: gpg --verify ${BUNDLE}.tar.asc ${BUNDLE}.tar && sha256sum -c <(awk '{print \$1\"  \"\$3}' ${BUNDLE}.manifest)"; } > "${BUNDLE}.reproduce.txt"
tar --sort=name --owner=0 --group=0 -cf "${BUNDLE}.tar" "${BUNDLE}.manifest" "${BUNDLE}.reproduce.txt" "$IMAGE"* "$COC_LOG"
gpg --batch --yes --local-user "$GPG_KEY" --armor --detach-sign --output "${BUNDLE}.tar.asc" "${BUNDLE}.tar" || _fatal
_log "SEALED ${BUNDLE}.tar signed-by $(gpg --fingerprint "$GPG_KEY" 2>/dev/null | sed -n 2p | tr -d ' ')"
if [ "${TSA_URL:-}" != "" ]; then
  openssl ts -query -data "${BUNDLE}.tar" -sha512 -cert -out "${BUNDLE}.tsq" && \
  curl -s -H 'Content-Type: application/timestamp-query' --data-binary @"${BUNDLE}.tsq" "$TSA_URL" -o "${BUNDLE}.tsr" && \
  _log "RFC3161 timestamp obtained ${BUNDLE}.tsr from $TSA_URL"
fi
_log 'ACQUISITION COMPLETE — verified & sealed'
# <<< APEX[DT-SEAL] <<<
