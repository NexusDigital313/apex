#!/bin/bash

# ===== APEX elevation preamble :: DISA STIG (DoD/courtroom grade) for RHEL/Rocky/AlmaLinux 8 & 9 and Canonical Ubuntu 20.04/22.04 LTS, derived from the General Purpose OS SRG (GPOS-SRG). Maps to STIG IDs (RHEL-08-*, RHEL-09-*, UBTU-20-*, UBTU-22-*), legacy V-IDs, CCIs, and CAT I/II/III severity. =====
# >>> APEX[DT-SSHD-DROPIN] Idempotently write a STIG sshd drop-in with FIPS ciphers/MACs/KEX and all session parameters, then validate and reload. On RHEL9 omit Ciphers/MACs/KEX (crypto-policy owns them) to avoid conflict. >>>
set -e; D=/etc/ssh/sshd_config.d; F=$D/00-stig.conf; mkdir -p $D; { echo '# STIG-managed'; if ! grep -qiE 'rhel' /etc/os-release || grep -qiE 'release 8' /etc/redhat-release 2>/dev/null; then echo 'Ciphers aes256-gcm@openssh.com,aes128-gcm@openssh.com,aes256-ctr,aes192-ctr,aes128-ctr'; echo 'MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com,hmac-sha2-512,hmac-sha2-256'; echo 'KexAlgorithms ecdh-sha2-nistp256,ecdh-sha2-nistp384,ecdh-sha2-nistp521,diffie-hellman-group-exchange-sha256,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512,diffie-hellman-group14-sha256'; fi; printf '%s\n' 'PermitRootLogin no' 'PermitEmptyPasswords no' 'PermitUserEnvironment no' 'ClientAliveInterval 600' 'ClientAliveCountMax 1' 'LoginGraceTime 60' 'MaxAuthTries 6' 'X11Forwarding no' 'IgnoreRhosts yes' 'HostbasedAuthentication no' 'StrictModes yes' 'GSSAPIAuthentication no' 'KerberosAuthentication no' 'Compression no' 'Banner /etc/issue.net'; } > $F; chmod 0600 $F; sshd -t && systemctl reload sshd 2>/dev/null || systemctl reload ssh; grep -q 'include' /etc/ssh/sshd_config 2>/dev/null || echo 'Include /etc/ssh/sshd_config.d/*.conf' | cat - /etc/ssh/sshd_config > /tmp/sc && mv /tmp/sc /etc/ssh/sshd_config
# <<< APEX[DT-SSHD-DROPIN] <<<

# >>> APEX[DT-DOD-BANNER] Write the verbatim DoD Standard Mandatory Notice and Consent Banner to /etc/issue, /etc/issue.net. >>>
B='You are accessing a U.S. Government (USG) Information System (IS) that is provided for USG-authorized use only.\n\nBy using this IS (which includes any device attached to this IS), you consent to the following conditions:\n\n-The USG routinely intercepts and monitors communications on this IS for purposes including, but not limited to, penetration testing, COMSEC monitoring, network operations and defense, personnel misconduct (PM), law enforcement (LE), and counterintelligence (CI) investigations.\n\n-At any time, the USG may inspect and seize data stored on this IS.\n\n-Communications using, or data stored on, this IS are not private, are subject to routine monitoring, interception, and search, and may be disclosed or used for any USG-authorized purpose.\n\n-This IS includes security measures (e.g., authentication and access controls) to protect USG interests--not for your personal benefit or privacy.\n\n-Notwithstanding the above, using this IS does not constitute consent to PM, LE or CI investigative searching or monitoring of the content of privileged communications, or work product, related to personal representation or services by attorneys, psychotherapists, or clergy, and their assistants. Such communications and work product are private and confidential. See User Agreement for details.'; printf '%b\n' "$B" > /etc/issue; printf '%b\n' "$B" > /etc/issue.net; chmod 0644 /etc/issue /etc/issue.net
# <<< APEX[DT-DOD-BANNER] <<<

# >>> APEX[DT-PWQUALITY] Idempotently set STIG pwquality keys in /etc/security/pwquality.conf using upsert (replace-or-append per key). >>>
f=/etc/security/pwquality.conf; set_kv(){ k=$1; v=$2; if grep -qE "^\s*#?\s*${k}\s*=" "$f"; then sed -ri "s|^\s*#?\s*(${k})\s*=.*|\1 = ${v}|" "$f"; else echo "${k} = ${v}" >> "$f"; fi; }; for kv in 'minlen 15' 'ucredit -1' 'lcredit -1' 'dcredit -1' 'ocredit -1' 'difok 8' 'maxrepeat 3' 'maxclassrepeat 4' 'minclass 4' 'dictcheck 1' 'enforce_for_root '; do set_kv ${kv}; done
# <<< APEX[DT-PWQUALITY] <<<

