# शायरी संग्रह (Shayri)

A personal collection of Hindi poems: Flask + SQLite (FTS5 full-text search), served by gunicorn behind Caddy with basic auth.

## Features
- Add / edit / delete poems with poet, title, tags, meanings & notes, source, favourite flag
- Full-text search across poem, title, poet, tags and notes, with highlighted matches
  - substring search (trigram index), works for Devanagari and Latin text
  - ignores nukta (ज़ = ज) and chandrabindu/anusvara (हँ = हं) differences
  - `"quoted phrase"` for exact phrases
- Search by poet (partial name), browse poets A–Z with counts, browse tags
- Favourites, random poem, prev/next, "more by this poet"
- Reading view: clean white sheet; ghazal couplets spread to equal width on alternating bands; adjustable size, copy, share, print
- Warns about duplicates (same first line) when adding
- Draft autosave while writing a new poem; poet & tag autocomplete
- Dark mode, mobile friendly
- Backup/restore as JSON (links on the home page)

## Run locally
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python app.py          # http://127.0.0.1:8001

## Deploy
    sudo cp deploy/shayri.service /etc/systemd/system/
    sudo systemctl daemon-reload && sudo systemctl enable --now shayri
    caddy hash-password               # paste hash into deploy/Caddyfile.snippet
    # append the snippet to /etc/caddy/Caddyfile, then
    sudo systemctl reload caddy

Database: `/var/lib/shayri/shayri.db` (set by `SHAYRI_DB`).
Backups: `deploy/backup.sh` (add to cron), or the "Backup (JSON)" link at the bottom of the home page.

## Bulk import from Excel
For a sheet with columns `POET`, `TITLE`, `TEXT` (HTML like `<p>`/`<br />` in TEXT is converted to lines; ` / poet` is stripped from titles; duplicates are skipped):

    .venv/bin/python import_excel.py poems.xlsx --dry-run --show 3   # preview
    SHAYRI_DB=/var/lib/shayri/shayri.db .venv/bin/python import_excel.py poems.xlsx
