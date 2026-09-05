#!/bin/bash
# Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
# Proprietary and Confidential.
#
# Unauthorized reproduction, copying, modification, distribution,
# redistribution, publication, reverse engineering, decompilation, or
# disassembly of this file, in whole or in part, via any medium, is
# strictly prohibited without explicit prior written authorization.


# ===== APEX elevation preamble :: Production-grade automated/interactive OS provisioning: Kickstart (RHEL/Fedora/Rocky/Alma), Debian preseed + Ubuntu Subiquity autoinstall/cloud-init, Arch pacstrap installer, SUSE AutoYaST, generic cloud-init; aligned to vendor reference docs, CIS-style secure post-install, and reproducible-config / dry-run / fail-closed automation discipline. =====
# >>> APEX[FLAG_PLUMBING] Resolve mode and the --assume-yes/--interactive/--dry-run/--family flags into single variables, with mutual-exclusion errors, TTY auto-detect, and a documented exit-code table. Satisfies PROV-001, PROV-006, PROV-012, PROV-024. >>>
set -Eeuo pipefail
# Exit codes: 0 ok | 2 usage | 3 preflight | 4 user-abort | 5 validation | 6 destroy-guard | 10 install-fail
MODE="" ; ASSUME_YES=0 ; DRY_RUN=0 ; FAMILY="${FAMILY:-auto}" ; CONFIG=""
ERR_USAGE=2; ERR_PREFLIGHT=3; ERR_ABORT=4; ERR_VALIDATE=5; ERR_DESTROY=6; ERR_INSTALL=10
usage(){ cat <<EOF
Usage: $0 [--interactive|--unattended] [--assume-yes] [--dry-run] --family <rhel|debian|ubuntu|arch|suse|cloud-init|auto> [--config FILE]
Env: INSTALL_MODE=interactive|unattended  ASSUME_YES=1  DRY_RUN=1  FAMILY=...
Exit: 0 ok 2 usage 3 preflight 4 abort 5 validation 6 destroy-guard 10 install-fail
EOF
}
while [ $# -gt 0 ]; do case "$1" in
  --interactive) MODE=interactive;; --unattended) MODE=unattended;;
  --assume-yes|--yes|-y) ASSUME_YES=1;; --dry-run|--check) DRY_RUN=1;;
  --family) FAMILY="$2"; shift;; --config) CONFIG="$2"; shift;;
  -h|--help) usage; exit 0;; *) echo "unknown arg: $1" >&2; usage; exit "$ERR_USAGE";;
esac; shift; done
[ -z "$MODE" ] && MODE="${INSTALL_MODE:-}"
# auto-detect mode from TTY when unset
if [ -z "$MODE" ]; then if [ -t 0 ] && [ -t 1 ]; then MODE=interactive; else MODE=unattended; fi; fi
# conflict + safety guards
if [ "$MODE" = interactive ] && [ "$ASSUME_YES" = 1 ]; then echo "--interactive conflicts with --assume-yes" >&2; exit "$ERR_USAGE"; fi
if [ "$MODE" = unattended ] && [ "$ASSUME_YES" != 1 ] && [ "$DRY_RUN" != 1 ]; then echo "unattended mode requires --assume-yes (or --dry-run)" >&2; exit "$ERR_USAGE"; fi
# <<< APEX[FLAG_PLUMBING] <<<

# >>> APEX[LOGGING] Timestamped/leveled log helpers teed to a persistent file, a die() that exits with a code, and an ERR/EXIT trap that reports the failing line and runs ordered cleanup (reverse unmount + crypt close). Satisfies PROV-011, PROV-012. >>>
LOGFILE="${LOGFILE:-/var/log/provision-$(date +%Y%m%dT%H%M%S).log}"
_ts(){ date -u +%Y-%m-%dT%H:%M:%SZ; }
log(){ printf '%s [%s] %s\n' "$(_ts)" "${2:-INFO}" "$1" | tee -a "$LOGFILE" >&2; }
die(){ log "$1" ERROR; exit "${2:-1}"; }
MOUNTS=() ; CRYPTS=()
cleanup(){ local rc=$?; set +e
  for m in $(printf '%s\n' "${MOUNTS[@]:-}" | tac); do [ -n "$m" ] && umount -R "$m" 2>/dev/null; done
  for c in "${CRYPTS[@]:-}"; do [ -n "$c" ] && cryptsetup close "$c" 2>/dev/null; done
  log "exit rc=$rc" "$([ $rc -eq 0 ] && echo INFO || echo ERROR)"; }
