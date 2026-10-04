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
     APEX-injected blocks, heredoc bodies, comments and quoted strings are
     skipped (see _protected_spans), so the engine never edits generated code
     or text the script prints or writes.
  4. Validate-and-rollback: every rewrite is parsed (bash -n + shellcheck) as
     part of the whole script; any rewrite that would break it is reverted and
     reported as "declined" rather than emitted.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .classify import BASH_COMPATIBLE, shebang_interpreter
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


def _split_header(body: str) -> Tuple[str, str]:
    """Split off the comment block directly under the shebang (license or
    copyright notice, description) so it stays at the top of the output instead
    of being pushed below the preamble. Comments are not logic, so this still
    honours "preamble before any other logic"."""
    lines = body.split("\n")
    n = 0
    while (n < len(lines) and lines[n].startswith("#")
           and not lines[n].startswith(("# >>> APEX[", "# ===== "))):
        n += 1
    return "\n".join(lines[:n]), "\n".join(lines[n:])


def _split_shebang(text: str) -> Tuple[str, str]:
    """Return (shebang_line_without_newline, rest)."""
    if text.startswith("#!"):
        nl = text.find("\n")
        if nl == -1:
            return text, ""
        return text[:nl], text[nl + 1 :]
    return DEFAULT_SHEBANG, text


def _bash_shebang(shebang: str) -> Optional[str]:
    """The injected blocks use bash features (local, set -E, declare -A, printf -v),
    so a POSIX sh / dash / ash shebang is upgraded. Returns the replacement (with
    any shell flags such as `#!/bin/sh -e` kept as a `set` line), or None when no
    upgrade is needed."""
    interp, args = shebang_interpreter(shebang)
    if interp not in ("sh", "dash", "ash"):
        return None
    flags = [a for a in args if a[:1] in "-+"]
    return DEFAULT_SHEBANG + ("\nset " + " ".join(flags) if flags else "")


# Characters that end an unquoted shell word (a heredoc delimiter, here).
_WORD_END = " \t\n;&|<>()"


def _heredoc_word(text: str, i: int) -> Tuple[str, int]:
    """Parse the heredoc delimiter word starting at `i`.

    Returns (delimiter with quoting removed, index after the word). Handles
    `EOF`, `'EOF'`, `"EOF"`, `\\EOF` and mixed forms such as `E"O"F`.
    """
    n = len(text)
    word: List[str] = []
    while i < n and text[i] not in _WORD_END:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            word.append(text[i + 1])
            i += 2
        elif ch in "'\"":
            j = text.find(ch, i + 1)
            if j == -1 or "\n" in text[i + 1:j]:
                break
            word.append(text[i + 1:j])
            i = j + 1
        else:
            word.append(ch)
            i += 1
    return "".join(word), i


def _heredoc_body_end(text: str, start: int, delim: str, strip_tabs: bool) -> int:
    """End of the body that starts at `start` (terminator line included).

    Matches bash exactly: the terminator is the delimiter alone on its line,
    with leading TABs allowed only for `<<-`. Unterminated -> end of text.
    """
    pos, n = start, len(text)
    while pos < n:
        nl = text.find("\n", pos)
        end = n if nl == -1 else nl
        line = text[pos:end]
        if (line.lstrip("\t") if strip_tabs else line) == delim:
            return end
        pos = end + 1
    return n


