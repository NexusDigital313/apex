#!/bin/zsh
# Double-click launcher (macOS): runs APEX from wherever this folder lives.
cd "${0:A:h}" || exit 1
python3 apex.py banner
python3 apex.py --help
exec zsh -l
