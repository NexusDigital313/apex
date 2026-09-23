#!/usr/bin/env python3
"""APEX test harness — pure stdlib, zero deps (matches the tool's own ethos).

Locks the invariants the whole project rests on. For every example script:
  1. GOLDEN      elevate(script) matches tests/golden/<name>.apex.sh
  2. IDEMPOTENT  elevate(elevate(script)) == elevate(script)   (a re-run is a no-op)
  3. VALID       the elevated script passes bash -n + shellcheck

Plus focused rewrite-safety cases: a single-target `rm -rf` is rewritten to
safe_rm, while multi-target / glob / command-substitution / heredoc-body forms
are left untouched (the transformer must fail safe, never silently corrupt).

Usage:
    python3 tests/run.py            # run all checks, non-zero exit on failure
    python3 tests/run.py --update   # regenerate the golden files, then run
"""

from __future__ import annotations

import os
import sys

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

    def run(self) -> int:
        self.golden_suite()
        self.rewrite_suite()
        print("\n{0} passed, {1} failed".format(self.passed, self.failed))
        return 1 if self.failed else 0


if __name__ == "__main__":
    sys.exit(Runner(update="--update" in sys.argv).run())
