#!/bin/bash
# Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
# Proprietary and Confidential.
#
# Unauthorized reproduction, copying, modification, distribution,
# redistribution, publication, reverse engineering, decompilation, or
# disassembly of this file, in whole or in part, via any medium, is
# strictly prohibited without explicit prior written authorization.

# nightly backup
SRC=/var/www
DEST=/backups/www-`date +%F`.tar.gz
tar czf $DEST $SRC
rm -f /backups/www-old.tar.gz
echo backup done
