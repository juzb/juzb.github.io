#!/usr/bin/env python3
"""
check_sync.py - fail the build if the generated page lost something the PDF has.

    python3 check_sync.py cv.pdf index.html

Both artefacts are built from the same .tex, so every word of the PDF must
also appear on the page.  The reverse is not checked: the page legitimately
carries extra content (portrait, intro, featured paper).

The comparison is forgiving about *form* and strict about *content*:

  * order is ignored, because the contact block is a row of columns in the
    PDF and a stack of lists on the page;
  * accents, case, punctuation, dashes and the build date are ignored;
  * a PDF word may be covered by part of a page word, because pdftotext
    drops f-ligatures and splits those words ("Eckhoff" comes out as
    "Eckho", "artificial" as "arti" + "cial").  One page word can cover
    several such fragments.

What it catches: a dropped entry, a title still stuck inside a macro, a
section the parser mis-split, a publication that lost its author list.

Needs `pdftotext` (poppler-utils).
"""

from __future__ import annotations

import collections
import re
import subprocess
import sys
import unicodedata

MIN_LEN = 3          # ignore very short words; ligature crumbs land there


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text)          # build date
    text = text.replace("’", "").replace("'", "")
    return [w for w in re.findall(r"[a-z0-9]+", text) if len(w) >= MIN_LEN]


def html_to_text(path: str) -> str:
    page = open(path, encoding="utf-8").read()
    page = re.sub(r"<(style|script)\b.*?</\1>", " ", page, flags=re.S)
    page = re.sub(r"<[^>]+>", " ", page)
    for ent, rep in (("&amp;", "&"), ("&nbsp;", " "), ("&ndash;", "-"),
                     ("&mdash;", "-"), ("&rsquo;", "'"), ("&lt;", "<"),
                     ("&gt;", ">"), ("&quot;", '"'), ("&copy;", "(c)")):
        page = page.replace(ent, rep)
    return page


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    pdf_path, html_path = sys.argv[1], sys.argv[2]

    try:
        pdf_text = subprocess.run(["pdftotext", "-raw", pdf_path, "-"],
                                  capture_output=True, check=True,
                                  text=True).stdout
    except FileNotFoundError:
        print("check_sync: pdftotext not installed - skipping check", file=sys.stderr)
        return 0

    # -raw, not -layout: the year sits in a zero-width box at the start of its
    # entry, so in layout order it can land between the two halves of a
    # hyphenated word ("... of pro-  2019" / "teins.").  Content order keeps
    # the halves together, which is what the de-hyphenation below needs.
    #
    # The year column lets TeX hyphenate, so a word can be split across two
    # lines.  Joining every such pair blindly would also weld real compounds
    # ("light-switchable"), so the join below is left to the pool check.
    pool = collections.Counter(words(html_to_text(html_path)))
    pdf_words = words(pdf_text)

    # A hyphenated break whose hyphen did not survive extraction (Type 3
    # bitmap fonts drop it) leaves two adjacent fragments.  Join a pair back
    # up only when the page actually has the whole word and neither half is a
    # word in its own right, so this can never paper over missing content.
    joined, i = [], 0
    while i < len(pdf_words):
        if i + 1 < len(pdf_words):
            a, b = pdf_words[i], pdf_words[i + 1]
            if pool[a + b] > 0 and not pool[a] and not pool[b]:
                joined.append(a + b)
                i += 2
                continue
        joined.append(pdf_words[i])
        i += 1
    pdf_words = joined
    missing: list[str] = []

    for w in pdf_words:
        if pool[w] > 0:
            pool[w] -= 1
            continue
        # ligature fallback: the PDF word is the head or the tail of a page
        # word.  Consume that part and leave the remainder available, so one
        # page word can cover both halves of a split.
        cands = sorted((c for c, n in pool.items() if n > 0 and len(c) > len(w)),
                       key=len)
        for cand in cands:
            if cand.startswith(w):
                rest = cand[len(w):]
            elif cand.endswith(w):
                rest = cand[:-len(w)]
            else:
                continue
            # only accept it if the missing piece contains an f: every
            # ligature pdftotext drops here is ff / fi / fl / ffi / ffl.
            # Without this guard "mann" (from "Hoffmann") would happily eat
            # "Hollmann" and report a false difference.
            if "f" not in rest:
                continue
            pool[cand] -= 1
            if rest:
                pool[rest] += 1
            break
        else:
            missing.append(w)

    if missing:
        uniq = collections.Counter(missing)
        print(f"check_sync: FAIL - {len(missing)} word(s) in the PDF are not "
              f"on the page:", file=sys.stderr)
        for w, n in uniq.most_common(25):
            print(f"  {w}" + (f" (x{n})" if n > 1 else ""), file=sys.stderr)
        if len(uniq) > 25:
            print(f"  ... and {len(uniq) - 25} more", file=sys.stderr)
        print("  Something in the .tex did not reach index.html: a dropped "
              "entry, an unparsed macro, a mis-split section.", file=sys.stderr)
        return 1

    print(f"check_sync: OK - all {len(pdf_words)} PDF words appear on the page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
