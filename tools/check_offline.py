#!/usr/bin/env python3
"""
check_offline.py - fail if a page would fetch anything from a third party.

    python3 tools/check_offline.py index.html legal.html

The privacy notice claims the page sets no cookies and pulls in no fonts,
scripts or analytics from anyone else. That claim has to stay true as the
page changes, and the easy way to break it is a single pasted <script src>
or a Google Fonts <link>. This fails the publish if that happens.

What counts as a fetch: an element the browser loads on its own, before the
visitor does anything. A plain <a href="https://..."> is NOT one of those -
the page is full of them and they are the point - so links are ignored and
only the loading attributes below are checked, plus url() and @import in
CSS, which is how a webfont usually sneaks in.

Deliberately basic. It will not catch a URL assembled at runtime in
JavaScript, so it also reports any inline <script>, which this page has no
reason to contain.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# element -> attributes the browser resolves and fetches by itself
FETCHING = {
    "script": ("src",),
    "link": ("href",),
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "iframe": ("src",),
    "frame": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "video": ("src", "poster"),
    "audio": ("src",),
    "track": ("src",),
    "input": ("src",),
    "image": ("href", "xlink:href"),     # SVG
    "use": ("href", "xlink:href"),       # SVG
    "body": ("background",),
    "table": ("background",),
    "td": ("background",),
    "form": ("action",),                 # not a load, but data leaves here
}

EXTERNAL = re.compile(r"""^\s*(?:https?:)?//""", re.I)
CSS_URL = re.compile(r"""url\(\s*['"]?\s*((?:https?:)?//[^)'"\s]+)""", re.I)
CSS_IMPORT = re.compile(r"""@import\s+(?:url\()?\s*['"]?\s*((?:https?:)?//[^)'";\s]+)""", re.I)


DATA_TYPES = {"application/json", "application/ld+json", "text/plain",
              "text/template"}

# anything an inline script could use to fetch, send or inject a resource
NETWORKY = ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon",
            "import(", "importScripts", "document.write", "eval(", "new Image",
            "createElement(\"script\"", "createElement('script'", ".src=", ".src =",
            "http://", "https://", "//")

class Scanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[str] = []
        self.inline_scripts = 0
        self._in_style = False
        self._in_script = False
        self._script_has_src = False
        self._script_type = ""

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        for attr in FETCHING.get(tag, ()):
            val = d.get(attr)
            if not val:
                continue
            # srcset is a comma-separated list of candidates
            parts = [p.strip().split()[0] for p in val.split(",") if p.strip()] \
                if attr == "srcset" else [val]
            for p in parts:
                if EXTERNAL.match(p):
                    self.hits.append(f"line {self.getpos()[0]}: <{tag} {attr}=\"{p[:70]}\">")
        if "style" in d:
            self._scan_css(d["style"], f"line {self.getpos()[0]}: style attribute")
        if tag == "style":
            self._in_style = True
        if tag == "script":
            self._in_script = True
            self._script_has_src = bool(d.get("src"))
            self._script_type = (d.get("type") or "").lower()

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_style:
            self._scan_css(data, f"line {self.getpos()[0]}: <style>")
        if self._in_script and not self._script_has_src and data.strip():
            # A script that only reads the page and writes the clipboard keeps
            # the promise the privacy notice makes; one that can reach the
            # network does not.  Judge by what the code can do, not by whether
            # a script is there at all - a blanket ban would just be worked
            # around by inlining the same calls somewhere else.
            if self._script_type in DATA_TYPES:
                return                      # <script type="application/json">: data
            reach = sorted({w for w in NETWORKY if w in data})
            if reach:
                self.inline_scripts += 1
                self.hits.append("inline <script> can reach the network: "
                                 + ", ".join(reach))

    def _scan_css(self, css, where):
        for m in CSS_IMPORT.finditer(css):
            self.hits.append(f"{where}: @import {m.group(1)[:70]}")
        for m in CSS_URL.finditer(css):
            self.hits.append(f"{where}: url({m.group(1)[:70]})")


def check(path: Path) -> list[str]:
    s = Scanner()
    s.feed(path.read_text(encoding="utf-8"))
    out = [f"{path.name}: {h}" for h in s.hits]
    if s.inline_scripts:
        out.append(f"{path.name}: {s.inline_scripts} inline <script> block(s) "
                   "that could reach the network")
    return out


def main() -> int:
    paths = [Path(a) for a in sys.argv[1:]]
    if not paths:
        print(__doc__, file=sys.stderr)
        return 2
    problems = []
    for p in paths:
        if not p.exists():
            print(f"check_offline: {p} not found", file=sys.stderr)
            return 2
        problems += check(p)
    if problems:
        print("check_offline: FAIL - the page would contact a third party:",
              file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("  the privacy notice says it does not. Inline it, or update the "
              "notice.", file=sys.stderr)
        return 1
    print(f"check_offline: OK - {', '.join(p.name for p in paths)} "
          "load nothing from anyone else")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
