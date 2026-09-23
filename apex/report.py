"""Elevation report + unified diff."""

from __future__ import annotations

import difflib
from typing import Dict, List, Optional

from .classify import ClassificationResult
from .rubrics import Rubric


def unified_diff(original: str, elevated: str, path: str = "script") -> str:
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        elevated.splitlines(keepends=True),
        fromfile="a/{0}".format(path),
        tofile="b/{0}".format(path),
    )
    return "".join(diff)


def build_report(
    classification: ClassificationResult,
    rubric: Rubric,
    engine: str,
    applied_transforms: List[str],
    rewritten_rewrites: Optional[List[str]] = None,
    decisions: Optional[List] = None,
    validation_tool: str = "",
    validation_warnings: Optional[List[str]] = None,
) -> str:
    decisions = decisions or []
    rewritten_rewrites = rewritten_rewrites or []
    validation_warnings = validation_warnings or []
    tmap = {t.id: t for t in rubric.transforms}
    rmap = {r.id: r for r in rubric.rewrites}

    out: List[str] = []
    out.append("# APEX Elevation Report")
    out.append("")
    out.append("| | |")
    out.append("|---|---|")
    out.append("| Detected domain | `{0}` (confidence {1}) |".format(
        classification.domain, classification.confidence))
    out.append("| Target standard | **{0}** |".format(rubric.target_standard))
    out.append("| Engine | {0} |".format(engine))
    out.append("| Language | {0} |".format(classification.language))
    out.append("| Controls in rubric | {0} ({1} automatable) |".format(
        len(rubric.controls), len(rubric.automatable_controls)))
    out.append("")
    if classification.matched:
        out.append("**Why this domain:** matched " + ", ".join("`{0}`".format(m) for m in classification.matched[:12]))
        out.append("")

    out.append("## What was elevated")
    out.append("")
    if applied_transforms:
        for tid in applied_transforms:
            t = tmap.get(tid)
            desc = t.description if t else tid
            out.append("- **{0}** — {1} _(block injected)_".format(tid, desc))
    if rewritten_rewrites:
        for rid in rewritten_rewrites:
            rw = rmap.get(rid)
            desc = rw.description if rw else rid
            out.append("- **{0}** — {1} _(code rewritten in place)_".format(rid, desc))
    if not applied_transforms and not rewritten_rewrites:
        out.append("_No changes (already at standard for this domain, or nothing applicable)._")
    out.append("")

    # The self-driving decision trace: exactly what APEX detected and why it
    # applied or skipped each block. This is the "it read my script" evidence.
    if decisions:
        out.append("## Self-driving decision trace")
        out.append("")
        out.append("| Transform | Verdict | Reason |")
        out.append("|---|---|---|")
        icon = {"applied": "✅ applied", "rewritten": "🔁 rewritten", "already-present": "⏭ already present",
                "not-needed": "➖ not needed", "declined": "🚫 declined"}
        for d in decisions:
            out.append("| `{0}` | {1} | {2} |".format(
                d.id, icon.get(d.verdict, d.verdict), d.reason))
        out.append("")

    out.append("## Controls covered by the rubric")
    out.append("")
    out.append("| Control | Severity | Auto | Title |")
    out.append("|---|---|---|---|")
    for c in rubric.controls:
        out.append("| `{0}` | {1} | {2} | {3} |".format(
            c.id, c.severity, "yes" if c.automatable else "manual", c.title))
    out.append("")

    out.append("## Validation")
    out.append("")
    out.append("- syntax: **checked**{0}".format(" via {0}".format(validation_tool) if validation_tool else ""))
    if validation_warnings:
        out.append("- linter notes ({0}):".format(len(validation_warnings)))
        for w in validation_warnings[:15]:
            out.append("  - `{0}`".format(w))
    out.append("")

    if rubric.references:
        out.append("## References")
        out.append("")
        for ref in rubric.references:
            out.append("- {0}".format(ref))
        out.append("")

    return "\n".join(out)
