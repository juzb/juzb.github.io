#!/usr/bin/env python3
"""
shrink_image.py - make a web-sized copy of an image, without metadata.

    python3 tools/shrink_image.py juzb.jpg                 # -> juzb_web.jpg
    python3 tools/shrink_image.py juzb.jpg -o small.jpg --max-px 1000
    python3 tools/shrink_image.py featured.pdf -o fig.png  # rasterises first
    python3 tools/shrink_image.py *.jpg --in-place

Three resizing backends, tried in this order, so it works on a bare Mac:

  pillow   best: exact control, guaranteed metadata removal
  sips     built into macOS, nothing to install
  magick   ImageMagick, if you happen to have it

Whatever resized the file, JPEG output then goes through a dependency-free
scrub that removes the EXIF, IPTC and comment segments (a camera file can
carry GPS coordinates and the capture history) while keeping the ICC colour
profile, so colours do not shift.

publish.sh calls process() from this file, so the web page and a manual run
produce the same thing.
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
from pathlib import Path

MAX_PX = 1400          # long edge; the page never shows an image wider than this
QUALITY = 82


# --------------------------------------------------------------------------
# JPEG metadata scrub - no dependencies, works on whatever the backend wrote
# --------------------------------------------------------------------------

STRIP_MARKERS = {0xE1, 0xE2 - 1, 0xED, 0xFE}      # APP1 (EXIF/XMP), APP13, COM
STRIP_MARKERS = {0xE1, 0xED, 0xFE}                # keep APP2 = ICC profile


def strip_jpeg_metadata(path: Path) -> int:
    """Remove EXIF/XMP/IPTC/comment segments in place. Returns bytes removed."""
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        return 0
    out = bytearray(data[:2])
    i, removed = 2, 0
    while i < len(data) - 1:
        if data[i] != 0xFF:
            out.extend(data[i:])
            break
        marker = data[i + 1]
        if marker == 0xDA:                        # start of scan: rest is image
            out.extend(data[i:])
            break
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            out.extend(data[i:i + 2])
            i += 2
            continue
        if i + 4 > len(data):
            out.extend(data[i:])
            break
        length = int.from_bytes(data[i + 2:i + 4], "big")
        segment = data[i:i + 2 + length]
        if marker in STRIP_MARKERS:
            removed += len(segment)
        else:
            out.extend(segment)
        i += 2 + length
    if removed:
        path.write_bytes(bytes(out))
    return removed



# PNG chunks that carry no image data: text, XMP, EXIF, timestamps.
PNG_DROP = {b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"tIME", b"dSIG"}


def strip_png_metadata(path: Path) -> int:
    """Remove text/XMP/EXIF chunks from a PNG in place. Returns bytes removed.

    Keeps IHDR, PLTE, IDAT, IEND, transparency and the colour chunks, so the
    image and its colours are untouched - only the "made with Figma, on this
    date" kind of information goes."""
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return 0
    out, i, removed = bytearray(data[:8]), 8, 0
    while i + 8 <= len(data):
        length = int.from_bytes(data[i:i + 4], "big")
        ctype = data[i + 4:i + 8]
        chunk = data[i:i + 12 + length]          # len + type + data + crc
        if ctype in PNG_DROP:
            removed += len(chunk)
        else:
            out.extend(chunk)
        i += 12 + length
        if ctype == b"IEND":
            break
    if removed:
        path.write_bytes(bytes(out))
    return removed


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------

