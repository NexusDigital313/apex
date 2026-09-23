# APEX — elevate any script to its apex standard

APEX takes an executable script, works out *what kind* of script it is, and elevates it
to the highest known standard for that class — **fully offline, deterministically, with no
API key and zero third-party dependencies.**

| If it's…                         | APEX takes it to…                                                        |
|----------------------------------|--------------------------------------------------------------------------|
| a **CIS / hardening** script     | **DISA STIG** — FIPS crypto, STIG audit rules, faillock, DoD banner, CAT I/II/III |
| a **forensic / acquisition** script | **courtroom-admissible** — chain of custody, dual-hash + verify, write-block, `dc3dd`, GPG seal (FRE 901/902, Daubert, NIST SP 800-86, ACPO, RFC 3227) |
| an **OS-install / provisioning** script | **fully unattended *or* interactive** — preflight, destroy-guards, kickstart/preseed/autoinstall/pacstrap/AutoYaST, LUKS, secure post-install |
| **anything else**                | **production-grade** — strict mode, ERR/EXIT traps, logging, preflight, locking, CLI ergonomics |

## How it works — one self-driving engine

APEX has a single **deterministic, self-driving engine**. It:

1. **Classifies** the script (weighted signatures → a domain rubric).
2. **Reads the script's own content** and, for each block in the rubric, decides whether the
   script actually *needs* it — via a machine-evaluable `apply_when` gate (present/absent/
   any-present regex conditions). This is what makes it "self-driving": the number and choice
   of changes track the script in front of it, not a fixed constant. A raw script gets the
   full treatment; a script that already has strict mode and traps gets only what it's missing.
3. **Injects** only the needed blocks — clearly marked and idempotent. Every block is fenced
   with an `APEX[<id>]` marker, and gates are evaluated against your *original* code (APEX's
   own injected blocks are stripped before matching), so **re-running is a guaranteed no-op**.
4. **Rewrites the script's own lines** toward the standard so the injected helpers are actually
   *used*, not merely present — e.g. `rm -rf X` → `safe_rm X`, and legacy `` `…` `` →
   `$(…)`. Rewrites are conservative and **fail safe**: they fire only on real code (a thin
   lexer skips APEX blocks, heredoc bodies, quoted strings and comments), decline anything
   ambiguous (multi-target / glob / command-substitution), and every rewrite is parsed as part
   of the whole script and **rolled back** if it would break it.
5. **Validates** the result (`bash -n` / `shellcheck` / `py_compile`) before writing.

Every run prints a **decision trace** — exactly what APEX detected and why it applied, rewrote
or skipped each rule — and writes the same trace into the report:

```
  scanned 8 rubric block(s):
    ＋ DT-BASH-STRICT-HEADER      missing `set -euo pipefail`
    ＋ DT-BASH-LOG-TRAP-SCAFFOLD  missing `trap ... (ERR|EXIT)`
    ＋ DT-BASH-ARGPARSE-SKELETON  missing `getopts`
    ＋ DT-BASH-DESTRUCTIVE-GUARD  uses `rm -rf`; missing `safe_rm()`
    ↻ RW-BASH-RM-SAFE            1 site(s): route single-target `rm -rf` through safe_rm()
    ↻ RW-BASH-BACKTICKS          1 site(s): modernise `…` to $(…)
    － DT-BASH-CURL-HARDENING     no matching trigger pattern present
  done: 4 change(s), 2 rewrite(s), 4 block(s) not needed, engine=deterministic (self-driving)
```

The standards live in `rubrics/*.json` — each rubric is the encoded definition of the apex
standard for one domain (controls with real check/fix pairs + STIG IDs/CCIs, injectable shell
blocks and in-place rewrite rules, each carrying an `apply_when` gate). Edit the JSON to tune a
standard or add a new content-triggered block or rewrite; no code changes needed.

## Quick start

```bash
cd ~/Downloads/apex          # no install step, no dependencies — Python 3.8+ stdlib only

# what would it do?
python3 apex.py check examples/acquire.sh

# elevate it -> writes acquire.apex.sh + acquire.apex.report.md
python3 apex.py elevate examples/acquire.sh

# see the change trace + a unified diff, write nothing
python3 apex.py elevate examples/cis-baseline.sh --stdout --diff
```

## Commands

```
apex check   <script>            classify + show the target standard (no rewrite)
apex elevate <script> [opts]     elevate to the apex standard
apex list                        list domains and their apex standards
```

`elevate` options:

| flag | meaning |
|---|---|
| `-o <path>`       | output path (default `<name>.apex.<ext>`) |
| `--report <path>` | report path (`-` to skip) |
| `--in-place`      | overwrite the input (keeps `<name>.apex.bak`) |
| `--stdout`        | print elevated script to stdout (status + trace go to stderr) |
| `--diff`          | also print a unified diff |

## What you get

- the **elevated script** (executable, syntax-validated)
- an **elevation report** (`*.report.md`) — detected domain, target standard, the
  self-driving decision trace (applied / rewritten / already-present / not-needed / declined,
  with the reason for each), the full rubric control table, and references

## Layout

```
apex.py                 entrypoint
apex/
  classify.py           weighted-signature domain classifier (+ rubrics/classifier.json)
  rubrics.py            rubric + transform + rewrite model with the apply_when self-driving gate
  engine_deterministic.py   two-pass engine: block injection + in-place rewrite (lexer + rollback)
  validate.py           bash -n / shellcheck / py_compile syntax gate
  report.py             elevation report + decision trace + unified diff
  banner.py             the startup banner
  cli.py                orchestration
rubrics/*.json          the encoded apex standards (CIS->STIG, forensic, installer, generic)
examples/*.sh           sample before-scripts
tests/run.py            invariant harness: golden + idempotency + validity + rewrite-safety
```

> The deterministic engine targets shell for block injection and rewrites. For other languages
> APEX still classifies the script and reports the applicable control rubric, but makes no edits.