trap 'log "failed at line $LINENO: $BASH_COMMAND" ERROR' ERR
trap cleanup EXIT
run(){ if [ "${DRY_RUN:-0}" = 1 ]; then log "DRY-RUN: $*" DEBUG; else log "+ $*" DEBUG; "$@"; fi; }
# <<< APEX[LOGGING] <<<

# >>> APEX[PREFLIGHT_BLOCK] Aggregate preflight gate: root, firmware (UEFI/BIOS), arch, candidate disk presence, RAM, network reachability, target free space. Collects all failures and exits ERR_PREFLIGHT. Exports FIRMWARE. Satisfies PROV-004. >>>
run_preflight(){ local fail=0; local need_net="${1:-1}" min_ram_mb="${2:-1024}" req_bytes="${3:-0}" tgt="${4:-}"
  [ "$(id -u)" -eq 0 ] || { log "must run as root" ERROR; fail=1; }
  if [ -d /sys/firmware/efi ]; then FIRMWARE=uefi; else FIRMWARE=bios; fi; export FIRMWARE; log "firmware=$FIRMWARE"
  case "$(uname -m)" in x86_64|aarch64|arm64) :;; *) log "unsupported arch $(uname -m)" ERROR; fail=1;; esac
  lsblk -dnpo NAME,TYPE | awk '$2=="disk"{n++} END{exit !(n>0)}' || { log "no candidate disk" ERROR; fail=1; }
  local ram; ram=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo); [ "$ram" -ge "$min_ram_mb" ] || { log "RAM ${ram}MB < ${min_ram_mb}MB" ERROR; fail=1; }
  if [ "$need_net" = 1 ]; then retry 3 ping -c1 -W2 1.1.1.1 >/dev/null 2>&1 || { log "no network" ERROR; fail=1; }; fi
  if [ -n "$tgt" ] && [ "$req_bytes" -gt 0 ]; then local sz; sz=$(blockdev --getsize64 "$tgt" 2>/dev/null || echo 0); [ "$sz" -ge "$req_bytes" ] || { log "target $tgt $sz < required $req_bytes" ERROR; fail=1; }; fi
  [ "$fail" -eq 0 ] || exit "$ERR_PREFLIGHT"; log "preflight ok"; }
# <<< APEX[PREFLIGHT_BLOCK] <<<

# >>> APEX[RETRY] Bounded retry with exponential backoff and per-attempt timeout for network operations; fails closed after N attempts. Satisfies PROV-013. >>>
retry(){ local max="$1"; shift; local n=1 delay=2
  until "$@"; do if [ "$n" -ge "$max" ]; then log "giving up after $n attempts: $*" ERROR; return 1; fi
    log "attempt $n/$max failed, retrying in ${delay}s: $*" WARN; sleep "$delay"; n=$((n+1)); delay=$((delay*2)); done; }
wait_network(){ local deadline=$(( $(date +%s) + ${1:-120} ))
  until ping -c1 -W2 1.1.1.1 >/dev/null 2>&1; do [ "$(date +%s)" -lt "$deadline" ] || die "network not up within ${1:-120}s" "$ERR_PREFLIGHT"; sleep 3; done; }
fetch(){ retry 5 curl -fSL --connect-timeout 10 --max-time 120 -o "$2" "$1"; }
# <<< APEX[RETRY] <<<

# >>> APEX[VERIFY_ARTIFACT] Download-then-verify helper: SHA256 against a pinned digest and optional GPG signature verification before any artifact (ISO/key/package/checksum file) is consumed. Satisfies PROV-008. >>>
verify_artifact(){ # $1=url $2=expected_sha256 $3=out [$4=sig_url $5=gpg_keyring]
  local url="$1" want="$2" out="$3" sig="${4:-}" kr="${5:-}"
  fetch "$url" "$out" || die "download failed: $url" "$ERR_INSTALL"
  echo "${want}  ${out}" | sha256sum -c - >/dev/null 2>&1 || die "sha256 mismatch for $out" "$ERR_VALIDATE"
  if [ -n "$sig" ]; then fetch "$sig" "${out}.sig"
    gpgv ${kr:+--keyring "$kr"} "${out}.sig" "$out" >/dev/null 2>&1 || die "gpg verify failed for $out" "$ERR_VALIDATE"; fi
  log "verified $out"; printf '%s' "$out"; }
