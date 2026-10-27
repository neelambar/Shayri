#!/usr/bin/env bash
# Weekly off-server backup of everything the git repos don't hold (databases, secret,
# Caddyfile, live systemd units) to OneDrive via rclone. Keeps the newest $KEEP copies.
# Scheduled by deploy/offsite-backup.timer; restore steps are in deploy/SERVER.md.
set -euo pipefail

REMOTE=${REMOTE:-onedrive:ServerBackups}
KEEP=${KEEP:-3}
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# Consistent copy of a live SQLite database
snapshot() {
    python3 -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()" "$1" "$2"
}

snapshot /var/lib/shayri/shayri.db "$STAGE/shayri.db"
snapshot /var/lib/kundli/kundli.db "$STAGE/kundli.db"
gzip "$STAGE"/*.db
cp /var/lib/shayri/.shayri_secret "$STAGE/shayri_secret"
cp /etc/caddy/Caddyfile /etc/systemd/system/shayri.service /etc/systemd/system/kundli.service "$STAGE/"
[ -f /home/ubuntu/Shayri/poems_raw.xlsx ] && cp /home/ubuntu/Shayri/poems_raw.xlsx "$STAGE/"
{
    echo "Backup taken $(date -Is) on $(hostname)"
    for repo in /home/ubuntu/Shayri /home/ubuntu/Kundli; do
        echo "$(basename "$repo") at commit $(git -C "$repo" rev-parse --short HEAD)"
    done
} > "$STAGE/MANIFEST.txt"

rclone copy "$STAGE" "$REMOTE/$(date +%F)"

# Delete all but the newest $KEEP dated folders
rclone lsf --dirs-only "$REMOTE" | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}/$' | sort -r | tail -n +$((KEEP + 1)) |
    while read -r old; do rclone purge "$REMOTE/$old"; done

echo "Backup uploaded to $REMOTE/$(date +%F)"
