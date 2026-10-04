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

        # multi-line double-quoted string body must be preserved
        ml = 'M="danger:\nrm -rf /\nend"\n'
        self.check("multiline-string body preserved", body_has(ml, "rm -rf /"))

        # backticks -> $(...), but NOT inside a single-quoted (literal) string
        self.check("backticks -> $()", body_has('d=`date +%F`\n', "d=$(date +%F)"))
        self.check("backticks in '' preserved", body_has("echo '`literal`'\n", "'`literal`'"))

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
            harness = ("blkid(){ echo 1111-AAAA; }; openssl(){ echo HASH; }\n"
                       "f(){ local disk=/dev/sda vg=vg0 hooks=x\n" + snippet + "\n}\nf\n")
            for fw in ("uefi", "bios"):
                for enc in ("0", "1"):
                    env = dict(os.environ, FIRMWARE=fw, ENCRYPT=enc, CRYPT_PART="/dev/sda2",
                               HOSTNAME="h", USERNAME="u", USER_PW="p", SSH_PUBKEY="k")
                    out = subprocess.run(["bash", "-euc", harness], env=env, capture_output=True,
                                         text=True, timeout=30)
                    parsed = subprocess.run(["bash", "-n"], input=out.stdout, capture_output=True,
                                            text=True, timeout=30)
                    want_crypt = ("cryptdevice=UUID=1111-AAAA" in out.stdout) == (enc == "1")
                    self.check("arch chroot script[{0},encrypt={1}]".format(fw, enc),
                               out.returncode == 0 and parsed.returncode == 0 and want_crypt,
                               (out.stderr or parsed.stderr).strip()[-200:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def run(self) -> int:
        self.golden_suite()
        self.rewrite_suite()
        self.runtime_suite()
        print("\n{0} passed, {1} failed".format(self.passed, self.failed))
        return 1 if self.failed else 0


if __name__ == "__main__":
    sys.exit(Runner(update="--update" in sys.argv).run())
