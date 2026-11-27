#!/usr/bin/env python3
"""
make_public.py - publish the private CV source to the public repo.

    cd ~/Documents/cv_private && python3 tools/make_public.py

What it does, in order:

  1. compiles the private PDF              (cv_private/juzb.pdf)
  2. strips private content from the .tex:
       - a line whose comment starts with "private" is dropped entirely
             +49 151 00000000  %private        <- an example, not a real number
       - everything between "%%% BEGIN private" and "%%% END private" is dropped
       - inside \\begin{document}...\\end{document} all comments are removed
         (a comment-only line disappears with its newline, as in TeX)
       - the "%%% BEGIN web.*" blocks are always kept: they are the web page's
         content and they live in the preamble
  3. writes the result to the public repo  (cv/juzb.tex)
  4. compiles the public PDF               (cv/cv.pdf)
  5. compares the two PDFs and prints where they differ
  6. regenerates cv/index.html from the public .tex and checks it against the
     public PDF
  7. refuses to finish if anything from a dropped line shows up in the public
     .tex, PDF or HTML

Nothing is pushed; review with git in the cv repo and commit when happy.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import os
import re
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

PRIVATE_LINE = re.compile(r"(?<!\\)%\s*private\b", re.I)
BLOCK_BEGIN = re.compile(r"^\s*%%%\s*BEGIN\s+private\s*$", re.I)
NOTES_BEGIN = re.compile(r"^\s*%%%\s*BEGIN\s+notes\s*$", re.I)
NOTES_END = re.compile(r"^\s*%%%\s*END\s+notes\s*$", re.I)
BLOCK_END = re.compile(r"^\s*%%%\s*END\s+private\s*$", re.I)
WEB_BEGIN = re.compile(r"^\s*%%%\s*BEGIN\s+web\.", re.I)
WEB_END = re.compile(r"^\s*%%%\s*END\s+web\.", re.I)

LATEX_WORDS = {
    "begin", "end", "tabular", "href", "mailto", "section", "textbf", "textit",
    "hfill", "vspace", "newcommand", "documentclass", "usepackage", "nobold",
    "makeheading", "https", "http", "center", "normalfont", "normalsize",
}



sys.path.insert(0, str(Path(__file__).resolve().parent))
from shrink_image import process as publish_image   # noqa: E402
import todos                                        # noqa: E402

# macOS puts the TeX and poppler binaries somewhere a non-login shell (and
# anything launched from an editor) often does not see.
EXTRA_TOOL_DIRS = [
    "/Library/TeX/texbin",          # MacTeX / BasicTeX
    "/usr/texbin",                  # older MacTeX
    "/opt/homebrew/bin",            # Homebrew, Apple silicon
    "/usr/local/bin",               # Homebrew, Intel
    "/opt/local/bin",               # MacPorts
]

INSTALL_HINTS = {
    "pdflatex": (
        "no usable LaTeX found. Install BasicTeX (~100 MB) or MacTeX from\n"
        "   https://tug.org/mactex/ - they are plain .pkg installers.\n"
        "   Afterwards:\n"
        "     sudo /Library/TeX/texbin/tlmgr update --self\n"
        "     sudo /Library/TeX/texbin/tlmgr install extsizes ncctools doi \\\n"
        "          lastpage enumitem fancyhdr geometry hyperref cm-super\n"
        "   Note: conda's texlive-core ships binaries with an empty package\n"
        "   tree (no article.cls) - remove it so it cannot shadow MacTeX:\n"
        "     conda remove texlive-core"),
    "pdftotext": "poppler is missing:  conda install -c conda-forge poppler",
    "pdfinfo": "poppler is missing:  conda install -c conda-forge poppler",
    "pdftoppm": "poppler is missing:  conda install -c conda-forge poppler",
}


NOTICE_MD = """# Licence

This repository is a mix: most of it is mine and carries no restrictions, two
parts are not mine and do.

