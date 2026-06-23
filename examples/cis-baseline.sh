#!/bin/bash
# CIS baseline hardening (Level 1) - minimal
echo "applying CIS hardening"
sysctl -w net.ipv4.ip_forward=0
echo "PermitRootLogin no" >> /etc/ssh/sshd_config
chmod 600 /etc/ssh/sshd_config
systemctl restart sshd