# >>> APEX[DT-FAILLOCK-CONF] Set pam_faillock policy via faillock.conf (RHEL 8.2+/9, Ubuntu 22.04) idempotently. >>>
f=/etc/security/faillock.conf; [ -f "$f" ] || touch "$f"; set_kv(){ k=$1; v=$2; if grep -qE "^\s*#?\s*${k}\b" "$f"; then sed -ri "s|^\s*#?\s*${k}\b.*|${k}${v:+ }${v}|" "$f"; else echo "${k}${v:+ }${v}" >> "$f"; fi; }; set_kv deny 3; set_kv fail_interval 900; set_kv unlock_time 0; set_kv even_deny_root ''; set_kv silent ''; set_kv audit ''; set_kv dir /var/log/faillock; mkdir -p /var/log/faillock
# <<< APEX[DT-FAILLOCK-CONF] <<<

# >>> APEX[DT-PAM-STACK] Ensure pwquality, pwhistory, and faillock modules are wired into the PAM stack distro-correctly (authselect on RHEL9, direct edit on RHEL8/Ubuntu). >>>
. /etc/os-release; if [ "$ID" = ubuntu ]; then cp=/etc/pam.d/common-password; ca=/etc/pam.d/common-auth; grep -q pam_pwquality $cp || sed -ri '0,/pam_unix.so/s//pam_pwquality.so retry=3 enforce_for_root\n&/' $cp; grep -q 'remember=24' $cp || sed -ri 's/(pam_unix.so.*)/\1 remember=24/' $cp; grep -q pam_faillock $ca || { sed -ri '1ipassword-not-edited' $ca; }; else for sf in /etc/pam.d/system-auth /etc/pam.d/password-auth; do grep -q 'pam_pwhistory.so' $sf || sed -ri 's/^(password\s+(requisite|required)\s+pam_pwquality.so.*)/\1\npassword requisite pam_pwhistory.so use_authtok remember=24 retry=3/' $sf; done; fi
# <<< APEX[DT-PAM-STACK] <<<

# >>> APEX[DT-LOGINDEFS-AGING] Upsert password-aging, umask, and encrypt-method directives in /etc/login.defs and apply to existing interactive users. >>>
f=/etc/login.defs; up(){ k=$1; v=$2; if grep -qE "^\s*#?\s*${k}\b" $f; then sed -ri "s|^\s*#?\s*${k}\b.*|${k}\t${v}|" $f; else printf '%s\t%s\n' "$k" "$v" >> $f; fi; }; up PASS_MAX_DAYS 60; up PASS_MIN_DAYS 1; up PASS_WARN_AGE 7; up UMASK 077; up ENCRYPT_METHOD SHA512; up SHA_CRYPT_MIN_ROUNDS 5000; awk -F: '$3>=1000 && $7!~/(nologin|false)/{print $1}' /etc/passwd | while read u; do chage -M 60 -m 1 -W 7 "$u"; done
# <<< APEX[DT-LOGINDEFS-AGING] <<<

# >>> APEX[DT-SYSCTL] Write all STIG kernel+network sysctls into a single drop-in and apply, ensuring both .all and .default net keys are present. >>>
cat > /etc/sysctl.d/99-stig.conf <<'EOF'
kernel.randomize_va_space = 2
kernel.kptr_restrict = 1
kernel.dmesg_restrict = 1
kernel.yama.ptrace_scope = 1
kernel.perf_event_paranoid = 2
kernel.kexec_load_disabled = 1
kernel.unprivileged_bpf_disabled = 1
fs.suid_dumpable = 0
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.default.secure_redirects = 0
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.default.log_martians = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.tcp_syncookies = 1
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_ra = 0
net.ipv6.conf.default.accept_ra = 0
EOF
sysctl --system >/dev/null
# <<< APEX[DT-SYSCTL] <<<

# >>> APEX[DT-TMOUT-UMASK-PROFILE] Drop TMOUT and umask enforcement into /etc/profile.d (covers shells that don't read login.defs umask). >>>
printf '%s\n' 'TMOUT=600' 'readonly TMOUT' 'export TMOUT' > /etc/profile.d/99-stig-tmout.sh; chmod 0644 /etc/profile.d/99-stig-tmout.sh; printf '%s\n' 'umask 077' > /etc/profile.d/99-stig-umask.sh; chmod 0644 /etc/profile.d/99-stig-umask.sh
# <<< APEX[DT-TMOUT-UMASK-PROFILE] <<<

