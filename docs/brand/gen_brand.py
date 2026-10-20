#!/usr/bin/env python3
"""Generate every aloop brand asset in docs/brand/ from one palette and one mark geometry.

    python3 docs/brand/gen_brand.py          # needs: pip install 'fonttools[woff]' uharfbuzz cairosvg; the cairo library; npm (fonts)

Outputs (all text outlined, so nothing depends on an installed font):
  mark-{dark,light,mono}.svg  mark-16.svg  favicon.svg (adapts to the colour scheme)
  favicon-{16,32}.png  app-icon-{180,512}.png
  lockup-{dark,light}.svg     the mark beside the "aloop" wordmark
  banner-{dark,light}.{svg,png}   1280x320 README header (PNG at 2x)
  social-preview.png          1280x640, for GitHub Settings → Social preview (PNG: GitHub refuses SVG)
  og-card.png                 1200x630, the site's og:image
The usage rules are in docs/brand/README.md.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _brandlib as L  # noqa: E402

OUT = os.path.dirname(os.path.abspath(__file__))

# ── Palette (tokens; contrast ratios checked in check_palette) ─────────────────────────────
DARK = {
    "bg": "#0f1117", "panel": "#171a23", "line": "#2a2e3a",
    "ink": "#e8e6df", "muted": "#9a9890",
    "gold": "#d9a441",            # the accent: mark body and accent text on dark grounds
    "gold-text": "#d9a441",
    "red": "#dc5a56", "green": "#5cb85c",
}
LIGHT = {
    "bg": "#faf8f2", "panel": "#ffffff", "line": "#e2dfd4",
    "ink": "#1d1d1b", "muted": "#5f5e58",
    "gold": "#b47a1e",            # the mark's gold on light grounds (3:1 for graphics)
    "gold-text": "#8a5a12",       # accent text on light grounds (4.5:1)
    "red": "#c0392b", "green": "#2e7d32",
}
THEMES = {"dark": DARK, "light": LIGHT}

# ── The mark ─────────────────────────────────────────────────────────────────────────────
# A geometric lowercase "a" for aloop / Aladdin. Its bowl is the loop: a ring that is RED on
# top (the failing proof) and GREEN below (earned). Its stem is the gate: two gold posts the
# ring passes between, standing one unit proud of the ring so the gate still shows as a notch
# in a single colour. The loop goes into the gate red and comes out green: models explore,
# gates decide. Paint order: the whole ring green, the red top half over it (so a seam can
# only ever show green), then the posts. Chosen over a lamp and a flame-in-an-"a" because it
# is the only one of the three that reads at 16 px (docs/brand/README.md, "The mark").
#
# 24 grid: ring centre (11,12), centreline r 7.5, stroke 3 (outer x2..20, y3..21);
#          posts x17..21, y3..9 and y15..21 (the window y9..15 holds the ring's right side).
# 16 cut:  whole pixels; ring centre (7,8), r 5, stroke 2; posts x11..14, y2..6 and y10..14.
GRID = {
    24: {"ring": (11, 12, 7.5, 3), "red": "M3.5 12a7.5 7.5 0 0 1 15 0", "posts": "M17 3h4v6h-4zM17 15h4v6h-4z"},
    16: {"ring": (7, 8, 5, 2), "red": "M2 8a5 5 0 0 1 10 0", "posts": "M11 2h3v4h-3zM11 10h3v4h-3z"},
}


def _mark(grid, gold, red, green, adaptive=False):
    g = GRID[grid]
    cx, cy, r, w = g["ring"]
    paint = (lambda k, c, prop: 'class="%s"' % k) if adaptive else (lambda k, c, prop: '%s="%s"' % (prop, c))
    return ('<circle cx="%g" cy="%g" r="%g" fill="none" %s stroke-width="%g"/>' % (cx, cy, r, paint("n", green, "stroke"), w)
            + ('<path d="%s" fill="none" %s stroke-width="%g"/>' % (g["red"], paint("r", red, "stroke"), w) if red else "")
            + '<path d="%s" %s/>' % (g["posts"], paint("g", gold, "fill")))


def mark_body(t, mono=None, grid=24):
    """The mark as SVG elements on its grid (24 master, or the 16 px cut), for theme dict t.
    mono: one colour for every part (the single-colour variant; the red half is dropped)."""
    if mono:
        return _mark(grid, mono, None, mono)
    return _mark(grid, t["gold"], t["red"], t["green"])


def adaptive_body(grid=24):
    """The mark with classes and a prefers-color-scheme style: light palette, dark on dark."""
    style = ('<style>.g{fill:%s}.r{stroke:%s}.n{stroke:%s}@media (prefers-color-scheme:dark){.g{fill:%s}.r{stroke:%s}.n{stroke:%s}}</style>'
             % (LIGHT["gold"], LIGHT["red"], LIGHT["green"], DARK["gold"], DARK["red"], DARK["green"]))
    return style + _mark(grid, None, True, None, adaptive=True)


def svg(w, h, body, view=None, title=None):
    t = '<title>%s</title>' % title if title else ""
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%g" height="%g" viewBox="%s" role="img"%s>%s%s</svg>'
            % (w, h, view or "0 0 %g %g" % (w, h), ' aria-label="%s"' % title if title else "", t, body))


def nest(x, y, size, inner, grid=24):
    return '<svg x="%g" y="%g" width="%g" height="%g" viewBox="0 0 %d %d">%s</svg>' % (x, y, size, size, grid, grid, inner)


def png(svg_text, path, scale=1.0):
    import cairosvg
    cairosvg.svg2png(bytestring=svg_text.encode(), write_to=path, scale=scale)


def write(name, text):
    with open(os.path.join(OUT, name), "w") as f:
        f.write(text + "\n")


# ── Small drawn glyphs (the latin font subset has no ✓ or arrows) ──────────────────────────
def tick(x, y, s, color, w=2.2):
    return ('<path d="M%g %g l%g %g l%g %g" fill="none" stroke="%s" stroke-width="%g" stroke-linecap="round" stroke-linejoin="round"/>'
            % (x, y, s * .35, s * .35, s * .65, -s * .7, color, w))


def cross(x, y, s, color, w=2.2):
    return ('<path d="M%g %g l%g %g m0 %g l%g %g" fill="none" stroke="%s" stroke-width="%g" stroke-linecap="round"/>'
            % (x, y, s, s, -s, -s, s, color, w))


def dot(cx, cy, r, color):
    return '<circle cx="%g" cy="%g" r="%g" fill="%s"/>' % (cx, cy, r, color)


def text(s, font, size, x, y, color, track=0.0, anchor="start"):
    d, w = L.text_path(s, font, size, 0, 0, track)
    if anchor == "middle":
        x -= w / 2
    elif anchor == "end":
        x -= w
    return '<path transform="translate(%g %g)" d="%s" fill="%s"/>' % (x, y, d, color), w


# ── Compositions ───────────────────────────────────────────────────────────────────────────
WORD = "aloop"


def wordmark(t, x, y, size):
    return text(WORD, "display", size, x, y, t["ink"], track=-0.02)


def terminal(t, x, y, w, scale=1.0):
    """A small terminal card: the intent in, RED fails, GREEN passes, the gate accepts."""
    s = scale
    lh, pad, fs = 34 * s, 22 * s, 17 * s
    h = pad * 2 + lh * 4 - 8 * s
    out = ['<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="%s" stroke="%s" stroke-width="%g"/>'
           % (x, y, w, h, 14 * s, t["panel"], t["line"], 1.5 * s)]
    by = y + pad + fs
    p, pw = text("$", "mono", fs, x + pad, by, t["gold-text"])
    out.append(p)
    p, _ = text('aladdin "add CSV export"', "mono-regular", fs, x + pad + pw + 10 * s, by, t["ink"])
    out.append(p)
    rows = [("RED", "tests fail first", t["red"]), ("GREEN", "tests pass", t["green"]), ("GATE", "accepted", t["gold"])]
    for i, (lab, msg, c) in enumerate(rows):
        yy = by + lh * (i + 1)
        out.append(dot(x + pad + 6 * s, yy - fs * .36, 6 * s, c))
        p, _ = text(lab, "mono", fs, x + pad + 22 * s, yy, c if lab != "GATE" else t["gold-text"])
        out.append(p)
        p, _ = text(msg, "mono-regular", fs, x + pad + 104 * s, yy, t["muted"])
        out.append(p)
        out.append(tick(x + w - pad - 18 * s, yy - fs * .55, 16 * s, t["green"], 2.4 * s) if i else
                   cross(x + w - pad - 16 * s, yy - fs * .78, 12 * s, t["red"], 2.4 * s))
    return "".join(out), h


def glow(cx, cy, r, color, op):
    gid = "g%d%d" % (int(cx), int(cy))
    return ('<defs><radialGradient id="%s"><stop offset="0" stop-color="%s" stop-opacity="%g"/>'
            '<stop offset="1" stop-color="%s" stop-opacity="0"/></radialGradient></defs>'
            '<circle cx="%g" cy="%g" r="%g" fill="url(#%s)"/>' % (gid, color, op, color, cx, cy, r, gid))


def banner(theme):
    t = THEMES[theme]
    W, H = 1280, 320
    b = ['<rect width="%d" height="%d" fill="%s"/>' % (W, H, t["bg"])]
    b.append(glow(150, 160, 190, t["gold"], .22 if theme == "dark" else .14))
    b.append(nest(58, 64, 192, mark_body(t)))
    p, _ = text("ALADDIN'S LOOP", "mono", 15, 282, 96, t["gold-text"], track=0.18)
    b.append(p)
    p, ww = wordmark(t, 278, 186, 104)
    b.append(p)
    p, _ = text("Intent is all you need.", "display-medium", 30, 282, 234, t["ink"])
    b.append(p)
    p, _ = text("Models explore. Gates decide.", "mono-regular", 18, 282, 270, t["muted"])
    b.append(p)
    card, ch = terminal(t, 840, 0, 384)
    b.append('<g transform="translate(0 %g)">%s</g>' % ((H - ch) / 2 - 10, card))
    p, _ = text("Claude Code · Codex · OpenCode", "mono-regular", 14, 840 + 384, (H + ch) / 2 + 18, t["muted"], anchor="end")
    b.append(p)
    return svg(W, H, "".join(b), title="aloop, Aladdin's Loop: intent is all you need")


def social(theme="dark", W=1280, H=640):
    t = THEMES[theme]
    b = ['<rect width="%d" height="%d" fill="%s"/>' % (W, H, t["bg"])]
    # The stack is drawn for a 640-high canvas, ink from y=76 (top of the mark) to y=608 (footer
    # descenders); dy centres it on any H.
    dy = (H - (608 - 76)) / 2 - 76
    b.append(glow(W * .5, 192 + dy, 260, t["gold"], .2))
    ms = 176
    b.append(nest((W - ms) / 2, 54 + dy, ms, mark_body(t)))
    p, _ = text(WORD, "display", 120, W / 2, 338 + dy, t["ink"], track=-0.02, anchor="middle")
    b.append(p)
    p, _ = text("Intent is all you need.", "display-medium", 40, W / 2, 400 + dy, t["gold-text"], anchor="middle")
    b.append(p)
    p, _ = text("Plan with a human. Build test-first. Gates decide what lands.", "mono-regular", 22, W / 2, 452 + dy, t["muted"], anchor="middle")
    b.append(p)
    # the loop, as chips: RED → GREEN → GATE
    chips = [("RED", t["red"]), ("GREEN", t["green"]), ("GATE", t["gold-text"])]
    cw, gap, cy = 150, 46, 500 + dy
    x0 = W / 2 - (cw * 3 + gap * 2) / 2
    for i, (lab, c) in enumerate(chips):
        x = x0 + i * (cw + gap)
        b.append('<rect x="%g" y="%g" width="%g" height="48" rx="24" fill="%s" stroke="%s" stroke-width="2"/>' % (x, cy, cw, t["panel"], c))
        p, _ = text(lab, "mono", 20, x + cw / 2, cy + 31, c, track=0.08, anchor="middle")
        b.append(p)
        if i < 2:
            ax = x + cw + 8
            b.append('<path d="M%g %g h%g m-8 -7 l8 7 l-8 7" fill="none" stroke="%s" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
                     % (ax, cy + 24, gap - 16, t["muted"]))
    p, _ = text("Claude Code · Codex · OpenCode", "mono-regular", 20, W / 2, 604 + dy, t["muted"], anchor="middle")
    b.append(p)
    return svg(W, H, "".join(b), title="aloop: intent is all you need")


def lockup(theme):
    t = THEMES[theme]
    size = 64
    ch = L.cap_height("display", size)
    mbox = 1.25 * size
    p, ww = wordmark(t, mbox + 0.12 * size, 0, size)
    W = mbox + 0.12 * size + ww + 4
    H = mbox
    base = (H + ch) / 2
    body = nest(0, 0, mbox, mark_body(t)) + '<g transform="translate(0 %g)">%s</g>' % (base, p)
    return svg(W, H, body, title="aloop")


def favicon():
    # Tabs draw the icon at 16 CSS px, so it uses the 16 px cut (whole pixels at 1x and 2x).
    return svg(16, 16, adaptive_body(16), view="0 0 16 16", title="aloop")


def app_icon(px):
    t = DARK
    r = px * 0.2237
    inner = nest(px * .17, px * .17, px * .66, mark_body(t))
    return svg(px, px, '<rect width="%d" height="%d" rx="%g" fill="%s"/>%s%s' % (px, px, r, t["bg"], glow(px / 2, px / 2, px * .55, t["gold"], .25), inner))


def check_palette():
    """Every text token on every ground its usage note names, and every mark colour, meets WCAG."""
    need = []
    for name, t in THEMES.items():
        for tok, lim in (("ink", 4.5), ("muted", 4.5), ("gold-text", 4.5), ("red", 4.5), ("green", 4.5), ("gold", 3.0)):
            for ground in ("bg", "panel"):
                r = L.ratio(t[tok], t[ground])
                if r < lim:
                    need.append("%s %s on %s: %.2f < %g" % (name, tok, ground, r, lim))
    return need


def main():
    bad = check_palette()
    if bad:
        sys.exit("palette fails contrast:\n  " + "\n  ".join(bad))
    for theme, t in THEMES.items():
        write("mark-%s.svg" % theme, svg(96, 96, mark_body(t), view="0 0 24 24", title="aloop"))
        write("lockup-%s.svg" % theme, lockup(theme))
        bn = banner(theme)
        write("banner-%s.svg" % theme, bn)
        png(bn, os.path.join(OUT, "banner-%s.png" % theme), scale=2)
    write("mark-mono.svg", svg(96, 96, mark_body(DARK, mono="currentColor"), view="0 0 24 24", title="aloop"))
    write("mark-16.svg", svg(16, 16, adaptive_body(16), view="0 0 16 16", title="aloop"))
    write("mark.svg", svg(96, 96, adaptive_body(24), view="0 0 24 24", title="aloop"))
    write("favicon.svg", favicon())
    png(svg(16, 16, mark_body(LIGHT, grid=16), view="0 0 16 16"), os.path.join(OUT, "favicon-16.png"))
    png(svg(32, 32, mark_body(LIGHT), view="0 0 24 24"), os.path.join(OUT, "favicon-32.png"))
    for px in (180, 512):
        png(app_icon(px), os.path.join(OUT, "app-icon-%d.png" % px))
    png(social("dark"), os.path.join(OUT, "social-preview.png"))
    png(social("dark", 1200, 630), os.path.join(OUT, "og-card.png"))
    print("brand assets written to", os.path.relpath(OUT))


if __name__ == "__main__":
    main()
