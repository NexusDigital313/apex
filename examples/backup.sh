#!/bin/bash
# nightly backup
SRC=/var/www
DEST=/backups/www-`date +%F`.tar.gz
tar czf $DEST $SRC
rm -f /backups/www-old.tar.gz
echo backup done
