# APEX Elevation Report

| | |
|---|---|
| Detected domain | `installer` (confidence 29.0) |
| Target standard | **Production-grade automated/interactive OS provisioning: Kickstart (RHEL/Fedora/Rocky/Alma), Debian preseed + Ubuntu Subiquity autoinstall/cloud-init, Arch pacstrap installer, SUSE AutoYaST, generic cloud-init; aligned to vendor reference docs, CIS-style secure post-install, and reproducible-config / dry-run / fail-closed automation discipline.** |
| Engine | deterministic |
| Language | shell |
| Controls in rubric | 24 (24 automatable) |

**Why this domain:** matched `pacstrap`, `genfstab`, `mkfs.`, `parted`, `pacstrap`, `parted`, `re:\bmkfs(\.\w+)?\b`, `genfstab`

## What was elevated

- **FLAG_PLUMBING** — Resolve mode and the --assume-yes/--interactive/--dry-run/--family flags into single variables, with mutual-exclusion errors, TTY auto-detect, and a documented exit-code table. Satisfies PROV-001, PROV-006, PROV-012, PROV-024.
- **LOGGING** — Timestamped/leveled log helpers teed to a persistent file, a die() that exits with a code, and an ERR/EXIT trap that reports the failing line and runs ordered cleanup (reverse unmount + crypt close). Satisfies PROV-011, PROV-012.
- **PREFLIGHT_BLOCK** — Aggregate preflight gate: root, firmware (UEFI/BIOS), arch, candidate disk presence, RAM, network reachability, target free space. Collects all failures and exits ERR_PREFLIGHT. Exports FIRMWARE. Satisfies PROV-004.
- **RETRY** — Bounded retry with exponential backoff and per-attempt timeout for network operations; fails closed after N attempts. Satisfies PROV-013.
- **VERIFY_ARTIFACT** — Download-then-verify helper: SHA256 against a pinned digest and optional GPG signature verification before any artifact (ISO/key/package/checksum file) is consumed. Satisfies PROV-008.
- **RESOLVE_TARGET_DISK** — Enumerate candidate disks, subtract the running-root disk, live medium, and any disk with mounted partitions / LVM-MD-crypt holders; expose a safe candidate list for the picker and validate an unattended TARGET_DISK against the exclusion set. Satisfies PROV-003.
- **CONFIRM_DESTROY_GUARD** — Single mandatory gate in front of every destructive disk op: prints the resolved device, its current layout and holders, then requires a typed device-name match (interactive) or ASSUME_YES (unattended). Satisfies PROV-002, PROV-001.
- **WHIPTAIL_MENU_SKELETON** — Reusable whiptail TUI helpers (menu/input/password/yesno) with a disk picker fed from candidate_disks, per-field validation loops, and a final plan-review screen that triggers confirm_destroy. dialog fallback. Satisfies PROV-023, PROV-015.
- **VALIDATORS** — Input validators for hostname, username, timezone, IP/CIDR, and disk, used in both TUI loops and unattended config load. Satisfies PROV-015.
- **LUKS_PARTITION_FLOW** — Arch/manual LUKS2 + LVM full-disk-encryption flow with the passphrase sourced from prompt/env/keyfile (never embedded), correct crypttab and initramfs HOOKS, firmware-aware ESP. Satisfies PROV-016, PROV-020.
- **VALIDATE_CONFIG** — Family-dispatched generated-config validator that fails closed before apply. Satisfies PROV-007.
- **KICKSTART_SCAFFOLD** — Parameterized Kickstart with %packages, LVM (+ optional LUKS), firmware-aware bootloader, hashed rootpw, %pre and a %post --log secure hook. Satisfies PROV-017, PROV-009, PROV-016.
- **AUTOINSTALL_SCAFFOLD** — Ubuntu Subiquity autoinstall user-data (#cloud-config + autoinstall:v1) with hashed password, key-only ssh, storage layout switch (direct/lvm/zfs or dm_crypt LUKS), and curtin in-target hardening late-commands. Satisfies PROV-018, PROV-009, PROV-010, PROV-016.
- **PRESEED_SCAFFOLD** — Debian classic preseed with partman LVM+crypto recipe, crypted passwords, pkgsel, firmware-aware grub, and an in-target late_command hardening hook. Satisfies PROV-019, PROV-009, PROV-010, PROV-016.
- **ARCH_INSTALLER_SCAFFOLD** — Ordered Arch installer: partition/luks/lvm/mkfs/mount -> pacstrap -> genfstab -> arch-chroot config (timezone/locale/hostname/initramfs HOOKS/bootloader/user/ssh/secure hook), with verified keyring/mirrors and reverse-order teardown via the EXIT trap. Satisfies PROV-020, PROV-016, PROV-010, PROV-005.
- **AUTOYAST_SCAFFOLD** — Parameterized AutoYaST XML with partitioning (LVM/encryption), software, hashed users, bootloader, security, and a chroot post-script hardening hook; validated for well-formedness. Satisfies PROV-021, PROV-009, PROV-010.
- **CLOUDINIT_SCAFFOLD** — Generic cloud-init user-data + meta-data with key-only users, ssh_pwauth off, package upgrade, and hardening runcmd; builds a labeled cidata NoCloud seed ISO. Satisfies PROV-022, PROV-009, PROV-010.
- **REPRODUCIBILITY_STAMP** — Centralize inputs in one sourced config, normalize nondeterministic fields, and emit a config hash for diff-review and provenance. Satisfies PROV-014, PROV-024.

## Controls covered by the rubric

| Control | Severity | Auto | Title |
|---|---|---|---|
| `PROV-001` | critical | yes | Mode is explicit and mutually exclusive: --interactive (TUI) vs --unattended/--assume-yes (no prompts), selectable by flag or env |
| `PROV-002` | critical | yes | DESTRUCTIVE confirmation guard before any disk wipe / partition / mkfs / luksFormat / pvcreate |
| `PROV-003` | critical | yes | Live-system / target-disk safety interlock (never wipe the running root or a mounted/in-use disk) |
| `PROV-004` | critical | yes | Preflight checks: UEFI vs BIOS, arch, disk presence, free space, RAM, network reachability, root/privilege |
| `PROV-005` | high | yes | Idempotency / safe re-runnability |
| `PROV-006` | high | yes | Dry-run / --check mode that produces and validates artifacts without touching disks |
| `PROV-007` | critical | yes | Generated config is validated against its schema before use (ksvalidator / cloud-init schema / autoinstall / AutoYaST) |
| `PROV-008` | critical | yes | Checksum + signature verification of ISOs and downloaded packages/keys |
| `PROV-009` | critical | yes | No plaintext passwords in config; encrypted/hashed credentials and root SSH password-login disabled |
| `PROV-010` | high | yes | Secure post-install hooks: firewall on, SSH keys (not passwords), updates applied, fail2ban + unattended-upgrades/dnf-automatic |
| `PROV-011` | high | yes | Logging: timestamped, leveled, to a file AND console, with secret redaction |
| `PROV-012` | high | yes | Strict shell hygiene + meaningful exit codes + error trap |
| `PROV-013` | high | yes | Network bring-up with bounded retry, backoff, and timeout |
| `PROV-014` | high | yes | Reproducible / declarative config: single source of truth, pinned versions, deterministic output |
| `PROV-015` | high | yes | Input validation for every operator-supplied value (hostname, disk, username, timezone, IP, partition sizes) |
| `PROV-016` | high | yes | LUKS full-disk encryption option with safe passphrase/key handling |
| `PROV-017` | high | yes | Kickstart correctness: %pre/%packages/%post, partitioning (LVM/LUKS), network, bootloader, %post --log |
| `PROV-018` | high | yes | Ubuntu Subiquity autoinstall correctness: cloud-init user-data wrapper, autoinstall YAML, storage (direct/lvm/zfs), late-commands |
| `PROV-019` | medium | yes | Debian classic preseed correctness (legacy/d-i path) |
| `PROV-020` | medium | yes | Arch scripted installer correctness: pacstrap / genfstab / arch-chroot with verified mirrors and ordered teardown |
| `PROV-021` | medium | yes | SUSE AutoYaST correctness: well-formed, schema-valid XML with partitioning, software, users, and post scripts |
| `PROV-022` | medium | yes | Generic cloud-init correctness: meta-data/user-data/network-config, schema-valid, key-only, hardening via runcmd |
| `PROV-023` | high | yes | Interactive TUI (whiptail/dialog) for disk, hostname, user, timezone, profile with validation and a final confirm-before-destroy review |
| `PROV-024` | medium | yes | Single family dispatcher + clean separation of generate vs apply, with usage/help and self-test |

## Validation

- syntax: **checked** via bash -n + shellcheck
- linter notes (3):
  - `/var/folders/lv/p5gxlnjj7y1bntsc4jzq5zlw0000gn/T/apex.7v0m9058.sh:273:1: error: Couldn't parse this command expansion. Fix to allow more checks. [SC1073]`
  - `/var/folders/lv/p5gxlnjj7y1bntsc4jzq5zlw0000gn/T/apex.7v0m9058.sh:273:110: error: '(' is invalid here. Did you forget to escape it? [SC1036]`
  - `/var/folders/lv/p5gxlnjj7y1bntsc4jzq5zlw0000gn/T/apex.7v0m9058.sh:273:110: error: Expected end of $(..) expression. Fix any mentioned problems and try again. [SC1072]`

## References

- Red Hat Enterprise Linux - Performing an automated installation using Kickstart (and the Kickstart syntax reference / %pre, %packages, %post, partitioning, bootloader)
- pykickstart / ksvalidator documentation
- Ubuntu Subiquity autoinstall reference (autoinstall.html) and autoinstall schema (version 1)
- cloud-init documentation: NoCloud datasource, user-data modules, and `cloud-init schema` / `cloud-init devel schema` validation
- Debian Installer preseed documentation (appendix B, partman-auto / partman-crypto recipes, late_command)
- Arch Linux Installation guide + dm-crypt/Encrypting an entire system + mkinitcpio + GRUB (cryptdevice) wiki pages
- openSUSE/SUSE AutoYaST guide (profile structure, partitioning, scripts, schema/RNG validation)
- curtin documentation (in-target, storage config, dm_crypt)
- systemd-cryptsetup / crypttab(5) and cryptsetup LUKS2 manual
- CIS Distribution-Independent Linux Benchmark (basis for the secure post-install hooks: firewall, sshd hardening, automatic updates)
- OpenSSH sshd_config(5) (PasswordAuthentication, PermitRootLogin), fail2ban and unattended-upgrades / dnf-automatic documentation
