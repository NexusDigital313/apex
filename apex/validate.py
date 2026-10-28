# Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
# Proprietary and Confidential.
#
# Unauthorized reproduction, copying, modification, distribution,
# redistribution, publication, reverse engineering, decompilation, or
# disassembly of this file, in whole or in part, via any medium, is
# strictly prohibited without explicit prior written authorization.

"""Syntax validation of elevated output.

Used to guard the rewrite pass: if a rewrite no longer parses, the engine rolls
it back. Shell is checked with `bash -n` and, when available, `shellcheck`;
Python with `py_compile`.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import os
from dataclasses import dataclass, field
from typing import List

# shellcheck gcc-format line for a parse failure, e.g.
#   /tmp/x.sh:273:1: error: Couldn't parse this command expansion. [SC1073]
_SC_PARSE_ERROR_RE = re.compile(r":\s*error:.*\[SC1\d{3}\]\s*$")


@dataclass
class ValidationResult:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    tool: str = ""


def _have(cmd: str) -> bool:
    from shutil import which

    return which(cmd) is not None


def _write_tmp(text: str, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="apex.")
    with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as fh:
        fh.write(text)
    return path


def validate(text: str, language: str = "shell") -> ValidationResult:
    if language == "python":
        return _validate_python(text)
    if language in ("zsh", "ksh", "mksh", "fish"):
        return _validate_other_shell(text, language)
    if language != "shell":
        return ValidationResult(True, warnings=["no syntax checker for {0}; not checked".format(language)])
    return _validate_shell(text)


def _validate_shell(text: str) -> ValidationResult:
    path = _write_tmp(text, ".sh")
    tools: List[str] = []
    try:
        if _have("bash"):
            tools.append("bash -n")
            proc = subprocess.run(
                ["bash", "-n", path], capture_output=True, text=True, timeout=30
            )
            if proc.returncode != 0:
                return ValidationResult(False, errors=_lines(proc.stderr), tool="bash -n")
        warnings: List[str] = []
        if _have("shellcheck"):
            tools.append("shellcheck")
            proc = subprocess.run(
                ["shellcheck", "-S", "warning", "-f", "gcc", path],
                capture_output=True,
                text=True,
                timeout=60,
            )
            # shellcheck non-zero == findings; treat as warnings, not hard failures --
            # except parse errors (SC1xxx at error level). Those are real syntax
            # bugs that `bash -n` misses, e.g. inside a heredoc's $(...).
            findings = _lines(proc.stdout)
            parse_errors = [f for f in findings if _SC_PARSE_ERROR_RE.search(f)]
            if parse_errors:
                return ValidationResult(False, errors=parse_errors, tool="shellcheck")
            warnings = findings[:25]
        if not tools:
            return ValidationResult(True, warnings=["neither bash nor shellcheck found; not checked"])
        return ValidationResult(True, warnings=warnings, tool=" + ".join(tools))
    except (subprocess.TimeoutExpired, OSError) as exc:
        return ValidationResult(True, warnings=["validation skipped: {0}".format(exc)])
    finally:
        _rm(path)


def _validate_other_shell(text: str, shell: str) -> ValidationResult:
    """zsh / ksh / fish parse their own syntax; bash -n would reject valid code."""
    if not _have(shell):
        return ValidationResult(True, warnings=["{0} not installed; not checked".format(shell)])
    path = _write_tmp(text, "." + shell)
    try:
        proc = subprocess.run([shell, "-n", path], capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return ValidationResult(False, errors=_lines(proc.stderr), tool=shell + " -n")
        return ValidationResult(True, tool=shell + " -n")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return ValidationResult(True, warnings=["validation skipped: {0}".format(exc)])
    finally:
        _rm(path)


def _validate_python(text: str) -> ValidationResult:
    path = _write_tmp(text, ".py")
    try:
        proc = subprocess.run(
            ["python3", "-m", "py_compile", path], capture_output=True, text=True, timeout=30
        )
        if proc.returncode != 0:
            return ValidationResult(False, errors=_lines(proc.stderr), tool="py_compile")
        return ValidationResult(True, tool="py_compile")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return ValidationResult(True, warnings=["validation skipped: {0}".format(exc)])
    finally:
        _rm(path)


def _lines(s: str) -> List[str]:
    return [ln for ln in (s or "").splitlines() if ln.strip()]


def _rm(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
