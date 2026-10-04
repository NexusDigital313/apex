#!/usr/bin/env python3
"""APEX test harness — pure stdlib, zero deps (matches the tool's own ethos).

Locks the invariants the whole project rests on. For every example script:
  1. GOLDEN      elevate(script) matches tests/golden/<name>.apex.sh
  2. IDEMPOTENT  elevate(elevate(script)) == elevate(script)   (a re-run is a no-op)
  3. VALID       the elevated script passes bash -n + shellcheck
  4. ORDERED     every helper APEX injects is defined before the script calls it

Plus focused rewrite-safety cases: a single-target `rm -rf` is rewritten to
safe_rm, while multi-target / glob / command-substitution / heredoc-body forms
are left untouched (the transformer must fail safe, never silently corrupt).

And a runtime suite that EXECUTES output (bash -n alone can't see a helper
called before it is defined, or a broken $(...) inside a heredoc).

Usage:
    python3 tests/run.py            # run all checks, non-zero exit on failure
    python3 tests/run.py --update   # regenerate the golden files, then run
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apex import classify as classify_mod          # noqa: E402
from apex import engine_deterministic as det        # noqa: E402
from apex import validate as validate_mod           # noqa: E402
from apex.rubrics import load_rubrics               # noqa: E402

EX_DIR = os.path.join(ROOT, "examples")
GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")

_RUBRICS = load_rubrics()


def _elevate(src: str):
    cls = classify_mod.classify(src)
    rubric = _RUBRICS.get(cls.domain) or _RUBRICS.get("generic")
    return det.elevate(src, rubric, cls.language)


_FUNC_DEF_RE = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{?", re.MULTILINE)
_BLOCK_RE = re.compile(r"^# >>> APEX\[.*?^# <<< APEX\[[^\]]*\] <<<$", re.MULTILINE | re.DOTALL)


def _called_before_defined(text: str):
    """Helpers APEX injected that the script's own code calls before they exist.

    bash defines functions top-down, so a call that runs above the definition
    fails with `command not found` even though `bash -n` is happy.
    """
    defined = {}  # name -> line number of its definition inside an APEX block
    for m in _BLOCK_RE.finditer(text):
        for f in _FUNC_DEF_RE.finditer(m.group(0)):
            line = text.count("\n", 0, m.start() + f.start()) + 1
            defined.setdefault(f.group(1), line)
    user = _BLOCK_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    bad = []
    for name, def_line in defined.items():
        call = re.search(r"^\s*{0}\b(?!\s*\(\))".format(re.escape(name)), user, re.MULTILINE)
        if call:
            call_line = user.count("\n", 0, call.start()) + 1
            if call_line < def_line:
                bad.append("{0} called at line {1}, defined at line {2}".format(name, call_line, def_line))
    return bad


def _examples():
    for name in sorted(os.listdir(EX_DIR)):
        if name.endswith(".sh") and not name.endswith(".apex.sh"):
            yield name, os.path.join(EX_DIR, name)


class Runner:
    def __init__(self, update: bool):
        self.update = update
        self.passed = 0
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed += 1
            print("  \033[32mPASS\033[0m {0}".format(name))
        else:
            self.failed += 1
            print("  \033[31mFAIL\033[0m {0}{1}".format(name, "  -- " + detail if detail else ""))

    # -- invariant suite over the example corpus ---------------------------
    def golden_suite(self) -> None:
        print("golden / idempotency / validity:")
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        for name, path in _examples():
            src = open(path, encoding="utf-8").read()
            out = _elevate(src).elevated
            gp = os.path.join(GOLDEN_DIR, name.replace(".sh", ".apex.sh"))

            if self.update:
                open(gp, "w", encoding="utf-8").write(out)

            if not os.path.exists(gp):
                self.check("golden[{0}]".format(name), False, "no golden (run --update)")
            else:
                golden = open(gp, encoding="utf-8").read()
                self.check("golden[{0}]".format(name), out == golden, "output != golden")

            rerun = _elevate(out).elevated
            self.check("idempotent[{0}]".format(name), rerun == out, "re-run changed the output")

            val = validate_mod.validate(out, "shell")
            self.check("valid[{0}]".format(name), val.ok, "; ".join(val.errors[:3]))

            bad = _called_before_defined(out)
            self.check("defined-before-use[{0}]".format(name), not bad, "; ".join(bad))

    # -- focused rewrite-safety behaviour ----------------------------------
    def rewrite_suite(self) -> None:
        print("rewrite safety:")
        g = _RUBRICS["generic"]

        def elev(snippet: str):
            return det.elevate("#!/bin/bash\n" + snippet, g, "shell")

        rewrote = lambda s: "RW-BASH-RM-SAFE" in elev(s).rewritten          # noqa: E731
        body_has = lambda s, frag: any(                                     # noqa: E731
            frag in ln for ln in elev(s).elevated.splitlines() if "APEX" not in ln)

        self.check("single-target -> rewritten", rewrote('rm -rf "$WORK"\n'))
        self.check("single-target call is safe_rm", body_has('rm -rf "$WORK"\n', 'safe_rm "$WORK"'))
        self.check("multi-target  -> declined", not rewrote('rm -rf a b c\n'))
        self.check("glob          -> declined", not rewrote('rm -rf /tmp/build/*\n'))
        self.check("command-sub   -> declined", not rewrote('rm -rf $(mktemp -d)\n'))
        self.check("no danger flag-> declined", not rewrote('rm plain.txt\n'))
        # safe_rm runs `rm -rf`, so only rewrite calls that were already -r AND -f
        self.check("rm -f (file)  -> declined", not rewrote('rm -f "$LOG"\n'))
        self.check("rm -r (no -f) -> declined", not rewrote('rm -r "$DIR"\n'))
        self.check("extra option  -> declined", not rewrote('rm -rf --one-file-system "$DIR"\n'))
        self.check("rm -rf --     -> rewritten", rewrote('rm -rf -- "$DIR"\n'))

        # A guard block left at the END by an older APEX run must not receive
        # new safe_rm calls: they would run before safe_rm() is defined.
        old_layout = ('#!/bin/bash\nrm -rf "$WORK"\n\n# ===== APEX appended helpers =====\n'
                      '# >>> APEX[DT-BASH-DESTRUCTIVE-GUARD] guard >>>\nsafe_rm(){ :; }\n'
                      '# <<< APEX[DT-BASH-DESTRUCTIVE-GUARD] <<<\n')
        res = det.elevate(old_layout, g, "shell")
        self.check("helper below code -> declined", "RW-BASH-RM-SAFE" not in res.rewritten
                   and "RW-BASH-RM-SAFE" in res.declined)

        # heredoc BODY must be preserved verbatim, even though it "looks" like code
        hd = 'cat <<EOF\nrm -rf /tmp/x\nEOF\nrm -rf "$REAL"\n'
        self.check("heredoc body preserved", body_has(hd, "rm -rf /tmp/x"))
        self.check("real code beside heredoc rewritten", body_has(hd, 'safe_rm "$REAL"'))

        # heredoc forms the lexer must recognise, and prose that must not flip quoting
        self.check("<<\\EOF body preserved", body_has('cat <<\\EOF\nrm -rf /tmp/x\nEOF\n', "rm -rf /tmp/x"))
        self.check("<<'EOF' body preserved", body_has("cat <<'EOF'\nrm -rf /tmp/x\nEOF\n", "rm -rf /tmp/x"))
        self.check("indented non-terminator kept in body",
                   body_has('cat <<EOF\n  EOF\nrm -rf /tmp/x\nEOF\n', "rm -rf /tmp/x"))
        self.check("apostrophe in heredoc keeps later '' literal",
                   body_has("cat <<EOF\ndon't\nEOF\necho 'rm -rf /tmp/x'\n", "echo 'rm -rf /tmp/x'"))
        self.check("$'\\'' keeps later '' literal",
                   body_has("echo $'it\\'s'\necho 'rm -rf /tmp/x'\n", "echo 'rm -rf /tmp/x'"))
        self.check("here-string is not a heredoc", rewrote('cat <<<foo\nrm -rf "$X"\n'))
        self.check("arithmetic << is not a heredoc", rewrote('x=$((1<<2))\nrm -rf "$X"\n'))
        self.check("rm -rf mid-line not rewritten",
                   not rewrote("echo 'a' rm -rf /tmp/x\n") and not rewrote("true; rm -rf a 'b c'\n"))

        # multi-line double-quoted string body must be preserved
        ml = 'M="danger:\nrm -rf /\nend"\n'
        self.check("multiline-string body preserved", body_has(ml, "rm -rf /"))

        # backticks -> $(...), but NOT inside a single-quoted (literal) string
        self.check("backticks -> $()", body_has('d=`date +%F`\n', "d=$(date +%F)"))
        self.check("backticks in '' preserved", body_has("echo '`literal`'\n", "'`literal`'"))

        # forensic: the script's own bare dd becomes the verified acquisition, and only
        # a dd that reads the whole source into an image file is rewritten
        f = _RUBRICS["forensic"]

        def acquired(snippet: str) -> bool:
            res = det.elevate("#!/bin/bash\n" + snippet, f, "shell")
            return "RW-FOR-DD-DC3DD" in res.rewritten

        out = _elevate(open(os.path.join(EX_DIR, "acquire.sh"), encoding="utf-8").read()).elevated
        user = _BLOCK_RE.sub("", out)
        self.check("forensic: dd -> acquire_image", "acquire_image $SRC $OUT" in user
                   and not re.search(r"^\s*dd\s", user, re.MULTILINE))
        self.check("forensic: dd conv/status kept as acquisition",
                   acquired("dd if=/dev/sdb of=img.dd bs=1M conv=noerror,sync status=progress\n"))
        self.check("forensic: partial read (count=) not rewritten", not acquired('dd if="$S" of=x.img count=10\n'))
        self.check("forensic: dd from /dev/zero not rewritten", not acquired("dd if=/dev/zero of=pad.bin bs=1M\n"))
        self.check("forensic: disk-to-disk not rewritten", not acquired("dd if=/dev/sdb of=/dev/sdc\n"))

    # -- execute elevated output, not just parse it ------------------------
    def runtime_suite(self) -> None:
        print("runtime:")
        if not shutil.which("bash"):
            print("  (skipped: bash not found)")
            return
        tmp = tempfile.mkdtemp(prefix="apex-test.")
        try:
            # 1. An elevated script whose `rm -rf` was rewritten to safe_rm runs.
            work = os.path.join(tmp, "work")
            os.makedirs(os.path.join(work, "sub"))
            res = det.elevate('#!/bin/bash\nrm -rf "$WORK"\necho done\n', _RUBRICS["generic"], "shell")
            script = os.path.join(tmp, "clean.apex.sh")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(res.elevated)
            env = dict(os.environ, WORK=work, ASSUME_YES="true")
            proc = subprocess.run(["bash", script], env=env, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=60)
            self.check("rewritten rm -rf script runs", proc.returncode == 0 and proc.stdout.strip() == "done",
                       "exit {0}: {1}".format(proc.returncode, proc.stderr.strip()[-200:]))
            self.check("rewritten rm -rf deletes target", not os.path.exists(work))

            # 2. The Arch scaffold's chroot heredoc expands on the host and yields
            #    a script bash can parse, for every firmware/encryption combination.
            impl = next(t.implementation for t in _RUBRICS["installer"].transforms
                        if t.id == "ARCH_INSTALLER_SCAFFOLD")
            start = impl.find("  local grub_install")
            if start == -1:  # layout without the host-side GRUB variables
                start = impl.index("  arch-chroot /mnt")
            end = impl.index("\nCHROOT", start) + len("\nCHROOT")
            snippet = impl[start:end].replace("arch-chroot /mnt /bin/bash -euo pipefail <<CHROOT", "cat <<CHROOT")
            harness = ("blkid(){ echo 1111-AAAA; }; openssl(){ cat >/dev/null; echo HASH; }\n"
                       "f(){ local disk=/dev/sda vg=vg0 hooks=x\n" + snippet + "\n}\nf\n")
            for fw in ("uefi", "bios"):
                for enc in ("0", "1"):
                    env = dict(os.environ, FIRMWARE=fw, ENCRYPT=enc, CRYPT_PART="/dev/sda2",
                               TARGET_HOSTNAME="h", USERNAME="u", USER_PW="p", SSH_PUBKEY="k")
                    out = subprocess.run(["bash", "-euc", harness], env=env, capture_output=True,
                                         text=True, timeout=30)
                    parsed = subprocess.run(["bash", "-n"], input=out.stdout, capture_output=True,
                                            text=True, timeout=30)
                    want_crypt = ("cryptdevice=UUID=1111-AAAA" in out.stdout) == (enc == "1")
                    self.check("arch chroot script[{0},encrypt={1}]".format(fw, enc),
                               out.returncode == 0 and parsed.returncode == 0 and want_crypt,
                               (out.stderr or parsed.stderr).strip()[-200:])
            self._arch_install_cases(tmp)
            self._behaviour_cases(tmp)
            self._forensic_cases(tmp)
            self._cis_cases(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # Every external command install_arch touches, replaced by a recorder.
    _STUBS = ("pacman sgdisk wipefs pvcreate vgcreate lvcreate mkfs.ext4 mkfs.fat mkswap mount "
              "umount mkdir swapon pacstrap udevadm blkid openssl lsblk findmnt").split()
    _DESTRUCTIVE = ("sgdisk", "wipefs", "pvcreate", "mkfs.", "mount ", "pacstrap", "pacman",
                    "cryptsetup luksFormat", "genfstab", "arch-chroot", "swapon")

    def _arch_install_cases(self, tmp: str) -> None:
        """Run the real install_arch with every system command stubbed, for each
        disk type / firmware / encryption combination, in dry-run and real mode."""
        blocks = {t.id: t.implementation for t in _RUBRICS["installer"].transforms}
        mnt = os.path.join(tmp, "mnt")
        code = "\n".join(blocks[b] for b in ("LOGGING", "RETRY", "RESOLVE_TARGET_DISK",
                                              "CONFIRM_DESTROY_GUARD", "LUKS_PARTITION_FLOW",
                                              "ARCH_INSTALLER_SCAFFOLD")).replace("/mnt", mnt)
        stubs = "".join('{0}(){{ echo "{0} $*" >> "$CALLS"; {1}}}\n'.format(
            s, "echo 1111-AAAA; " if s == "blkid" else "cat >/dev/null; echo HASH; " if s == "openssl" else "")
            for s in self._STUBS)
        stubs += ('cryptsetup(){ echo "cryptsetup $*" >> "$CALLS"; cat >/dev/null; }\n'
                  'genfstab(){ echo "genfstab $*" >> "$CALLS"; }\n'
                  'arch-chroot(){ echo "arch-chroot $*" >> "$CALLS"; cat > "$CHROOT"; }\n')
        harness = ("set -Eeuo pipefail\nMODE=unattended ASSUME_YES=1\n" + code + "\n" + stubs +
                   "validate_target(){ :; }\ninstall_arch\n")
        for disk, p in (("/dev/sda", "/dev/sda"), ("/dev/nvme0n1", "/dev/nvme0n1p")):
            for fw in ("uefi", "bios"):
                for enc in ("0", "1"):
                    for dry in ("1", "0"):
                        shutil.rmtree(mnt, ignore_errors=True)
                        os.makedirs(os.path.join(mnt, "etc"))
                        calls, chroot = os.path.join(tmp, "calls"), os.path.join(tmp, "chroot.sh")
                        for f in (calls, chroot):
                            open(f, "w").close()
                        env = dict(os.environ, CALLS=calls, CHROOT=chroot, LOGFILE=os.path.join(tmp, "log"),
                                   TARGET_DISK=disk, FIRMWARE=fw, ENCRYPT=enc, DRY_RUN=dry,
                                   LUKS_PASSPHRASE="correct horse", TARGET_HOSTNAME="h", USERNAME="u",
                                   USER_PW="p", SSH_PUBKEY="k")
                        proc = subprocess.run(["bash", "-c", harness], env=env, stdin=subprocess.DEVNULL,
                                              capture_output=True, text=True, timeout=60)
                        log = open(calls).read()
                        name = "arch install[{0},{1},encrypt={2},{3}]".format(
                            os.path.basename(disk), fw, enc, "dry-run" if dry == "1" else "real")
                        if dry == "1":
                            ran = [c for c in self._DESTRUCTIVE if c in log]
                            self.check(name, proc.returncode == 0 and not ran,
                                       "ran for real: {0}; {1}".format(ran, proc.stderr.strip()[-200:]))
                            continue
                        want = []
                        if fw == "uefi":
                            want.append("mkfs.fat -F32 {0}1".format(p))
                        elif enc == "1":
                            want.append("mkfs.ext4 -F {0}2".format(p))  # plain /boot for GRUB
                        want.append("cryptsetup luksFormat --batch-mode --type luks2 {0}{1}".format(
                            p, 2 if fw == "uefi" else 3) if enc == "1" else "pvcreate {0}2".format(p))
                        want.append("genfstab -U")
                        missing = [w for w in want if w not in log]
                        parsed = subprocess.run(["bash", "-n", chroot], capture_output=True, text=True)
                        text = open(chroot).read()
                        ok = (proc.returncode == 0 and not missing and parsed.returncode == 0
                              and "/etc/hostname" in text and "echo 'h'" in text and "u:HASH" in text)
                        self.check(name, ok, "exit {0}; missing {1}; {2}".format(
                            proc.returncode, missing, (proc.stderr or parsed.stderr).strip()[-200:]))

    def _behaviour_cases(self, tmp: str) -> None:
        """Elevating must not change what an ordinary script does."""
        g = _RUBRICS["generic"]

        def run_elevated(src: str, args=(), env=None, lang="shell"):
            path = os.path.join(tmp, "b.apex.sh")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(det.elevate(src, g, lang).elevated)
            return subprocess.run(["bash", path] + list(args), env=dict(os.environ, **(env or {})),
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)

        # unattended delete with no approval: stop loudly, don't skip silently
        work = os.path.join(tmp, "w")
        os.makedirs(work, exist_ok=True)
        p = run_elevated('#!/bin/bash\nrm -rf "$WORK"\necho done\n', env={"WORK": work})
        self.check("no terminal, no --yes -> error, nothing deleted",
                   p.returncode != 0 and os.path.isdir(work) and "--yes" in p.stderr)
        p = run_elevated('#!/bin/bash\nrm -rf "$WORK"\necho done\n', args=["--yes"], env={"WORK": work})
        self.check("--yes approves the delete", p.returncode == 0 and not os.path.exists(work), p.stderr[-200:])

        # the script's own flags and argument count survive
        own = '#!/bin/bash\nF=0\nfor a in "$@"; do case $a in --force) F=1;; esac; done\necho "$F $#"\n'
        self.check("script's own --force still works", run_elevated(own, ["--force"]).stdout.strip() == "1 1")
        self.check("no args -> $# is 0", run_elevated(own).stdout.strip() == "0 0")

        # the script's own VERSION and word splitting survive
        p = run_elevated('#!/bin/bash\nVERSION=2.1\nL="a b"\nfor x in $L; do echo "$x"; done\necho "$VERSION"\n')
        self.check("own VERSION + word splitting kept", p.stdout.split() == ["a", "b", "2.1"], p.stderr[-200:])

        # the injected lock is released on exit, so the next run is not refused, and a
        # script's own cleanup() does not replace APEX's EXIT handler
        lock_src = ('#!/bin/bash\n# nightly cron job\ncleanup(){ echo "user cleanup ran" >&2; }\n'
                    'rsync --version >/dev/null 2>&1 || true\necho ok\n')
        runs = [run_elevated(lock_src, env={"TMPDIR": tmp}) for _ in range(2)]
        self.check("lock released between runs",
                   all(r.returncode == 0 and r.stdout.strip() == "ok" for r in runs)
                   and not any(n.endswith(".lock") for n in os.listdir(tmp)), runs[-1].stderr[-200:])
        self.check("own cleanup() does not hijack the EXIT trap",
                   not any("user cleanup ran" in r.stderr for r in runs))

        # shebangs: POSIX sh upgraded with its flags, other shells left alone
        out = det.elevate('#!/bin/sh -e\nrm -rf "$X"\n', g, "shell").elevated.splitlines()
        self.check("#!/bin/sh -e -> bash + set -e", out[:2] == ["#!/usr/bin/env bash", "set -e"], str(out[:2]))
        zsh = '#!/usr/bin/env zsh\nprint -l ${(k)m}\nrm -rf "$X"\n'
        lang = classify_mod.detect_language(zsh, "x.sh")
        self.check("zsh script left unchanged", lang == "zsh" and det.elevate(zsh, g, lang).elevated == zsh)

        # one keyword listed in both signature sets is not a domain
        self.check("lone keyword -> generic", classify_mod.classify("pacstrap\n").domain == "generic")

        # a copyright/license notice under the shebang stays at the top
        notice = "#!/bin/bash\n# Copyright (c) 2026 Example. All Rights Reserved.\n# Proprietary.\n\nrm -rf \"$X\"\n"
        top = det.elevate(notice, g, "shell").elevated.splitlines()[:3]
        self.check("notice stays under the shebang",
                   top == ["#!/bin/bash", "# Copyright (c) 2026 Example. All Rights Reserved.", "# Proprietary."], str(top))

        # --in-place: exact backup, permissions kept
        src = os.path.join(tmp, "inplace.sh")
        raw = b"#!/bin/bash\n# caf\xe9\nrm -rf \"$X\"\n"
        with open(src, "wb") as fh:
            fh.write(raw)
        os.chmod(src, 0o700)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "apex.py"), "--no-banner", "elevate", src,
                            "--in-place", "--report", "-"], capture_output=True, text=True, timeout=120)
        bak = open(src + ".apex.bak", "rb").read() if os.path.exists(src + ".apex.bak") else b""
        self.check("--in-place backup is byte-exact", bak == raw, p.stderr[-200:])
        self.check("--in-place keeps non-UTF-8 bytes", b"caf\xe9" in open(src, "rb").read())
        self.check("--in-place keeps mode 700", (os.stat(src).st_mode & 0o777) == 0o700)

        # output that fails validation is never written, so --in-place can't break a script
        bad = os.path.join(tmp, "broken.sh")
        raw = b'#!/bin/bash\nif true; then\nrm -rf "$X"\n'
        with open(bad, "wb") as fh:
            fh.write(raw)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "apex.py"), "--no-banner", "elevate", bad,
                            "--in-place", "--report", "-"], capture_output=True, text=True, timeout=120)
        self.check("invalid result -> exit 1, input untouched",
                   p.returncode == 1 and open(bad, "rb").read() == raw, p.stderr[-200:])

        # -o pointing at the input needs --in-place (which keeps a backup)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "apex.py"), "--no-banner", "elevate", src,
                            "-o", src, "--report", "-"], capture_output=True, text=True, timeout=120)
        self.check("-o <input> refused without --in-place", p.returncode == 1 and "--in-place" in p.stderr)

    # dc3dd stand-in: copies if= to of= and writes a hashlog in dc3dd's format
    # (CORRUPT=1 records a wrong source sha256)
    _DC3DD = r'''#!/bin/bash
echo "dc3dd $*" >> "$CALLS"
[ "$*" = --version ] && { echo "dc3dd 7.2.646"; exit 0; }
for a; do case $a in if=*) i=${a#if=};; of=*) o=${a#of=};; hlog=*) h=${a#hlog=};; log=*) l=${a#log=};; esac; done
cat "$i" > "$o"; echo "0 bad sectors" > "$l"
s256=$(sha256sum < "$i" | cut -d' ' -f1); [ -n "${CORRUPT:-}" ] && s256=$(echo x | sha256sum | cut -d' ' -f1)
{ echo "command line: dc3dd $*"; echo "   $s256 (sha256)"; echo "   $(sha512sum < "$i" | cut -d' ' -f1) (sha512)"; } > "$h"
'''
    _GPG = r'''#!/bin/bash
echo "gpg $*" >> "$CALLS"
case " $* " in
  *" --list-secret-keys "*) [ -z "${NOKEY:-}" ]; exit ;;
  *" --fingerprint "*) echo "fpr:::::::::ABCDEF0123:"; exit 0 ;;
esac
for ((i = 1; i <= $#; i++)); do [ "${!i}" = --output ] && { j=$((i + 1)); echo sig > "${!j}"; }; done
exit 0
'''

    def _forensic_cases(self, tmp: str) -> None:
        """Run an elevated acquisition against a real (loop) block device with dc3dd
        and gpg stubbed: one verified read, sealed manifests, hard fail on mismatch."""
        if os.geteuid() != 0 or not shutil.which("losetup") or not shutil.which("blockdev"):
            print("  (forensic runtime skipped: needs root, losetup and blockdev)")
            return
        work = tempfile.mkdtemp(prefix="forensic.", dir=tmp)
        disk = os.path.join(work, "disk.raw")
        with open(disk, "wb") as fh:
            fh.write(os.urandom(3 << 20))
        att = subprocess.run(["losetup", "-f", "--show", disk], capture_output=True, text=True)
        if att.returncode != 0:
            print("  (forensic runtime skipped: cannot attach a loop device)")
            return
        loop = att.stdout.strip()
        try:
            bindir = os.path.join(work, "bin")
            os.makedirs(bindir)
            for name, body in (("dc3dd", self._DC3DD), ("gpg", self._GPG),
                               ("dd", '#!/bin/bash\necho "dd $*" >> "$CALLS"\nexec /bin/dd "$@"\n')):
                path = os.path.join(bindir, name)
                with open(path, "w") as fh:
                    fh.write(body)
                os.chmod(path, 0o755)
            src = open(os.path.join(EX_DIR, "acquire.sh"), encoding="utf-8").read()
            f = _RUBRICS["forensic"]
            scripts = {"plain": det.elevate(src, f, "shell").elevated,
                       "exit0": det.elevate(src.replace('$SRC"', '$SRC"; exit 0'), f, "shell").elevated}

            def run(case: str, script: str, **extra):
                subprocess.run(["blockdev", "--setrw", loop])
                ev = os.path.join(work, case)
                os.makedirs(ev)
                path = os.path.join(work, case + ".sh")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(scripts[script])
                calls = os.path.join(work, case + ".calls")
                open(calls, "w").close()
                env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], CALLS=calls,
                           COC_LOG=os.path.join(work, case + ".coc"), EXAMINER="e", EXAMINER_ORG="o",
                           EXAMINER_CREDS="c", EVIDENCE_ID="EV1", CASE_NUMBER="C1", LOCATION="l",
                           PURPOSE="p", SOURCE_DESC="d", GPG_KEY="k", **extra)
                p = subprocess.run(["bash", path, loop, os.path.join(ev, "a.img")], env=env,
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
                return p, ev, open(calls).read(), open(env["COC_LOG"], errors="replace").read()

            p, ev, calls, coc = run("ok", "plain")
            images = calls.count("dc3dd if=")
            self.check("forensic: one dc3dd read, no bare dd", p.returncode == 0 and images == 1
                       and not re.search(r"^dd ", calls, re.MULTILINE), p.stderr[-300:])
            ro = subprocess.run(["blockdev", "--getro", loop], capture_output=True, text=True).stdout.strip()
            self.check("forensic: source left read-only", ro == "1")
            tars = [n for n in os.listdir(ev) if n.endswith(".tar")]
            sums = subprocess.run("sha256sum -c *.SHA256SUMS && sha512sum -c *.SHA512SUMS", shell=True,
                                  cwd=ev, capture_output=True, text=True)
            members = subprocess.run(["tar", "-tf", tars[0]], cwd=ev, capture_output=True,
                                     text=True).stdout.split() if tars else []
            self.check("forensic: sealed manifests verify", len(tars) == 1 and sums.returncode == 0
                       and "a.img: OK" in sums.stdout and os.path.exists(os.path.join(ev, tars[0] + ".asc")),
                       sums.stdout[-200:] + sums.stderr[-200:])
            self.check("forensic: seal does not copy the image", members and "a.img" not in members, str(members))
            self.check("forensic: openssl version recorded",
                       re.search(r"TOOL    openssl = OpenSSL", coc) is not None or not shutil.which("openssl"))

            p, ev, calls, coc = run("mismatch", "plain", CORRUPT="1")
            self.check("forensic: hash mismatch -> fatal, not sealed", p.returncode != 0
                       and "sha256 MISMATCH" in coc and not any(n.endswith(".tar") for n in os.listdir(ev)))

            p, ev, calls, coc = run("exit0", "exit0")
            self.check("forensic: script ending in `exit 0` is still sealed", p.returncode == 0
                       and calls.count("--detach-sign") == 1, p.stderr[-300:])

            p, ev, calls, coc = run("nokey", "plain", NOKEY="1")
            self.check("forensic: missing signing key fails before imaging",
                       p.returncode != 0 and "dc3dd if=" not in calls)

            p, ev, calls, coc = run("overlap", "plain", WIPE_TARGET="1", SOURCE_DEV=loop, TARGET_DEV=loop)
            self.check("forensic: wipe refuses target == source", p.returncode != 0
                       and "overlaps source" in coc and "dc3dd if=" not in calls)
        finally:
            subprocess.run(["blockdev", "--setrw", loop])
            subprocess.run(["losetup", "-d", loop])

    def _cis_cases(self, tmp: str) -> None:
        """Run CIS blocks against a scratch /etc with system commands stubbed."""
        root = tempfile.mkdtemp(prefix="cis.", dir=tmp)
        bindir = os.path.join(root, "bin")
        os.makedirs(bindir)
        calls = os.path.join(root, "calls")
        for name in ("sshd", "systemctl", "apt-get", "augenrules"):
            path = os.path.join(bindir, name)
            with open(path, "w") as fh:
                fh.write('#!/bin/bash\necho "{0} $*" >> "$CALLS"\n{1}'.format(
                    name, "exit ${SSHD_RC:-0}\n" if name == "sshd" else ""))
            os.chmod(path, 0o755)
        etc = os.path.join(root, "etc")
        files = {
            "os-release": 'ID=debian\nVERSION="12 (bookworm)"\n',
            "ssh/sshd_config": "PasswordAuthentication yes\n",
            "chrony/chrony.conf": ("pool ntp.ubuntu.com        iburst maxsources 4\n"
                                   "server time.cloudflare.com iburst nts\n"),
            "pam.d/common-password": "password [success=1 default=ignore] pam_unix.so obscure yescrypt\n",
            "pam.d/common-auth": "auth required pam_unix.so\n",
        }
        for rel, body in files.items():
            os.makedirs(os.path.dirname(os.path.join(etc, rel)), exist_ok=True)
            with open(os.path.join(etc, rel), "w") as fh:
                fh.write(body)
        blocks = {t.id: t.implementation.replace("/etc/", etc + "/").replace("/var/", root + "/var/")
                  for t in _RUBRICS["cis_hardening"].transforms}
        env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], CALLS=calls)

        def run(code: str, **extra):
            return subprocess.run(["bash", "-c", code], env=dict(env, **extra), stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=60)

        p = run(blocks["DT-SSHD-DROPIN"] + "\nfalse\necho USER-CODE-RAN\n")
        self.check("cis: sshd block does not leak set -e", "USER-CODE-RAN" in p.stdout, p.stderr[-200:])
        dropin = os.path.join(etc, "ssh/sshd_config.d/00-stig.conf")
        p = run(blocks["DT-SSHD-DROPIN"] + "\necho USER-CODE-RAN\n", SSHD_RC="1")
        self.check("cis: rejected sshd drop-in removed, script continues",
                   not os.path.exists(dropin) and "USER-CODE-RAN" in p.stdout and "sshd -t" in p.stderr)
        shutil.move(os.path.join(etc, "ssh"), os.path.join(etc, "ssh.off"))
        p = run(blocks["DT-SSHD-DROPIN"] + "\necho USER-CODE-RAN\n")
        shutil.move(os.path.join(etc, "ssh.off"), os.path.join(etc, "ssh"))
        self.check("cis: no sshd installed -> skipped", p.returncode == 0 and "USER-CODE-RAN" in p.stdout)

        run(blocks["DT-CHRONY"] + "\n" + blocks["DT-CHRONY"])
        conf = open(os.path.join(etc, "chrony/chrony.conf")).read().splitlines()
        self.check("cis: chrony keeps source options, idempotent",
                   conf[:2] == ["pool ntp.ubuntu.com maxsources 4 iburst maxpoll 16",
                                "server time.cloudflare.com nts iburst maxpoll 16"]
                   and conf.count("port 0") == 1 and "systemctl restart chrony" in open(calls).read(), str(conf))

        p = run(blocks["DT-PAM-STACK"] + '\necho "VERSION=$VERSION"\n', VERSION="myapp-1.0")
        self.check("cis: PAM on Debian edits common-password, keeps VERSION",
                   "VERSION=myapp-1.0" in p.stdout and "system-auth" not in p.stderr
                   and "remember=24" in open(os.path.join(etc, "pam.d/common-password")).read(), p.stderr[-200:])

        finalize = os.path.join(etc, "audit/rules.d/99-finalize.rules")
        run(blocks["DT-AUDIT-RULES"])
        mutable = not os.path.exists(finalize)
        run(blocks["DT-AUDIT-RULES"], AUDIT_IMMUTABLE="1")
        self.check("cis: audit -e 2 only with AUDIT_IMMUTABLE=1",
                   mutable and os.path.exists(finalize) and open(finalize).read() == "-e 2\n")

    def run(self) -> int:
        self.golden_suite()
        self.rewrite_suite()
        self.runtime_suite()
        print("\n{0} passed, {1} failed".format(self.passed, self.failed))
        return 1 if self.failed else 0


if __name__ == "__main__":
    sys.exit(Runner(update="--update" in sys.argv).run())