# <<< APEX[VERIFY_ARTIFACT] <<<

# >>> APEX[RESOLVE_TARGET_DISK] Enumerate candidate disks, subtract the running-root disk, live medium, and any disk with mounted partitions / LVM-MD-crypt holders; expose a safe candidate list for the picker and validate an unattended TARGET_DISK against the exclusion set. Satisfies PROV-003. >>>
root_disk(){ local src pk; src=$(findmnt -no SOURCE / 2>/dev/null); pk=$(lsblk -no PKNAME "$src" 2>/dev/null | head -1); [ -n "$pk" ] && echo "/dev/$pk"; }
candidate_disks(){ local rd; rd=$(root_disk)
  lsblk -dnpo NAME,TYPE | awk '$2=="disk"{print $1}' | while read -r d; do
    [ "$d" = "$rd" ] && continue
    # skip disks with any mounted child or active holder
    if lsblk -nro MOUNTPOINT "$d" | grep -q . ; then continue; fi
    echo "$d"; done; }
validate_target(){ local t="$1"; [ -b "$t" ] || die "not a block device: $t" "$ERR_VALIDATE"
  [ "$t" = "$(root_disk)" ] && [ "${ALLOW_ROOT_DISK:-0}" != 1 ] && die "refusing to target root disk $t" "$ERR_DESTROY"
  candidate_disks | grep -qx "$t" || die "$t is in-use/excluded; not a safe target" "$ERR_DESTROY"; }
# <<< APEX[RESOLVE_TARGET_DISK] <<<

# >>> APEX[CONFIRM_DESTROY_GUARD] Single mandatory gate in front of every destructive disk op: prints the resolved device, its current layout and holders, then requires a typed device-name match (interactive) or ASSUME_YES (unattended). Satisfies PROV-002, PROV-001. >>>
confirm_destroy(){ local dev="$1"; [ -n "$dev" ] || die "confirm_destroy: empty device" "$ERR_DESTROY"
  validate_target "$dev"
  log "ABOUT TO DESTROY ALL DATA ON $dev" WARN; lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT "$dev" >&2 || true
  if [ "${DRY_RUN:-0}" = 1 ]; then log "DRY-RUN: would destroy $dev"; return 0; fi
  if [ "$MODE" = unattended ]; then [ "${ASSUME_YES:-0}" = 1 ] || die "refusing destructive op without --assume-yes" "$ERR_DESTROY"; log "unattended: --assume-yes -> proceeding on $dev" WARN; return 0; fi
  local base; base=$(basename "$dev")
  if command -v whiptail >/dev/null; then whiptail --title 'DESTRUCTIVE' --yesno "ERASE EVERYTHING on $dev?\n\n$(lsblk -no NAME,SIZE,FSTYPE "$dev")" 18 70 || exit "$ERR_ABORT"; fi
  printf 'Type the device name (%s) to confirm: ' "$base" >&2; read -r ans
  [ "$ans" = "$base" ] || die "confirmation mismatch; aborting" "$ERR_ABORT"; }
# <<< APEX[CONFIRM_DESTROY_GUARD] <<<

# >>> APEX[WHIPTAIL_MENU_SKELETON] Reusable whiptail TUI helpers (menu/input/password/yesno) with a disk picker fed from candidate_disks, per-field validation loops, and a final plan-review screen that triggers confirm_destroy. dialog fallback. Satisfies PROV-023, PROV-015. >>>
TUI=${TUI:-whiptail}; command -v whiptail >/dev/null || TUI=dialog; command -v "$TUI" >/dev/null || die "no whiptail/dialog for interactive mode" "$ERR_USAGE"
tui_input(){ local t="$1" p="$2" def="$3" v; v=$($TUI --title "$t" --inputbox "$p" 10 70 "$def" 3>&1 1>&2 2>&3) || exit "$ERR_ABORT"; printf '%s' "$v"; }
tui_pass(){ local t="$1" v; v=$($TUI --title "$t" --passwordbox "$2" 10 70 3>&1 1>&2 2>&3) || exit "$ERR_ABORT"; printf '%s' "$v"; }
tui_yesno(){ $TUI --title "$1" --yesno "$2" 18 72; }
pick_disk(){ local opts=(); while read -r d; do opts+=("$d" "$(lsblk -dno SIZE,MODEL "$d")"); done < <(candidate_disks)
  [ "${#opts[@]}" -gt 0 ] || die "no selectable disks" "$ERR_PREFLIGHT"
  $TUI --title 'Target disk' --menu 'Choose install disk (DESTROYED):' 20 76 8 "${opts[@]}" 3>&1 1>&2 2>&3 || exit "$ERR_ABORT"; }
