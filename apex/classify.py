"""Script classifier.

Decides which domain a script belongs to by scoring weighted signatures
(shebang / keyword / regex) against the script body. Signatures can be overridden
by a rubrics/classifier.json (produced by the design workflow); otherwise the
built-in taxonomy below is used.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .rubrics import RUBRIC_DIR

# Built-in signature taxonomy. weight is additive; kind is keyword | regex | shebang.
DEFAULT_DOMAINS: Dict[str, Dict] = {
    "cis_hardening": {
        "display_name": "CIS / security hardening",
        "min_confidence": 3.0,
        "signatures": [
            {"kind": "keyword", "pattern": "pam_pwquality", "weight": 3},
            {"kind": "keyword", "pattern": "pam_faillock", "weight": 3},
            {"kind": "keyword", "pattern": "auditd", "weight": 2},
            {"kind": "keyword", "pattern": "audit.rules", "weight": 2},
            {"kind": "keyword", "pattern": "sshd_config", "weight": 2},
            {"kind": "keyword", "pattern": "sysctl", "weight": 1.5},
            {"kind": "keyword", "pattern": "gpgcheck", "weight": 2},
            {"kind": "keyword", "pattern": "fips", "weight": 1.5},
            {"kind": "keyword", "pattern": "aide", "weight": 1.5},
            {"kind": "regex", "pattern": r"\bCIS\b", "weight": 3},
            {"kind": "regex", "pattern": r"\bSTIG\b", "weight": 2},
            {"kind": "regex", "pattern": r"(?i)benchmark", "weight": 1.5},
            {"kind": "regex", "pattern": r"(?i)hardening", "weight": 2},
            {"kind": "regex", "pattern": r"(?im)^\s*#.*Ensure ", "weight": 2},
            {"kind": "regex", "pattern": r"chmod\s+0?[0-7]00\s", "weight": 1},
        ],
    },
    "forensic": {
        "display_name": "Forensic / acquisition / IR",
        "min_confidence": 3.0,
        "signatures": [
            {"kind": "regex", "pattern": r"(?i)chain.of.custody", "weight": 4},
            {"kind": "keyword", "pattern": "dc3dd", "weight": 4},
            {"kind": "keyword", "pattern": "dcfldd", "weight": 4},
            {"kind": "keyword", "pattern": "ewfacquire", "weight": 4},
            {"kind": "keyword", "pattern": "LiME", "weight": 3},
            {"kind": "keyword", "pattern": "avml", "weight": 3},
            {"kind": "keyword", "pattern": "volatility", "weight": 3},
            {"kind": "keyword", "pattern": "blockdev --setro", "weight": 3},
            {"kind": "keyword", "pattern": "write-block", "weight": 3},
            {"kind": "keyword", "pattern": "sha256sum", "weight": 1.5},
            {"kind": "keyword", "pattern": "sha512sum", "weight": 1.5},
            {"kind": "regex", "pattern": r"(?i)\bevidence\b", "weight": 2},
            {"kind": "regex", "pattern": r"(?i)acquisition", "weight": 2},
            {"kind": "regex", "pattern": r"(?i)examiner", "weight": 2},
            {"kind": "regex", "pattern": r"\bdd\s+if=/dev/", "weight": 2},
            {"kind": "regex", "pattern": r"(?i)imaging", "weight": 1.5},
        ],
    },
    "installer": {
        "display_name": "Linux OS install / provisioning",
        "min_confidence": 3.0,
        "signatures": [
            {"kind": "keyword", "pattern": "kickstart", "weight": 3},
            {"kind": "keyword", "pattern": "preseed", "weight": 3},
            {"kind": "keyword", "pattern": "autoinstall", "weight": 3},
            {"kind": "keyword", "pattern": "cloud-init", "weight": 2.5},
            {"kind": "keyword", "pattern": "debootstrap", "weight": 3},
            {"kind": "keyword", "pattern": "pacstrap", "weight": 3},
            {"kind": "keyword", "pattern": "genfstab", "weight": 3},
            {"kind": "keyword", "pattern": "arch-chroot", "weight": 3},
            {"kind": "keyword", "pattern": "AutoYaST", "weight": 3},
            {"kind": "keyword", "pattern": "grub-install", "weight": 2},
            {"kind": "keyword", "pattern": "mkfs.", "weight": 1.5},
            {"kind": "keyword", "pattern": "parted", "weight": 1.5},
            {"kind": "keyword", "pattern": "%packages", "weight": 3},
            {"kind": "keyword", "pattern": "cryptsetup", "weight": 1.5},
            {"kind": "regex", "pattern": r"(?i)\binstall(er|ation)\b.*\b(os|linux|disk)\b", "weight": 1.5},
        ],
    },
}

PRODUCTION_HINTS = [
    {"kind": "regex", "pattern": r"set\s+-e", "weight": 0.5},
    {"kind": "keyword", "pattern": "getopts", "weight": 0.5},
]


@dataclass
class ClassificationResult:
    domain: str
    confidence: float
    scores: Dict[str, float] = field(default_factory=dict)
    matched: List[str] = field(default_factory=list)
    language: str = "shell"

    @property
    def is_generic(self) -> bool:
        return self.domain == "generic"


def detect_language(text: str, filename: str = "") -> str:
    head = text[:200]
    if filename.endswith(".py") or re.search(r"^#!.*\bpython", head):
        return "python"
    if filename.endswith((".sh", ".bash")) or re.search(r"^#!.*\b(bash|sh|zsh|ksh)", head):
        return "shell"
    # default to shell for bare scripts
    return "shell"


def _load_domains() -> Dict[str, Dict]:
    """Built-in taxonomy MERGED with rubrics/classifier.json (if present).

    The built-in signatures are tuned to catch *un-elevated* scripts (a bare `dd`
    acquisition, a minimal CIS script); the workflow-generated classifier adds
    richer signatures for already-advanced scripts. Union the signatures and take
    the lower confidence threshold so both basic and advanced inputs route right.
    """
    # start from a copy of the built-ins
    domains: Dict[str, Dict] = {}
    for key, cfg in DEFAULT_DOMAINS.items():
        domains[key] = {
            "display_name": cfg["display_name"],
            "min_confidence": float(cfg["min_confidence"]),
            "signatures": list(cfg["signatures"]),
        }

    override = os.path.join(RUBRIC_DIR, "classifier.json")
    if os.path.isfile(override):
        try:
            with open(override, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for entry in data.get("domains", []):
                key = entry.get("key")
                if not key:
                    continue
                if key in domains:
                    domains[key]["signatures"].extend(entry.get("signatures", []))
                    domains[key]["min_confidence"] = min(
                        domains[key]["min_confidence"], float(entry.get("min_confidence", 3.0))
                    )
                else:
                    domains[key] = {
                        "display_name": entry.get("display_name", key),
                        "min_confidence": float(entry.get("min_confidence", 3.0)),
                        "signatures": entry.get("signatures", []),
                    }
        except (OSError, ValueError, KeyError):
            pass
    return domains


# A single token repeated many times (e.g. `sysctl` x40) must not dominate; a real
# domain script trips several DISTINCT signatures, not one token over and over.
_MATCH_CAP = 3
# A domain win must beat the runner-up by this margin (same additive scale as the
# signature weights) — a near-tie is a contested/mixed script and degrades to generic.
_MARGIN = 1.0
# ...and must rest on at least this many DISTINCT signatures. One lone keyword
# (e.g. an incidental `bootloader` reference) is not enough to define a domain.
_MIN_DISTINCT = 2


def _score(text: str, signatures: List[Dict]) -> Tuple[float, List[str]]:
    score = 0.0
    matched: List[str] = []
    for sig in signatures:
        kind = sig.get("kind", "keyword")
        pattern = sig.get("pattern", "")
        weight = float(sig.get("weight", 1))
        if not pattern:
            continue
        if kind == "regex":
            try:
                count = len(re.findall(pattern, text))
            except re.error:
                continue
            if count:
                score += weight * min(count, _MATCH_CAP)
                matched.append("re:" + pattern)
        else:  # keyword / shebang both treated as substring
            count = text.count(pattern)
            if count:
                score += weight * min(count, _MATCH_CAP)
                matched.append(pattern)
    return score, matched


def classify(text: str, filename: str = "") -> ClassificationResult:
    domains = _load_domains()
    scores: Dict[str, float] = {}
    matched_by: Dict[str, List[str]] = {}

    for key, cfg in domains.items():
        score, matched = _score(text, cfg.get("signatures", []))
        scores[key] = round(score, 2)
        matched_by[key] = matched

    # Rank the real (non-generic) domains; generic is the fall-through floor, never
    # a scored competitor.
    ranked = sorted(
        ((k, scores[k]) for k in domains if k != "generic"),
        key=lambda kv: -kv[1],
    )

    best_domain = "generic"
    best_score = 0.0
    best_matched: List[str] = []

    if ranked:
        top_key, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        min_conf = domains[top_key].get("min_confidence", 3.0)
        # Claim the domain only when the signal is real (clears threshold), broad
        # (≥2 distinct signatures, not one incidental keyword), and decisive
        # (beats the runner-up by the margin). Otherwise degrade to generic.
        if (top_score >= min_conf
                and len(matched_by[top_key]) >= _MIN_DISTINCT
                and (top_score - second_score) >= _MARGIN):
            best_domain = top_key
            best_score = top_score
            best_matched = matched_by[top_key]

    if best_domain == "generic":
        # confidence for generic = how production-ready it already looks (low = needs work)
        prod, _ = _score(text, PRODUCTION_HINTS)
        best_score = round(prod, 2)
        best_matched = []

    return ClassificationResult(
        domain=best_domain,
        confidence=round(best_score, 2),
        scores=scores,
        matched=best_matched,
        language=detect_language(text, filename),
    )