def _png_squeeze(dst: Path) -> str:
    """Make a PNG smaller without a visible change.

    Two safe wins, both verified before they are kept:

      * a fully opaque alpha channel is dead weight - drop it
      * a figure exported from a vector tool has few real colours, but
        resampling smears them into thousands; a 256-colour palette puts
        them back.  Kept only if the mean per-channel error stays under
        1/255, i.e. below what an eye or a screen can show.

    Returns a note, or "" if nothing was worth keeping."""
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        return ""
    before = dst.stat().st_size
    try:
        with Image.open(dst) as im:
            im.load()
            note = []
            if im.mode in ("RGBA", "LA") and im.getchannel("A").getextrema() == (255, 255):
                im = im.convert("RGB" if im.mode == "RGBA" else "L")
                note.append("opaque alpha dropped")
            if im.mode not in ("RGB", "L"):
                return ""
            ref = im.convert("RGB")
            pal = ref.quantize(colors=256, method=Image.MEDIANCUT,
                               dither=Image.FLOYDSTEINBERG)
            err = max(ImageStat.Stat(ImageChops.difference(ref, pal.convert("RGB"))).mean)
            cand = io.BytesIO()
            (pal if err <= 1.0 else im).save(cand, "PNG", optimize=True,
                                             compress_level=9)
            if err <= 1.0:
                note.append(f"256 colours, mean error {err:.2f}/255")
            if cand.tell() >= before:
                return ""
            dst.write_bytes(cand.getvalue())
            return (f"squeezed {before // 1024} -> {cand.tell() // 1024} kB ("
                    + ", ".join(note) + ")") if note else ""
    except Exception:                                       # noqa: BLE001
        return ""


def _pillow(src: Path, dst: Path, max_px: int, quality: int) -> tuple[int, int]:
    from PIL import Image, ImageOps
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)          # bake the rotation in
        im.thumbnail((max_px, max_px))
        if dst.suffix.lower() in {".jpg", ".jpeg"}:
            im.convert("RGB").save(dst, "JPEG", quality=quality, optimize=True,
                                   progressive=True)
        else:
            im.save(dst, optimize=True)
        return im.size


def _sips(src: Path, dst: Path, max_px: int, quality: int) -> tuple[int, int]:
    """macOS built-in. -Z scales the long edge and keeps the aspect ratio."""
    subprocess.run(["sips", "-Z", str(max_px),
                    "--setProperty", "formatOptions", str(quality),
                    str(src), "--out", str(dst)],
                   check=True, capture_output=True, text=True)
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(dst)],
                         capture_output=True, text=True).stdout
    dims = [int(w.split(":")[1]) for w in out.splitlines() if ":" in w and
            w.strip().startswith(("pixelWidth", "pixelHeight"))]
    return (dims[0], dims[1]) if len(dims) == 2 else (0, 0)


def _magick(src: Path, dst: Path, max_px: int, quality: int) -> tuple[int, int]:
    exe = shutil.which("magick") or shutil.which("convert")
    subprocess.run([exe, str(src), "-auto-orient", "-resize",
                    f"{max_px}x{max_px}>", "-quality", str(quality),
                    "-strip", str(dst)], check=True, capture_output=True)
    out = subprocess.run([shutil.which("identify") or exe, "-format", "%w %h",
                          str(dst)], capture_output=True, text=True).stdout
    try:
        w, h = out.split()[:2]
        return int(w), int(h)
    except ValueError:
        return (0, 0)


def available_backends() -> list[str]:
    found = []
    try:
        import PIL  # noqa: F401
        found.append("pillow")
    except ImportError:
        pass
    if shutil.which("sips"):
        found.append("sips")
    if shutil.which("magick") or shutil.which("convert"):
        found.append("magick")
    return found


# --------------------------------------------------------------------------

