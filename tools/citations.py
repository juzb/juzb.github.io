#!/usr/bin/env python3
"""
citations.py - the publication list as plain text, for pasting into forms.

    python3 tools/citations.py juzb.tex                  # newest first, numbered
    python3 tools/citations.py juzb.tex --oldest-first
    python3 tools/citations.py juzb.tex --enrich         # add volume/pages from Crossref
    python3 tools/citations.py juzb.tex --ascii -o cites.txt

One citation per entry, full author lists, no LaTeX, no markup:

    1. A. Author, B. Author. Title. Journal 12, 345-356 (2024). https://doi.org/10.x/y

one citation per line, so a single line can go straight into a single field.

It reads the same \\section{Publications} the CV and the web page are built
from, so the list cannot drift from them. The CV carries authors, title,
journal, year and DOI but not volume or pages; --enrich fetches those from
Crossref and silently leaves them out if the network is unavailable.
"""

from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tex2html as T                                     # noqa: E402

HREF = re.compile(r"\\href\{(\S+?)\}\{(.*)\}\s*(?:\\\\)?\s*$")
YEAR = re.compile(r"\\hfill\s*\\href\{\S+?\}\{(\d{4})\}")
JOURNAL = re.compile(r"\s*\\textit\{(.*)\}\s*$")
DOI = re.compile(r"doi\.org/(10\.[^\s/]+/\S+)$", re.I)


def detex(s: str, mark_me: bool = False) -> str:
    """LaTeX fragment -> plain text, via the converter the page already uses,
    so one escape table serves both and an unknown macro still stops us."""
    h = T.convert(s)
    h = re.sub(r"<strong>(.*?)</strong>", r"**\1**" if mark_me else r"\1", h, flags=re.S)
    h = re.sub(r"<[^>]+>", "", h)
    return re.sub(r"\s+", " ", _html.unescape(h)).strip()


def parse(tex: str, mark_me: bool = False) -> list[dict]:
    lines = tex.split("\n")
    start = next(i for i, l in enumerate(lines) if l.strip() == "\\section{Publications}")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("\\section{")), len(lines))

    entries: list[dict] = []
    for raw in lines[start + 1:end]:
        line = re.sub(r"(?<!\\)%.*$", "", raw).strip()
        if not line:
            continue
        m = HREF.match(line)
        if not m:
            continue
        url, inner = m.group(1), m.group(2)
        if not entries or entries[-1]["url"] != url:
            entries.append({"url": url, "title": [], "authors": [], "journal": "",
                            "year": ""})
        e = entries[-1]
        y = YEAR.search(line)
        if y:
            e["year"] = y.group(1)
        jm = JOURNAL.search(inner)
        if jm:
            e["journal"] = detex(jm.group(1))
            inner = JOURNAL.sub("", inner)
        for t in re.findall(r"\\nobold\{(.*?)\}\}", line):
            e["title"].append(detex(t))
            inner = ""
        if inner.strip():
            e["authors"].append(detex(inner, mark_me))

    for e in entries:
        e["title"] = " ".join(e["title"]).strip()
        e["authors"] = " ".join(e["authors"]).strip().rstrip(".")
        d = DOI.search(e["url"])
        e["doi"] = d.group(1) if d else ""
    return entries


def enrich(entries: list[dict]) -> int:
    """Add volume/issue/pages from Crossref. Returns how many were filled."""
    filled = 0
    for e in entries:
        if not e["doi"]:
            continue
        try:
            req = urllib.request.Request(
                f"https://api.crossref.org/works/{e['doi']}",
                headers={"User-Agent": "cv-citations/1.0 (+https://juzb.github.io)"})
            with urllib.request.urlopen(req, timeout=15) as r:
                msg = json.load(r)["message"]
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            print(f"   note: no Crossref data for {e['doi']} ({type(exc).__name__})",
                  file=sys.stderr)
            continue
        e["volume"] = msg.get("volume", "")
        e["pages"] = (msg.get("page") or "").replace("-", "\u2013")
        filled += 1
    return filled


def format_one(e: dict) -> str:
    bits = []
    if e["authors"]:
        bits.append(e["authors"] + ".")
    if e["title"]:
        bits.append(e["title"] if e["title"].endswith(".") else e["title"] + ".")
    tail = e["journal"]
    if e.get("volume"):
        tail += f" {e['volume']}"
        if e.get("pages"):
            tail += f", {e['pages']}"
    if e["year"]:
        tail = (tail + f" ({e['year']})").strip()
    if tail:
        bits.append(tail + ("" if tail.endswith(".") else "."))
    out = " ".join(bits)
    link = f"https://doi.org/{e['doi']}" if e["doi"] else e["url"]
    return out, link


def to_ascii(s: str) -> str:
    s = (s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2019", "'")
          .replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"'))
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path, nargs="?", default=Path("juzb.tex"))
    ap.add_argument("-o", "--out", type=Path, help="write here instead of stdout")
    ap.add_argument("--oldest-first", action="store_true")
    ap.add_argument("--no-numbers", action="store_true")
    ap.add_argument("--mark-me", action="store_true",
                    help="wrap your own name in ** ** ")
    ap.add_argument("--ascii", action="store_true",
                    help="strip accents, for forms that mangle UTF-8")
    ap.add_argument("--enrich", action="store_true",
                    help="add volume and pages from Crossref (needs network)")
    args = ap.parse_args()

    try:
        # resolve_authors() turns \ablong back into a real line break and
        # expands the \shortauthors markup, so the export always carries the
        # full author list whatever the switch in the .tex is set to
        raw = T.resolve_authors(args.source.read_text(encoding="utf-8"))
        entries = parse(raw, args.mark_me)
    except T.TexError as exc:
        print(f"citations: {exc}", file=sys.stderr)
        return 1
    if not entries:
        print("citations: no publications found", file=sys.stderr)
        return 1

    if args.enrich:
        n = enrich(entries)
        print(f"   enriched {n}/{len(entries)} from Crossref", file=sys.stderr)

    entries.sort(key=lambda e: e["year"], reverse=not args.oldest_first)

    out = []
    for i, e in enumerate(entries, 1):
        body, link = format_one(e)
        prefix = "" if args.no_numbers else f"{i}. "
        out.append(f"{prefix}{body} {link}")
    text = "\n\n".join(out) + "\n"
    if args.ascii:
        text = to_ascii(text)

    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"citations: wrote {len(entries)} citations to {args.out}",
              file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
