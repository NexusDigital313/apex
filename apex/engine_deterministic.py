"""Deterministic, self-driving elevation engine.

Safe, offline, idempotent, and content-aware. It reads the target script and
runs two passes:
  1. INJECT — add the rubric blocks the script is missing (a domain preamble
     after the shebang, append-blocks at the end).
  2. REWRITE — transform the script's OWN lines toward the standard (e.g.
     `rm -rf X` -> `safe_rm X`), so injected helpers are actually used rather
     than merely present.

Four guards keep this safe and repeatable:
  1. Idempotency: every block is fenced with an `APEX[<id>]` marker and never
     re-added; rewrite patterns match only the original form, so a re-run is a
     true no-op.
  2. Self-driving gate: each transform/rewrite carries an `apply_when`
     condition; it fires only when the script's content shows it is needed.
  3. Region protection: rewrites are applied only to real user code —
     APEX-injected blocks and heredoc bodies are skipped (see _protected_spans),
     so the engine never edits generated code or here-document text.
  4. Validate-and-rollback: every rewrite is parsed (bash -n + shellcheck) as
     part of the whole script; any rewrite that would break it is reverted and
     reported as "declined" rather than emitted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .rubrics import Rewrite, Rubric, Transform

DEFAULT_SHEBANG = "#!/usr/bin/env bash"

# Matches a whole APEX-injected block (fence to fence) and the section headers
# APEX emits, so the self-driving gate never sees APEX's own text as if it were
# the user's script — that keeps re-runs a true no-op.
_APEX_BLOCK_RE = re.compile(
    r"^# >>> APEX\[.*?\].*?>>>\n.*?^# <<< APEX\[.*?\] <<<\n?",
    re.DOTALL | re.MULTILINE,
)
_APEX_HEADER_RE = re.compile(r"^# ===== (?:end )?APEX .*=====\n?", re.MULTILINE)


def _user_code(text: str) -> str:
    """The script with APEX-injected blocks/headers removed — what the gate scans."""
    stripped = _APEX_BLOCK_RE.sub("", text)
    return _APEX_HEADER_RE.sub("", stripped)


@dataclass
class Decision:
    """One transform's outcome, for the report and the self-driving narration."""
    id: str
    verdict: str  # "applied" | "already-present" | "not-needed"
    reason: str


@dataclass
class DeterministicResult:
    elevated: str
    applied: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)       # marker already in file
    not_needed: List[str] = field(default_factory=list)    # gate said the script is fine
    rewritten: List[str] = field(default_factory=list)     # rewrite rules that fired
    declined: List[str] = field(default_factory=list)      # rewrites gated/rolled back
    decisions: List[Decision] = field(default_factory=list)
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


# Heredoc opener: `<< WORD`, `<<-WORD`, `<< "WORD"`, `<< 'WORD'`. The `\w+`
# after the optional quote means `<<<` (here-string) is NOT matched — it has no
# body to protect. This is the first brick of the lexer: rewrites must not touch
# text inside a heredoc body (it parses fine, so rollback can't catch a mistake).
_HEREDOC_START_RE = re.compile(r"<<-?\s*(['\"]?)(?P<delim>\w+)\1")


def _heredoc_spans(text: str) -> List[Tuple[int, int]]:
    """(start, end) spans covering each heredoc BODY (delimiter line included)."""
    spans: List[Tuple[int, int]] = []
    for m in _HEREDOC_START_RE.finditer(text):
        nl = text.find("\n", m.end())
        if nl == -1:
            continue
        body_start = nl + 1
        term = re.compile(r"^[ \t]*" + re.escape(m.group("delim")) + r"[ \t]*$", re.MULTILINE)
        tm = term.search(text, body_start)
        spans.append((body_start, tm.end() if tm else len(text)))
    return spans


