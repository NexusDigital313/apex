# Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
# Proprietary and Confidential.
#
# Unauthorized reproduction, copying, modification, distribution,
# redistribution, publication, reverse engineering, decompilation, or
# disassembly of this file, in whole or in part, via any medium, is
# strictly prohibited without explicit prior written authorization.

"""APEX — elevate any executable script to its apex standard.

CIS -> STIG. Forensic -> courtroom-bulletproof. Installer -> unattended/interactive.
Anything else -> production-grade.

APEX is fully offline and deterministic: it reads a script, detects what it is
missing against a domain rubric, and injects only the hardening blocks that
script actually needs. No API key, no network, no LLM.
"""

__version__ = "1.0.0"
