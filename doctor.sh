#!/usr/bin/env bash
# What publish.sh needs, and whether this machine has it.
for d in /Library/TeX/texbin /usr/texbin /opt/homebrew/bin /usr/local/bin /opt/local/bin; do
  [ -d "$d" ] && PATH="$d:$PATH"
done
[ -n "${CONDA_PREFIX:-}" ] && [ -d "$CONDA_PREFIX/bin" ] && PATH="$PATH:$CONDA_PREFIX/bin"
export PATH
ok=0
for t in pdflatex pdftotext pdfinfo pdftoppm python3; do
  p=$(command -v "$t" || true)
  printf '%-10s %s\n' "$t" "${p:-MISSING}"
  [ -z "$p" ] && ok=1
done
py=$(command -v python3)
python3 -c "import PIL; print('Pillow     ' + PIL.__version__)" 2>/dev/null \
  || echo "Pillow     MISSING in $py  (optional: resizes images, strips EXIF)"
echo
if [ "$ok" = 0 ]; then
  echo "all set - ./build.sh and ./publish.sh will work"
else
  echo "missing pieces:"
  command -v pdflatex >/dev/null || echo "  LaTeX:   install BasicTeX or MacTeX from https://tug.org/mactex/ (conda texlive-core will NOT do)"
  command -v pdftotext >/dev/null || echo "  poppler: conda install -c conda-forge poppler   (or brew install poppler)"
fi