def _lex_spans(text: str) -> List[Tuple[int, int]]:
    """Thin shell lexer: spans a rewrite must never touch.

    A single left-to-right scan (honouring backslash escapes) that protects:
      - comments        `# …` to end of line (only at a token boundary, so
                        `${#arr}` / `$#` are not mistaken for a comment);
      - single-quoted   '…' — always (content is literal, e.g. a `\\`backtick\\``
                        inside '…' must stay literal);
      - double-quoted   "…" — only when it spans a newline (a multi-line string
                        whose inner lines can masquerade as commands). Single-line
                        "$var" arguments stay live so real code is still rewritten.

    Not a full parser — nested `$( )` and the `'\\''` idiom are left to the
    validate-and-rollback backstop — but it closes the cases where a rewrite
    would otherwise parse cleanly yet corrupt string/comment text.
    """
    spans: List[Tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "#":
            prev = text[i - 1] if i > 0 else "\n"
            if prev in " \t\n;&|(":
                j = text.find("\n", i)
                j = n if j == -1 else j
                spans.append((i, j))
                i = j
                continue
            i += 1
            continue
        if ch == "'":
            j = text.find("'", i + 1)
            j = n if j == -1 else j + 1
            spans.append((i, j))
            i = j
            continue
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            if "\n" in text[i:j]:
                spans.append((i, j))
            i = j
            continue
        i += 1
    return spans


def _protected_spans(text: str) -> List[Tuple[int, int]]:
    """(start, end) regions never rewritten: APEX blocks/headers, heredoc bodies,
    and the comment / single-quoted / multi-line-string spans from the lexer.

    Overlapping spans are coalesced so the segment walk in _sub_user_regions
    stays correct.
    """
    spans = [(m.start(), m.end()) for m in _APEX_BLOCK_RE.finditer(text)]
    spans += [(m.start(), m.end()) for m in _APEX_HEADER_RE.finditer(text)]
    spans += _heredoc_spans(text)
    spans += _lex_spans(text)
    spans.sort()
    merged: List[Tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _sub_user_regions(text: str, rx, repl: str) -> Tuple[str, int]:
    """Apply rx->repl only to the user's code, skipping APEX-injected spans.

    Keeps rewrites off APEX's own blocks (e.g. the `rm -rf` inside a generated
    safe_rm/cleanup trap), which is what makes a re-run a true no-op.
    """
    out: List[str] = []
    i = n = 0
    for start, end in _protected_spans(text):
        seg, cnt = rx.subn(repl, text[i:start])
        out.append(seg)
        out.append(text[start:end])
        n += cnt
        i = end
    seg, cnt = rx.subn(repl, text[i:])
    out.append(seg)
    n += cnt
    return "".join(out), n


def _default_validator(text: str) -> Tuple[bool, str]:
    from . import validate as _v  # local import avoids an import cycle

    r = _v.validate(text, "shell")
    return r.ok, "; ".join(r.errors[:3])


def _render(shebang: str, preamble_blocks: List[str], body: str,
            append_blocks: List[str], rubric: Rubric) -> str:
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
    return "\n".join(p for p in parts if p is not None).rstrip("\n") + "\n"


def elevate(text: str, rubric: Rubric, language: str = "shell",
            validator: Optional[Callable[[str], Tuple[bool, str]]] = None) -> DeterministicResult:
    result = DeterministicResult(elevated=text)

    if language != "shell":
        result.notes.append(
            "deterministic shell transforms do not target {0}; classification and the "
            "control rubric still apply, but no blocks were injected".format(language)
        )
        return result

    validate_fn = validator or _default_validator
    shebang, body = _split_shebang(text)
    # Evaluate every self-driving gate against the user's own code, never
    # against blocks APEX injected on a previous pass.
    scan_text = _user_code(text)
    preamble_blocks: List[str] = []
    append_blocks: List[str] = []

    # ---- pass 1: additive block injection --------------------------------
    for t in rubric.transforms:
        marker_id = "APEX[{0}]".format(t.id)
        if marker_id in text:
            result.skipped.append(t.id)
            result.decisions.append(Decision(t.id, "already-present", "APEX marker already in file"))
            continue

        apply_it, reason = t.matches(scan_text, language)
        if not apply_it:
            result.not_needed.append(t.id)
            result.decisions.append(Decision(t.id, "not-needed", reason))
            continue

        block = _wrap(t)
        if t.placement == "append":
            append_blocks.append(block)
        else:
            preamble_blocks.append(block)
        result.applied.append(t.id)
        result.decisions.append(Decision(t.id, "applied", reason))

    # shebang upgrade is decided up-front so rewrite candidates validate as bash
    upgraded = _needs_bash(shebang)
    out_shebang = DEFAULT_SHEBANG if upgraded else shebang

    def _assemble(_body: str) -> str:
        return _render(out_shebang, preamble_blocks, _body, append_blocks, rubric)

    # ---- pass 2: content rewrites (validated, rolled back on failure) ----
    # Each rewrite may only fire when its companion helper block is present
    # (applied now or already in the file); the whole candidate is then parsed,
    # and any rewrite that would break the script is reverted and reported.
    present_or_applied = set(result.applied) | set(result.skipped)
    body_work = body
    for rw in rubric.rewrites:
        ok, reason = rw.gate(scan_text, language)
        if not ok:
            result.declined.append(rw.id)
            result.decisions.append(Decision(rw.id, "not-needed", reason))
            continue
        if rw.requires_transform and rw.requires_transform not in present_or_applied:
            result.declined.append(rw.id)
            result.decisions.append(Decision(
                rw.id, "declined", "needs block {0}, which is not present".format(rw.requires_transform)))
            continue
        try:
            rx = re.compile(rw.pattern, re.MULTILINE)
        except re.error as exc:
            result.declined.append(rw.id)
            result.decisions.append(Decision(rw.id, "declined", "invalid pattern: {0}".format(exc)))
            continue
        new_body, hits = _sub_user_regions(body_work, rx, rw.replacement)
        if hits == 0:
            result.decisions.append(Decision(rw.id, "not-needed", "no matching call in the script"))
            continue
        vok, verr = validate_fn(_assemble(new_body))
        if not vok:
            result.declined.append(rw.id)
            result.decisions.append(Decision(
                rw.id, "declined", "would break syntax ({0}); left as-is".format(verr or "parse error")))
            continue
        body_work = new_body
        result.rewritten.append(rw.id)
        result.decisions.append(Decision(
            rw.id, "rewritten", "{0} site(s): {1}".format(hits, rw.description)))

    if not (result.applied or result.rewritten):
        if result.skipped and not result.not_needed:
            result.notes.append("script already carries every APEX block for this domain (no-op)")
        else:
            result.notes.append("script already meets this domain's deterministic bar (nothing to inject)")
        result.elevated = text
        return result

    if upgraded:
        result.notes.append(
            "upgraded shebang '{0}' -> '{1}' (injected blocks use bash features)".format(
                shebang.strip(), DEFAULT_SHEBANG)
        )

    result.elevated = _assemble(body_work)
    return result
