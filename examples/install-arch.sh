#!/bin/bash
# Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
# Proprietary and Confidential.
#
# Unauthorized reproduction, copying, modification, distribution,
# redistribution, publication, reverse engineering, decompilation, or
# disassembly of this file, in whole or in part, via any medium, is
# strictly prohibited without explicit prior written authorization.

# automate arch install
DISK=/dev/sda
parted -s $DISK mklabel gpt
parted -s $DISK mkpart primary 1MiB 100%
mkfs.ext4 ${DISK}1
mount ${DISK}1 /mnt
pacstrap /mnt base linux
genfstab -U /mnt >> /mnt/etc/fstab
echo "installed"
