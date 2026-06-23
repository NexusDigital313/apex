"""LLM elevation engine — the intelligent, semantics-preserving rewrite.

Runs only when ANTHROPIC_API_KEY is set. The (already deterministically
pre-hardened) script plus the domain rubric go to claude-opus-4-8; it applies
every automatable control, preserves behaviour, and returns a strict JSON
contract: the full elevated script + a control-by-control changelog.

Design notes baked in from the Anthropic SDK guidance:
  * model claude-opus-4-8, adaptive thinking for the hard rewrite.
  * the rubric system prompt is large + stable -> cache_control ephemeral so
    repeated elevations in one session pay ~0.1x for it.
  * output_config.format pins the JSON shape (validated server-side).
  * streamed, because a full rewritten script can be long.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .rubrics import Rubric

MODEL = "claude-opus-4-8"
MAX_TOKENS = 32000

OUTPUT_SCHEMA: Dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "elevated_script": {"type": "string"},
        "changelog": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "control_id": {"type": "string"},
                    "what_changed": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["control_id", "what_changed", "rationale"],
            },
        },
        "residual_risks": {"type": "array", "items": {"type": "string"}},
        "manual_followups": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["elevated_script", "changelog", "residual_risks", "manual_followups"],
}


@dataclass
class LLMResult:
    elevated: str
    changelog: List[Dict] = field(default_factory=list)
    residual_risks: List[str] = field(default_factory=list)
    manual_followups: List[str] = field(default_factory=list)


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _controls_table(rubric: Rubric) -> str:
    lines = []
    for c in rubric.controls:
        auto = "auto" if c.automatable else "manual"
        lines.append(
            "- [{id}] ({sev}, {auto}) {title}\n    fix: {fix}".format(
                id=c.id, sev=c.severity, auto=auto, title=c.title, fix=c.fix
            )
        )
    return "\n".join(lines)


def build_system_prompt(rubric: Rubric) -> str:
    return (
        "You are APEX, an expert that elevates executable scripts to the highest known "
        "standard for their domain. You are operating in the `{domain}` domain; the apex "
        "standard here is: {target}.\n\n"
        "Your job: rewrite the user's script so it fully meets that standard, WITHOUT "
        "changing what it was built to do.\n\n"
        "Hard rules:\n"
        "  1. Preserve original behaviour and intent. Never silently drop functionality.\n"
        "  2. Apply EVERY automatable control in the rubric below. Comment each non-obvious "
        "hardening inline with its control id, e.g. `# [{first_ctrl}] ...`.\n"
        "  3. The result must be runnable as-is. Preserve/repair the shebang. Keep it idempotent.\n"
        "  4. Never add a destructive operation without an explicit confirmation guard.\n"
        "  5. Do not weaken any control already present. Do not invent fake standard/IDs.\n"
        "  6. Keep APEX[...] fenced blocks that are already present; integrate, don't duplicate.\n\n"
        "Domain summary: {summary}\n\n"
        "Domain-specific guidance:\n{guidance}\n\n"
        "Rubric controls (the definition of the apex standard):\n{controls}\n\n"
        "Return STRICT JSON only, matching the provided schema: the full elevated script in "
        "`elevated_script`, a `changelog` entry per control you applied (control_id, "
        "what_changed, rationale), any `residual_risks`, and `manual_followups` that cannot be "
        "automated (e.g. hardware write-blocker attestation, organisation-specific values)."
    ).format(
        domain=rubric.domain,
        target=rubric.target_standard,
        summary=rubric.summary,
        guidance=rubric.llm_guidance or "(none)",
        controls=_controls_table(rubric) or "(none)",
        first_ctrl=rubric.controls[0].id if rubric.controls else "CTRL",
    )


def elevate(text: str, rubric: Rubric, language: str = "shell") -> LLMResult:
    if not available():
        raise LLMUnavailable("ANTHROPIC_API_KEY not set")
    try:
        import anthropic  # noqa: WPS433 (lazy import so the tool runs without the SDK)
    except ImportError as exc:  # pragma: no cover
        raise LLMUnavailable("the `anthropic` package is not installed (pip install anthropic)") from exc

    client = anthropic.Anthropic()

    system = [
        {
            "type": "text",
            "text": build_system_prompt(rubric),
            "cache_control": {"type": "ephemeral"},
        }
    ]
    user = (
        "Language: {lang}\n"
        "Elevate the following script to the apex standard and return the JSON contract.\n\n"
        "----- BEGIN SCRIPT -----\n{script}\n----- END SCRIPT -----"
    ).format(lang=language, script=text)

    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=system,
            output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            messages=[{"role": "user", "content": user}],
        ) as stream:
            final = stream.get_final_message()
    except Exception as exc:  # pragma: no cover - network/runtime errors surface to caller
        raise LLMUnavailable("Anthropic API call failed: {0}".format(exc)) from exc

    if final.stop_reason == "refusal":
        raise LLMUnavailable("model refused the request (stop_reason=refusal)")

    payload: Optional[str] = None
    for block in final.content:
        if getattr(block, "type", None) == "text":
            payload = block.text
            break
    if not payload:
        raise LLMUnavailable("model returned no text block")

    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise LLMUnavailable("could not parse JSON contract: {0}".format(exc)) from exc

    return LLMResult(
        elevated=data.get("elevated_script", text),
        changelog=data.get("changelog", []),
        residual_risks=data.get("residual_risks", []),
        manual_followups=data.get("manual_followups", []),
    )
