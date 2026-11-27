#!/usr/bin/env python3
"""
compare_pdfs.py - say where two builds of the CV differ.

    python3 tools/compare_pdfs.py cv_private/juzb.pdf cv/cv.pdf

Prints three things:

  * page count
  * a text diff: which lines exist in one PDF and not the other (the private
    lines you removed should be the whole of it)
  * layout drift: lines that survive in both but moved horizontally, i.e.
    the removal changed the typesetting somewhere it should not have

Exit status: 0 identical, 1 text differs as expected, 2 layout drift found
(worth a look before committing).  Needs pdftotext (poppler-utils).
"""

from __future__ import annotations

import collections
import difflib
import re
import subprocess
import sys


def pages(pdf: str) -> int:
    out = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
    m = re.search(r"Pages:\s+(\d+)", out)
    return int(m.group(1)) if m else -1


def text_lines(pdf: str) -> list[str]:
    out = subprocess.run(["pdftotext", "-layout", pdf, "-"],
                         capture_output=True, text=True, check=True).stdout
    return [re.sub(r"\s+", " ", l).strip() for l in out.splitlines() if l.strip()]


def word_boxes(pdf: str) -> dict[str, list[tuple[float, float]]]:
    """word text -> list of (xMin, xMax), ignoring the y position."""
    xml = subprocess.run(["pdftotext", "-bbox", pdf, "-"],
                         capture_output=True, text=True, check=True).stdout
    boxes: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
    for m in re.finditer(r'<word xMin="([\d.]+)" yMin="[\d.]+" xMax="([\d.]+)" '
                         r'yMax="[\d.]+">(.*?)</word>', xml):
        boxes[m.group(3)].append((float(m.group(1)), float(m.group(2))))
    return boxes


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 3
    a, b = sys.argv[1], sys.argv[2]
    na, nb = pages(a), pages(b)
    print(f"   pages: {na} (private) vs {nb} (public)"
          + ("" if na == nb else "   <-- PAGE COUNT CHANGED"))

    la, lb = text_lines(a), text_lines(b)
    only_a = [l for l in difflib.unified_diff(la, lb, lineterm="", n=0)
              if l.startswith("-") and not l.startswith("---")]
    only_b = [l for l in difflib.unified_diff(la, lb, lineterm="", n=0)
              if l.startswith("+") and not l.startswith("+++")]

    if not only_a and not only_b:
        print("   text: identical")
    else:
        print(f"   text: {len(only_a)} line(s) only in the private PDF, "
              f"{len(only_b)} only in the public one")
        for l in only_a[:15]:
            print(f"     private only: {l[1:][:100]}")
        for l in only_b[:15]:
            print(f"     public  only: {l[1:][:100]}")
        if len(only_a) > 15 or len(only_b) > 15:
            print("     ...")

    # horizontal drift of words that exist in both
    ba, bb = word_boxes(a), word_boxes(b)
    drift = []
    for word, xs in ba.items():
        ys = bb.get(word)
        if not ys or len(xs) != len(ys):
            continue
        for (x0, x1), (u0, u1) in zip(sorted(xs), sorted(ys)):
            if abs(x0 - u0) > 0.5 or abs(x1 - u1) > 0.5:
                drift.append((word, round(x0, 1), round(u0, 1)))
                break
    if drift:
        print(f"   layout: {len(drift)} word(s) sit at a different x position "
              f"- the public build is not just the private one minus those lines")
        for w, x, u in drift[:10]:
            print(f"     {w!r}: x {x} -> {u}")
        return 2
    print("   layout: every shared word keeps its horizontal position")
    return 0 if not (only_a or only_b) else 1


if __name__ == "__main__":
    raise SystemExit(main())
