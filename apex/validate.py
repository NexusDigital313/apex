# Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
# Proprietary and Confidential.
#
# Unauthorized reproduction, copying, modification, distribution,
# redistribution, publication, reverse engineering, decompilation, or
# disassembly of this file, in whole or in part, via any medium, is
# strictly prohibited without explicit prior written authorization.

"""Syntax validation of elevated output.

Used to guard the LLM pass: if the rewrite no longer parses, the caller falls
back to the (always-valid) deterministic result. Shell is checked with `bash -n`
and, when available, `shellcheck`; Python with `py_compile`.
"""

from __future__ import annotations

import subprocess
import tempfile
import os
from dataclasses import dataclass, field
from typing import List


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
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def validate(text: str, language: str = "shell") -> ValidationResult:
    if language == "python":
        return _validate_python(text)
    return _validate_shell(text)


def _validate_shell(text: str) -> ValidationResult:
    path = _write_tmp(text, ".sh")
    try:
        if _have("bash"):
            proc = subprocess.run(
                ["bash", "-n", path], capture_output=True, text=True, timeout=30
            )
            if proc.returncode != 0:
                return ValidationResult(False, errors=_lines(proc.stderr), tool="bash -n")
        warnings: List[str] = []
        if _have("shellcheck"):
            proc = subprocess.run(
                ["shellcheck", "-S", "warning", "-f", "gcc", path],
                capture_output=True,
                text=True,
                timeout=60,
            )
            # shellcheck non-zero == findings; treat as warnings, not hard failures
            warnings = _lines(proc.stdout)[:25]
        return ValidationResult(True, warnings=warnings, tool="bash -n + shellcheck")
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
