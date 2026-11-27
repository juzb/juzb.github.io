#!/usr/bin/env python3
"""
tex2html.py - build index.html (a GitHub Pages CV) from the CV LaTeX source.

The LaTeX file stays the single source of truth.  Web-only content (photo,
intro text, featured paper) lives in the .tex as specially marked comments,
so LaTeX ignores it completely and the PDF is unchanged.

    %%% BEGIN web.intro
    % Some text, **bold**, *italic*, [a link](https://example.org).
    %
    % A second paragraph.
    %%% END web.intro

Usage:
    python3 tex2html.py cv.tex -o index.html [--pdf cv.pdf] [--line-breaks soft]

Design rule: this converter is STRICT.  Any LaTeX macro it does not know
about aborts the build with a message instead of silently emitting junk.
Add unknown macros to MACROS / IGNORE below when you introduce them.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# macro table - everything the body of the CV is allowed to use
# --------------------------------------------------------------------------

# \macro{arg} -> wrap the converted argument in these tags
MACROS = {
    "textbf": ("<strong>", "</strong>"),
    "textit": ("<em>", "</em>"),
    "emph": ("<em>", "</em>"),
    "texttt": ("<code>", "</code>"),
    "textsc": ('<span class="sc">', "</span>"),
    "underline": ("<u>", "</u>"),
    # \\nobold is the identity macro in the .tex; on the web it marks the
    # title of a publication, which is what makes title/author splitting work
    "nobold": ('<span class="ptitle">', "</span>"),
    "normalfont": ("", ""),
    "normalsize": ("", ""),
    # the \shortauthors markup: resolve_authors() normally expands these away
    # before conversion, so this is for any other reader of the source - it
    # always keeps the full author list, which is what the web and the
    # citation export both want
    "corr": ("", ""),
    "au": ("", ""),
    "auetal": ("", ""),
    "auetalend": ("", ""),
}

# \macro{arg} -> drop the macro AND its argument
DROP_WITH_ARG = {"phantomsection", "label", "index"}

# \macro -> drop, no argument
IGNORE = {"hfill", "noindent", "par", "today", "isotoday", "scshape", "bfseries",
          "itshape", "small", "large", "Large", "centering", "raggedright",
          "ablong", "abshort", "yearcolumn", "sloppy"}

# \x -> literal character
CHAR_ESCAPES = {"&": "&", "%": "%", "_": "_", "#": "#", "$": "$",
                "{": "{", "}": "}", " ": " "}

KNOWN_WEB_KEYS = {
    "web.photo", "web.photo.credit", "web.photo.alt",
    "web.intro", "web.links",
    "web.featured.title", "web.featured.url", "web.featured.venue",
    "web.featured.abstract", "web.featured.figure", "web.featured.caption",
    "web.featured.repo",
    "web.viva", "web.viva.show",
    "web.impressum", "web.impressum.show",
    "web.privacy", "web.privacy.show",
}

LEGAL_FILE = "legal.html"       # one page holding both notices


class TexError(Exception):
    pass


TRUE = {"yes", "y", "true", "on", "1", "ja"}
FALSE = {"no", "n", "false", "off", "0", "nein", ""}


def flag(blocks: dict, key: str, default: bool = False) -> bool:
    """Read an on/off web block. Anything unrecognised is an error, not a
    silent 'off' - a typo there would quietly unpublish a legal notice."""
    raw = blocks.get(key)
    if raw is None:
        return default
    v = raw.strip().lower()
    if v in TRUE:
        return True
    if v in FALSE:
        return False
    fail(f"{key} must be yes or no, not {raw.strip()!r}")


def fail(msg: str, context: str = "") -> None:
    if context:
        snippet = " ".join(context.split())[:160]
        msg = f"{msg}\n  near: {snippet}"
    raise TexError(msg)


# --------------------------------------------------------------------------
# stage 1 - web blocks (read BEFORE comments are stripped)
# --------------------------------------------------------------------------

BEGIN_RE = re.compile(r"^%%%\s*BEGIN\s+(\S+)\s*$")
END_RE = re.compile(r"^%%%\s*END\s+(\S+)\s*$")


def extract_web_blocks(raw: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    key = None
    buf: list[str] = []
    for lineno, line in enumerate(raw.splitlines(), 1):
        stripped = line.strip()
        m = BEGIN_RE.match(stripped)
        if m:
            if key is not None:
                fail(f"line {lineno}: BEGIN {m.group(1)} inside unclosed block {key}")
            key = m.group(1)
            if key not in KNOWN_WEB_KEYS:
                fail(f"line {lineno}: unknown web block '{key}'. "
                     f"Known keys: {', '.join(sorted(KNOWN_WEB_KEYS))}")
            if key in blocks:
                fail(f"line {lineno}: duplicate web block '{key}'")
            buf = []
            continue
        m = END_RE.match(stripped)
        if m:
            if key is None:
                fail(f"line {lineno}: END {m.group(1)} without BEGIN")
            if m.group(1) != key:
                fail(f"line {lineno}: END {m.group(1)} closes block {key}")
            blocks[key] = "\n".join(buf).strip("\n")
            key = None
            continue
        if key is not None:
            if not stripped.startswith("%"):
                fail(f"line {lineno}: non-comment line inside web block '{key}' "
                     f"(every line must start with %)")
            body = stripped[1:]
            buf.append(body[1:] if body.startswith(" ") else body)
    if key is not None:
        fail(f"web block '{key}' is never closed")
    return blocks


# --------------------------------------------------------------------------
# stage 2 - strip comments, isolate the document body
# --------------------------------------------------------------------------

def strip_comments(raw: str) -> str:
    """Remove % comments.  A line that is *only* a comment disappears
    completely - TeX swallows it together with its newline - otherwise a
    comment in the middle of an entry would look like a paragraph break."""
    out = []
    for line in raw.splitlines():
        res, i, had_comment = [], 0, False
        while i < len(line):
            c = line[i]
            if c == "\\" and i + 1 < len(line):
                res.append(line[i:i + 2])
                i += 2
                continue
            if c == "%":
                had_comment = True
                break
            res.append(c)
            i += 1
        kept = "".join(res)
        if had_comment and not kept.strip() and line.strip():
            continue          # comment-only line: drop it, newline included
        out.append(kept)
    return "\n".join(out)


def resolve_year(raw: str) -> str:
    """Put \\yr{...} back where the year used to sit.

    In the PDF the year is a zero-width box at the start of an entry, so that
    it lands on the entry's first line however the title wraps.  The page has
    no such column, and its parser expects the old "title \\hfill year" shape,
    so each \\yr is moved to the end of its own source line.
    """
    out = []
    for line in raw.split("\n"):
        i = line.find("\\yr{")
        if i == -1:
            out.append(line)
            continue
        year, k = read_group(line, i + len("\\yr"))
        rest = (line[:i] + line[k:]).rstrip()
        br = ""
        if rest.endswith("\\\\"):
            rest, br = rest[:-2].rstrip(), "\\\\"
        out.append(f"{rest} \\hfill {year}{br}")
    return "\n".join(out)


def resolve_authors(raw: str) -> str:
    """Expand the \\shortauthors markup away, always to the full author list.

    \\au, \\auetal and \\auetalend hide the middle authors in the PDF when the
    switch is on; \\corr marks the ones kept regardless.  The page scrolls, so
    it always shows every author - and by rewriting the markup into what the
    source looked like before it existed, the generated HTML stays identical
    whatever the switch is set to.
    """
    out = []
    i = 0
    while True:
        m = re.compile(r"\\(au|auetal|auetalend|corr)\{").search(raw, i)
        if not m:
            out.append(raw[i:])
            break
        out.append(raw[i:m.start()])
        body, k = read_group(raw, m.end() - 1)
        out.append(body)
        i = k
    text = "".join(out)
    # only spaces and tabs, never the newline: the line break after \\ is what
    # separates one author line from the next
    text = re.sub(r"\\ablong(?:\{\})?[ \t]*", r"\\\\", text)   # the PDF's long-form break
    text = re.sub(r"\\abshort(?:\{\})?[ \t]*", "", text)      # only exists in short mode
    return text


def resolve_disstitle(raw: str) -> str:
    """Expand \\disstitle{...} before anything else sees it.

    The dissertation link is a LaTeX \\newif in the preamble, because it has to
    switch the PDF as well as this page. The preamble never reaches convert(),
    so read the flag and the URL here and rewrite the call into a plain \\href
    (or into bare text), which the converter already knows how to handle."""
    on = re.search(r"^\s*\\disslink(true|false)\s*$", raw, re.M)
    linked = bool(on) and on.group(1) == "true"
    m = re.search(r"\\newcommand\{\\dissurl\}\{([^}]*)\}", raw)
    url = m.group(1) if m else ""
    if linked and not url:
        fail("\\disslinktrue is set but \\dissurl is empty")
    out, i = [], 0
    while True:
        j = raw.find("\\disstitle{", i)
        if j < 0:
            out.append(raw[i:])
            return "".join(out)
        out.append(raw[i:j])
        body, k = read_group(raw, j + len("\\disstitle"))
        out.append(f"\\href{{{url}}}{{{body}}}" if linked else body)
        i = k


def document_body(src: str) -> str:
    try:
        body = src.split(r"\begin{document}", 1)[1]
        return body.split(r"\end{document}", 1)[0]
    except IndexError:
        fail(r"could not find \begin{document} ... \end{document}")


# --------------------------------------------------------------------------
# stage 3 - inline LaTeX -> HTML
# --------------------------------------------------------------------------

def read_group(s: str, i: int) -> tuple[str, int]:
    """Read a balanced {...} group starting at or after index i."""
    while i < len(s) and s[i].isspace():
        i += 1
    if i >= len(s) or s[i] != "{":
        fail("expected '{'", s[max(0, i - 40):i + 40])
    depth, start = 0, i
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1:i], i + 1
        i += 1
    fail("unbalanced braces", s[start:start + 80])


def convert(s: str) -> str:
    """Convert an inline LaTeX fragment to HTML."""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+)\*?", s[i:])
            if m:
                name = m.group(1)
                i += m.end()
                if name in MACROS:
                    arg, i = read_group(s, i)
                    pre, post = MACROS[name]
                    out.append(pre + convert(arg) + post)
                elif name == "href":
                    url, i = read_group(s, i)
                    txt, i = read_group(s, i)
                    url = " ".join(url.split()).replace("\\", "")
                    out.append(f'<a href="{html.escape(url, quote=True)}">'
                               f"{convert(txt)}</a>")
                elif name in DROP_WITH_ARG:
                    _, i = read_group(s, i)
                elif name in IGNORE:
                    pass
                else:
                    fail(f"unknown macro \\{name} - add it to MACROS or IGNORE "
                         "in tex2html.py", s[max(0, i - 60):i + 60])
            else:
                nxt = s[i + 1] if i + 1 < n else ""
                if nxt in CHAR_ESCAPES:
                    out.append(html.escape(CHAR_ESCAPES[nxt]))
                    i += 2
                else:
                    fail(f"unsupported escape '\\{nxt}'", s[max(0, i - 60):i + 60])
        elif c in "{}":
            i += 1
        elif c == "~":
            out.append("&nbsp;")
            i += 1
        elif s.startswith("---", i):
            out.append("&mdash;")
            i += 3
        elif s.startswith("--", i):
            out.append("&ndash;")
            i += 2
        elif c == "'":
            out.append("&rsquo;")
            i += 1
        else:
            out.append(html.escape(c))
            i += 1
    return "".join(out)


def tidy(fragment: str) -> str:
    """Collapse the whitespace that LaTeX would have collapsed anyway."""
    return re.sub(r"\s+", " ", fragment).strip()


def strip_links(fragment: str) -> str:
    return re.sub(r"</?a\b[^>]*>", "", fragment)


def dedupe_links(fragment: str) -> str:
    """Keep the first <a> of each target in an entry, unwrap later duplicates.

    In the Publications section every source line is individually wrapped in
    \\href{doi}{...}; on the web only the title should be the link.
    """
    seen: set[str] = set()
    out, pos = [], 0
    for m in re.finditer(r'<a href="([^"]+)">(.*?)</a>', fragment, re.S):
        out.append(fragment[pos:m.start()])
        if m.group(1) in seen:
            out.append(m.group(2))
        else:
            seen.add(m.group(1))
            out.append(m.group(0))
        pos = m.end()
    out.append(fragment[pos:])
    return "".join(out)


def linkify_dois(text: str) -> str:
    """Escape `text`, turning any DOI in it into a doi.org link.

    The venue line is written as plain prose in the .tex, so the DOI is
    spelled `doi:10.1234/xyz` (or already as a URL).  Splitting on the match
    and escaping the parts separately means the link is built from the raw
    DOI, so an escaped entity can never end up inside the href.
    """
    pat = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)(10\.\d{4,9}/\S+)",
                     re.I)
    out, pos = [], 0
    for m in pat.finditer(text):
        doi = m.group(1).rstrip(".,;:)]}")           # prose punctuation, not DOI
        tail = m.group(1)[len(doi):]
        out.append(html.escape(text[pos:m.start()]))
        out.append(f'<a href="https://doi.org/{html.escape(doi, quote=True)}">'
                   f"{html.escape(m.group(0)[:len(m.group(0)) - len(tail)])}</a>")
        out.append(html.escape(tail))
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def pretty_date(text: str) -> str:
    text = strip_links(tidy(text))
    return re.sub(r"(\d{4})\s*-\s*(\d{4})", r"\1 &ndash; \2", text)


# --------------------------------------------------------------------------
# stage 4 - structure
# --------------------------------------------------------------------------

BIB: dict[str, tuple[str, str]] = {}      # link -> (key, raw BibTeX entry)


def read_bib(path) -> dict[str, tuple[str, str]]:
    """Map each work's link to its BibTeX source, so the page can offer it.

    The key is the same URL the .tex links to - doi.org for anything with a
    DOI, the url field otherwise - which is how an entry on the page finds
    its own BibTeX without needing a citation key in the LaTeX.
    """
    text = Path(path).read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        i = text.index("{", m.start())
        depth, j = 0, i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        raw = text[m.start():j + 1]
        body = text[i + 1:j]
        doi = re.search(r"\bdoi\s*=\s*\{([^}]*)\}", body)
        url = re.search(r"\burl\s*=\s*\{([^}]*)\}", body)
        link = ("https://doi.org/" + doi.group(1)) if doi else (url.group(1) if url else None)
        if link:
            out[link] = (m.group(2), raw)
    return out


def cite_links(fragment: str) -> str:
    """The doi/url and bib links that close a publication entry."""
    m = re.search(r'<a href="([^"]+)"', fragment)
    if not m or m.group(1) not in BIB:
        return ""
    link = m.group(1)
    key, _ = BIB[link]
    if link.startswith("https://doi.org/"):
        ident, word, hover = link[len("https://doi.org/"):], "doi", "copy DOI"
    else:
        ident, word, hover = link, "url", "copy URL"
    return ('<p class="cite">'
            f'<a href="{html.escape(link, quote=True)}" '
            f'data-copy="{html.escape(ident, quote=True)}" title="{hover}">{word}</a>'
            f'<a href="juzb.bib" data-bib="{html.escape(key, quote=True)}" '
            'title="copy bibtex string">bib</a></p>')


class Entry:
    def __init__(self, lead: str, detail: list[str], date: str):
        self.lead, self.detail, self.date = lead, detail, date


def parse_entry(chunk: str, section: str = "") -> Entry | None:
    lines = [ln for ln in re.split(r"\\\\", chunk)]
    date = ""
    parts: list[str] = []
    for line in lines:
        if "\\hfill" in line:
            before, after = line.split("\\hfill", 1)
            if not date:
                date = pretty_date(convert(after))
            else:  # a second \hfill in one entry: keep its text inline
                before = before + after
            line = before
        frag = tidy(convert(line))
        parts.append(frag)
    parts = [p for p in parts if p]
    if not parts:
        return None
    if section.lower() == "publications":
        # Title = the leading run(s) the source marks with \nobold; the rest
        # (authors, venue) reflows into one paragraph, because those line
        # breaks only exist to fit the PDF column.
        titles, i = [], 0
        while i < len(parts) and 'class="ptitle"' in parts[i]:
            titles.append(parts[i])
            i += 1
        if titles:
            # A title that the PDF breaks over several lines arrives as several
            # \href chunks sharing one DOI. Fuse them into a single link, so the
            # whole title is clickable and carries one unbroken underline
            # instead of the first chunk only (dedupe_links would otherwise
            # unwrap every chunk after the first).
            hrefs = re.findall(r'<a href="([^"]+)">', " ".join(titles))
            chunks = []
            for t in titles:
                t = strip_links(t).strip()
                m = re.fullmatch(r'<span class="ptitle">(.*)</span>', t, re.S)
                chunks.append(m.group(1) if m else t)
            text = f'<span class="ptitle">{tidy(" ".join(chunks))}</span>'
            lead = f'<a href="{hrefs[0]}">{text}</a>' if hrefs else text
            rest = parts[i:]
        else:                                   # \nobold not used: fall back
            lead, rest = parts[0], parts[1:]
        detail = [" ".join(rest)] if rest else []
    elif date:
        lead, detail = parts[0], parts[1:]
    else:          # no year: the whole entry is one flowing paragraph
        lead, detail = "", parts
    return Entry(lead, detail, date)


def split_entries(text: str) -> list[str]:
    text = re.sub(r"\\vspace\{[^{}]*\}", "\n\n", text)
    text = re.sub(r"\\(?:blankline|halfblankline|newpage|pagebreak)\b", "\n\n", text)
    return [c.strip() for c in re.split(r"\n\s*\n", text) if c.strip()]


def parse_contact(sec: str) -> list[list[str]]:
    """Each \\begin{tabular} ... \\end{tabular} is one column of the block."""
    cols = []
    for m in re.finditer(r"\\begin\{tabular\}", sec):
        i = m.end()
        if sec[i:i + 1] == "[":                       # optional [t]
            i = sec.index("]", i) + 1
        _, i = read_group(sec, i)                     # the column spec {@{}l@{}}
        end = sec.index(r"\end{tabular}", i)
        items = [tidy(convert(x)) for x in re.split(r"\\\\", sec[i:end])]
        items = [x for x in items if x]
        if items:
            cols.append(items)
    return cols


def parse_document(body: str):
    head = body.split(r"\section{", 1)[0]
    m = re.search(r"\\makeheading", head)
    if not m:
        fail(r"no \makeheading found")
    i = m.end()
    if head[i:].lstrip().startswith("["):      # skip the optional argument
        i = head.index("[", i)
        depth = 0
        while i < len(head):
            if head[i] == "[":
                depth += 1
            elif head[i] == "]":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
    name_tex, _ = read_group(head, i)
    name = tidy(convert(name_tex))

    sections = []
    for part in body.split(r"\section{")[1:]:
        title_tex, rest = read_group("{" + part, 0)
        rest = part[len(title_tex) + 1:]
        sections.append((tidy(convert(title_tex)), rest))
    return name, sections


# --------------------------------------------------------------------------
# stage 5 - tiny markdown for the web blocks
# --------------------------------------------------------------------------

MD_LINK = re.compile(r"\[([^\]]+)\]\(((?:https?://|mailto:)[^)\s]+)\)")


def mdlinks(s: str) -> str:
    return MD_LINK.sub(r'<a href="\2">\1</a>', s)


def md(text: str) -> str:
    """Render a web block.

    LaTeX first - \\href{url}{text}, \\textbf{...}, \\textit{...}, \\&, ~ and
    friends all work, because these blocks live in a .tex file and that is
    what the fingers type.  Markdown (**bold**, *italic*, [text](url)) works
    too.  A blank "%" line starts a new paragraph.  An unknown macro stops
    the build, same as in the CV body."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out = []
    for p in paras:
        s = convert(" ".join(p.split()))
        s = mdlinks(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
        out.append(f"<p>{s}</p>")
    return "\n".join(out)


# --------------------------------------------------------------------------
# stage 6 - render
# --------------------------------------------------------------------------

CSS = """
:root{
  --fg:#15171a; --muted:#5c6066; --faint:#6e7379; --rule:#d7d9dd; --bg:#fff;
  --label-col:7.5rem; --gap:1.6rem;
}
@media (prefers-color-scheme: dark){
  :root{ --fg:#e6e7e9; --muted:#9aa0a6; --faint:#80868d; --rule:#33363b; --bg:#15171a; }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);
  font-family:"Iowan Old Style","Charter","Palatino Linotype",Palatino,Georgia,"Times New Roman",serif;
  font-size:15.5px;line-height:1.45;text-rendering:optimizeLegibility}
.page{max-width:62rem;margin:0 auto;padding:3rem 1.25rem 5rem}
a{color:inherit;text-decoration:underline;text-decoration-thickness:.5px;
  text-underline-offset:2px;text-decoration-color:var(--rule)}
a:hover{text-decoration-color:currentColor}
.sc{font-variant:small-caps}

header.masthead{display:flex;align-items:baseline;justify-content:space-between;
  gap:1rem;flex-wrap:wrap;border-bottom:1.5px solid var(--fg);
  padding-bottom:.35rem;margin-bottom:1.6rem}
h1{font-size:1.65rem;line-height:1.1;margin:0;font-weight:700;letter-spacing:-.01em}
.meta{font-size:.82rem;color:var(--muted);white-space:nowrap}
.meta a{margin-left:.9rem}

/* intro: portrait + text */
.hero{display:grid;grid-template-columns:1fr 1fr;gap:2rem;margin-bottom:2.4rem;
  align-items:start}
.hero.no-photo{grid-template-columns:1fr}
.hero figure{margin:0;grid-column:2;grid-row:1}   /* portrait sits right */
.hero .intro{grid-column:1;grid-row:1;min-width:0}
.hero img{width:100%;height:auto;display:block;border-radius:2px}
.hero figcaption, .credit{font-size:.72rem;color:var(--muted);margin-top:.35rem;line-height:1.3}
.hero p{margin:0 0 .7rem}
.hero p:last-child{margin-bottom:0}
.links{margin:1rem 0 0;font-size:.92rem}
.links a{color:var(--fg);text-decoration-color:var(--rule)}
.links .sep{color:var(--muted);margin:0 .55rem;user-select:none}

/* featured paper */
.featured{margin:0 0 2.4rem;padding:1.1rem 0 0;border-top:1px solid var(--rule)}
.featured h2{font-size:.88rem;font-weight:400;font-variant:small-caps;
  letter-spacing:.06em;color:var(--muted);margin:0 0 .5rem}
.featured .paper-title{font-size:1.12rem;font-weight:700;margin:0 0 .2rem;line-height:1.25}
.featured .venue{color:var(--muted);font-size:.88rem;margin:0 0 .8rem}
.featured .cols{display:grid;grid-template-columns:1fr 1fr;gap:1.8rem;align-items:start}
.featured figure{margin:0}
.featured img{width:100%;height:auto;display:block;border:1px solid var(--rule);border-radius:2px}
.featured .abstract{margin:0;text-align:justify;hyphens:auto}

.cv-heading{display:flex;align-items:baseline;justify-content:space-between;
  gap:1rem;flex-wrap:wrap;margin:0 0 1.4rem;padding-bottom:.35rem;
  border-bottom:1.5px solid var(--fg)}
.cv-heading h2{font-size:1.25rem;font-weight:700;margin:0}
.cv-heading .who{color:var(--muted);margin:.15rem 0 0;font-size:.95rem}
.cv-heading .dl{font-size:.9rem;white-space:nowrap}

/* the CV body reads in one colour: the entry text, its sub-lines, the dates
   and the section labels are all --muted, so nothing in the block sits at a
   different weight of grey */
section.cv{display:grid;grid-template-columns:var(--label-col) 1fr;
  column-gap:var(--gap);margin-bottom:1.5rem;color:var(--muted)}
section.cv > h2{grid-column:1;margin:.1rem 0 0;font-size:.88rem;font-weight:400;
  font-variant:small-caps;letter-spacing:.06em;color:var(--muted)}
.entries{grid-column:2;display:flex;flex-direction:column;gap:.7rem;min-width:0}
.entry{display:grid;grid-template-columns:1fr auto;column-gap:1.5rem;
  align-items:baseline;break-inside:avoid}
.entry .body{min-width:0}
.entry p{margin:0}
.entry .date{font-size:.9rem;white-space:nowrap;
  font-variant-numeric:tabular-nums;text-align:right}
.contact{grid-column:2;display:flex;flex-wrap:wrap;gap:.3rem 3rem;justify-content:space-between}
.contact ul{list-style:none;margin:0;padding:0}
.contact li{white-space:nowrap}

@media (max-width:46rem){
  .featured .cols{grid-template-columns:1fr}
  .hero{grid-template-columns:1fr;gap:1.1rem}
  .hero figure, .hero .intro{grid-column:1;grid-row:auto}
  .hero figure{max-width:22rem}
}
@media (max-width:40rem){
  .page{padding:2rem 1rem 3rem}
  .hero{grid-template-columns:1fr;gap:1rem}
  .hero figure, .hero .intro{grid-column:1;grid-row:auto}
  .hero figure{max-width:18rem}
  section.cv{grid-template-columns:1fr;row-gap:.45rem;margin-bottom:1.75rem}
  section.cv > h2{border-bottom:1px solid var(--rule);padding-bottom:.2rem}
  .entries,.contact{grid-column:1}
  .entry{grid-template-columns:1fr;row-gap:.1rem}
  .entry .date{text-align:left;order:-1;font-size:.8rem}
  .contact{flex-direction:column;gap:.9rem}
}
/* short biography: off unless web.viva.show says otherwise */
.bio{margin:0 0 2.4rem;padding:1.1rem 0 0;border-top:1px solid var(--rule)}
.bio h2{font-size:.88rem;font-weight:400;font-variant:small-caps;
  letter-spacing:.06em;color:var(--muted);margin:0 0 .6rem}
.bio p{margin:0 0 .7rem;max-width:46rem}

/* legal notices: as quiet as they can be while still being findable - the
   law wants them "leicht erkennbar", so --faint stays above 4.5:1 contrast */
.cite{margin:.15rem 0 0;font-size:.74rem;letter-spacing:.02em}
.cite a{color:var(--faint);text-decoration:none;border-bottom:1px solid var(--rule);
  margin-right:.9em;cursor:pointer}
.cite a:hover{color:var(--fg);border-bottom-color:currentColor}
.legalbar{margin:4rem 0 0;padding-top:.9rem;border-top:1px solid var(--rule);
  font-size:.74rem;color:var(--faint);text-align:right;letter-spacing:.02em}
.legalbar a{color:inherit;text-decoration-color:var(--rule)}
.legal h2{font-size:.88rem;font-weight:400;font-variant:small-caps;
  letter-spacing:.06em;color:var(--muted);margin:0 0 .6rem}
.legal section{margin:0 0 2.6rem;max-width:42rem}
.legal p{margin:0 0 .7rem}
.legal .addr{line-height:1.6}
.legal .back{font-size:.8rem;color:var(--faint)}
.legal .masthead h1 a{text-decoration:none}
.legal .masthead h1 a:hover{text-decoration:underline;
  text-decoration-thickness:.5px;text-underline-offset:3px;
  text-decoration-color:var(--rule)}

@media print{
  :root{--fg:#000;--muted:#333;--rule:#999;--bg:#fff;--label-col:6.5rem;--gap:1rem}
  body{font-size:9.5pt;line-height:1.35}
  .page{max-width:none;padding:0}
  .meta a, .hero, .featured, .bio, .cv-heading .dl, .cv-heading .who,
  .legalbar{display:none}
  a{text-decoration:none}
  section.cv{margin-bottom:1rem}
  .entries{gap:.45rem}
}
"""


def render(name, sections, blocks, pdf_name, line_breaks):
    br = "<br>\n" if line_breaks == "keep" else " "
    today = _dt.date.today().isoformat()
    o: list[str] = []
    a = o.append

    a("<!DOCTYPE html>")
    a('<html lang="en">')
    a("<head>")
    a('<meta charset="utf-8">')
    a('<meta name="viewport" content="width=device-width, initial-scale=1">')
    a(f"<title>{html.escape(name)} &mdash; CV</title>")
    a(f'<meta name="description" content="{html.escape(name)} &mdash; curriculum vitae.">')
    a(f"<style>{CSS}</style>")
    a("</head>")
    a("<body>")
    a('<main class="page">')

    meta = [today]
    a('  <header class="masthead">')
    a(f"    <h1>{html.escape(name)}</h1>")
    a(f'    <div class="meta">{" ".join(meta)}</div>')
    a("  </header>")

    # ---- portrait + intro -------------------------------------------------
    photo = blocks.get("web.photo", "").strip()
    intro = blocks.get("web.intro", "").strip()
    if photo or intro:
        a(f'  <div class="hero{"" if photo else " no-photo"}">')
        if photo:
            credit = blocks.get("web.photo.credit", "").strip()
            alt = blocks.get("web.photo.alt", f"Portrait of {name}").strip()
            a("    <figure>")
            a(f'      <img src="{html.escape(photo, quote=True)}" '
              f'alt="{html.escape(alt, quote=True)}">')
            if credit:
                a(f"      <figcaption>{md(credit)[3:-4]}</figcaption>")
            a("    </figure>")
        a('    <div class="intro">')
        a(md(intro) if intro else "")
        links = [ln.strip() for ln in blocks.get("web.links", "").splitlines()
                 if ln.strip()]
        if links:
            sep = '<span class="sep">&middot;</span>'
            a('      <p class="links">' + sep.join(convert(ln) for ln in links)
              + "</p>")
        a("    </div>")
        a("  </div>")

    # ---- featured paper ---------------------------------------------------
    ftitle = blocks.get("web.featured.title", "").strip()
    if ftitle:
        furl = blocks.get("web.featured.url", "").strip()
        fvenue = blocks.get("web.featured.venue", "").strip()
        fabs = blocks.get("web.featured.abstract", "").strip()
        ffig = blocks.get("web.featured.figure", "").strip()
        # a figure exported as PDF is rasterised by the publish step; the page
        # must point at the PNG, because browsers will not show a PDF in <img>
        if ffig.lower().endswith(".pdf"):
            ffig = ffig[:-4] + ".png"
        fcap = blocks.get("web.featured.caption", "").strip()
        if not furl:
            fail("web.featured.title is set but web.featured.url is missing")
        a('  <section class="featured">')
        a("    <h2>Featured paper</h2>")
        a(f'    <p class="paper-title"><a href="{html.escape(furl, quote=True)}">'
          f"{html.escape(ftitle)}</a></p>")
        frepo = blocks.get("web.featured.repo", "").strip()
        if fvenue or frepo:
            bits = [linkify_dois(fvenue)] if fvenue else []
            if frepo:
                # show the repo by its path, not the whole URL
                label = re.sub(r"^https?://(www\.)?", "", frepo).rstrip("/")
                bits.append(f'<a href="{html.escape(frepo, quote=True)}">'
                            f"{html.escape(label)}</a>")
            a(f'    <p class="venue">{" &middot; ".join(bits)}</p>')
        a('    <div class="cols">')
        a(f'      <div class="abstract">{md(fabs)}</div>' if fabs else "      <div></div>")
        if ffig:
            a("      <figure>")
            a(f'        <img src="{html.escape(ffig, quote=True)}" '
              f'alt="Figure from {html.escape(ftitle, quote=True)}">')
            if fcap:
                a(f"        <figcaption>{md(fcap)[3:-4]}</figcaption>")
            a("      </figure>")
        a("    </div>")
        a("  </section>")

    # ---- short biography (web.viva), only when switched on ----------------
    viva = blocks.get("web.viva", "").strip()
    if viva and flag(blocks, "web.viva.show"):
        a('  <section class="bio">')
        a("    <h2>Short biography</h2>")
        a(f"    {md(viva)}")
        a("  </section>")

    # ---- the CV itself ----------------------------------------------------
    a('  <div class="cv-heading">')
    a("    <div>")
    a("      <h2>Curriculum vitae</h2>")
    a(f'      <p class="who">{html.escape(name)}</p>')
    a("    </div>")
    if pdf_name:
        a(f'    <a class="dl" href="{html.escape(pdf_name, quote=True)}">'
          f"Download PDF</a>")
    a("  </div>")

    for title, raw in sections:
        a('  <section class="cv">')
        a(f"    <h2>{title}</h2>")
        if title.lower() == "contact":
            cols = parse_contact(raw)
            a('    <div class="contact">')
            for col in cols:
                a("      <ul>" + "".join(f"<li>{x}</li>" for x in col) + "</ul>")
            a("    </div>")
        else:
            a('    <div class="entries">')
            for chunk in split_entries(raw):
                e = parse_entry(chunk, title)
                if e is None:
                    continue
                lead = dedupe_links(e.lead)
                detail = dedupe_links(br.join(e.detail))
                # a link already used in the lead must not repeat in the detail
                if lead and detail:
                    used = set(re.findall(r'<a href="([^"]+)">', lead))
                    for u in used:
                        detail = re.sub(
                            r'<a href="%s">(.*?)</a>' % re.escape(u),
                            r"\1", detail, flags=re.S)
                a('      <article class="entry">')
                a('        <div class="body">')
                if lead:
                    a(f"          <p>{lead}</p>")
                if detail:
                    cls = ' class="sub"' if lead else ""
                    a(f"          <p{cls}>{detail}</p>")
                if title.lower().startswith("publication"):
                    cite = cite_links(lead or detail)
                    if cite:
                        a("          " + cite)
                a("        </div>")
                a(f'        <div class="date">{e.date}</div>')
                a("      </article>")
            a("    </div>")
        a("  </section>")

    # ---- legal notices: one discreet line, linking to the separate page ---
    legal = legal_links(blocks)
    if legal:
        a(f'  <p class="legalbar">{" &middot; ".join(legal)}</p>')

    a("</main>")
    if BIB:
        data = {key: raw for key, raw in BIB.values()}
        blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
        a(f'<script type="application/json" id="bibdata">{blob}</script>')
        # The only script on the page. It copies, nothing else: no network, no
        # storage, no third party. Without it the links still work - doi goes
        # to doi.org, bib downloads the whole bibliography.
        a("""<script>
(function () {
  var store = null;
  function bib() {
    if (store === null) {
      try { store = JSON.parse(document.getElementById("bibdata").textContent); }
      catch (e) { store = {}; }
    }
    return store;
  }
  document.addEventListener("click", function (ev) {
    var a = ev.target.closest && ev.target.closest("a[data-copy], a[data-bib]");
    if (!a || !navigator.clipboard) return;
    var text = a.dataset.copy || bib()[a.dataset.bib];
    if (!text) return;
    ev.preventDefault();
    navigator.clipboard.writeText(text).then(function () {
      var was = a.textContent;
      a.textContent = "copied";
      setTimeout(function () { a.textContent = was; }, 1200);
    });
  });
})();
</script>""")
    a("</body>")
    a("</html>")
    return "\n".join(x for x in o if x is not None) + "\n"


def legal_links(blocks) -> list[str]:
    """The footer links, for whichever notices are switched on."""
    out = []
    if flag(blocks, "web.impressum.show") and blocks.get("web.impressum", "").strip():
        out.append(f'<a href="{LEGAL_FILE}#impressum">Impressum</a>')
    if flag(blocks, "web.privacy.show") and blocks.get("web.privacy", "").strip():
        out.append(f'<a href="{LEGAL_FILE}#datenschutz">Datenschutz</a>')
    return out


def render_legal(name, blocks):
    """The standalone notices page, or None when both are switched off.

    Same stylesheet and page frame as the CV, so it does not read as a
    bolted-on afterthought."""
    want_imp = flag(blocks, "web.impressum.show") and blocks.get("web.impressum", "").strip()
    want_dsg = flag(blocks, "web.privacy.show") and blocks.get("web.privacy", "").strip()
    if not (want_imp or want_dsg):
        return None
    o: list[str] = []
    a = o.append
    a("<!DOCTYPE html>")
    a('<html lang="de">')
    a("<head>")
    a('<meta charset="utf-8">')
    a('<meta name="viewport" content="width=device-width, initial-scale=1">')
    a(f"<title>{html.escape(name)} &mdash; Impressum &amp; Datenschutz</title>")
    a('<meta name="robots" content="noindex">')
    a(f"<style>{CSS}</style>")
    a("</head>")
    a("<body>")
    a('<main class="page legal">')
    a('  <header class="masthead">')
    a(f'    <h1><a href="index.html">{html.escape(name)}</a></h1>')
    a('    <div class="meta"><a href="index.html">&larr; CV</a></div>')
    a("  </header>")
    if want_imp:
        a('  <section id="impressum">')
        a("    <h2>Impressum</h2>")
        # an address needs its line breaks, which md() would collapse
        lines = [mdlinks(tidy(convert(l)))
                 for l in blocks["web.impressum"].strip().split("\n")]
        a(f'    <p class="addr">{"<br>".join(l for l in lines if l)}</p>')
        a("  </section>")
    if want_dsg:
        a('  <section id="datenschutz">')
        a("    <h2>Datenschutz</h2>")
        a(f'    {md(blocks["web.privacy"].strip())}')
        a("  </section>")
    a('  <p class="back"><a href="index.html">&larr; back to the CV</a></p>')
    a("</main>")
    a("</body>")
    a("</html>")
    return "\n".join(x for x in o if x is not None) + "\n"


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path, help="the CV .tex file")
    ap.add_argument("-o", "--out", type=Path, default=Path("index.html"))
    ap.add_argument("--bib", type=Path, default=None,
                    help="juzb.bib - adds the doi/bib copy links to each "
                         "publication entry")
    ap.add_argument("--pdf", default="cv.pdf",
                    help="name of the PDF to link in the header ('' to omit)")
    ap.add_argument("--line-breaks", choices=("keep", "soft"), default="keep",
                    help="keep: a LaTeX \\\\ becomes <br> (default, mirrors the PDF). "
                         "soft: \\\\ becomes a space and the text reflows.")
    args = ap.parse_args()

    raw = args.source.read_text(encoding="utf-8")
    try:
        raw = resolve_disstitle(raw)
        raw = resolve_authors(raw)
        raw = resolve_year(raw)
        if args.bib and args.bib.exists():
            BIB.update(read_bib(args.bib))
        blocks = extract_web_blocks(raw)
        body = document_body(strip_comments(raw))
        name, sections = parse_document(body)
        out = render(name, sections, blocks, args.pdf, args.line_breaks)
        legal = render_legal(name, blocks)
    except TexError as exc:
        print(f"tex2html: {exc}", file=sys.stderr)
        return 1

    args.out.write_text(out, encoding="utf-8")

    # The notices live in their own file. Switching them off has to remove
    # that file, otherwise the old one stays reachable at its URL.
    legal_path = args.out.parent / LEGAL_FILE
    if legal:
        legal_path.write_text(legal, encoding="utf-8")
        shown = [k.split(".")[1] for k in ("web.impressum", "web.privacy")
                 if flag(blocks, k + ".show") and blocks.get(k, "").strip()]
        print(f"tex2html: wrote {legal_path} ({', '.join(shown)})")
    elif legal_path.exists():
        legal_path.unlink()
        print(f"tex2html: removed {legal_path} (both notices are off)")
    n_entries = out.count('<article class="entry">')
    print(f"tex2html: wrote {args.out} "
          f"({len(sections)} sections, {n_entries} entries, "
          f"{len(blocks)} web blocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
