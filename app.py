"""Shayri - a personal collection of Hindi poems (Flask + SQLite FTS5).

Authentication is handled by Caddy (basic_auth) in front of this app; the app
itself only listens on localhost.
"""
import json
import os
import random
import re
import secrets
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, url_for)
from markupsafe import Markup, escape

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("SHAYRI_DB", str(BASE_DIR / "shayri.db"))
PER_PAGE = 20

SORTS = {
    "relevance": "Best match",
    "new": "Newest first",
    "old": "Oldest first",
    "updated": "Recently edited",
    "title": "Title",
    "poet": "Poet",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS poems (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    poet        TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '',   -- comma separated, no spaces around commas
    notes       TEXT NOT NULL DEFAULT '',   -- meanings of difficult words, context
    source      TEXT NOT NULL DEFAULT '',   -- book, link, etc.
    favorite    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS poems_poet ON poems(poet);
-- Search index holds a "folded" copy of the text (see fold()). The trigram
-- tokenizer gives substring matching, which works far better for Devanagari
-- than word tokenizers do.
CREATE VIRTUAL TABLE IF NOT EXISTS poems_fts USING fts5(
    title, poet, body, tags, notes, tokenize='trigram'
);
"""

FTS_COLS = ("title", "poet", "body", "tags", "notes")


def _secret_key():
    if os.environ.get("SHAYRI_SECRET"):
        return os.environ["SHAYRI_SECRET"]
    path = Path(DB_PATH).with_name(".shayri_secret")
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
app.secret_key = _secret_key()


# ---------------------------------------------------------------- text utils

def clean(s):
    """Normalise user input: NFC, unix newlines, no trailing spaces."""
    s = unicodedata.normalize("NFC", (s or "").replace("\r\n", "\n").replace("\r", "\n"))
    return "\n".join(line.rstrip() for line in s.split("\n")).strip()


def fold(s):
    """Search key: ignore nukta (ज़ = ज) and chandrabindu vs anusvara (हँ = हं).

    Hindi/Urdu poetry is typed inconsistently, so search should not care.
    """
    s = unicodedata.normalize("NFD", s or "")
    s = s.replace("़", "").replace("ँ", "ं")
    return unicodedata.normalize("NFC", s).casefold()


def term_regex(terms):
    """Regex that finds folded search terms inside the original (unfolded) text."""
    parts = []
    for t in sorted(set(terms), key=len, reverse=True):
        p = ""
        for ch in t:
            if ch == "ं":
                p += "[ँं]"
            else:
                p += re.escape(ch) + "़?"
        parts.append(p)
    return re.compile("|".join(parts), re.IGNORECASE) if parts else None


def highlight(text, terms):
    pat = term_regex(terms or [])
    if not pat or not text:
        return escape(text)
    text = unicodedata.normalize("NFD", text)
    out, last = [], 0
    for m in pat.finditer(text):
        out.append(escape(unicodedata.normalize("NFC", text[last:m.start()])))
        out.append(Markup("<mark>%s</mark>") % unicodedata.normalize("NFC", m.group()))
        last = m.end()
    out.append(escape(unicodedata.normalize("NFC", text[last:])))
    return Markup("").join(out)


def parse_terms(q):
    """Split a query into folded terms; "quoted phrases" stay together."""
    terms = []
    for quoted, word in re.findall(r'"([^"]+)"|(\S+)', q or ""):
        t = fold(quoted or word).strip()
        if t:
            terms.append(t)
    return terms


def split_tags(tags):
    return [t for t in (tags or "").split(",") if t]


def normalize_tags(raw):
    seen = []
    for t in re.split(r"[,\n،]", clean(raw)):
        t = " ".join(t.split()).lstrip("#")
        if t and t not in seen:
            seen.append(t)
    return ",".join(seen)


def first_line(body):
    for line in (body or "").split("\n"):
        if line.strip():
            return line.strip()
    return ""


def dup_key(body):
    return " ".join(fold(first_line(body)).split())


def excerpt(body, terms, n=4):
    """First n lines, or n lines around the first search hit."""
    lines = (body or "").split("\n")
    start = 0
    pat = term_regex(terms or [])
    if pat:
        for i, line in enumerate(lines):
            if pat.search(unicodedata.normalize("NFD", line)):
                start = max(0, i - 1) if i >= n else 0
                break
    chunk = lines[start:start + n]
    more = len(lines) > start + n
    return chunk, start > 0, more


app.jinja_env.filters["hl"] = highlight
app.jinja_env.filters["tags"] = split_tags
app.jinja_env.globals["SORTS"] = SORTS


def stanzas(body):
    """Split a poem into stanzas (lists of lines) at blank lines."""
    out = []
    for block in re.split(r"\n\s*\n", (body or "").strip()):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if lines:
            out.append(lines)
    return out


@app.template_filter("title_of")
def title_of(poem):
    return poem["title"] or first_line(poem["body"]) or "(untitled)"


@app.template_filter("date")
def fmt_date(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return ""


# ---------------------------------------------------------------- database

def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)


def reindex(db, pid):
    row = db.execute("SELECT * FROM poems WHERE id=?", (pid,)).fetchone()
    db.execute("DELETE FROM poems_fts WHERE rowid=?", (pid,))
    if row:
        db.execute(
            "INSERT INTO poems_fts(rowid, title, poet, body, tags, notes) VALUES (?,?,?,?,?,?)",
            (pid, fold(row["title"]), fold(row["poet"]), fold(row["body"]),
             fold(row["tags"].replace(",", " ")), fold(row["notes"])),
        )


def now():
    return datetime.now().isoformat(timespec="seconds")


def save_poem(db, data, pid=None):
    fields = ("title", "poet", "body", "tags", "notes", "source", "favorite")
    values = [data[f] for f in fields]
    if pid is None:
        ts = data.get("created_at") or now()
        cur = db.execute(
            f"INSERT INTO poems ({', '.join(fields)}, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            values + [ts, data.get("updated_at") or ts],
        )
        pid = cur.lastrowid
    else:
        db.execute(
            f"UPDATE poems SET {', '.join(f + '=?' for f in fields)}, updated_at=? WHERE id=?",
            values + [now(), pid],
        )
    reindex(db, pid)
    return pid


def find_duplicates(db, body, exclude_id=None):
    key = dup_key(body)
    if not key:
        return []
    rows = db.execute("SELECT id, title, poet, body FROM poems").fetchall()
    return [r for r in rows if r["id"] != exclude_id and dup_key(r["body"]) == key]


def all_poets(db):
    return [r[0] for r in db.execute(
        "SELECT DISTINCT poet FROM poems WHERE poet != '' ORDER BY poet")]


def tag_counts(db):
    counts = {}
    for (tags,) in db.execute("SELECT tags FROM poems WHERE tags != ''"):
        for t in split_tags(tags):
            counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def get_poem(pid):
    poem = get_db().execute("SELECT * FROM poems WHERE id=?", (pid,)).fetchone()
    if poem is None:
        abort(404)
    return poem


# ---------------------------------------------------------------- security

@app.before_request
def same_origin_posts():
    """Basic-auth credentials are sent automatically by browsers, so block
    cross-site form posts (CSRF) by checking where the request came from."""
    if request.method != "POST":
        return
    origin = request.headers.get("Origin")
    if origin:
        if urlparse(origin).netloc != request.host:
            abort(403)
    elif request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
        abort(403)


def safe_next(default):
    nxt = request.values.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return default


# ---------------------------------------------------------------- routes

@app.route("/")
def index():
    db = get_db()
    q = request.args.get("q", "").strip()
    by = request.args.get("by", "").strip()          # partial poet search
    poet = request.args.get("poet")                   # exact poet (from links)
    tag = request.args.get("tag", "").strip()
    fav = request.args.get("fav") == "1"
    terms = parse_terms(q)
    sort = request.args.get("sort") or ("relevance" if terms else "new")
    if sort not in SORTS or (sort == "relevance" and not terms):
        sort = "new"
    page = max(1, request.args.get("page", 1, type=int))

    joins, conds, params = [], [], []
    if terms or by:
        joins.append("JOIN poems_fts ON poems_fts.rowid = p.id")
    long_terms = [t for t in terms if len(t) >= 3]
    if long_terms:
        conds.append("poems_fts MATCH ?")
        params.append(" AND ".join('"%s"' % t.replace('"', '""') for t in long_terms))
    for t in terms:
        if len(t) < 3:  # trigram index needs 3+ characters; fall back to LIKE
            like = "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conds.append("(" + " OR ".join(f"poems_fts.{c} LIKE ? ESCAPE '\\'" for c in FTS_COLS) + ")")
            params += [like] * len(FTS_COLS)
    if by:
        like = "%" + fold(by).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conds.append("poems_fts.poet LIKE ? ESCAPE '\\'")
        params.append(like)
    if poet is not None:
        conds.append("p.poet = ?")
        params.append(poet)
    if tag:
        conds.append("(',' || p.tags || ',') LIKE ?")
        params.append(f"%,{tag},%")
    if fav:
        conds.append("p.favorite = 1")

    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    base = f"FROM poems p {' '.join(joins)}{where}"
    order = {
        "relevance": "bm25(poems_fts), p.id DESC" if long_terms else "p.id DESC",
        "new": "p.created_at DESC, p.id DESC",
        "old": "p.created_at ASC, p.id ASC",
        "updated": "p.updated_at DESC",
        "title": "CASE WHEN p.title='' THEN p.body ELSE p.title END",
        "poet": "p.poet = '', p.poet, p.created_at DESC",
    }[sort]
    total = db.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    pages = max(1, -(-total // PER_PAGE))
    page = min(page, pages)
    rows = db.execute(
        f"SELECT p.* {base} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [PER_PAGE, (page - 1) * PER_PAGE],
    ).fetchall()

    poems = []
    for r in rows:
        lines, cut_top, more = excerpt(r["body"], terms, n=2)
        poems.append({"row": r, "lines": lines, "cut_top": cut_top, "more": more})

    stats = db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT NULLIF(poet,'')), SUM(favorite) FROM poems").fetchone()
    filtering = bool(terms or by or poet is not None or tag or fav)
    return render_template(
        "index.html", poems=poems, total=total, page=page, pages=pages,
        q=q, by=by, poet=poet, tag=tag, fav=fav, sort=sort, terms=terms,
        filtering=filtering, stats=stats, poets=all_poets(db),
        tags=[t for t, _ in tag_counts(db)],
    )


@app.route("/poem/<int:pid>")
def view(pid):
    db = get_db()
    poem = get_poem(pid)
    terms = parse_terms(request.args.get("q", ""))
    more = db.execute(
        "SELECT id, title, body FROM poems WHERE poet=? AND poet!='' AND id!=? ORDER BY random() LIMIT 8",
        (poem["poet"], pid)).fetchall()
    prev_id = db.execute("SELECT max(id) FROM poems WHERE id<?", (pid,)).fetchone()[0]
    next_id = db.execute("SELECT min(id) FROM poems WHERE id>?", (pid,)).fetchone()[0]
    verses = stanzas(poem["body"])
    # ghazal / sher: every stanza is a couplet, so lines are spread to equal width
    couplets = bool(verses) and all(len(st) == 2 for st in verses)
    return render_template("poem.html", poem=poem, terms=terms, more=more,
                           prev_id=prev_id, next_id=next_id,
                           verses=verses, couplets=couplets)


def form_data():
    f = request.form
    return {
        "title": " ".join(clean(f.get("title")).split()),
        "poet": " ".join(clean(f.get("poet")).split()),
        "body": clean(f.get("body")),
        "tags": normalize_tags(f.get("tags", "")),
        "notes": clean(f.get("notes")),
        "source": clean(f.get("source")),
        "favorite": 1 if f.get("favorite") else 0,
    }


def render_form(poem, pid=None, dups=None):
    db = get_db()
    return render_template("form.html", poem=poem, pid=pid, dups=dups or [],
                           poets=all_poets(db), all_tags=[t for t, _ in tag_counts(db)])


@app.route("/add", methods=["GET", "POST"])
def add():
    db = get_db()
    if request.method == "GET":
        empty = {"title": "", "poet": request.args.get("poet", ""), "body": "",
                 "tags": "", "notes": "", "source": "", "favorite": 0}
        return render_form(empty)
    data = form_data()
    if not data["body"]:
        flash("Please write the poem text.", "error")
        return render_form(data)
    dups = find_duplicates(db, data["body"])
    if dups and not request.form.get("allow_duplicate"):
        return render_form(data, dups=dups)
    with db:
        pid = save_poem(db, data)
    flash("Poem saved.", "ok")
    if request.form.get("another"):
        return redirect(url_for("add", poet=data["poet"]))
    return redirect(url_for("view", pid=pid))


@app.route("/poem/<int:pid>/edit", methods=["GET", "POST"])
def edit(pid):
    db = get_db()
    poem = get_poem(pid)
    if request.method == "GET":
        return render_form(dict(poem), pid)
    data = form_data()
    if not data["body"]:
        flash("Please write the poem text.", "error")
        return render_form(data, pid)
    with db:
        save_poem(db, data, pid)
    flash("Changes saved.", "ok")
    return redirect(url_for("view", pid=pid))


@app.route("/poem/<int:pid>/delete", methods=["POST"])
def delete(pid):
    db = get_db()
    poem = get_poem(pid)
    with db:
        db.execute("DELETE FROM poems WHERE id=?", (pid,))
        reindex(db, pid)
    flash(f"Deleted “{title_of(poem)}”.", "ok")
    return redirect(url_for("index"))


@app.route("/poem/<int:pid>/favorite", methods=["POST"])
def favorite(pid):
    db = get_db()
    poem = get_poem(pid)
    new = 0 if poem["favorite"] else 1
    with db:
        db.execute("UPDATE poems SET favorite=? WHERE id=?", (new, pid))
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(favorite=bool(new))
    return redirect(safe_next(url_for("view", pid=pid)))


@app.route("/random")
def random_poem():
    db = get_db()
    cond, params = "", []
    if request.args.get("poet"):
        cond, params = " WHERE poet=?", [request.args["poet"]]
    elif request.args.get("fav") == "1":
        cond = " WHERE favorite=1"
    ids = [r[0] for r in db.execute("SELECT id FROM poems" + cond, params)]
    if not ids:
        flash("No poems yet - add one first!", "error")
        return redirect(url_for("index"))
    return redirect(url_for("view", pid=random.choice(ids)))


@app.route("/poets")
def poets():
    rows = get_db().execute(
        "SELECT poet, COUNT(*) AS n, SUM(favorite) AS favs FROM poems GROUP BY poet ORDER BY poet = '', poet"
    ).fetchall()
    groups = {}
    for r in rows:
        name = r["poet"]
        letter = unicodedata.normalize("NFD", name)[:1].upper() if name else "?"
        groups.setdefault(letter, []).append(r)
    return render_template("poets.html", groups=groups, count=len(rows))


@app.route("/tags")
def tags():
    return render_template("tags.html", tags=tag_counts(get_db()))


@app.route("/export.json")
def export_json():
    rows = get_db().execute("SELECT * FROM poems ORDER BY id").fetchall()
    data = [dict(r) | {"tags": split_tags(r["tags"])} for r in rows]
    stamp = datetime.now().strftime("%Y-%m-%d")
    return Response(
        json.dumps({"app": "shayri", "exported_at": now(), "poems": data},
                   ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=shayri-{stamp}.json"},
    )


@app.route("/import", methods=["GET", "POST"])
def import_poems():
    if request.method == "GET":
        return render_template("import.html")
    db = get_db()
    file = request.files.get("file")
    try:
        payload = json.load(file.stream) if file and file.filename else None
    except (ValueError, UnicodeDecodeError):
        payload = None
    items = payload.get("poems") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        flash("That doesn't look like a Shayri export (.json).", "error")
        return redirect(url_for("import_poems"))
    existing = {fold(" ".join(r[0].split())) for r in db.execute("SELECT body FROM poems")}
    added = skipped = 0
    with db:
        for it in items:
            if not isinstance(it, dict) or not clean(it.get("body")):
                skipped += 1
                continue
            tags = it.get("tags", "")
            if isinstance(tags, list):
                tags = ",".join(str(t) for t in tags)
            data = {
                "title": clean(it.get("title")), "poet": clean(it.get("poet")),
                "body": clean(it.get("body")), "tags": normalize_tags(tags),
                "notes": clean(it.get("notes")), "source": clean(it.get("source")),
                "favorite": 1 if it.get("favorite") else 0,
                "created_at": it.get("created_at"), "updated_at": it.get("updated_at"),
            }
            key = fold(" ".join(data["body"].split()))
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            save_poem(db, data)
            added += 1
    flash(f"Imported {added} poem(s); skipped {skipped} duplicate/empty.", "ok")
    return redirect(url_for("index"))


@app.route("/healthz")
def healthz():
    get_db().execute("SELECT 1")
    return "ok"


@app.errorhandler(404)
def not_found(_e):
    return render_template("message.html", title="Not found",
                           message="This poem doesn't exist (maybe it was deleted)."), 404


@app.errorhandler(403)
def forbidden(_e):
    return render_template("message.html", title="Not allowed",
                           message="This request came from another site and was blocked."), 403


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=8001)
