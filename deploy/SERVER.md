# Server setup, backup and restore

How the server that hosts **shayri.nilambar.com** and **kundli.nilambar.com** is put together, how it is backed up, and how to rebuild it on a new machine.

## How it fits together

```
Internet ──► Caddy (:80/:443, HTTPS + basic auth) ──┬─ kundli.nilambar.com ──► gunicorn 127.0.0.1:8000  (~/Kundli, kundli.service)
                                                    └─ shayri.nilambar.com ──► gunicorn 127.0.0.1:8001  (~/Shayri, shayri.service)
```

| What | Where on the server | Where it is kept safe |
|---|---|---|
| Code | `~/Shayri`, `~/Kundli` | GitHub: `neelambar/Shayri`, `neelambar/Kundli` |
| Shayri database | `/var/lib/shayri/shayri.db` | OneDrive backup |
| Shayri secret key (signs sessions) | `/var/lib/shayri/.shayri_secret` | OneDrive backup |
| Kundli database | `/var/lib/kundli/kundli.db` | OneDrive backup |
| Caddy config (domains, password hash) | `/etc/caddy/Caddyfile` | OneDrive backup |
| systemd units as running live | `/etc/systemd/system/{shayri,kundli}.service` | OneDrive backup |
| Raw import spreadsheet | `~/Shayri/poems_raw.xlsx` | OneDrive backup |

## Software needed

| Software | Why | Install |
|---|---|---|
| Ubuntu 24.04 LTS (or another recent Debian/Ubuntu) | operating system; current server is 24.04 on ARM64 (Ampere) | — |
| git | get the code | `apt` |
| Python 3.12 with `venv` | both apps (Flask + gunicorn, installed per app into `.venv`) | `apt` |
| `python3-dev`, `build-essential` | compiling `pyswisseph` for Kundli on ARM | `apt` |
| Caddy 2 | HTTPS certificates, password, reverse proxy | Caddy's apt repository (below) |
| rclone | download/upload backups from OneDrive | `apt` |

Open ports **80** and **443** to the internet (and 22 for SSH). Nothing else needs to be open; the apps listen on localhost only.

## Weekly backup

`deploy/offsite-backup.sh` runs every Sunday at 03:00 (systemd timer, catches up after a missed run). It:

1. takes consistent snapshots of both SQLite databases and gzips them,
2. copies the secret key, Caddyfile, live service units and `poems_raw.xlsx`,
3. writes `MANIFEST.txt` with the date and the git commit of each repo,
4. uploads it all to `OneDrive/ServerBackups/<YYYY-MM-DD>/`,
5. deletes all but the newest **3** dated folders.

Files are **not encrypted**: keep the OneDrive account to yourself.

### Setting it up (one time per server)

1. **Connect rclone to OneDrive.** The server has no browser, so sign-in happens on your laptop:
   - On the laptop, install rclone (<https://rclone.org/downloads/>) and run `rclone authorize "onedrive"`. A browser opens; sign in to Microsoft. The terminal then prints a token (a block of JSON).
   - On the server, run `rclone config` → `n` (new remote) → name `onedrive` → storage `onedrive` → leave client id/secret empty → region `global` → advanced config `n` → auto config `n` → paste the token → choose your OneDrive (usually "OneDrive Personal or Business") → confirm.
   - Test: `rclone lsd onedrive:` lists your OneDrive folders.
2. **Install the timer:**
   ```bash
   sudo cp ~/Shayri/deploy/offsite-backup.service ~/Shayri/deploy/offsite-backup.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now offsite-backup.timer
   ```
3. **Run it once now** and check it:
   ```bash
   sudo systemctl start offsite-backup.service
   journalctl -u offsite-backup -n 20 --no-pager     # ends with "Backup uploaded to ..."
   rclone ls onedrive:ServerBackups
   systemctl list-timers offsite-backup.timer         # shows the next run
   ```

If the OneDrive sign-in expires (e.g. after a password change), the backup fails with an auth error in the journal; fix it with `rclone config reconnect onedrive:` (it will again ask you to run `rclone authorize` on the laptop).

## Restoring on a new server

The steps assume the user `ubuntu` with the repos in its home folder. With a different user or paths, edit `User=` and the paths in the `.service` files before installing them.

**1. Create the server and open ports 80 and 443.**
On Oracle Cloud this is two places: the subnet's Security List (ingress TCP 80 and 443 from `0.0.0.0/0`) *and* the instance's own iptables:
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```
Other providers usually only need the firewall in their web console.

**2. Lower the DNS TTL** of `shayri` and `kundli` records to a few minutes (at your DNS provider), so the switch in step 9 takes effect quickly.

**3. Install the software:**
```bash
sudo apt update
sudo apt install -y git python3-venv python3-dev build-essential rclone \
                    debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

**4. Connect rclone to OneDrive** (step 1 of "Setting it up" above), then download the newest backup:
```bash
rclone lsf onedrive:ServerBackups                      # pick the newest date
rclone copy onedrive:ServerBackups/YYYY-MM-DD ~/restore
cat ~/restore/MANIFEST.txt
```

**5. Get the code and install the Python packages:**
```bash
cd ~
git clone https://github.com/neelambar/Shayri.git
git clone https://github.com/neelambar/Kundli.git
python3 -m venv Shayri/.venv && Shayri/.venv/bin/pip install -r Shayri/requirements.txt
python3 -m venv Kundli/.venv && Kundli/.venv/bin/pip install -r Kundli/requirements-server.txt
cp ~/restore/poems_raw.xlsx ~/Shayri/ 2>/dev/null || true
```

**6. Put the data in place:**
```bash
sudo mkdir -p /var/lib/shayri /var/lib/kundli
gunzip -c ~/restore/shayri.db.gz | sudo tee /var/lib/shayri/shayri.db >/dev/null
gunzip -c ~/restore/kundli.db.gz | sudo tee /var/lib/kundli/kundli.db >/dev/null
sudo cp ~/restore/shayri_secret /var/lib/shayri/.shayri_secret
sudo chown -R ubuntu:ubuntu /var/lib/shayri /var/lib/kundli
sudo chmod 600 /var/lib/shayri/.shayri_secret
```

**7. Start the apps** (the backed-up unit files are exactly what ran on the old server):
```bash
sudo cp ~/restore/shayri.service ~/restore/kundli.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shayri kundli
curl -s localhost:8001/healthz; echo     # Shayri
curl -s localhost:8000/healthz; echo     # Kundli
```

**8. Start Caddy** with the old config (same domains and password):
```bash
sudo cp ~/restore/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

**9. Point DNS at the new server.** Change the `A` records of `shayri.nilambar.com` and `kundli.nilambar.com` to the new public IP. Caddy fetches HTTPS certificates by itself once the names resolve to the new machine (check with `journalctl -u caddy -f`). Then open both sites and log in.

**10. Turn the weekly backup back on** ("Install the timer" above), and delete `~/restore`.

## Updating the apps

- Kundli: `~/Kundli/deploy/update.sh`
- Shayri: `cd ~/Shayri && git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart shayri`