def _lex_spans(text: str, skip: Optional[List[Tuple[int, int]]] = None) -> List[Tuple[int, int]]:
    """Thin shell lexer: spans a rewrite must never touch.

    A single left-to-right scan (honouring backslash escapes) that protects:
      - comments        `# …` to end of line (only at a token boundary, so
                        `${#arr}` / `$#` are not mistaken for a comment);
      - single-quoted   '…' — always (content is literal, e.g. a `\\`backtick\\``
                        inside '…' must stay literal); `$'…'` honours `\\'`;
      - double-quoted   "…" — only when it spans a newline (a multi-line string
                        whose inner lines can masquerade as commands). Single-line
                        "$var" arguments stay live so real code is still rewritten;
      - heredoc bodies  for every `<<WORD` / `<<-WORD` / `<<'WORD'` / `<<\\WORD`
                        opener, the body from the next line through the
                        terminator. Bodies are skipped, not lexed, so an
                        apostrophe in prose (`don't`) cannot flip the quote
                        state of the code that follows.

    `skip` spans (APEX's own blocks) are jumped over unscanned.

    Not a full parser — nested `$( )` is left to the validate-and-rollback
    backstop — but it closes the cases where a rewrite would otherwise parse
    cleanly yet corrupt string/comment text.
    """
    spans: List[Tuple[int, int]] = []
    skip_at = {s: e for s, e in (skip or [])}
    pending: List[Tuple[str, bool]] = []  # heredocs opened on the current line
    i, n = 0, len(text)
    while i < n:
        if i in skip_at:
            i = max(skip_at[i], i + 1)
            continue
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "\n":
            i += 1
            for delim, strip_tabs in pending:
                end = _heredoc_body_end(text, i, delim, strip_tabs)
                spans.append((i, end))
                i = end + 1
            pending = []
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
        if ch == "<" and text.startswith("<<", i):
            if text.startswith("<<<", i):  # here-string: no body
                i += 3
                continue
            j = i + 2
            strip_tabs = text.startswith("-", j)
            if strip_tabs:
                j += 1
            while j < n and text[j] in " \t":
                j += 1
            delim, j = _heredoc_word(text, j)
            # `$(( x << 2 ))` is a shift, not a heredoc
            if delim and not delim.isdigit():
                pending.append((delim, strip_tabs))
            i = max(j, i + 2)
            continue
        if ch == "'":
            ansi_c = i > 0 and text[i - 1] == "$"
            j = i + 1
            while j < n and text[j] != "'":
                j += 2 if (ansi_c and text[j] == "\\") else 1
            j = n if j >= n else j + 1
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
    """(start, end) regions never rewritten: APEX blocks/headers, plus the
    comment / single-quoted / multi-line-string / heredoc-body spans from the
    lexer. Sorted and coalesced."""
    apex = [(m.start(), m.end()) for m in _APEX_BLOCK_RE.finditer(text)]
    apex += [(m.start(), m.end()) for m in _APEX_HEADER_RE.finditer(text)]
    spans = apex + _lex_spans(text, skip=apex)
    spans.sort()
    merged: List[Tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _sub_user_regions(text: str, rx, repl: str) -> Tuple[str, int]:
    """Apply rx->repl only to the user's code, skipping protected spans.

    Matching runs over the WHOLE text, so `^`/`$` mean real line boundaries
    (not the edge of a code segment between two strings); any match that
    touches a protected span is left alone. Keeps rewrites off APEX's own
    blocks (e.g. the `rm -rf` inside a generated safe_rm), which is what makes
    a re-run a true no-op, and off string/comment/heredoc text.
    """
    spans = _protected_spans(text)
    starts = [s for s, _ in spans]
    out: List[str] = []
    last = n = 0
    for m in rx.finditer(text):
        k = bisect.bisect_right(starts, m.start()) - 1
        if k >= 0 and spans[k][1] > m.start():
            continue  # match begins inside a protected span
        if k + 1 < len(spans) and spans[k + 1][0] < m.end():
            continue  # match runs into the next protected span
        out.append(text[last:m.start()])
        out.append(m.expand(repl))
        last = m.end()
        n += 1
    out.append(text[last:])
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


def _helper_after_body(tid: str, rubric: Rubric, text: str, applied: List[str]) -> bool:
    """True when block `tid` ends up below the user's code, so a rewritten call
    would run before the helper function exists (bash defines top-down)."""
    if tid in applied:
        t = next((t for t in rubric.transforms if t.id == tid), None)
        return t is not None and t.placement == "append"
    # already in the file from an earlier run: is it in the appended section?
    header = text.find("# ===== APEX appended helpers =====")
    return header != -1 and text.find("APEX[{0}]".format(tid)) > header


def elevate(text: str, rubric: Rubric, language: str = "shell",
            validator: Optional[Callable[[str], Tuple[bool, str]]] = None) -> DeterministicResult:
    result = DeterministicResult(elevated=text)

    if language != "shell":
        result.notes.append(
            "deterministic shell transforms do not target {0}; classification and the "
            "control rubric still apply, but no blocks were injected".format(language)
        )
        return result

    interp, _ = shebang_interpreter(text)
    if interp and interp.rstrip("0123456789.") not in BASH_COMPATIBLE:
        result.notes.append(
            "script runs under {0}; APEX's blocks are bash, so it was left unchanged".format(interp))
        return result

    validate_fn = validator or _default_validator
    shebang, body = _split_shebang(text)
    header, body = _split_header(body)
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
    new_shebang = _bash_shebang(shebang)
    upgraded = new_shebang is not None
    out_shebang = new_shebang if upgraded else shebang
    if header:
        out_shebang += "\n" + header

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
        if rw.requires_transform and _helper_after_body(rw.requires_transform, rubric, text, result.applied):
            result.declined.append(rw.id)
            result.decisions.append(Decision(
                rw.id, "declined", "block {0} would be defined after the code that calls it".format(
                    rw.requires_transform)))
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
                shebang.strip(), out_shebang.replace("\n", "; "))
        )

    result.elevated = _assemble(body_work)
    return result
