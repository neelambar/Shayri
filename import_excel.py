"""Bulk-import poems from an Excel (.xlsx) or CSV file with columns POET, TITLE, TEXT.

TEXT is HTML (<p>, <br />, <sup> ...); it is converted to plain lines with a
blank line between stanzas, footnote markers become superscript digits and a
"शब्दार्थ:" glossary goes into the notes field (see html_to_poem). A trailing
" / <poet>" is removed from titles. Poems whose text is already in the collection are
skipped, so running the same file twice is safe.

    .venv/bin/python import_excel.py poems.csv --dry-run --show 3
    SHAYRI_DB=/var/lib/shayri/shayri.db .venv/bin/python import_excel.py poems.csv
"""
import argparse
import csv
import html
import re
import sys

from openpyxl import load_workbook

import app as shayri
from app import clean, fold

# Rows that are a whole scraped web page (scripts, menus), not a poem
JUNK = re.compile(r"<script|<html|<head\b|wgServer", re.IGNORECASE)
DROP = re.compile(r"<!--.*?-->|<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
PRE = re.compile(r"<pre\b[^>]*>(.*?)</pre\s*>", re.IGNORECASE | re.DOTALL)
SUP = re.compile(r"<sup\b[^>]*>(.*?)</sup\s*>", re.IGNORECASE | re.DOTALL)
REFS = re.compile(r"<ol\b[^>]*references[^>]*>(.*?)</ol\s*>", re.IGNORECASE | re.DOTALL)
LI = re.compile(r"<li\b[^>]*>(.*?)</li\s*>", re.IGNORECASE | re.DOTALL)
BR = re.compile(r"<br\b[^>]*>", re.IGNORECASE)
BLOCK = re.compile(r"</?(p|div|li|dd|dt|dl|ul|ol|tr|table|center|blockquote|h[1-6])\b[^>]*>", re.IGNORECASE)
TAG = re.compile(r"<[^>]+>")
SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
# "शब्दार्थ:" heading that starts the glossary of difficult words
GLOSS = re.compile(r"^शब्दार्थ\s*[:ः]?\s*[-–:]*\s*(.*)$")
LINE, PARA = "\x01", "\x02"


def _footnote(m):
    inner = TAG.sub("", m.group(1))
    digits = re.sub(r"\D", "", inner)
    return digits.translate(SUPERSCRIPT) if digits else inner


def _references(m):
    items = [TAG.sub("", li).replace("↑", "").strip() for li in LI.findall(m.group(1))]
    return PARA + LINE.join(f"{i}. {t}" for i, t in enumerate(items, 1)) + PARA


def html_to_poem(s):
    """Convert one TEXT cell to (body, notes); None for a scraped web page.

    In this HTML a line ends with <br />, and an empty line (<br /><br />, or a
    paragraph holding only <br />) is a stanza break. Some poems put every
    line in its own <p>; others use one <p> per stanza. Footnote markers
    (<sup>1</sup>, <sup>[1]</sup>) become superscript digits, and the
    "शब्दार्थ:" glossary goes into notes.
    """
    s = str(s or "")
    if JUNK.search(s):
        return None
    s = DROP.sub("", s)
    # inside <pre> source newlines are line breaks; elsewhere just HTML whitespace
    s = PRE.sub(lambda m: "<p>" + m.group(1).replace("\n", "<br />") + "</p>", s)
    s = re.sub(r"\s+", " ", s)
    s = SUP.sub(_footnote, s)
    s = REFS.sub(_references, s)
    s = BR.sub(LINE, s)
    s = BLOCK.sub(PARA, s)
    s = html.unescape(TAG.sub("", s)).replace("\xa0", " ").replace("\u200b", "")

    paras = []  # each a list of lines; "" is a stanza break
    for chunk in s.split(PARA):
        lines = [" ".join(line.split()) for line in chunk.split(LINE)]
        if len(lines) > 1 and not lines[-1]:
            lines.pop()  # a <br /> right before </p> just ends the last line
        if any(lines):
            paras.append(lines)
        elif LINE in chunk:
            paras.append([""])  # paragraph holding only <br />
    text = [p for p in paras if any(p)]
    one_line = sum(1 for p in text if len([x for x in p if x]) == 1)
    per_line = len(text) >= 3 and one_line >= 0.7 * len(text)

    out = []
    for p in paras:
        if out and not per_line:
            out.append("")
        out.extend(p)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip().split("\n")

    notes = []
    for i, line in enumerate(out[1:], 1):
        m = GLOSS.match(line)
        if m and (not m.group(1) or "=" in m.group(1)):
            notes = [m.group(1)] + out[i + 1:]
            out = out[:i]
            break
    body = clean("\n".join(out))
    notes = "\n".join(x for x in notes if x.strip())
    # glossary entries are often separated only by source newlines: "1. a= b 2. c= d"
    notes = clean(re.sub(r"\s+(?=\d{1,3}\s*[.)]\s)", "\n", notes))
    return body, notes


def clean_title(title, poet):
    """Drop the poet from "Title / Poet" or "Title / Poet / Part 1"."""
    title = clean(html.unescape(str(title or "")).replace("\xa0", " "))
    parts = [" ".join(p.split()) for p in re.split(r"\s+/\s*|\s*/\s+", title)]
    if poet and poet in parts[1:]:
        del parts[len(parts) - 1 - parts[::-1].index(poet)]
    elif len(parts) > 1:  # poet spelled differently in the title
        parts.pop()
    return " / ".join(parts)


def raw_rows(path):
    """(poet, title, text) cells from an .xlsx or .csv file with a header row."""
    if path.lower().endswith(".csv"):
        csv.field_size_limit(sys.maxsize)
        f = open(path, newline="", encoding="utf-8-sig")
        rows = csv.reader(f)
    else:
        rows = load_workbook(path, read_only=True).active.iter_rows(values_only=True)
    header = [str(h or "").strip().upper() for h in next(rows)]
    try:
        cols = [header.index(c) for c in ("POET", "TITLE", "TEXT")]
    except ValueError:
        sys.exit(f"Expected columns POET, TITLE, TEXT; found {header}")
    for row in rows:
        yield tuple(row[i] if i < len(row) else None for i in cols)


def read_rows(path):
    for poet, title, text in raw_rows(path):
        poet = " ".join(clean(str(poet or "")).split())
        yield poet, clean_title(title, poet), html_to_poem(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("xlsx")
    ap.add_argument("--dry-run", action="store_true", help="parse and count, don't write")
    ap.add_argument("--show", type=int, default=0, metavar="N", help="print the first N parsed poems")
    args = ap.parse_args()

    shayri.init_db()
    db = shayri.connect()
    existing = {fold(" ".join(r[0].split())) for r in db.execute("SELECT body FROM poems")}
    added = dup = empty = 0
    junk = []
    with db:
        for n, (poet, title, poem) in enumerate(read_rows(args.xlsx), 1):
            if poem is None:
                junk.append(f"{title} / {poet}")
                continue
            body, notes = poem
            if n <= args.show:
                print(f"--- {title} / {poet}\n{body}\n" + (f"[notes]\n{notes}\n" if notes else ""))
            if not body:
                empty += 1
                continue
            key = fold(" ".join(body.split()))
            if key in existing:
                dup += 1
                continue
            existing.add(key)
            if not args.dry_run:
                shayri.save_poem(db, {"title": title, "poet": poet, "body": body,
                                      "tags": "", "notes": notes, "source": "", "favorite": 0})
            added += 1
            if n % 2000 == 0:
                print(f"  {n} rows read...", file=sys.stderr)
        if args.dry_run:
            db.rollback()
    for j in junk:
        print(f"  skipped (web page, not a poem): {j}", file=sys.stderr)
    verb = "Would import" if args.dry_run else "Imported"
    print(f"{verb} {added} poem(s); skipped {dup} duplicate(s), {empty} empty row(s), "
          f"{len(junk)} web page(s). DB: {shayri.DB_PATH}")


if __name__ == "__main__":
    main()