| Files | Terms |
|---|---|
| `tools/`, `*.sh`, `index.html`, `legal.html` | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) \u2014 no rights reserved. Copy, change, sell, no attribution needed. |
| `juzb.tex`, `cv_juzb_*.pdf` | Built on the *Simple LaTeX CV Template*, \u00a9 2007\u20132011 Theodore P. Pavlic, [CC BY-NC 3.0 US](http://creativecommons.org/licenses/by-nc/3.0/us/), modified. Redistribute only under those terms: keep the attribution at the end of the `.tex`, non-commercial use only. The CV text inside is mine and is CC0, but the file as a whole is a derivative of the template. |
| `juzb.jpg` | \u00a9 BIH \\| Heidi Scherm 2026. Not mine to license \u2014 all rights reserved. |
| `featured.png` | Figure from [the omnigenic neural network preprint](https://doi.org/10.64898/2026.07.28.26359187), \u00a9 the authors. |

There is deliberately no `LICENSE` file: a single one would make the whole
repository look CC0, and `juzb.tex` and `juzb.jpg` are not.
"""


TOOLS_README = """# How this site is built

Everything on this site is generated from one file, `juzb.tex`, by the
scripts in this folder. The `.tex` is both the source of the PDF and the
source of the web page, so the two cannot drift apart: a check in the
pipeline fails the build if a word in the PDF is missing from the page.

## Rebuild it

    ./doctor.sh                 # is this machine equipped?
    ./build.sh                  # juzb.tex -> juzb.pdf
    python3 tools/tex2html.py juzb.tex -o index.html --pdf cv_juzb_<date>.pdf
    python3 tools/check_sync.py cv_juzb_<date>.pdf index.html

The generated `index.html` is byte-identical to the one committed here.

## What you need

LaTeX, poppler, and optionally Pillow:

    # LaTeX: BasicTeX or MacTeX from https://tug.org/mactex/
    sudo tlmgr update --self
    sudo tlmgr install extsizes ncctools doi lastpage enumitem \
                       fancyhdr geometry hyperref cm-super
    # poppler (pdftotext, pdfinfo, pdftoppm)
    brew install poppler         # or: conda install -c conda-forge poppler
    # optional, for the image step
    python3 -m pip install pillow

`cm-super` matters: without it a `[T1]{fontenc}` document falls back to
bitmap Type 3 fonts, and the PDF neither zooms nor lets you select text.
Conda's `texlive-core` ships binaries with an empty package tree and will
shadow a real installation - `conda remove texlive-core` if it is present.

## The scripts

| script | what it does |
|---|---|
| `doctor.sh` | reports which of the tools above this machine has |
| `build.sh` | compiles the `.tex` (three passes, for `lastpage`) |
| `publish.sh` | runs `tools/make_public.py`; see below |
| `tools/tex2html.py` | renders `index.html` and `legal.html` from the `.tex` |
| `tools/check_sync.py` | asserts every word of the PDF appears on the page |
| `tools/check_offline.py` | fails if the page would load anything from a third party |
| `tools/compare_pdfs.py` | diffs two builds of the PDF, by text and by layout |
| `tools/shrink_image.py` | web-sizes images, strips EXIF/XMP, squeezes PNGs |
| `tools/bib2tex.py` | writes the Publications section from `juzb.bib` |
| `tools/todos.py` | lists the working notes kept in the `.tex` |
| `tools/citations.py` | prints the publication list as plain text, for pasting into forms |
| `tools/make_public.py` | the private-to-public pipeline |

## How the content gets into the page

The page is not a second copy of the CV. Text that belongs only to the web
version - the intro, the portrait credit, the featured paper, the legal
notices - lives in the `.tex` as marked comment blocks:

    %%% BEGIN web.intro
    % Some text, with **markdown** and [links](https://example.org).
    %%% END web.intro

LaTeX ignores them, `tex2html.py` reads them. The legal notices have their
own `web.impressum.show` / `web.privacy.show` switches; set either to `no`
and `legal.html` is not written, and an existing one is deleted.

## The two-repository split

The source of truth is a private repository holding the unfiltered `.tex`.
`publish.sh` runs `make_public.py`, which:

1. compiles the private PDF;
2. strips anything marked `%private`, every `%%% BEGIN private` block, the
   working notes, and all comments inside `\\begin{document}`;
3. builds the public PDF and the page **in a staging directory**;
4. compares the two PDFs, by text and by horizontal position, so the only
   difference is the content that was deliberately removed;
5. checks that no token unique to a removed line appears anywhere it
   publishes - the `.tex`, the PDF, both pages, and these scripts;
6. mirrors staging into the public repo only if every check passed.

Because of step 6, a run that finds private content changes nothing here.

## What this repository cannot reproduce

Two things, by design:

- the lines stripped at step 2, which exist only in the private repository;
- the original images. `juzb.jpg` and `featured.png` here are the web-sized,
  metadata-stripped outputs of `shrink_image.py`; the full-resolution
  originals are not published.

Everything else - the PDF, both pages - rebuilds from what is in this
repository.
"""


def ensure_tools_on_path() -> None:
    dirs = [d for d in EXTRA_TOOL_DIRS if Path(d).is_dir()]
    dirs += sorted(glob.glob("/usr/local/texlive/*/bin/*"), reverse=True)
    conda = os.environ.get("CONDA_PREFIX")          # poppler/pillow often live here
    if conda and Path(conda, "bin").is_dir():
        dirs.append(str(Path(conda, "bin")))
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])


def require(tool: str) -> None:
    exe = shutil.which(tool)
    if exe is None:
        print(f"!! {tool} not found on this machine.", file=sys.stderr)
        print("   " + INSTALL_HINTS.get(tool, f"install {tool} and try again."),
              file=sys.stderr)
        sys.exit(1)
    if tool == "pdflatex" and shutil.which("kpsewhich"):
        probe = subprocess.run(["kpsewhich", "article.cls"], capture_output=True,
                               text=True)
        if not probe.stdout.strip():
            print(f"!! {exe} exists but its package tree is empty "
                  "(article.cls not found).", file=sys.stderr)
            print("   " + INSTALL_HINTS["pdflatex"], file=sys.stderr)
            sys.exit(1)



def check_fonts(pdf: Path) -> bool:
    """True if the PDF's fonts are fine. False if it embeds Type 3 bitmaps.

    That happens when cm-super is missing: the text then looks fuzzy at zoom
    and extracts as "scienti c" instead of "scientific", which matters for
    anything that parses the PDF."""
    if not shutil.which("pdffonts"):
        return True
    out = subprocess.run(["pdffonts", str(pdf)], capture_output=True,
                         text=True).stdout.splitlines()[2:]
    kinds = {" ".join(line.split()[1:3]) for line in out if line.strip()}
    if any(k.startswith("Type 3") for k in kinds):
        print(f"   !! {pdf.name} embeds Type 3 (bitmap) fonts - the text will "
              "not extract or zoom properly.", file=sys.stderr)
        print("      Install cm-super:  sudo /Library/TeX/texbin/tlmgr install "
              "cm-super", file=sys.stderr)
        return False
    if kinds:
        print(f"   fonts: {', '.join(sorted(kinds))}")
    return True


def run(cmd: list[str], cwd: Path, quiet: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True,
                         env=build_env())
    if res.returncode != 0 and not quiet:
        print(res.stdout[-3000:], file=sys.stderr)
    return res


def build_env() -> dict:
    """pdfTeX stamps a build time into every PDF, so two identical builds
    differ in their bytes and the PDF shows up as changed on every publish.
    SOURCE_DATE_EPOCH pins that stamp to midnight today, which makes a rebuild
    byte-identical while still dating the file the day it was made."""
    env = dict(os.environ)
    midnight = _dt.datetime.combine(_dt.date.today(), _dt.time.min,
                                    tzinfo=_dt.timezone.utc)
    env["SOURCE_DATE_EPOCH"] = str(int(midnight.timestamp()))
    env["FORCE_SOURCE_DATE"] = "1"
    return env


def compile_pdf(tex: Path, cwd: Path) -> Path:
    """Three pdflatex runs: lastpage and hyperref need a settled .aux, and
    with only two the footer page count can lag a page break by one run."""
    for _ in range(3):
        res = run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex.name], cwd)
        if res.returncode != 0:
            print(f"!! pdflatex failed in {cwd}", file=sys.stderr)
            log = (cwd / (tex.stem + ".log"))
            if log.exists():
                errs = [l for l in log.read_text(errors="replace").splitlines()
                        if l.startswith("!")]
                print("\n".join(errs[:20]), file=sys.stderr)
            sys.exit(1)
    pdf = cwd / (tex.stem + ".pdf")
    overfull = []
    log = cwd / (tex.stem + ".log")
    if log.exists():
        overfull = [l for l in log.read_text(errors="replace").splitlines()
                    if l.startswith("Overfull")]
    if overfull:
        print(f"   warning: {len(overfull)} overfull box(es) in {pdf.name}")
        for l in overfull[:5]:
            print(f"     {l[:110]}")
    return pdf


# --------------------------------------------------------------------------
# the filter
# --------------------------------------------------------------------------

def strip_comment(line: str) -> tuple[str, bool]:
    """Return (line without its comment, had_comment). Honours \\%."""
    out, i, had = [], 0, False
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line):
            out.append(line[i:i + 2])
            i += 2
            continue
        if line[i] == "%":
            had = True
            break
        out.append(line[i])
        i += 1
    return "".join(out), had


def make_public_tex(src: str) -> tuple[str, list[tuple[int, str]]]:
    lines = src.splitlines()
    out: list[str] = []
    dropped: list[tuple[int, str]] = []
    in_doc = False
    in_private_block = False
    in_web_block = False
    in_notes = False
    make_public_tex.notes_removed = 0

    for n, line in enumerate(lines, 1):
        # the working list never reaches the public repo. It is notes, not
        # secrets, so it is counted on its own and kept out of the leak
        # check, which would otherwise treat its ordinary words as secrets
        if NOTES_BEGIN.match(line):
            in_notes = True
        if in_notes:
            make_public_tex.notes_removed += 1
            if NOTES_END.match(line):
                in_notes = False
            continue
        if BLOCK_BEGIN.match(line):
            in_private_block = True
            dropped.append((n, line))
            continue
        if BLOCK_END.match(line):
            in_private_block = False
            dropped.append((n, line))
            continue
        if in_private_block:
            dropped.append((n, line))
            continue

        if WEB_BEGIN.match(line):
            in_web_block = True
            out.append(line)
            continue
        if WEB_END.match(line):
            in_web_block = False
            out.append(line)
            continue
        if in_web_block:                      # web content: keep verbatim
            out.append(line)
            continue

        if PRIVATE_LINE.search(line):         # %private marker
            dropped.append((n, line))
            continue

        if r"\begin{document}" in line:
            in_doc = True
        if r"\end{document}" in line:
            in_doc = False

        if in_doc:
            body, had = strip_comment(line)
            if had and not body.strip() and line.strip():
                continue                      # comment-only line: drop it
            out.append(body.rstrip() if had else line)
        else:
            out.append(line)

    text = "\n".join(out) + "\n"
    # a row removed from the bottom of a tabular can leave a dangling \\,
    # which would add an empty row to the block
    text = re.sub(r"\\\\(\s*)\n(\s*)\\end\{tabular\}", r"\1\n\2\\end{tabular}", text)
    # collapse the runs of blank lines the stripping leaves behind
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text, dropped


# --------------------------------------------------------------------------
# leak check
# --------------------------------------------------------------------------

def secrets_from(dropped: list[tuple[int, str]], kept: str) -> set[str]:
    """Tokens that live only in the dropped lines.

    A word that also occurs in the kept source ("Berlin") is not a secret -
    only things unique to the removed lines are, such as a second address or
    a phone number."""
    kept_flat = kept.lower()
    kept_squashed = re.sub(r"\s+", "", kept_flat)
    out: set[str] = set()
    for _, line in dropped:
        text = re.sub(r"\\[A-Za-z]+", " ", line)
        text = re.sub(r"[{}%\\]", " ", text)
        for tok in re.findall(r"[A-Za-z0-9._@+-]{5,}", text):
            low = tok.lower()
            if low in LATEX_WORDS or low == "private":
                continue
            if low in kept_flat:            # also used in public content
                continue
            out.add(tok)
        for num in re.findall(r"\d[\d ]{6,}\d", text):     # phone numbers
            num = num.strip()
            if re.sub(r"\s+", "", num).lower() not in kept_squashed:
                out.add(num)
    return out


def leak_check(secrets: set[str], targets: dict[str, str]) -> list[str]:
    hits = []
    for secret in sorted(secrets):
        squashed = secret.replace(" ", "")
        for name, content in targets.items():
            flat = content.replace(" ", "")
            if secret in content or (len(squashed) > 6 and squashed in flat):
                hits.append(f"{secret!r} appears in {name}")
    return hits


# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# staging -> public repo
# --------------------------------------------------------------------------

# Only these are the pipeline's to manage. Anything else in the public repo
# (.git, .gitignore, .nojekyll, notes you keep there) is left alone, and a
# file matching one of these that the build no longer produces is removed,
# so a renamed PDF or a switched-off legal.html does not linger.
MANAGED = ("juzb.tex", "juzb.bib", "cv_juzb_*.pdf", "index.html", "legal.html",
           "*.png", "*.jpg", "*.jpeg", "*.svg", "*.sh", "NOTICE.md", "tools/*")


def mirror(stage: Path, pub: Path) -> tuple[list[str], list[str], list[str]]:
    """Copy the staged build over the public repo. Returns (new, updated, removed)."""
    staged = {str(f.relative_to(stage)): f
              for f in stage.rglob("*") if f.is_file()}
    new, updated = [], []
    for rel, src in sorted(staged.items()):
        dst = pub / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            new.append(rel)
        elif dst.read_bytes() != src.read_bytes():
            updated.append(rel)
        shutil.copy2(src, dst)

    removed = []
    for pattern in MANAGED:
        for old in pub.glob(pattern):
            if not old.is_file():
                continue
            rel = str(old.relative_to(pub))
            if rel not in staged:
                try:
                    old.unlink()
                    removed.append(rel)
                except OSError:
                    pass
    return new, updated, removed


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    ensure_tools_on_path()
    for tool in ("pdflatex", "pdftotext", "pdfinfo"):
        require(tool)
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("   note: Pillow is not installed, images will be copied at full "
              "size with their EXIF intact - python3 -m pip install --user pillow")
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--private", type=Path, default=here.parent)
    siblings = here.parent.parent
    default_public = next((siblings / name for name in
                           ("juzb.github.io", "cv", "cv_public")
                           if (siblings / name / ".git").exists()),
                          siblings / "juzb.github.io")
    ap.add_argument("--public", type=Path, default=default_public,
                    help=f"public repo (default: {default_public.name})")
    ap.add_argument("--tex", default="juzb.tex")
    ap.add_argument("--pdf", default=None,
                    help="PDF name in the public repo "
                         "(default: cv_juzb_YYYY-MM-DD.pdf, today's date)")
    ap.add_argument("--allow-type3", action="store_true",
                    help="publish even if the PDF embeds Type 3 bitmap fonts")
    ap.add_argument("--skip-build", action="store_true",
                    help="reuse the existing private PDF")
    args = ap.parse_args()

    if args.pdf is None:
        args.pdf = f"cv_juzb_{_dt.date.today().isoformat()}.pdf"
    priv, pub = args.private.resolve(), args.public.resolve()
    src_tex = priv / args.tex
    if not src_tex.exists():
        print(f"!! {src_tex} not found", file=sys.stderr)
        return 1
    pub.mkdir(parents=True, exist_ok=True)

    # Everything is built in a staging directory and only mirrored into the
    # public repo once the leak check has passed, so a run that finds private
    # content never leaves it sitting in the repo, not even uncommitted.
    # It lives outside both repos: nothing scratch belongs in them, and a
    # checkout on a filesystem that forbids deletes cannot clean one up.
    stage = Path(tempfile.mkdtemp(prefix="cv-stage-"))
    (stage / "tools").mkdir(parents=True)

    # 0. the one mistake that produces a baffling LaTeX error: a line pasted
    #    into a web block without its leading %
    raw = src_tex.read_text(encoding="utf-8")
    bad, inblock = [], None
    for n, line in enumerate(raw.splitlines(), 1):
        if re.match(r"\s*%%%\s*BEGIN\s+web\.", line):
            inblock = line.strip()
            continue
        if re.match(r"\s*%%%\s*END\s+web\.", line):
            inblock = None
            continue
        if inblock and line.strip() and not line.lstrip().startswith("%"):
            bad.append((n, inblock, line))
    if bad:
        print("!! a line inside a web block is not commented out - LaTeX would "
              "try to typeset it in the preamble:", file=sys.stderr)
        for n, blk, line in bad:
            print(f"     line {n} of {blk}: {line.strip()[:70]}...", file=sys.stderr)
        print("   put '% ' in front of it and run again.", file=sys.stderr)
        return 1

    bib = priv / "juzb.bib"
    gen = Path(__file__).resolve().parent / "bib2tex.py"
    if bib.exists() and gen.exists():
        print(f"0. bibliography    {bib.name} -> the Publications section")
        r = subprocess.run([sys.executable, str(gen), str(bib), str(src_tex)],
                           capture_output=True, text=True)
        if r.returncode:
            print("!! bib2tex failed:", r.stderr.strip(), file=sys.stderr)
            return 1
        print("   " + r.stdout.strip().splitlines()[0].strip())

    print(f"1. private build   {src_tex}")
    priv_pdf = (priv / (src_tex.stem + ".pdf")) if args.skip_build else compile_pdf(src_tex, priv)
    if not priv_pdf.exists():
        print("!! no private PDF; run without --skip-build", file=sys.stderr)
        return 1

    print("2. filtering       %private lines and in-document comments")
    public_tex, dropped = make_public_tex(src_tex.read_text(encoding="utf-8"))
    priv_lines = [(n, l) for n, l in dropped if PRIVATE_LINE.search(l) or True]
    print(f"   dropped {len(dropped)} private line(s):")
    for n, l in dropped[:12]:
        print(f"     {n:>4}: {l.strip()[:90]}")
    if len(dropped) > 12:
        print(f"     ... and {len(dropped) - 12} more")

    out_tex = stage / args.tex
    out_tex.write_text(public_tex, encoding="utf-8")
    notes = getattr(make_public_tex, "notes_removed", 0)
    if notes:
        print(f"   removed {notes} line(s) of working notes")
    print(f"3. wrote           {out_tex}")

    print("4. public build")
    built = compile_pdf(out_tex, stage)
    pub_pdf = stage / args.pdf
    if built.resolve() != pub_pdf.resolve():
        shutil.move(str(built), str(pub_pdf))
    fonts_ok = check_fonts(pub_pdf)
    if not fonts_ok and not args.allow_type3:
        print("   refusing to publish a bitmap-font PDF; the public repo is "
              "untouched.", file=sys.stderr)
        print("   build on a machine that has cm-super, or pass --allow-type3 "
              "if you really mean it.", file=sys.stderr)
        return 1

    print("5. comparing the two PDFs")
    cmp_script = here / "compare_pdfs.py"
    res = subprocess.run([sys.executable, str(cmp_script), str(priv_pdf), str(pub_pdf)],
                         text=True)
    layout_changed = res.returncode == 2

    print("6. web page")
    # orcid2bib.py stays private: in a clone it would reach out to ORCID on
    # every build and rewrite that clone's juzb.bib.  The published bib plus
    # bib2tex.py are all anyone needs to rebuild the CV.
    published_tools = sorted(x.name for x in here.glob("*.py")
                             if x.name != "orcid2bib.py")
    for script in published_tools:
        shutil.copy2(here / script, stage / "tools" / script)
    (stage / "tools" / "README.md").write_text(TOOLS_README, encoding="utf-8")
    # the bibliography travels with the site: it is public data, and it is what
    # the published build.sh needs to regenerate the Publications section
    if (priv / "juzb.bib").exists():
        shutil.copy2(priv / "juzb.bib", stage / "juzb.bib")
    (stage / "NOTICE.md").write_text(NOTICE_MD, encoding="utf-8")
    # the shell wrappers too, so the public repo is a working checkout and
    # not just the rendered output
    published_shell = sorted(x.name for x in priv.glob("*.sh"))
    for script in published_shell:
        dst = stage / script
        shutil.copy2(priv / script, dst)
        dst.chmod(0o755)
    html = stage / "index.html"
    r = subprocess.run([sys.executable, str(here / "tex2html.py"), str(out_tex),
                        "-o", str(html), "--pdf", args.pdf]
                       + (["--bib", str(stage / "juzb.bib")]
                          if (stage / "juzb.bib").exists() else []), text=True)
    if r.returncode != 0:
        return 1
    r = subprocess.run([sys.executable, str(here / "check_sync.py"),
                        str(pub_pdf), str(html)], text=True)
    if r.returncode != 0:
        return 1

    # images referenced by the page: rasterise PDFs, shrink, strip EXIF
    missing, rewrites = [], {}
    page = html.read_text(encoding="utf-8")
    for img in sorted(set(re.findall(r'<img src="([^"]+)"', page))):
        if img.startswith(("http://", "https://", "data:")):
            continue
        source = priv / img
        if not source.exists() and img.lower().endswith(".png"):
            pdf_source = source.with_suffix(".pdf")   # the page asks for the
            if pdf_source.exists():                   # raster of a PDF figure
                source = pdf_source
        if source.exists():
            try:
                written, note = publish_image(source, stage / img)
            except Exception as exc:                  # noqa: BLE001
                print(f"   warning: {img}: {exc}")
                continue
            print("   " + note)
            if written.name != img:
                rewrites[img] = written.name
        else:
            missing.append(img)
    if rewrites:
        for old_name, new_name in rewrites.items():
            page = page.replace(f'src="{old_name}"', f'src="{new_name}"')
        html.write_text(page, encoding="utf-8")
        print("   rewrote the page to point at "
              + ", ".join(sorted(rewrites.values())))
    if missing:
        print(f"   warning: image(s) referenced but not found: {', '.join(missing)}")

    # the privacy notice promises the page contacts nobody; hold it to that
    pages = [html] + ([stage / "legal.html"] if (stage / "legal.html").exists() else [])
    r = subprocess.run([sys.executable, str(here / "check_offline.py"),
                        *[str(x) for x in pages]], text=True)
    if r.returncode != 0:
        return 1

    print("7. leak check")
    targets = {
        "juzb.tex": public_tex,
        "cv.pdf": subprocess.run(["pdftotext", str(pub_pdf), "-"], text=True,
                                 capture_output=True).stdout,
        "index.html": page,
    }
    legal_page = stage / "legal.html"
    if legal_page.exists():
        targets["legal.html"] = legal_page.read_text(encoding="utf-8")
    # the tools ship too now, so their comments and docstrings are published
    # text and have to be checked like any other file
    for name in published_tools:
        targets[f"tools/{name}"] = (stage / "tools" / name).read_text(encoding="utf-8")
    for name in published_shell:
        targets[name] = (stage / name).read_text(encoding="utf-8")
    targets["NOTICE.md"] = NOTICE_MD
    hits = leak_check(secrets_from(dropped, public_tex), targets)
    if hits:
        print("!! PRIVATE CONTENT FOUND - NOTHING WAS COPIED TO THE PUBLIC REPO:",
              file=sys.stderr)
        print(f"   the staged build is in {stage} for inspection; the public "
              "repo is untouched.", file=sys.stderr)
        for h in hits:
            print(f"     {h}", file=sys.stderr)
        return 1
    guarded = sorted(secrets_from(dropped, public_tex))
    print(f"   clean - {len(guarded)} private token(s) checked and absent: "
          + (", ".join(guarded[:6]) + (" ..." if len(guarded) > 6 else "")
             if guarded else "(none unique to the dropped lines)"))

    for junk in stage.glob("juzb.*"):
        if junk.suffix in {".aux", ".log", ".out", ".toc", ".fls", ".fdb_latexmk"}:
            try:
                junk.unlink()
            except OSError:
                pass        # read-only checkout, or no permission: harmless

    print("8. publishing")
    new, updated, removed = mirror(stage, pub)
    for label, items in (("new", new), ("updated", updated), ("removed", removed)):
        for rel in items:
            print(f"   {label:>7}  {rel}")
    if not (new or updated or removed):
        print("   the public repo already matches the build")

    # Anything in the public repo that this script did not put there is a
    # stray: a leftover from an earlier layout, a file dropped in by another
    # program, an export nobody meant to publish.  Git would commit it with
    # the rest, so fail on it - unless .gitignore already excludes it, in
    # which case just say it is there, so nothing sits in the repo unseen.
    staged_rel = {str(f.relative_to(stage)) for f in stage.rglob("*") if f.is_file()}
    strays, ignored = [], []
    for f in sorted(pub.rglob("*")):
        if not f.is_file() or ".git" in f.relative_to(pub).parts:
            continue
        rel = str(f.relative_to(pub))
        if rel in staged_rel or rel in (".gitignore", ".nojekyll"):
            continue
        q = subprocess.run(["git", "-C", str(pub), "check-ignore", "-q", rel])
        (ignored if q.returncode == 0 else strays).append(rel)
    if ignored:
        print(f"   {len(ignored)} ignored file(s) present, not published: "
              + ", ".join(ignored[:4]) + (" ..." if len(ignored) > 4 else ""))
    if strays:
        print("!! the public repo holds files this build did not write, and "
              "git would commit them:", file=sys.stderr)
        for rel in strays:
            print(f"     {rel}", file=sys.stderr)
        print("   delete them, or add them to the public .gitignore.", file=sys.stderr)
        shutil.rmtree(stage, ignore_errors=True)
        return 1

    shutil.rmtree(stage, ignore_errors=True)

    # the pasteable citation list: a private convenience, built from the same
    # source as the CV and deliberately not enriched, so it says exactly what
    # the CV says. It is written into the private repo only - never staged,
    # so the mirror cannot carry it to the public one.
    print("9. citations")
    cites = priv / "citations.txt"
    r = subprocess.run([sys.executable, str(here / "citations.py"),
                        str(src_tex), "-o", str(cites)], text=True)
    if r.returncode != 0:
        print("   warning: citations.txt was not regenerated", file=sys.stderr)

    print("10. open items")
    print(todos.report(src_tex.read_text(encoding="utf-8")))

    print("\ndone. review and commit:")
    print(f"   git -C {pub} status --short")
    print(f"   git -C {pub} add -A && git -C {pub} commit -m 'update CV' && git -C {pub} push")
    if layout_changed:
        print("\nNOTE: the comparison found layout changes beyond the removed "
              "lines - look at the report above before committing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
