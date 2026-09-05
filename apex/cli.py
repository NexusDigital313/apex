# Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
# Proprietary and Confidential.
#
# Unauthorized reproduction, copying, modification, distribution,
# redistribution, publication, reverse engineering, decompilation, or
# disassembly of this file, in whole or in part, via any medium, is
# strictly prohibited without explicit prior written authorization.

"""APEX command-line interface.

    apex elevate <script> [--mode auto|deterministic|llm] [options]
    apex check   <script>
    apex list
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional

from . import __version__
from . import classify as classify_mod
from . import engine_deterministic as det
from . import engine_llm as llm
from . import report as report_mod
from . import validate as validate_mod
from .rubrics import Rubric, load_rubrics


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


class C:
    def __init__(self, on: bool):
        self.on = on

    def _w(self, code: str, s: str) -> str:
        return "\033[{0}m{1}\033[0m".format(code, s) if self.on else s

    def bold(self, s: str) -> str:
        return self._w("1", s)

    def green(self, s: str) -> str:
        return self._w("32", s)

    def yellow(self, s: str) -> str:
        return self._w("33", s)

    def red(self, s: str) -> str:
        return self._w("31", s)

    def cyan(self, s: str) -> str:
        return self._w("36", s)

    def dim(self, s: str) -> str:
        return self._w("2", s)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _pick_rubric(rubrics: Dict[str, Rubric], domain: str) -> Optional[Rubric]:
    if domain in rubrics:
        return rubrics[domain]
    return rubrics.get("generic")


def cmd_list(args: argparse.Namespace) -> int:
    c = C(_supports_color())
    rubrics = load_rubrics()
    if not rubrics:
        print(c.red("no rubrics found in rubrics/"))
        return 1
    print(c.bold("APEX domains / apex standards:"))
    print()
    for key, r in rubrics.items():
        print("  {0:<16} {1}".format(c.cyan(key), c.bold(r.target_standard)))
        print("  {0}{1}".format(" " * 16, c.dim(r.summary)))
        print("  {0}{1}".format(" " * 16, c.dim("{0} controls, {1} deterministic transforms".format(
            len(r.controls), len(r.transforms)))))
        print()
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    c = C(_supports_color())
    text = _read(args.script)
    cls = classify_mod.classify(text, args.script)
    rubrics = load_rubrics()
    rubric = _pick_rubric(rubrics, cls.domain)

    print(c.bold("APEX classification"))
    print("  file      : {0}".format(args.script))
    print("  language  : {0}".format(cls.language))
    print("  domain    : {0} {1}".format(c.cyan(cls.domain), c.dim("(confidence {0})".format(cls.confidence))))
    if rubric:
        print("  -> elevate to: {0}".format(c.bold(rubric.target_standard)))
        print("  rubric    : {0} controls ({1} automatable), {2} transforms".format(
            len(rubric.controls), len(rubric.automatable_controls), len(rubric.transforms)))
    print("  scores    :")
    for d, s in sorted(cls.scores.items(), key=lambda kv: -kv[1]):
        print("              {0:<16} {1}".format(d, s))
    if cls.matched:
        print("  matched   : {0}".format(", ".join(cls.matched[:12])))
    return 0


def cmd_elevate(args: argparse.Namespace) -> int:
    c = C(_supports_color())
    # Status goes to stderr so --stdout / --diff produce clean machine-usable output.
    def emit(s: str = "") -> None:
        print(s, file=sys.stderr)

    original = _read(args.script)
    cls = classify_mod.classify(original, args.script)
    rubrics = load_rubrics()
    rubric = _pick_rubric(rubrics, cls.domain)
    if rubric is None:
        emit(c.red("no rubric available for domain '{0}' (and no generic fallback)".format(cls.domain)))
        return 1

    emit(c.bold("APEX"))
    emit("  {0} -> {1} {2}".format(
        c.cyan(cls.domain), c.bold(rubric.target_standard),
        c.dim("(confidence {0})".format(cls.confidence))))

    mode = args.mode
    llm_ok = llm.available()
    if mode == "auto":
        mode = "llm" if llm_ok else "deterministic"
    if mode == "llm" and not llm_ok:
        emit(c.red("  llm mode requested but ANTHROPIC_API_KEY is not set"))
        return 2

    # Always run the deterministic pre-pass first (safe, idempotent baseline).
    det_res = det.elevate(original, rubric, cls.language)
    elevated = det_res.elevated
    engine_label = "deterministic"
    changelog: List[Dict] = []
    residual: List[str] = []
    followups: List[str] = []
    for note in det_res.notes:
        emit(c.dim("  note: {0}".format(note)))

    if mode == "llm":
        emit(c.dim("  invoking {0} for the intelligent rewrite...".format(llm.MODEL)))
        try:
            llm_res = llm.elevate(elevated, rubric, cls.language)
        except llm.LLMUnavailable as exc:
            emit(c.yellow("  llm pass unavailable ({0}); keeping deterministic result".format(exc)))
        else:
            v = validate_mod.validate(llm_res.elevated, cls.language)
            if v.ok:
                elevated = llm_res.elevated
                engine_label = "deterministic + {0}".format(llm.MODEL)
                changelog = llm_res.changelog
                residual = llm_res.residual_risks
                followups = llm_res.manual_followups
            else:
                emit(c.yellow("  llm output failed syntax check; keeping deterministic result"))
                for e in v.errors[:5]:
                    emit(c.dim("    {0}".format(e)))

    # Validate final result.
    final_val = validate_mod.validate(elevated, cls.language)
    if not final_val.ok:
        emit(c.red("  WARNING: elevated script failed syntax validation:"))
        for e in final_val.errors[:8]:
            emit(c.dim("    {0}".format(e)))

    # Build report.
    report = report_mod.build_report(
        cls, rubric, engine_label,
        applied_transforms=det_res.applied,
        changelog=changelog, residual_risks=residual, manual_followups=followups,
        validation_tool=final_val.tool, validation_warnings=final_val.warnings,
    )

    # Output.
    if args.stdout:
        sys.stdout.write(elevated)
    else:
        out_path = args.output or _default_out(args.script)
        if args.in_place:
            _backup(args.script)
            out_path = args.script
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(elevated)
        _chmod_x(out_path, original)
        emit(c.green("  elevated -> {0}".format(out_path)))

        report_path = args.report or (_strip_ext(out_path) + ".report.md")
        if args.report != "-":
            with open(report_path, "w", encoding="utf-8") as fh:
                fh.write(report)
            emit(c.green("  report   -> {0}".format(report_path)))

    if args.diff:
        diff = report_mod.unified_diff(original, elevated, os.path.basename(args.script))
        sys.stdout.write("\n" + _colorize_diff(diff, c) + "\n")

    n_changes = len(changelog) or len(det_res.applied)
    emit(c.bold("  done: {0} change(s), engine={1}{2}".format(
        n_changes, engine_label, ", syntax OK" if final_val.ok else ", SYNTAX ERRORS")))
    return 0 if final_val.ok else 1


def _default_out(path: str) -> str:
    stem, ext = os.path.splitext(path)
    return "{0}.apex{1}".format(stem, ext or ".sh")


def _strip_ext(path: str) -> str:
    return os.path.splitext(path)[0]


def _backup(path: str) -> None:
    bak = path + ".apex.bak"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as src, open(bak, "w", encoding="utf-8") as dst:
            dst.write(src.read())
    except OSError:
        pass


def _chmod_x(out_path: str, original_text: str) -> None:
    # keep it executable if it has a shebang
    if original_text.startswith("#!"):
        try:
            os.chmod(out_path, 0o755)
        except OSError:
            pass


def _colorize_diff(diff: str, c: C) -> str:
    lines = []
    for ln in diff.splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            lines.append(c.green(ln))
        elif ln.startswith("-") and not ln.startswith("---"):
            lines.append(c.red(ln))
        elif ln.startswith("@@"):
            lines.append(c.cyan(ln))
        else:
            lines.append(ln)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apex",
        description="Elevate any executable script to its apex standard "
                    "(CIS->STIG, forensic->courtroom, installer->unattended, *->production-grade).",
    )
    p.add_argument("--version", action="version", version="apex {0}".format(__version__))
    sub = p.add_subparsers(dest="command")

    pe = sub.add_parser("elevate", help="elevate a script to its apex standard")
    pe.add_argument("script")
    pe.add_argument("--mode", choices=["auto", "deterministic", "llm"], default="auto",
                    help="auto (default): use the LLM if ANTHROPIC_API_KEY is set, else deterministic")
    pe.add_argument("-o", "--output", help="write elevated script here (default: <name>.apex.<ext>)")
    pe.add_argument("--report", help="write report here ('-' to skip; default <out>.report.md)")
    pe.add_argument("--in-place", action="store_true", help="overwrite the input (keeps <name>.apex.bak)")
    pe.add_argument("--stdout", action="store_true", help="print elevated script to stdout, write nothing")
    pe.add_argument("--diff", action="store_true", help="also print a unified diff")
    pe.set_defaults(func=cmd_elevate)

    pc = sub.add_parser("check", help="classify a script and show the target standard (no rewrite)")
    pc.add_argument("script")
    pc.set_defaults(func=cmd_check)

    pl = sub.add_parser("list", help="list domains and their apex standards")
    pl.set_defaults(func=cmd_list)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print("apex: {0}".format(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
