"""Devanagari -> readable Roman (Hinglish) transliteration: कौन-सा -> kaun-sa.

Not a scholarly scheme (no diacritics); it aims at how people type Hindi/Urdu
poetry in English: dil, zindagi, khwaab, aankhen, nahin, ghazal.

How it works: each word is split into aksharas (consonant + vowel), then the
Hindi "schwa deletion" rules decide which inherent "a" sounds are silent
(कमरा = kamra, not kamara; समझना = samajhna), then each part is spelled out.

    python3 translit.py "कौन-सा लालच? धरा की"          # try it
    python3 translit.py --backfill [--all] [--db PATH]  # fill the database
"""
import re
import unicodedata

VERSION = 2  # bump when the rules change, then run --backfill --all

CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "ळ": "l",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
}
# consonant + nukta (Urdu sounds); NFD splits क़ into क + ़
NUKTA = {"क": "q", "ख": "kh", "ग": "gh", "ज": "z", "झ": "zh", "ड": "d",
         "ढ": "dh", "फ": "f", "य": "y", "र": "r", "न": "n", "ल": "l"}
VOWELS = {  # independent vowel letters
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ii", "उ": "u", "ऊ": "uu",
    "ऋ": "ri", "ॠ": "ri", "ऌ": "li", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
    "ऍ": "e", "ऎ": "e", "ऑ": "o", "ऒ": "o",
}
MATRAS = {  # vowel signs
    "ा": "aa", "ि": "i", "ी": "ii", "ु": "u", "ू": "uu", "ृ": "ri", "ॄ": "ri",
    "ॢ": "li", "े": "e", "ै": "ai", "ो": "o", "ौ": "au",
    "ॅ": "e", "ॆ": "e", "ॉ": "o", "ॊ": "o",
}
VIRAMA, NUKTA_SIGN = "्", "़"
NASALS = "ंँ"
VISARGA = "ः"
LABIALS = set("पफबभम")
DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
PUNCT = {"।": ".", "॥": ".", "ॐ": "Om", "ऽ": "", "‌": "", "‍": ""}

# Spelling of each vowel: (inside a word, at the end of a word)
SPELL = {"aa": ("aa", "a"), "ii": ("ee", "i"), "uu": ("oo", "u")}
# Names read better without doubled vowels: Gulzar, Mahadevi, Suryakant
SPELL_NAME = {"aa": ("a", "a"), "ii": ("i", "i"), "uu": ("u", "u")}

# Very common words where the usual romanisation differs from the rules.
# Keys are looked up with nukta removed and ँ written as ं.
WORDS = {
    "में": "mein", "मैं": "main", "हैं": "hain", "नहीं": "nahin", "नहिं": "nahin",
    "हूं": "hoon", "क्यों": "kyon", "क्यूं": "kyun", "यूं": "yun", "वो": "wo",
    "वहां": "vahan", "यहां": "yahan", "कहां": "kahan", "जहां": "jahan", "तहां": "tahan",
    "हां": "haan", "मां": "maa", "सिंह": "singh", "तू": "tu", "तो": "to", "कि": "ki", "की": "ki",
}

DEVA_RUN = re.compile(r"[ऀ-ॣॱ-ॿ‌‍]+")


def _key(word):
    return word.replace(NUKTA_SIGN, "").replace("ँ", "ं")


def _units(word):
    """Split an NFD word into units: {raw, c, v, nasal, visarga}.

    c is the Roman consonant (None for a bare vowel letter); v is a vowel
    name, "a" for the inherent vowel, or None after a virama (half letter).
    """
    units, i, n = [], 0, len(word)
    while i < n:
        ch = word[i]
        if ch in CONSONANTS:
            c = CONSONANTS[ch]
            if i + 1 < n and word[i + 1] == NUKTA_SIGN:
                c = NUKTA.get(ch, c)
                i += 1
            u = {"raw": ch, "c": c, "v": "a", "nasal": False, "visarga": False}
            if i + 1 < n and word[i + 1] == VIRAMA:
                u["v"] = None
                i += 1
            elif i + 1 < n and word[i + 1] in MATRAS:
                u["v"] = MATRAS[word[i + 1]]
                i += 1
            units.append(u)
        elif ch in VOWELS:
            units.append({"raw": ch, "c": None, "v": VOWELS[ch], "nasal": False, "visarga": False})
        elif ch in NASALS and units:
            units[-1]["nasal"] = True
        elif ch == VISARGA and units:
            units[-1]["visarga"] = True
        i += 1  # stray nukta, virama or matra: skip

    for a, b in zip(units, units[1:]):
        if a["raw"] == "ज" and a["v"] is None and b["raw"] == "ञ":
            a["c"], b["c"] = "g", "y"                      # ज्ञान = gyaan
        elif b["raw"] == "व" and a["v"] is None and b["c"] == "v":
            b["c"] = "w"                                   # ख़्वाब = khwaab, स्वर = swar
    return units


def _has_vowel(u):
    return u["v"] is not None and not u.get("silent")


