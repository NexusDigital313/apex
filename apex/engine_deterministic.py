# Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
# Proprietary and Confidential.
#
# Unauthorized reproduction, copying, modification, distribution,
# redistribution, publication, reverse engineering, decompilation, or
# disassembly of this file, in whole or in part, via any medium, is
# strictly prohibited without explicit prior written authorization.

"""Deterministic elevation engine.

Safe, offline, idempotent. It does NOT rewrite existing lines (that is the LLM
engine's job) — it injects clearly-marked blocks from the rubric: a domain
preamble right after the shebang, and any append-blocks at the end of the file.
Re-running is a no-op because every block is fenced with an `APEX[<id>]` marker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from .rubrics import Rubric, Transform

DEFAULT_SHEBANG = "#!/usr/bin/env bash"


@dataclass
class DeterministicResult:
    elevated: str
    applied: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _marker_open(t: Transform) -> str:
    return "# >>> APEX[{0}] {1} >>>".format(t.id, t.description)


def _marker_close(t: Transform) -> str:
    return "# <<< APEX[{0}] <<<".format(t.id)


def _wrap(t: Transform) -> str:
    return "{0}\n{1}\n{2}".format(_marker_open(t), t.implementation, _marker_close(t))


def _split_shebang(text: str) -> Tuple[str, str]:
    """Return (shebang_line_without_newline, rest)."""
    if text.startswith("#!"):
        nl = text.find("\n")
        if nl == -1:
            return text, ""
        return text[:nl], text[nl + 1 :]
    return DEFAULT_SHEBANG, text


def _needs_bash(shebang: str) -> bool:
    """The injected blocks use bash features (local, set -E, declare -A, printf -v).
    A POSIX sh / dash shebang must be upgraded for them to be valid."""
    s = shebang.strip()
    return bool(s) and "bash" not in s and s.rstrip("/").endswith(("sh", "dash"))


def elevate(text: str, rubric: Rubric, language: str = "shell") -> DeterministicResult:
    result = DeterministicResult(elevated=text)

    if language != "shell":
        result.notes.append(
            "deterministic transforms target shell; for {0} scripts run with an "
            "ANTHROPIC_API_KEY for the intelligent (LLM) pass".format(language)
        )
        return result

    shebang, body = _split_shebang(text)
    preamble_blocks: List[str] = []
    append_blocks: List[str] = []
    bash_upgrade = False

    for t in rubric.transforms:
        marker_id = "APEX[{0}]".format(t.id)
        if marker_id in text:
            result.skipped.append(t.id)
            continue
        block = _wrap(t)
        if t.placement == "append":
            append_blocks.append(block)
        else:
            preamble_blocks.append(block)
        result.applied.append(t.id)

    if not result.applied:
        result.notes.append("script already carries all APEX deterministic blocks (no-op)")
        result.elevated = text
        return result

    if _needs_bash(shebang):
        result.notes.append(
            "upgraded shebang '{0}' -> '{1}' (preamble uses bash features)".format(
                shebang.strip(), DEFAULT_SHEBANG)
        )
        shebang = DEFAULT_SHEBANG
        bash_upgrade = True

    parts: List[str] = [shebang.rstrip("\n")]
    if preamble_blocks:
        parts.append("")
        parts.append("# ===== APEX elevation preamble :: {0} =====".format(rubric.target_standard))
        parts.append("\n\n".join(preamble_blocks))
        parts.append("# ===== end APEX preamble =====")
    parts.append("")
    parts.append(body.rstrip("\n"))
    if append_blocks:
        parts.append("")
        parts.append("# ===== APEX appended helpers =====")
        parts.append("\n\n".join(append_blocks))

    result.elevated = "\n".join(p for p in parts if p is not None).rstrip("\n") + "\n"
    return result
