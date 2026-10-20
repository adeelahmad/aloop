#!/usr/bin/env python3
"""Generate every aloop brand asset in docs/brand/ from the Aladdin design system.

    python3 docs/brand/gen_brand.py   # needs: pip install 'fonttools[woff]' uharfbuzz cairosvg; the cairo library; npm (fonts);
                                      # node + playwright with Chromium for the two PNGs rendered from HTML

The colours are read from design-system/tokens/colors.css, so the design system stays the one
source. Outputs (SVG text is outlined, so nothing depends on an installed font):
  wordmark.svg  wordmark-on-blue.svg    "Aladdin" in DM Sans 800 over the four-segment loop bar (option D)
  signature.svg                         the four dots, in order: intent, RED, GREEN, gate
  icon.svg  icon-16.svg  favicon.svg    the on-blue wordmark's "A" over the loop bar, with a 16 px cut
  favicon-{16,32}.png  app-icon-{180,512}.png
  banner-{light,dark}.{svg,png}         1280x320 README header (PNG at 2x); dark is the on-blue variant
  social-preview.png                    2560x1280 (1280x640 at 2x), from design-system/social-preview.html,
                                        for GitHub Settings → Social preview (PNG: GitHub refuses SVG)
  og-card.png                           1200x630, from design-system/og-card.html, the site's og:image
The usage rules are in docs/brand/README.md and design-system/README.md.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _brandlib as L  # noqa: E402

OUT = L.HERE
T = L.tokens()
BLUE, RED, GREEN, GOLD = T["aladdin-blue"], T["aladdin-red"], T["aladdin-green"], T["aladdin-gold"]
WHITE = T["background"]
LOOP = (BLUE, RED, GREEN, GOLD)          # Intent → RED → GREEN → Gate
LOOP_ON_BLUE = (WHITE, RED, GREEN, GOLD)  # on blue grounds the first segment turns white
TRACK = -0.03                            # display tracking, em


def check_palette():
    """Every text pair the brand uses, at the WCAG level its usage note names. The core red, green
    and gold stay exact (the design system's own values) and carry no text: text in those roles
    uses the *-dark variants, which are checked here."""
    pairs = [
        ("text-primary", "background", 4.5), ("text-strong", "background", 4.5), ("text-secondary", "background", 4.5),
        ("text-secondary", "surface", 4.5), ("text-muted", "background", 3.0),
        ("aladdin-blue", "background", 4.5), ("blue-dark", "intent-surface", 4.5), ("red-dark", "red-surface", 4.5),
        ("gold-dark", "background", 4.5),
        # 4.49 and 4.34: kept exact, so on their tints these two carry bold titles only (3:1)
        ("green-dark", "green-surface", 3.0), ("gold-dark", "gate-surface", 3.0),
        ("text-on-color", "aladdin-blue", 4.5), ("term-fg", "term-bg", 4.5), ("term-muted", "term-bg", 4.5),
        ("term-red", "term-bg", 4.5), ("term-green", "term-bg", 4.5), ("term-gold", "term-bg", 4.5), ("term-blue", "term-bg", 4.5),
    ]
    bad = []
    for fg, bg, lim in pairs:
        r = L.ratio(T[fg], T[bg])
        if r < lim:
            bad.append("%s on %s: %.2f < %g" % (fg, bg, r, lim))
    return bad


# ── SVG helpers ────────────────────────────────────────────────────────────────────────────
def svg(w, h, body, view=None, title=None):
    t = '<title>%s</title>' % title if title else ""
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%g" height="%g" viewBox="%s" role="img"%s>%s%s</svg>'
            % (w, h, view or "0 0 %g %g" % (w, h), ' aria-label="%s"' % title if title else "", t, body))


def png(svg_text, path, scale=1.0):
    import cairosvg
    cairosvg.svg2png(bytestring=svg_text.encode(), write_to=path, scale=scale)


def write(name, text):
    with open(os.path.join(OUT, name), "w") as f:
        f.write(text + "\n")


def text(s, font, size, x, y, color, track=0.0, anchor="start"):
    """An outlined run of text, baseline at y. Returns (svg, advance)."""
    d, w, _ = L.text_path(s, font, size, 0, 0, track)
    if anchor == "middle":
        x -= w / 2
    elif anchor == "end":
        x -= w
    return '<path transform="translate(%g %g)" d="%s" fill="%s"/>' % (x, y, d, color), w


def loop_bar(x, y, w, h, colors):
    """The loop bar: four equal segments, the whole bar fully rounded. Each segment runs a hair
    under the next so no seam shows between them."""
    r, sw = h / 2, w / 4
    out = []
    for i, c in enumerate(colors):
        x0, x1 = x + i * sw, x + (i + 1) * sw + (0.02 * h if i < 3 else 0)
        if i == 0:
            d = "M%g %gH%gV%gH%gA%g %g 0 0 1 %g %gZ" % (x0 + r, y, x1, y + h, x0 + r, r, r, x0 + r, y)
        elif i == 3:
            d = "M%g %gH%gA%g %g 0 0 1 %g %gH%gZ" % (x0, y, x1 - r, r, r, x1 - r, y + h, x0)
        else:
            d = "M%g %gH%gV%gH%gZ" % (x0, y, x1, y + h, x0)
        out.append('<path d="%s" fill="%s"/>' % (d, c))
    return "".join(out)


def arrow(x, y, w, color, sw):
    """A drawn → (the latin font subsets have none), from x to x+w at height y."""
    hd = sw * 2.2
    return ('<path d="M%g %gH%gM%g %gL%g %gL%g %g" fill="none" stroke="%s" stroke-width="%g" stroke-linecap="round" stroke-linejoin="round"/>'
            % (x, y, x + w, x + w - hd, y - hd, x + w, y, x + w - hd, y + hd, color, sw))


# ── The wordmark (design system option D) ──────────────────────────────────────────────────
# "Aladdin" in DM Sans 800 at -0.03em, Aladdin Blue, over the loop bar. The bar is as wide as the
# word, 0.125 em high, and sits 1/6 em under the word's line box (line-height 1), exactly as the
# design system's wordmark card sets it in CSS.
WORD = "Aladdin"


def wordmark(x, baseline, size, word_color, colors):
    """(svg, ink box (x0, y0, x1, y1)) for the wordmark with its baseline at `baseline`."""
    p, adv, (bx0, by0, bx1, _) = L.text_path(WORD, "display", size, 0, 0, TRACK)
    asc, desc = L.line_metrics("display")
    bar_top = baseline + ((1 - asc + desc) / 2 + 1 / 6) * size
    bar_h = 0.125 * size
    body = ('<path transform="translate(%g %g)" d="%s" fill="%s"/>' % (x, baseline, p, word_color)
            + loop_bar(x, bar_top, adv, bar_h, colors))
    return body, (x + min(bx0, 0), baseline + by0, x + max(bx1, adv), bar_top + bar_h)


def wordmark_svg(word_color, colors, ground=None):
    size = 96
    pad = 0.3 * size if ground else 0
    _, (x0, y0, x1, y1) = wordmark(0, 0, size, word_color, colors)
    w, h = x1 - x0 + 2 * pad, y1 - y0 + 2 * pad
    body, _ = wordmark(pad - x0, pad - y0, size, word_color, colors)
    bg = '<rect width="%g" height="%g" rx="%g" fill="%s"/>' % (w, h, 16, ground) if ground else ""
    return svg(round(w, 2), round(h, 2), bg + body, title="Aladdin")


def signature_svg():
    r, gap = 11, 10
    body = "".join('<circle cx="%g" cy="%g" r="%g" fill="%s"/>' % (r + i * (2 * r + gap), r, r, c) for i, c in enumerate(LOOP))
    return svg(4 * 2 * r + 3 * gap, 2 * r, body, title="Intent, RED, GREEN, gate")


# ── The icon ───────────────────────────────────────────────────────────────────────────────
# The design system has no pictorial mark yet ("add one only when an original is provided"), so
# the icon is its on-blue wordmark cut to the initial: a white "A" over the loop bar, first
# segment white, on an Aladdin Blue tile (22.37% corner). Master at 512; a 16 px cut on whole
# pixels (cap y2..10, bar y12..14 in four 3 px segments) for tabs.
def icon_body(px):
    if px == 16:
        size = 8 * 100 / _cap_height(100)
        p, _, (bx0, _, bx1, _) = L.text_path("A", "display", size, 0, 0)
        ax = 8 - (bx0 + bx1) / 2
        return ('<rect width="16" height="16" rx="3.5" fill="%s"/>' % BLUE
                + '<path transform="translate(%g 10)" d="%s" fill="%s"/>' % (ax, p, WHITE)
                + loop_bar(2, 12, 12, 2, LOOP_ON_BLUE))
    s = px / 512
    size = 340 * s
    p, _, (bx0, by0, bx1, _) = L.text_path("A", "display", size, 0, 0)
    cap, gap, bar_h, bar_w = -by0, 40 * s, 34 * s, 300 * s
    top = (px - (cap + gap + bar_h)) / 2
    base = top + cap
    return ('<rect width="%g" height="%g" rx="%g" fill="%s"/>' % (px, px, px * 0.2237, BLUE)
            + '<path transform="translate(%g %g)" d="%s" fill="%s"/>' % (px / 2 - (bx0 + bx1) / 2, base, p, WHITE)
            + loop_bar((px - bar_w) / 2, base + gap, bar_w, bar_h, LOOP_ON_BLUE))


def _cap_height(size):
    _, _, (_, by0, _, _) = L.text_path("A", "display", size, 0, 0)
    return -by0


# ── README banners ─────────────────────────────────────────────────────────────────────────
def banner(theme):
    """1280x320. Light: the wordmark on the white canvas inside a hairline panel. Dark: the
    on-blue variant (white word, first bar segment white), which reads on GitHub's dark ground."""
    W, H = 1280, 320
    light = theme == "light"
    ink = T["text-primary"] if light else WHITE
    soft = T["text-secondary"] if light else WHITE
    gate_text = T["gold-dark"] if light else WHITE
    b = (['<rect x=".5" y=".5" width="%d" height="%d" rx="16" fill="%s" stroke="%s"/>' % (W - 1, H - 1, WHITE, T["border"])]
         if light else ['<rect width="%d" height="%d" rx="16" fill="%s"/>' % (W, H, BLUE)])
    wm, (x0, y0, x1, y1) = wordmark(72, 0, 116, BLUE if light else WHITE, LOOP if light else LOOP_ON_BLUE)
    tag_gap, tag_size = 50, 30
    group = (y1 - y0) + tag_gap
    dy = (H - group) / 2 - y0 - 14
    b.append('<g transform="translate(0 %g)">%s</g>' % (dy, wm))
    p, _ = text("Intent is all you need.", "text", tag_size, 76, y1 + dy + tag_gap, soft)
    b.append(p)
    # right column, right-aligned: the tagline pair, then the signature
    R = W - 72
    p1, w1 = text("Gates decide.", "display", 36, R, 128, gate_text, track=-0.02, anchor="end")
    p2, _ = text("Models explore. ", "display", 36, R - w1, 128, ink, track=-0.02, anchor="end")
    b += [p2, p1]
    p, _ = text("Plan. Test. Build. Improve. Repeat.", "text", 22, R, 170, soft, anchor="end")
    b.append(p)
    labels, fs, aw, gap = ["INTENT", "RED", "GREEN", "GATE"], 15, 18, 10
    widths = [L.text_path(s, "mono", fs, 0, 0, 0.08)[1] for s in labels]
    total = sum(widths) + 3 * (aw + 2 * gap)
    dots_w = 4 * 16 + 3 * 7
    x = R - total
    y = 226
    cols = LOOP if light else LOOP_ON_BLUE
    for i, c in enumerate(cols):
        b.append('<circle cx="%g" cy="%g" r="8" fill="%s"/>' % (x - dots_w - 18 + 8 + i * 23, y - 5.5, c))
    for i, s in enumerate(labels):
        p, w = text(s, "mono", fs, x, y, soft, track=0.08)
        b.append(p)
        x += w
        if i < 3:
            b.append(arrow(x + gap, y - 5.5, aw, soft, 1.6))
            x += aw + 2 * gap
    return svg(W, H, "".join(b), title="Aladdin: intent is all you need. Models explore. Gates decide.")


def main():
    bad = check_palette()
    if bad:
        sys.exit("palette fails contrast:\n  " + "\n  ".join(bad))
    write("wordmark.svg", wordmark_svg(BLUE, LOOP))
    write("wordmark-on-blue.svg", wordmark_svg(WHITE, LOOP_ON_BLUE, ground=BLUE))
    write("signature.svg", signature_svg())
    write("icon.svg", svg(512, 512, icon_body(512), title="Aladdin"))
    icon16 = svg(16, 16, icon_body(16), title="Aladdin")
    write("icon-16.svg", icon16)
    write("favicon.svg", icon16)  # tabs draw it at 16 CSS px: the whole-pixel cut
    png(icon16, os.path.join(OUT, "favicon-16.png"))
    png(icon16, os.path.join(OUT, "favicon-32.png"), scale=2)
    for px in (180, 512):
        png(svg(px, px, icon_body(px)), os.path.join(OUT, "app-icon-%d.png" % px))
    for theme in ("light", "dark"):
        bn = banner(theme)
        write("banner-%s.svg" % theme, bn)
        png(bn, os.path.join(OUT, "banner-%s.png" % theme), scale=2)
    L.render_html("social-preview.html", os.path.join(OUT, "social-preview.png"), 1280, 640, 2)
    L.render_html("og-card.html", os.path.join(OUT, "og-card.png"), 1200, 630, 1)
    print("brand assets written to", os.path.relpath(OUT))


if __name__ == "__main__":
    main()
