# How this site is built

Everything on this site is generated from one file, `juzb.tex`, by the
scripts in this folder. The `.tex` is both the source of the PDF and the
source of the web page, so the two cannot drift apart: a check in the
pipeline fails the build if a word in the PDF is missing from the page.

## Rebuild it

    ./doctor.sh                 # is this machine equipped?
    ./build.sh                  # juzb.tex -> juzb.pdf
    python3 tools/tex2html.py juzb.tex -o index.html --pdf cv_juzb_<date></date>.pdf
    python3 tools/check_sync.py cv_juzb_<date></date>.pdf index.html

The generated `index.html` is byte-identical to the one committed here.

## What you need

* [ ] LaTeX, poppler, and optionally Pillow:

    # LaTeX: BasicTeX or MacTeX from https://tug.org/mactex/
    sudo tlmgr update --self
    sudo tlmgr install extsizes ncctools doi lastpage enumitem                        fancyhdr geometry hyperref cm-super
    # poppler (pdftotext, pdfinfo, pdftoppm)
    brew install poppler         # or: conda install -c conda-forge poppler
    # optional, for the image step
    python3 -m pip install pillow

`cm-super` matters: without it a `[T1]{fontenc}` document falls back to
bitmap Type 3 fonts, and the PDF neither zooms nor lets you select text.
Conda's `texlive-core` ships binaries with an empty package tree and will
shadow a real installation - `conda remove texlive-core` if it is present.

## The scripts

| script                     | what it does                                                      |
| -------------------------- | ----------------------------------------------------------------- |
| `doctor.sh`              | reports which of the tools above this machine has                 |
| `build.sh`               | compiles the `.tex` (three passes, for `lastpage`)            |
| `publish.sh`             | runs `tools/make_public.py`; see below                          |
| `tools/tex2html.py`      | renders `index.html` and `legal.html` from the `.tex`       |
| `tools/check_sync.py`    | asserts every word of the PDF appears on the page                 |
| `tools/check_offline.py` | fails if the page would load anything from a third party          |
| `tools/compare_pdfs.py`  | diffs two builds of the PDF, by text and by layout                |
| `tools/shrink_image.py`  | web-sizes images, strips EXIF/XMP, squeezes PNGs                  |
| `tools/bib2tex.py`       | writes the Publications section from `juzb.bib`                 |
| `tools/todos.py`         | lists the working notes kept in the `.tex`                      |
| `tools/citations.py`     | prints the publication list as plain text, for pasting into forms |
| `tools/make_public.py`   | the private-to-public pipeline                                    |

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
   working notes, and all comments inside `\begin{document}`;
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
