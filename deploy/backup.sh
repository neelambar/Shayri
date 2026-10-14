#!/usr/bin/env bash
# Consistent online backup of the database; keeps the last 30 copies.
# Example cron (daily 3am):  0 3 * * * /home/ubuntu/Shayri/deploy/backup.sh
set -e
DB=${SHAYRI_DB:-/var/lib/shayri/shayri.db}
DIR=${BACKUP_DIR:-/home/ubuntu/Shayri/backups}
mkdir -p "$DIR"
python3 -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()" \
  "$DB" "$DIR/shayri-$(date +%F).db"
ls -1t "$DIR"/shayri-*.db | tail -n +31 | xargs -r rm --
