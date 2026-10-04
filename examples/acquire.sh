#!/bin/sh
# Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
# Proprietary and Confidential.
#
# Unauthorized reproduction, copying, modification, distribution,
# redistribution, publication, reverse engineering, decompilation, or
# disassembly of this file, in whole or in part, via any medium, is
# strictly prohibited without explicit prior written authorization.

# quick disk image
SRC=$1
OUT=$2
dd if=$SRC of=$OUT bs=4M
sha256sum $OUT > $OUT.sha256
echo "done imaging $SRC"