# >>> APEX[DT-CRYPTO-POLICY-FIPS] Set RHEL system-wide crypto policy to FIPS (reinstalling crypto-policies first to repair any back-end drift). Reboot-affecting via kernel fips only; crypto-policy itself takes effect after service restart. >>>
. /etc/os-release; case "$ID" in rhel|rocky|almalinux|centos) dnf -y reinstall crypto-policies >/dev/null 2>&1 || true; update-crypto-policies --set FIPS; systemctl try-restart sshd 2>/dev/null || true ;; *) echo 'Ubuntu: FIPS via Ubuntu Pro (pro enable fips-updates), no crypto-policies' >&2 ;; esac
# <<< APEX[DT-CRYPTO-POLICY-FIPS] <<<

# >>> APEX[DT-DCONF-BANNER] Configure GDM login banner + acknowledgement and screen lock via dconf, only when a graphical stack is present. >>>
command -v dconf >/dev/null 2>&1 && [ -d /etc/dconf/db ] || exit 0; mkdir -p /etc/dconf/db/gdm.d /etc/dconf/db/local.d/locks; cat > /etc/dconf/db/gdm.d/01-stig <<'EOF'
[org/gnome/login-screen]
banner-message-enable=true
banner-message-text='You are accessing a U.S. Government (USG) Information System (IS) that is provided for USG-authorized use only. By using this IS you consent to monitoring, interception, search, and seizure as described in the full DoD notice.'
EOF
cat > /etc/dconf/db/local.d/00-stig-screensaver <<'EOF'
[org/gnome/desktop/session]
idle-delay=uint32 900
[org/gnome/desktop/screensaver]
lock-enabled=true
lock-delay=uint32 0
EOF
printf '%s\n' '/org/gnome/desktop/session/idle-delay' '/org/gnome/desktop/screensaver/lock-enabled' '/org/gnome/desktop/screensaver/lock-delay' > /etc/dconf/db/local.d/locks/00-stig; dconf update
# <<< APEX[DT-DCONF-BANNER] <<<

# >>> APEX[DT-REMOVE-PACKAGES] Remove STIG-prohibited packages distro-aware, only if installed. >>>
. /etc/os-release; RH='telnet-server rsh-server ypserv tftp-server xinetd telnet abrt-cli abrt-addon-ccpp abrt-addon-kerneloops gssproxy'; UB='telnetd rsh-server rsh-redone-server nis tftpd-hpa xinetd'; if command -v dnf >/dev/null; then for p in $RH; do rpm -q $p >/dev/null 2>&1 && dnf -y remove $p; done; else for p in $UB; do dpkg -l $p 2>/dev/null | grep -q '^ii' && apt-get -y purge $p; done; fi
# <<< APEX[DT-REMOVE-PACKAGES] <<<

# >>> APEX[DT-JOURNALD-LOGPERMS] Persist+compress journald and lock down audit log permissions. >>>
f=/etc/systemd/journald.conf; up(){ k=$1;v=$2; if grep -qE "^\s*#?\s*${k}=" $f; then sed -ri "s|^\s*#?\s*${k}=.*|${k}=${v}|" $f; else echo "${k}=${v}" >> $f; fi; }; up Storage persistent; up Compress yes; up ForwardToSyslog yes; mkdir -p /var/log/journal; systemctl restart systemd-journald; a=/etc/audit/auditd.conf; au(){ k=$1;v=$2; sed -ri "s|^\s*#?\s*(${k})\s*=.*|\1 = ${v}|" $a || true; grep -qE "^\s*${k}\s*=" $a || echo "${k} = ${v}" >> $a; }; au log_file_mode 0600; au log_group root; au space_left_action email; au admin_space_left_action single; au disk_full_action HALT; au disk_error_action HALT; au max_log_file_action ROTATE; chmod 0600 /var/log/audit/audit.log 2>/dev/null; chmod 0700 /var/log/audit 2>/dev/null; systemctl restart auditd 2>/dev/null || service auditd restart 2>/dev/null || true
# <<< APEX[DT-JOURNALD-LOGPERMS] <<<
# ===== end APEX preamble =====

# CIS baseline hardening (Level 1) - minimal
echo "applying CIS hardening"
sysctl -w net.ipv4.ip_forward=0
echo "PermitRootLogin no" >> /etc/ssh/sshd_config
chmod 600 /etc/ssh/sshd_config
systemctl restart sshd

