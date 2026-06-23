#!/bin/bash
# automate arch install
DISK=/dev/sda
parted -s $DISK mklabel gpt
parted -s $DISK mkpart primary 1MiB 100%
mkfs.ext4 ${DISK}1
mount ${DISK}1 /mnt
pacstrap /mnt base linux
genfstab -U /mnt >> /mnt/etc/fstab
echo "installed"
