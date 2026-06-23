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
    mode: str,
    applied_transforms: List[str],
    changelog: Optional[List[Dict]] = None,
    residual_risks: Optional[List[str]] = None,
    manual_followups: Optional[List[str]] = None,
    validation_tool: str = "",
    validation_warnings: Optional[List[str]] = None,
) -> str:
    changelog = changelog or []
    residual_risks = residual_risks or []
    manual_followups = manual_followups or []
    validation_warnings = validation_warnings or []

    out: List[str] = []
    out.append("# APEX Elevation Report")
    out.append("")
    out.append("| | |")
    out.append("|---|---|")
    out.append("| Detected domain | `{0}` (confidence {1}) |".format(
        classification.domain, classification.confidence))
    out.append("| Target standard | **{0}** |".format(rubric.target_standard))
    out.append("| Engine | {0} |".format(mode))
    out.append("| Language | {0} |".format(classification.language))
    out.append("| Controls in rubric | {0} ({1} automatable) |".format(
        len(rubric.controls), len(rubric.automatable_controls)))
    out.append("")
    if classification.matched:
        out.append("**Why this domain:** matched " + ", ".join("`{0}`".format(m) for m in classification.matched[:12]))
        out.append("")

    out.append("## What was elevated")
    out.append("")
    if changelog:
        ctrl_by_id = {c.id: c for c in rubric.controls}
        for entry in changelog:
            cid = entry.get("control_id", "")
            ctrl = ctrl_by_id.get(cid)
            title = ctrl.title if ctrl else cid
            out.append("- **[{0}] {1}** — {2}".format(cid, title, entry.get("what_changed", "")))
            if entry.get("rationale"):
                out.append("  - _why:_ {0}".format(entry["rationale"]))
    elif applied_transforms:
        tmap = {t.id: t for t in rubric.transforms}
        for tid in applied_transforms:
            t = tmap.get(tid)
            desc = t.description if t else tid
            out.append("- **{0}** — {1}".format(tid, desc))
    else:
        out.append("_No changes applied (already at standard, or nothing to do)._")
    out.append("")

    out.append("## Controls covered by the rubric")
    out.append("")
    out.append("| Control | Severity | Auto | Title |")
    out.append("|---|---|---|---|")
    for c in rubric.controls:
        out.append("| `{0}` | {1} | {2} | {3} |".format(
            c.id, c.severity, "yes" if c.automatable else "manual", c.title))
    out.append("")

    if residual_risks:
        out.append("## Residual risks")
        out.append("")
        for r in residual_risks:
            out.append("- {0}".format(r))
        out.append("")

    if manual_followups:
        out.append("## Manual follow-ups (cannot be automated)")
        out.append("")
        for m in manual_followups:
            out.append("- [ ] {0}".format(m))
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