def _medial(units, i):
    """V C(a) C V -> the a is silent (कमरा = kamra)."""
    u, prev, nxt = units[i], units[i - 1], units[i + 1]
    if (u["c"] and u["v"] == "a" and not u["nasal"] and not u["visarga"]
            and nxt["c"] and _has_vowel(nxt)
            and _has_vowel(prev) and not prev["nasal"]):
        u["silent"] = True


def _delete_schwas(units):
    """Mark inherent vowels that Hindi does not pronounce (u["silent"])."""
    n = len(units)
    if n < 2:
        return
    last = units[-1]
    # plural/oblique ending (धड़कनें, महलों): treat the stem first, then the ending
    plural = n >= 3 and last["c"] and last["v"] in ("e", "o") and last["nasal"]
    if plural:
        ending = last["v"]
        last["v"], last["nasal"] = "a", False
    if last["c"] and last["v"] == "a" and not last["nasal"] and not last["visarga"]:
        # final "a" is silent, except after a cluster ending in r/y/v: mitra, satya
        if not (units[-2]["v"] is None and last["raw"] in "रयव"):
            last["silent"] = True
    # right to left, never two in a row
    for i in range(n - 2, 0, -1):
        _medial(units, i)
    if plural:
        last["v"], last["nasal"], last["silent"] = ending, True, False
        _medial(units, n - 2)


def _word(word, name=False):
    word = unicodedata.normalize("NFD", word)
    if _key(word) in WORDS:
        return WORDS[_key(word)]
    units = _units(word)
    _delete_schwas(units)
    spell = SPELL_NAME if name else SPELL
    out = []
    for i, u in enumerate(units):
        nxt = units[i + 1] if i + 1 < len(units) else None
        out.append(u["c"] or "")
        v = u["v"]
        if v and not u.get("silent"):
            final = nxt is None and (u["c"] or len(units) > 1)
            if v in spell:
                inner, end = spell[v]
                if v == "ii" and final and u["nasal"]:
                    v = "i"                                # कहीं = kahin
                else:
                    v = end if final and not u["nasal"] else inner
            out.append(v)
        if u["nasal"]:
            out.append("m" if nxt and nxt["raw"] in LABIALS and nxt["c"] else "n")
        if u["visarga"] and not (nxt and nxt["c"]):
            out.append("h")                                # अतः = atah, दुःख = dukh
    return "".join(out)


def to_latin(text, name=False):
    """Transliterate the Devanagari in text; everything else is kept as is."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    for k, v in PUNCT.items():
        text = text.replace(k, v)
    text = text.translate(DIGITS)
    return DEVA_RUN.sub(lambda m: _word(m.group(), name), text)


def _cap(s):
    return re.sub(r"[a-z]", lambda m: m.group().upper(), s, count=1)


def poem_latin(body):
    """Poem text, first letter of each line capitalised."""
    return "\n".join(_cap(line) for line in to_latin(body).split("\n"))


def title_latin(title):
    return _cap(to_latin(title))


def poet_latin(poet):
    """Poet name: every word capitalised, no doubled vowels (Faiz Ahmad Faiz)."""
    return re.sub(r"\b[a-z]", lambda m: m.group().upper(), to_latin(poet, name=True))


def latin_fields(data):
    return {"title_en": title_latin(data.get("title")), "poet_en": poet_latin(data.get("poet")),
            "body_en": poem_latin(data.get("body"))}


def backfill(db_path, everything=False):
    """Fill the English columns (only missing/outdated rows unless everything)."""
    import app  # adds the columns if needed
    db = app.connect()
    cond = "" if everything else " WHERE body_en IS NULL OR translit_v IS NOT ?"
    ids = [r[0] for r in db.execute("SELECT id FROM poems" + cond, () if everything else (VERSION,))]
    print(f"{len(ids)} poem(s) to update in {db_path}")
    for start in range(0, len(ids), 500):
        with db:
            for pid in ids[start:start + 500]:
                row = db.execute("SELECT title, poet, body FROM poems WHERE id=?", (pid,)).fetchone()
                en = latin_fields(dict(row))
                db.execute("UPDATE poems SET title_en=?, poet_en=?, body_en=?, translit_v=? WHERE id=?",
                           (en["title_en"], en["poet_en"], en["body_en"], VERSION, pid))
                app.reindex(db, pid)
        print(f"  {min(start + 500, len(ids))}/{len(ids)}", flush=True)
    db.close()


if __name__ == "__main__":
    import argparse
    import os
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="*", help="text to transliterate")
    ap.add_argument("--backfill", action="store_true", help="fill English columns in the database")
    ap.add_argument("--all", action="store_true", help="with --backfill: redo every poem")
    ap.add_argument("--db", help="database path (default: $SHAYRI_DB)")
    args = ap.parse_args()
    if args.backfill:
        if args.db:
            os.environ["SHAYRI_DB"] = args.db
        from app import DB_PATH
        backfill(DB_PATH, args.all)
    else:
        print(poem_latin(" ".join(args.text)))