def process(src: Path, dst: Path, max_px: int = MAX_PX, quality: int = QUALITY,
            max_kb: int | None = None, backend: str | None = None
            ) -> tuple[Path, str]:
    """Write a web-sized copy of src at dst. Returns (file written, report)."""
    before = src.stat().st_size // 1024

    if src.suffix.lower() == ".svg":              # vector: nothing to resize
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        return dst, f"{dst.name}: {before} kB, copied as vector (no resizing)"

    if src.suffix.lower() == ".pdf":              # a figure exported as PDF
        if not shutil.which("pdftoppm"):
            raise RuntimeError("pdftoppm (poppler) is needed to rasterise a PDF "
                               "figure: conda install -c conda-forge poppler")
        dst = dst.with_suffix(".png")
        res = subprocess.run(["pdftoppm", "-png", "-r", "150", "-singlefile",
                              str(src), str(dst.with_suffix(""))],
                             capture_output=True, text=True)
        if res.returncode != 0 or not dst.exists():
            raise RuntimeError(f"pdftoppm failed on {src.name}: "
                               f"{res.stderr.strip()[:120]}")
        src = dst                                 # resize the raster from here

    order = [backend] if backend else available_backends()
    if not order:
        raise RuntimeError(
            "no way to resize images. Any one of these fixes it:\n"
            "     conda install -c conda-forge pillow\n"
            "     python3 -m pip install --user pillow\n"
            "   (on macOS the built-in `sips` is used automatically if present)")

    last_error = None
    for name in order:
        try:
            size = {"pillow": _pillow, "sips": _sips, "magick": _magick}[name](
                src, dst, max_px, quality)
            break
        except Exception as exc:                  # noqa: BLE001
            last_error = f"{name}: {exc}"
    else:
        raise RuntimeError(f"every backend failed ({last_error})")

    # squeeze further if a size budget was given
    q = quality
    while max_kb and dst.stat().st_size // 1024 > max_kb and q > 40:
        q -= 8
        {"pillow": _pillow, "sips": _sips, "magick": _magick}[name](
            src if src != dst else dst, dst, max_px, q)

    # re-encoding can make an already-optimised PNG bigger; if nothing was
    # scaled down, keep the original bytes and only clean the metadata
    if (src != dst and dst.exists() and src.suffix.lower() == dst.suffix.lower()
            and dst.stat().st_size > src.stat().st_size):
        try:
            from PIL import Image as _I
            same = _I.open(src).size == _I.open(dst).size
        except Exception:                                   # noqa: BLE001
            same = False
        if same:
            shutil.copy2(src, dst)

    squeeze = _png_squeeze(dst) if dst.suffix.lower() == ".png" else ""

    removed = 0
    if dst.suffix.lower() in {".jpg", ".jpeg"}:
        removed = strip_jpeg_metadata(dst)
    elif dst.suffix.lower() == ".png":
        removed = strip_png_metadata(dst)

    after = dst.stat().st_size // 1024
    note = (f"{dst.name}: {before} kB -> {after} kB"
            + (f", {size[0]}x{size[1]}px" if size[0] else "")
            + f", via {name}"
            + (f", {removed} B of metadata removed" if removed else
               ", no metadata found")
            + (f"\n     {squeeze}" if squeeze else ""))
    return dst, note


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("-o", "--out", type=Path,
                    help="output file (single input only); default <name>_web.<ext>")
    ap.add_argument("--in-place", action="store_true", help="overwrite the input")
    ap.add_argument("--max-px", type=int, default=MAX_PX,
                    help=f"long edge in pixels (default {MAX_PX})")
    ap.add_argument("--quality", type=int, default=QUALITY,
                    help=f"JPEG quality (default {QUALITY})")
    ap.add_argument("--max-kb", type=int,
                    help="keep lowering quality until the file fits this size")
    ap.add_argument("--backend", choices=("pillow", "sips", "magick"))
    args = ap.parse_args()

    if args.out and len(args.images) > 1:
        print("-o takes a single input", file=sys.stderr)
        return 2
    if not available_backends() and not args.backend:
        print("shrink_image: nothing available to resize images with.",
              file=sys.stderr)
        print("   conda install -c conda-forge pillow   (or use macOS sips)",
              file=sys.stderr)
        return 1

    for src in args.images:
        if not src.exists():
            print(f"!! {src} not found", file=sys.stderr)
            return 1
        dst = (src if args.in_place else
               args.out or src.with_name(f"{src.stem}_web{src.suffix}"))
        try:
            _, note = process(src, dst, args.max_px, args.quality,
                              args.max_kb, args.backend)
        except Exception as exc:                  # noqa: BLE001
            print(f"!! {src.name}: {exc}", file=sys.stderr)
            return 1
        print(note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
