"""Shared helpers for docs/brand/gen_brand.py: the design-system tokens, outlined text (HarfBuzz
shaping, fontTools outlines, so no SVG depends on an installed font), WCAG contrast, the font
cache and the HTML renderer.

Fonts (all SIL OFL 1.1) are fetched once from the npm registry (@fontsource) into
~/.cache/aloop-brand/; nothing font-related is committed except the outlined paths.
"""
import io
import os
import re
import subprocess
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DS = os.path.join(HERE, "design-system")
CACHE = os.path.expanduser("~/.cache/aloop-brand")

DM_SANS = ("@fontsource-variable/dm-sans", "dm-sans-latin-opsz-normal.woff2")
FONTS = {
    # name: (npm package, file inside package/files/, variable-axis location or None)
    # The design system sets DM Sans 800 at -0.03em for display and 500 for text; browsers pick
    # the optical size from the font size, so display uses opsz 40 and text opsz 14.
    "display": DM_SANS + ({"wght": 800, "opsz": 40},),
    "heading": DM_SANS + ({"wght": 700, "opsz": 24},),
    "text": DM_SANS + ({"wght": 500, "opsz": 14},),
    "mono": ("@fontsource/jetbrains-mono", "jetbrains-mono-latin-500-normal.woff2", None),
}
# The faces the HTML cards (design-system/*.html) use, served to the renderer from the cache.
WEB_FONTS = [
    ("DM Sans", "100 1000") + DM_SANS,
    ("Caveat", "500", "@fontsource/caveat", "caveat-latin-500-normal.woff2"),
    ("Caveat", "700", "@fontsource/caveat", "caveat-latin-700-normal.woff2"),
    ("JetBrains Mono", "400", "@fontsource/jetbrains-mono", "jetbrains-mono-latin-400-normal.woff2"),
    ("JetBrains Mono", "500", "@fontsource/jetbrains-mono", "jetbrains-mono-latin-500-normal.woff2"),
    ("JetBrains Mono", "700", "@fontsource/jetbrains-mono", "jetbrains-mono-latin-700-normal.woff2"),
]


def tokens():
    """{name: '#RRGGBB'} for every hex colour token in design-system/tokens/colors.css."""
    css = open(os.path.join(DS, "tokens", "colors.css")).read()
    return {k: v.upper() for k, v in re.findall(r"--([\w-]+):(#[0-9A-Fa-f]{6})\b", css)}


def fetch(pkg, fname):
    """The cached copy of package/files/<fname> from an npm package (npm pack on first use)."""
    path = os.path.join(CACHE, fname)
    if os.path.exists(path):
        return path
    os.makedirs(CACHE, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = subprocess.run(["npm", "pack", "-s", pkg], cwd=tmp, check=True, capture_output=True, text=True)
        tgz = os.path.join(tmp, out.stdout.strip().splitlines()[-1])
        with tarfile.open(tgz) as tf:
            data = tf.extractfile("package/files/" + fname).read()
    with open(path, "wb") as f:
        f.write(data)
    return path


def font_path(name):
    """The TTF for a FONTS entry: decompressed and, for a variable font, instanced at its location."""
    pkg, fname, loc = FONTS[name]
    tag = "".join("-%s%g" % kv for kv in sorted((loc or {}).items()))
    ttf = os.path.join(CACHE, fname.replace(".woff2", tag + ".ttf"))
    if os.path.exists(ttf):
        return ttf
    from fontTools.ttLib import TTFont
    f = TTFont(fetch(pkg, fname))
    f.flavor = None
    if loc:
        from fontTools.varLib.instancer import instantiateVariableFont
        f = instantiateVariableFont(f, loc)
    buf = io.BytesIO()
    f.save(buf)
    with open(ttf, "wb") as out:
        out.write(buf.getvalue())
    return ttf


def text_path(text, font, size, x=0.0, y=0.0, track=0.0):
    """(svg path d, advance width, ink bounds (x0, y0, x1, y1)) for `text` set in FONTS[font] at
    `size` px, baseline at y. `track` is extra letter spacing in em, between letters only."""
    import uharfbuzz as hb
    from fontTools.pens.boundsPen import BoundsPen
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
    bounds = BoundsPen(glyphs)
    cx = 0.0
    n = len(buf.glyph_infos)
    for i, (info, pos) in enumerate(zip(buf.glyph_infos, buf.glyph_positions)):
        m = (s, 0, 0, -s, x + (cx + pos.x_offset) * s, y - pos.y_offset * s)
        g = glyphs[order[info.codepoint]]
        g.draw(TransformPen(pen, m))
        g.draw(TransformPen(bounds, m))
        cx += pos.x_advance + (track * face.upem if i < n - 1 else 0)
    return pen.getCommands(), cx * s, bounds.bounds


def line_metrics(font):
    """(ascent, descent) in em, as a browser lays out a line box (hhea, or OS/2 typo when
    USE_TYPO_METRICS is set)."""
    from fontTools.ttLib import TTFont
    tt = TTFont(font_path(font))
    upem, os2 = tt["head"].unitsPerEm, tt["OS/2"]
    if os2.fsSelection & (1 << 7):
        return os2.sTypoAscender / upem, -os2.sTypoDescender / upem
    return tt["hhea"].ascent / upem, -tt["hhea"].descent / upem


def has_glyphs(font, chars):
    from fontTools.ttLib import TTFont
    cmap = TTFont(font_path(font)).getBestCmap()
    return all(ord(c) in cmap for c in chars)


def lum(hexc):
    h = hexc.lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    la, lb = sorted([lum(a), lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def render_html(page, out, w, h, scale=1):
    """Screenshot design-system/<page> at w x h CSS px with the cached web fonts, via
    render.mjs (node + playwright + Chromium)."""
    faces = "".join("@font-face{font-family:'%s';font-weight:%s;src:url('file://%s')}" % (fam, wt, fetch(pkg, fn))
                    for fam, wt, pkg, fn in WEB_FONTS)
    with tempfile.NamedTemporaryFile("w", suffix=".css", delete=False) as css:
        css.write(faces)
    try:
        subprocess.run(["node", os.path.join(HERE, "render.mjs"), os.path.join(DS, page), out, str(w), str(h), str(scale), css.name],
                       check=True)
    finally:
        os.unlink(css.name)