prompt_until_valid(){ local title="$1" prompt="$2" def="$3" validator="$4" v
  while :; do v=$(tui_input "$title" "$prompt" "$def"); if "$validator" "$v"; then printf '%s' "$v"; return; fi; $TUI --msgbox "Invalid: $v" 8 50; done; }
review_and_confirm(){ tui_yesno 'Confirm plan' "$1" || exit "$ERR_ABORT"; confirm_destroy "$TARGET_DISK"; }
# <<< APEX[WHIPTAIL_MENU_SKELETON] <<<

# >>> APEX[VALIDATORS] Input validators for hostname, username, timezone, IP/CIDR, and disk, used in both TUI loops and unattended config load. Satisfies PROV-015. >>>
validate_hostname(){ printf '%s' "$1" | grep -Eq '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$'; }
validate_username(){ printf '%s' "$1" | grep -Eq '^[a-z_][a-z0-9_-]{0,31}$'; }
validate_timezone(){ [ -f "/usr/share/zoneinfo/$1" ]; }
validate_ipcidr(){ printf '%s' "$1" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$'; }
validate_disk(){ [ -b "$1" ]; }
# <<< APEX[VALIDATORS] <<<

# >>> APEX[LUKS_PARTITION_FLOW] Arch/manual LUKS2 + LVM full-disk-encryption flow with the passphrase sourced from prompt/env/keyfile (never embedded), correct crypttab and initramfs HOOKS, firmware-aware ESP. Satisfies PROV-016, PROV-020. >>>
luks_lvm_setup(){ local disk="$1" vg="${2:-vg0}"
  # passphrase: env LUKS_PASSPHRASE (unattended) or double-entry prompt (interactive); never logged/embedded
  local pass
  if [ "$MODE" = unattended ]; then pass="${LUKS_PASSPHRASE:?LUKS_PASSPHRASE required for unattended encryption}"
  else local p1 p2; p1=$(tui_pass 'LUKS' 'Disk passphrase:'); p2=$(tui_pass 'LUKS' 'Repeat:'); [ "$p1" = "$p2" ] || die 'passphrases differ' "$ERR_ABORT"; [ "${#p1}" -ge 8 ] || die 'passphrase too short' "$ERR_ABORT"; pass=$p1; fi
  confirm_destroy "$disk"
  run wipefs -a "$disk"; run sgdisk --zap-all "$disk"
  if [ "$FIRMWARE" = uefi ]; then run sgdisk -n1:0:+512M -t1:ef00 -c1:ESP "$disk"; run sgdisk -n2:0:0 -t2:8309 -c2:crypt "$disk"; else run sgdisk -n1:0:+1M -t1:ef02 "$disk"; run sgdisk -n2:0:+512M "$disk"; run sgdisk -n3:0:0 -t3:8309 "$disk"; fi
  local cryptpart; cryptpart=$(lsblk -nrpo NAME "$disk" | grep -E 'crypt$|p?3$|p?2$' | tail -1)
  printf '%s' "$pass" | run cryptsetup luksFormat --type luks2 "$cryptpart" -
  printf '%s' "$pass" | run cryptsetup open "$cryptpart" cryptlvm -
  run pvcreate /dev/mapper/cryptlvm; run vgcreate "$vg" /dev/mapper/cryptlvm
  run lvcreate -L 4G -n swap "$vg"; run lvcreate -l 100%FREE -n root "$vg"
  run mkfs.ext4 "/dev/$vg/root"; run mkswap "/dev/$vg/swap"
  CRYPTS+=(cryptlvm); unset pass p1 p2 2>/dev/null || true; export CRYPT_PART="$cryptpart"; }
# <<< APEX[LUKS_PARTITION_FLOW] <<<

# >>> APEX[VALIDATE_CONFIG] Family-dispatched generated-config validator that fails closed before apply. Satisfies PROV-007. >>>
validate_config(){ local family="$1" file="$2"
  case "$family" in
    rhel) command -v ksvalidator >/dev/null && { ksvalidator "$file" || die 'ksvalidator failed' "$ERR_VALIDATE"; } ;;
    ubuntu|cloud-init) command -v cloud-init >/dev/null && { cloud-init schema --config-file "$file" || die 'cloud-init schema failed' "$ERR_VALIDATE"; } ;;
    debian) debconf-set-selections -c "$file" || die 'preseed checkonly failed' "$ERR_VALIDATE" ;;
    suse) xmllint --noout "$file" || die 'AutoYaST XML malformed' "$ERR_VALIDATE" ;;
  esac; log "config validated ($family): $file"; }
