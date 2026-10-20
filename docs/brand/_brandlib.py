"""Shared helpers for docs/brand/gen_brand.py: outlined text (HarfBuzz shaping, fontTools
outlines, so no asset depends on an installed font), WCAG contrast, and the font cache.

Fonts (all SIL OFL 1.1) are fetched once from the npm registry (@fontsource) into
~/.cache/aloop-brand/; nothing font-related is committed except the outlined paths.
"""
import io
import os
import subprocess
import tarfile
import tempfile

CACHE = os.path.expanduser("~/.cache/aloop-brand")
FONTS = {
    # name: (npm package, file inside package/files/)
    "display": ("@fontsource/space-grotesk", "space-grotesk-latin-700-normal.woff2"),
    "display-medium": ("@fontsource/space-grotesk", "space-grotesk-latin-500-normal.woff2"),
    "mono": ("@fontsource/jetbrains-mono", "jetbrains-mono-latin-700-normal.woff2"),
    "mono-regular": ("@fontsource/jetbrains-mono", "jetbrains-mono-latin-500-normal.woff2"),
}


def font_path(name):
    """The TTF for a FONTS entry, fetched (npm pack) and decompressed on first use."""
    pkg, fname = FONTS[name]
    ttf = os.path.join(CACHE, fname.replace(".woff2", ".ttf"))
    if os.path.exists(ttf):
        return ttf
    from fontTools.ttLib import TTFont
    os.makedirs(CACHE, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = subprocess.run(["npm", "pack", "-s", pkg], cwd=tmp, check=True, capture_output=True, text=True)
        tgz = os.path.join(tmp, out.stdout.strip().splitlines()[-1])
        with tarfile.open(tgz) as tf:
            data = tf.extractfile("package/files/" + fname).read()
    f = TTFont(io.BytesIO(data))
    f.flavor = None
    f.save(ttf)
    return ttf


def text_path(text, font, size, x=0.0, y=0.0, track=0.0):
    """(svg path d, advance width) for `text` set in FONTS[font] at `size` px, baseline at y.
    `track` is extra letter spacing in em."""
    import uharfbuzz as hb
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    path = font_path(font)
    face = hb.Face(hb.Blob.from_file_path(path))
    hbfont = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hbfont, buf, {"kern": True, "liga": True})
    tt = TTFont(path)
    glyphs, order = tt.getGlyphSet(), tt.getGlyphOrder()
    s = size / face.upem
    pen = SVGPathPen(glyphs, ntos=lambda v: ("%.2f" % v).rstrip("0").rstrip("."))
    cx = 0.0
    n = len(buf.glyph_infos)
    for i, (info, pos) in enumerate(zip(buf.glyph_infos, buf.glyph_positions)):
        tp = TransformPen(pen, (s, 0, 0, -s, x + (cx + pos.x_offset) * s, y - pos.y_offset * s))
        glyphs[order[info.codepoint]].draw(tp)
        cx += pos.x_advance + (track * face.upem if i < n - 1 else 0)
    return pen.getCommands(), cx * s


def cap_height(font, size):
    from fontTools.ttLib import TTFont
    tt = TTFont(font_path(font))
    os2 = tt["OS/2"]
    return getattr(os2, "sCapHeight", 0.7 * tt["head"].unitsPerEm) * size / tt["head"].unitsPerEm


def lum(hexc):
    h = hexc.lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    la, lb = sorted([lum(a), lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)
