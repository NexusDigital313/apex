<!--
  Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
  Proprietary and Confidential.

  Unauthorized reproduction, copying, modification, distribution,
  redistribution, publication, reverse engineering, decompilation, or
  disassembly of this file, in whole or in part, via any medium, is
  strictly prohibited without explicit prior written authorization.
-->

# APEX — elevate any script to its apex standard

APEX takes an executable script, works out *what kind* of script it is, and rewrites it
up to the highest known standard for that class:

| If it's…                         | APEX takes it to…                                                        |
|----------------------------------|--------------------------------------------------------------------------|
| a **CIS / hardening** script     | **DISA STIG** — FIPS crypto, STIG audit rules, faillock, DoD banner, CAT I/II/III |
| a **forensic / acquisition** script | **courtroom-admissible** — chain of custody, dual-hash + verify, write-block, `dc3dd`, GPG seal (FRE 901/902, Daubert, NIST SP 800-86, ACPO, RFC 3227) |
| an **OS-install / provisioning** script | **fully unattended *or* interactive** — preflight, destroy-guards, kickstart/preseed/autoinstall/pacstrap/AutoYaST, LUKS, secure post-install |
| **anything else**                | **production-grade** — strict mode, ERR/EXIT traps, logging, preflight, locking, CLI ergonomics |

## How it works — two engines

1. **Deterministic engine** (always on, zero dependencies, fully offline). Classifies the
   script, then injects clearly-marked, idempotent blocks from the domain *rubric* — the
   chain-of-custody header, the STIG hardening library, the installer preflight + TUI, etc.
   Re-running is a no-op (every block is fenced with an `APEX[<id>]` marker). The output is
   always syntax-checked (`bash -n` + `shellcheck`).
2. **LLM engine** (when `ANTHROPIC_API_KEY` is set). The deterministically pre-hardened
   script plus the full rubric go to **Claude Opus 4.8**, which does the intelligent,
   behaviour-preserving rewrite — applying *every* automatable control, integrating rather
   than appending, and returning a control-by-control changelog. If its output fails the
   syntax gate, APEX falls back to the deterministic result.

The "exponential logic" lives in `rubrics/*.json` — each rubric is the encoded definition of
the apex standard for one domain (controls with real check/fix pairs + STIG IDs/CCIs,
injectable shell, and the LLM's rewrite guidance). Edit the JSON to tune a standard; no code
changes needed.

## Quick start

```bash
cd ~/Projects/apex

# what would it do?
python3 apex.py check examples/acquire.sh

# elevate it (offline, deterministic) -> writes acquire.apex.sh + acquire.apex.report.md
python3 apex.py elevate examples/acquire.sh

# elevate with the intelligent rewrite (needs the SDK + key)
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 apex.py elevate examples/cis-baseline.sh --mode llm --diff
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
| `--mode auto` (default) | use the LLM if `ANTHROPIC_API_KEY` is set, else deterministic |
| `--mode deterministic`  | offline rule engine only |
| `--mode llm`            | deterministic pre-pass, then Claude rewrite |
| `-o <path>`             | output path (default `<name>.apex.<ext>`) |
| `--report <path>`       | report path (`-` to skip) |
| `--in-place`            | overwrite the input (keeps `<name>.apex.bak`) |
| `--stdout`              | print elevated script to stdout (status goes to stderr) |
| `--diff`                | also print a unified diff |

## What you get

- the **elevated script** (executable, syntax-validated)
- an **elevation report** (`*.report.md`) — detected domain, target standard, every control
  applied (with rationale), residual risks, manual follow-ups, and the rubric's references

## Layout

```
apex.py                 entrypoint
apex/
  classify.py           weighted-signature domain classifier (+ rubrics/classifier.json)
  rubrics.py            rubric model + loader
  engine_deterministic.py   safe, idempotent, offline injection pass
  engine_llm.py         Claude Opus 4.8 intelligent rewrite (output_config JSON contract, cached rubric, streamed)
  validate.py           bash -n / shellcheck / py_compile syntax gate
  report.py             elevation report + unified diff
  cli.py                orchestration
rubrics/*.json          the encoded apex standards (CIS->STIG, forensic, installer, generic)
examples/*.sh           sample before-scripts
```

> Deterministic mode targets shell. For Python scripts, run with `ANTHROPIC_API_KEY` set so
> the LLM engine handles the rewrite.