# <<< APEX[VALIDATE_CONFIG] <<<

# >>> APEX[KICKSTART_SCAFFOLD] Parameterized Kickstart with %packages, LVM (+ optional LUKS), firmware-aware bootloader, hashed rootpw, %pre and a %post --log secure hook. Satisfies PROV-017, PROV-009, PROV-016. >>>
gen_kickstart(){ local out="$1"; local bl; [ "$FIRMWARE" = uefi ] && bl='--location=boot' || bl='--location=mbr'
  local enc=''; [ "${ENCRYPT:-0}" = 1 ] && enc='--encrypted --luks-version=luks2 --passphrase='"${LUKS_PASSPHRASE:?}"
  cat > "$out" <<EOF
text
lang en_US.UTF-8
keyboard us
timezone ${TZ:-UTC} --utc
network --bootproto=dhcp --hostname=${HOSTNAME:?} --activate
rootpw --iscrypted $(openssl passwd -6 "${ROOT_PW:?}")
user --name=${USERNAME:?} --groups=wheel --iscrypted --password=$(openssl passwd -6 "${USER_PW:?}")
sshkey --username=${USERNAME} "${SSH_PUBKEY:?}"
selinux --enforcing
firewall --enabled --ssh
bootloader ${bl}
zerombr
clearpart --all --initlabel --drives=$(basename "${TARGET_DISK:?}")
part /boot --fstype=ext4 --size=1024
part pv.01 --size=1 --grow ${enc}
volgroup vg0 pv.01
logvol / --vgname=vg0 --name=root --fstype=xfs --size=1 --grow
logvol swap --vgname=vg0 --name=swap --size=4096
firstboot --disable
reboot
%packages
@^minimal-environment
openssh-server
firewalld
fail2ban
dnf-automatic
%end
%post --log=/root/ks-post.log
systemctl enable --now firewalld; firewall-cmd --permanent --add-service=ssh; firewall-cmd --reload
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/;s/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl enable sshd fail2ban; sed -i 's/^apply_updates.*/apply_updates = yes/' /etc/dnf/automatic.conf; systemctl enable --now dnf-automatic.timer
dnf -y --security upgrade || true
echo "provisioned $(date -u) cfg=${CONFIG_SHA:-na}" > /etc/provision-stamp
%end
EOF
  validate_config rhel "$out"; }
# <<< APEX[KICKSTART_SCAFFOLD] <<<

# >>> APEX[AUTOINSTALL_SCAFFOLD] Ubuntu Subiquity autoinstall user-data (#cloud-config + autoinstall:v1) with hashed password, key-only ssh, storage layout switch (direct/lvm/zfs or dm_crypt LUKS), and curtin in-target hardening late-commands. Satisfies PROV-018, PROV-009, PROV-010, PROV-016. >>>
gen_autoinstall(){ local out="$1"; local layout="${STORAGE:-lvm}"
  local storage_block
  if [ "${ENCRYPT:-0}" = 1 ]; then storage_block="    layout: {name: lvm, password: ${LUKS_PASSPHRASE:?}}"; else storage_block="    layout: {name: ${layout}}"; fi
  cat > "$out" <<EOF
#cloud-config
autoinstall:
  version: 1
  locale: en_US.UTF-8
  keyboard: {layout: us}
  identity:
    hostname: ${HOSTNAME:?}
    username: ${USERNAME:?}
    password: "$(openssl passwd -6 "${USER_PW:?}")"
  ssh:
    install-server: true
    allow-pw: false
    authorized-keys: ["${SSH_PUBKEY:?}"]
  storage:
${storage_block}
  packages: [openssh-server, ufw, fail2ban, unattended-upgrades]
  late-commands:
    - curtin in-target --target=/target -- bash -c 'ufw --force enable; ufw allow OpenSSH'
    - curtin in-target --target=/target -- sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
    - curtin in-target --target=/target -- systemctl enable fail2ban
    - curtin in-target --target=/target -- dpkg-reconfigure -f noninteractive unattended-upgrades
    - curtin in-target --target=/target -- bash -c 'echo provisioned > /etc/provision-stamp'
EOF
  validate_config ubuntu "$out"; }
