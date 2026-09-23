"""APEX — elevate any executable script to its apex standard.

CIS -> STIG. Forensic -> courtroom-bulletproof. Installer -> unattended/interactive.
Anything else -> production-grade.

APEX is fully offline and deterministic: it reads a script, detects what it is
missing against a domain rubric, and injects only the hardening blocks that
script actually needs. No API key, no network, no LLM.
"""

__version__ = "1.0.0"