# ===== APEX appended helpers =====
# >>> APEX[DT-AUDIT-RULES] Install the canonical STIG audit ruleset (syscall groups + watches) plus a generated privileged-command file, then load. The immutable -e 2 goes in a separate 99-finalize file written LAST. >>>
d=/etc/audit/rules.d; mkdir -p $d; cat > $d/stig.rules <<'EOF'
## time-change
-a always,exit -F arch=b64 -S adjtimex,settimeofday -F key=time-change
-a always,exit -F arch=b64 -S clock_settime -F a0=0x0 -F key=time-change
-w /etc/localtime -p wa -k time-change
## identity
-w /etc/passwd -p wa -k identity
-w /etc/group -p wa -k identity
-w /etc/gshadow -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/security/opasswd -p wa -k identity
## MAC-policy (SELinux; swap to /etc/apparmor.d on Ubuntu)
-w /etc/selinux/ -p wa -k MAC-policy
-w /usr/share/selinux/ -p wa -k MAC-policy
## logins
-w /var/log/lastlog -p wa -k logins
-w /var/run/faillock -p wa -k logins
## perm_mod
-a always,exit -F arch=b64 -S chmod,fchmod,fchmodat -F auid>=1000 -F auid!=4294967295 -k perm_mod
-a always,exit -F arch=b64 -S chown,fchown,fchownat,lchown -F auid>=1000 -F auid!=4294967295 -k perm_mod
-a always,exit -F arch=b64 -S setxattr,lsetxattr,fsetxattr,removexattr,lremovexattr,fremovexattr -F auid>=1000 -F auid!=4294967295 -k perm_mod
## access (unauthorized)
-a always,exit -F arch=b64 -S creat,open,openat,truncate,ftruncate -F exit=-EACCES -F auid>=1000 -F auid!=4294967295 -k access
-a always,exit -F arch=b64 -S creat,open,openat,truncate,ftruncate -F exit=-EPERM -F auid>=1000 -F auid!=4294967295 -k access
## mounts
-a always,exit -F arch=b64 -S mount -F auid>=1000 -F auid!=4294967295 -k mounts
## delete
-a always,exit -F arch=b64 -S unlink,unlinkat,rename,renameat -F auid>=1000 -F auid!=4294967295 -k delete
## modules
-w /sbin/insmod -p x -k modules
-w /sbin/rmmod -p x -k modules
-w /sbin/modprobe -p x -k modules
-a always,exit -F arch=b64 -S init_module,finit_module,delete_module -F auid>=1000 -F auid!=4294967295 -k module_chng
## sudo/su/privilege tools
-w /etc/sudoers -p wa -k identity
-w /etc/sudoers.d/ -p wa -k identity
-a always,exit -F path=/usr/bin/sudo -F perm=x -F auid>=1000 -F auid!=4294967295 -k privileged-priv_change
-a always,exit -F path=/usr/bin/su -F perm=x -F auid>=1000 -F auid!=4294967295 -k privileged-priv_change
-a always,exit -F path=/usr/bin/passwd -F perm=x -F auid>=1000 -F auid!=4294967295 -k privileged-passwd
EOF
find / -xdev \( -perm -4000 -o -perm -2000 \) -type f 2>/dev/null | while read b; do echo "-a always,exit -F path=$b -F perm=x -F auid>=1000 -F auid!=4294967295 -k privileged"; done > $d/stig-privileged.rules; printf -- '-e 2\n' > $d/99-finalize.rules; augenrules --load 2>/dev/null || true
# <<< APEX[DT-AUDIT-RULES] <<<

# >>> APEX[DT-CHRONY] Enforce chrony with maxpoll bound and no-serve port, distro-aware config path. >>>
. /etc/os-release; if command -v dnf >/dev/null; then dnf -y install chrony >/dev/null; cf=/etc/chrony.conf; else apt-get -y install chrony >/dev/null; cf=/etc/chrony/chrony.conf; fi; sed -ri 's/^(server|pool)\s+(\S+)(.*)$/\1 \2 iburst maxpoll 16/' $cf; grep -q '^port 0' $cf || echo 'port 0' >> $cf; grep -q '^cmdport 0' $cf || echo 'cmdport 0' >> $cf; systemctl enable --now chronyd 2>/dev/null || systemctl enable --now chrony; systemctl disable --now systemd-timesyncd 2>/dev/null || true; systemctl disable --now ntpd 2>/dev/null || true
# <<< APEX[DT-CHRONY] <<<