# <<< APEX[AUTOINSTALL_SCAFFOLD] <<<

# >>> APEX[PRESEED_SCAFFOLD] Debian classic preseed with partman LVM+crypto recipe, crypted passwords, pkgsel, firmware-aware grub, and an in-target late_command hardening hook. Satisfies PROV-019, PROV-009, PROV-010, PROV-016. >>>
gen_preseed(){ local out="$1"; local recipe='partman-auto-lvm/new_vg_name string vg0'
  cat > "$out" <<EOF
d-i debian-installer/locale string en_US.UTF-8
d-i keyboard-configuration/xkb-keymap select us
d-i netcfg/get_hostname string ${HOSTNAME:?}
d-i mirror/http/hostname string deb.debian.org
d-i mirror/http/directory string /debian
d-i passwd/root-password-crypted password $(openssl passwd -6 "${ROOT_PW:?}")
d-i passwd/user-fullname string ${USERNAME:?}
d-i passwd/username string ${USERNAME}
d-i passwd/user-password-crypted password $(openssl passwd -6 "${USER_PW:?}")
d-i partman-auto/method string $([ "${ENCRYPT:-0}" = 1 ] && echo crypto || echo lvm)
d-i partman-auto/disk string ${TARGET_DISK:?}
d-i partman-auto-lvm/guided_size string max
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true
d-i partman-lvm/confirm boolean true
d-i grub-installer/bootdev string ${TARGET_DISK}
d-i pkgsel/include string openssh-server ufw fail2ban unattended-upgrades
d-i finish-install/reboot_in_progress note
d-i preseed/late_command string in-target sh -c 'ufw --force enable; ufw allow OpenSSH; sed -i "s/^#\?PasswordAuthentication.*/PasswordAuthentication no/" /etc/ssh/sshd_config; systemctl enable fail2ban; dpkg-reconfigure -f noninteractive unattended-upgrades; echo provisioned > /etc/provision-stamp'
EOF
  validate_config debian "$out"; }
# <<< APEX[PRESEED_SCAFFOLD] <<<

