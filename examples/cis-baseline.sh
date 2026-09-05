#!/bin/bash
# Copyright (c) 2026 Jacob Douglas Parmenter. All Rights Reserved.
# Proprietary and Confidential.
#
# Unauthorized reproduction, copying, modification, distribution,
# redistribution, publication, reverse engineering, decompilation, or
# disassembly of this file, in whole or in part, via any medium, is
# strictly prohibited without explicit prior written authorization.

# CIS baseline hardening (Level 1) - minimal
echo "applying CIS hardening"
sysctl -w net.ipv4.ip_forward=0
echo "PermitRootLogin no" >> /etc/ssh/sshd_config
chmod 600 /etc/ssh/sshd_config
systemctl restart sshd
