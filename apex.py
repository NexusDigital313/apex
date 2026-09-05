#!/usr/bin/env python3
# Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
# Proprietary and Confidential.
#
# Unauthorized reproduction, copying, modification, distribution,
# redistribution, publication, reverse engineering, decompilation, or
# disassembly of this file, in whole or in part, via any medium, is
# strictly prohibited without explicit prior written authorization.

"""APEX entrypoint. Works from any directory and via a symlink (e.g. ~/bin/apex)."""

import os
import sys

# Make the package importable no matter where we're invoked from, and resolve
# through a symlink to the real project directory.
_HERE = os.path.dirname(os.path.abspath(os.path.realpath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from apex.cli import main

if __name__ == "__main__":
    sys.exit(main())
