#!/usr/bin/env python3
"""
todos.py - the working list that lives in the .tex itself.

    python3 tools/todos.py juzb.tex          # open items
    python3 tools/todos.py juzb.tex --all    # open and done

Write one item per line anywhere in the source:

    %%% TODO check whether NetworkVI is published
    %%% DONE check the postal address

Flip TODO to DONE when an item is finished; the line stays as a record and
stops being printed.  publish.sh lists the open ones at the end of a run,
and make_public.py strips both kinds out of the public .tex, so the notes
never leave this repo.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TODO = re.compile(r"^\s*%+\s*(TODO|DONE)\b[:\-]?\s*(.*?)\s*$", re.I)


def scan(src: str) -> list[tuple[int, str, str]]:
    """(line number, state, text) for every TODO/DONE marker in the source."""
    found = []
    for n, line in enumerate(src.splitlines(), 1):
        m = TODO.match(line)
        if m and m.group(2):
            found.append((n, m.group(1).upper(), m.group(2)))
    return found


def report(src: str, indent: str = "   ") -> str:
    """The lines publish.sh prints: open items, or a note that there are none."""
    items = scan(src)
    open_ = [i for i in items if i[1] == "TODO"]
    done = len(items) - len(open_)
    if not open_:
        return f"{indent}nothing open" + (f" ({done} done)" if done else "")
    head = f"{indent}{len(open_)} open" + (f", {done} done" if done else "")
    body = "\n".join(f"{indent}  [ ] {t}  (line {n})" for n, _, t in open_)
    return head + "\n" + body


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    show_all = "--all" in sys.argv
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    src = Path(args[0]).read_text(encoding="utf-8")
    items = scan(src)
    if not items:
        print("no TODO markers found")
        return 0
    for n, state, text in items:
        if state == "TODO" or show_all:
            print(f"{'[ ]' if state == 'TODO' else '[x]'} {text}  (line {n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