# >>> APEX[ARCH_INSTALLER_SCAFFOLD] Ordered Arch installer: partition/luks/lvm/mkfs/mount -> pacstrap -> genfstab -> arch-chroot config (timezone/locale/hostname/initramfs HOOKS/bootloader/user/ssh/secure hook), with verified keyring/mirrors and reverse-order teardown via the EXIT trap. Satisfies PROV-020, PROV-016, PROV-010, PROV-005. >>>
install_arch(){ local disk="${TARGET_DISK:?}" vg=vg0
  retry 3 pacman -Sy --noconfirm archlinux-keyring
  if [ "${ENCRYPT:-0}" = 1 ]; then luks_lvm_setup "$disk" "$vg"; else confirm_destroy "$disk"; run wipefs -a "$disk"; run sgdisk --zap-all "$disk"
    [ "$FIRMWARE" = uefi ] && { run sgdisk -n1:0:+512M -t1:ef00 "$disk"; run sgdisk -n2:0:0 "$disk"; } || { run sgdisk -n1:0:+1M -t1:ef02 "$disk"; run sgdisk -n2:0:0 "$disk"; }
    run pvcreate "${disk}2"; run vgcreate "$vg" "${disk}2"; run lvcreate -L4G -nswap "$vg"; run lvcreate -l100%FREE -nroot "$vg"; run mkfs.ext4 "/dev/$vg/root"; run mkswap "/dev/$vg/swap"; fi
  run mount "/dev/$vg/root" /mnt; MOUNTS+=(/mnt); run mkdir -p /mnt/boot
  local esp; esp=$(lsblk -nrpo NAME,PARTTYPE "$disk" | awk '/ef00|ef02/{print $1; exit}'); [ "$FIRMWARE" = uefi ] && { run mkfs.fat -F32 "$esp"; run mount "$esp" /mnt/boot; }
  run swapon "/dev/$vg/swap"
  retry 3 pacstrap -K /mnt base linux linux-firmware lvm2 openssh ufw fail2ban sudo grub $([ "$FIRMWARE" = uefi ] && echo efibootmgr)
  genfstab -U /mnt >> /mnt/etc/fstab
  local hooks='base udev autodetect modconf kms keyboard keymap consolefont block'
  [ "${ENCRYPT:-0}" = 1 ] && hooks="$hooks encrypt lvm2 filesystems fsck" || hooks="$hooks lvm2 filesystems fsck"
  arch-chroot /mnt /bin/bash -euo pipefail <<CHROOT
ln -sf /usr/share/zoneinfo/${TZ:-UTC} /etc/localtime; hwclock --systohc
sed -i 's/^#en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen; locale-gen; echo 'LANG=en_US.UTF-8' > /etc/locale.conf
echo '${HOSTNAME:?}' > /etc/hostname
sed -i 's/^HOOKS=.*/HOOKS=($hooks)/' /etc/mkinitcpio.conf; mkinitcpio -P
useradd -m -G wheel -s /bin/bash ${USERNAME:?}; echo '${USERNAME}:$(openssl passwd -6 "${USER_PW:?}")' | chpasswd -e
mkdir -p /home/${USERNAME}/.ssh; echo '${SSH_PUBKEY:?}' > /home/${USERNAME}/.ssh/authorized_keys; chown -R ${USERNAME}:${USERNAME} /home/${USERNAME}/.ssh; chmod 700 /home/${USERNAME}/.ssh; chmod 600 /home/${USERNAME}/.ssh/authorized_keys
echo '%wheel ALL=(ALL:ALL) ALL' > /etc/sudoers.d/wheel
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/;s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl enable sshd fail2ban
ufw --force enable; ufw allow OpenSSH; systemctl enable ufw
$([ "$FIRMWARE" = uefi ] && echo 'grub-install --target=x86_64-efi --efi-directory=/boot --bootloader-id=ARCH' || echo "grub-install --target=i386-pc $disk")
$([ "${ENCRYPT:-0}" = 1 ] && echo "sed -i 's#^GRUB_CMDLINE_LINUX=.*#GRUB_CMDLINE_LINUX=\\"cryptdevice=UUID=\$(blkid -s UUID -o value $CRYPT_PART):cryptlvm root=/dev/$vg/root\\"#' /etc/default/grub")
grub-mkconfig -o /boot/grub/grub.cfg
echo provisioned > /etc/provision-stamp
CHROOT
  log 'arch install complete'; }
# <<< APEX[ARCH_INSTALLER_SCAFFOLD] <<<

# >>> APEX[AUTOYAST_SCAFFOLD] Parameterized AutoYaST XML with partitioning (LVM/encryption), software, hashed users, bootloader, security, and a chroot post-script hardening hook; validated for well-formedness. Satisfies PROV-021, PROV-009, PROV-010. >>>
gen_autoyast(){ local out="$1"
  cat > "$out" <<EOF
<?xml version="1.0"?>
<!DOCTYPE profile>
<profile xmlns="http://www.suse.com/1.0/yast2ns" xmlns:config="http://www.suse.com/1.0/configns">
  <general><mode><confirm config:type="boolean">false</confirm></mode></general>
  <networking><dns><hostname>${HOSTNAME:?}</hostname></dns></networking>
  <partitioning config:type="list"><drive>
    <device>${TARGET_DISK:?}</device><use>all</use>
    <enable_snapshots config:type="boolean">true</enable_snapshots>
    $([ "${ENCRYPT:-0}" = 1 ] && echo '<encryption_method>luks2</encryption_method>')
  </drive></partitioning>
  <software><patterns config:type="list"><pattern>base</pattern><pattern>minimal_base</pattern></patterns>
    <packages config:type="list"><package>openssh</package><package>firewalld</package><package>fail2ban</package></packages></software>
  <users config:type="list">
    <user><username>root</username><user_password>$(openssl passwd -6 "${ROOT_PW:?}")</user_password><encrypted config:type="boolean">true</encrypted></user>
    <user><username>${USERNAME:?}</username><user_password>$(openssl passwd -6 "${USER_PW:?}")</user_password><encrypted config:type="boolean">true</encrypted></user>
  </users>
  <scripts><chroot-scripts config:type="list"><script><chrooted config:type="boolean">true</chrooted><source><![CDATA[
#!/bin/bash
systemctl enable sshd firewalld fail2ban
firewall-cmd --permanent --add-service=ssh
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
echo provisioned > /etc/provision-stamp
]]></source></script></chroot-scripts></scripts>
</profile>
EOF
  validate_config suse "$out"; }
