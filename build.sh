#!/usr/bin/env bash
# Compile the CV so you can look at it: juzb.pdf
set -euo pipefail
cd "$(dirname "$0")"

# macOS: make sure the TeX binaries are reachable even from a non-login shell
for d in /Library/TeX/texbin /usr/texbin /opt/homebrew/bin /usr/local/bin; do
  [ -d "$d" ] && PATH="$d:$PATH"
done
export PATH

if ! command -v pdflatex >/dev/null; then
  echo "pdflatex not found. Install MacTeX (brew install --cask mactex-no-gui)" >&2
  echo "or BasicTeX, then open a new terminal." >&2
  exit 1
fi

TEX=${1:-juzb.tex}

# The Publications section is generated from juzb.bib, so regenerate it
# before compiling: otherwise a change to the bibliography sits in the .bib
# and never reaches the PDF.  Guarded, because the public repo may not
# carry the generator.
# Refresh the bibliography from ORCID before anything else: a work that is
# new there is merged into juzb.bib, a work that differs is only reported.
# Deliberately never fatal - being offline, or ORCID disagreeing with an
# entry you corrected on purpose, must not stop you compiling the CV.  It
# costs a request to ORCID and one per DOI to Crossref, so set
# CV_SKIP_ORCID=1 when you are compiling repeatedly and do not need it.
if [ -z "${CV_SKIP_ORCID:-}" ] && [ -f tools/orcid2bib.py ]; then
  python3 tools/orcid2bib.py || true
fi

if [ -f juzb.bib ] && [ -f tools/bib2tex.py ]; then
  python3 tools/bib2tex.py juzb.bib "$TEX" >/dev/null
fi
# three passes: hyperref/lastpage need a settled .aux, and with only two the
# page count in the footer can lag a page break by one run
for _ in 1 2 3; do
  pdflatex -interaction=nonstopmode -halt-on-error "$TEX" >/dev/null
done
rm -f "${TEX%.tex}".{aux,log,out,toc,fls,fdb_latexmk}
echo "built ${TEX%.tex}.pdf"
