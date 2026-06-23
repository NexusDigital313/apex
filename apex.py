#!/usr/bin/env python3
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