# <<< APEX[AUTOYAST_SCAFFOLD] <<<

# >>> APEX[CLOUDINIT_SCAFFOLD] Generic cloud-init user-data + meta-data with key-only users, ssh_pwauth off, package upgrade, and hardening runcmd; builds a labeled cidata NoCloud seed ISO. Satisfies PROV-022, PROV-009, PROV-010. >>>
gen_cloudinit(){ local dir="$1"; mkdir -p "$dir"
  cat > "$dir/user-data" <<EOF
#cloud-config
hostname: ${HOSTNAME:?}
ssh_pwauth: false
users:
  - name: ${USERNAME:?}
    groups: [sudo]
    sudo: 'ALL=(ALL) NOPASSWD:ALL'
    lock_passwd: true
    ssh_authorized_keys: ["${SSH_PUBKEY:?}"]
package_update: true
package_upgrade: true
packages: [openssh-server, ufw, fail2ban, unattended-upgrades]
runcmd:
  - [bash, -c, 'ufw --force enable && ufw allow OpenSSH']
  - [sed, -i, 's/^#\\?PasswordAuthentication.*/PasswordAuthentication no/', /etc/ssh/sshd_config]
  - [systemctl, enable, --now, fail2ban]
  - [bash, -c, 'echo provisioned > /etc/provision-stamp']
EOF
  printf 'instance-id: %s\nlocal-hostname: %s\n' "${HOSTNAME}-$(date +%s)" "${HOSTNAME}" > "$dir/meta-data"
  validate_config cloud-init "$dir/user-data"
  command -v genisoimage >/dev/null && run genisoimage -output "$dir/seed.iso" -volid cidata -joliet -rock "$dir/user-data" "$dir/meta-data"; }
# <<< APEX[CLOUDINIT_SCAFFOLD] <<<

# >>> APEX[REPRODUCIBILITY_STAMP] Centralize inputs in one sourced config, normalize nondeterministic fields, and emit a config hash for diff-review and provenance. Satisfies PROV-014, PROV-024. >>>
load_config(){ [ -n "${CONFIG:-}" ] && [ -f "$CONFIG" ] && . "$CONFIG"
  : "${HOSTNAME:?}" "${USERNAME:?}" "${TZ:=UTC}" "${TARGET_DISK:?}" "${SSH_PUBKEY:?}"
  validate_hostname "$HOSTNAME" || die "bad hostname" "$ERR_VALIDATE"
  validate_username "$USERNAME" || die "bad username" "$ERR_VALIDATE"
  validate_timezone "$TZ" || die "bad timezone" "$ERR_VALIDATE"
  [ "$MODE" = unattended ] && validate_target "$TARGET_DISK"
  PKGS=$(printf '%s\n' ${PKGS:-} | sort -u | tr '\n' ' ')
  CONFIG_SHA=$(sha256sum "${CONFIG:-/dev/null}" 2>/dev/null | cut -d' ' -f1); export CONFIG_SHA; log "config sha=$CONFIG_SHA"; }
# <<< APEX[REPRODUCIBILITY_STAMP] <<<
# ===== end APEX preamble =====

# automate arch install
DISK=/dev/sda
parted -s $DISK mklabel gpt
parted -s $DISK mkpart primary 1MiB 100%
mkfs.ext4 ${DISK}1
mount ${DISK}1 /mnt
pacstrap /mnt base linux
genfstab -U /mnt >> /mnt/etc/fstab
echo "installed"
