#!/bin/sh
# quick disk image
SRC=$1
OUT=$2
dd if=$SRC of=$OUT bs=4M
sha256sum $OUT > $OUT.sha256
echo "done imaging $SRC"
