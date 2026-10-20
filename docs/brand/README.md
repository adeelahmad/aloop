# Aladdin brand

aloop's brand is the owner's **Aladdin design system**. Its tokens, specimen cards and the two
social cards are vendored in [`design-system/`](design-system/README.md), which holds the full
rules. This page covers what the repo uses and how to regenerate it.

```sh
pip install 'fonttools[woff]' uharfbuzz cairosvg   # plus the cairo library and npm (fonts, @fontsource, SIL OFL)
python3 docs/brand/gen_brand.py                    # the two HTML cards also need node + playwright (Chromium)
```

Every asset comes from that one script. Colours are read from
[`design-system/tokens/colors.css`](design-system/tokens/colors.css), so a token change there
reaches every file. SVG text is outlined, so no asset depends on an installed font.

## Colour is semantic

| Role | Token | Hex | Use |
|---|---|---|---|
| Intent, orchestration | `--aladdin-blue` | `#1A73E8` | the wordmark, links, the first loop segment |
| Proof something is missing | `--aladdin-red` | `#EA4335` | RED; text in this role uses `--red-dark` `#C5221F` |
| Proof it now holds | `--aladdin-green` | `#34A853` | GREEN, accepted; text uses `--green-dark` `#1E7E43` |
| Gate, decision, budget | `--aladdin-gold` | `#F9AB00` | gates (the ◆), budgets; text uses `--gold-dark` `#B06000` |
| Evidence, history | grays | `#202124` `#5F6368` `#F1F3F4` | text, provenance, measured numbers |

The signature order is always Intent → RED → GREEN → Gate. Colour is never decorative: no
gradients as fills and no coloured glows.

**Contrast** (checked by `check_palette()` in the generator, which exits non-zero on a miss):
blue on white is 4.51:1, and every `*-dark` text variant on white is at least 4.65:1. The core
red, green and gold are the design system's exact values and carry graphics, never text. Two tint
pairs are kept exact at under 4.5:1, so they carry bold titles only: green-dark on its tint
(4.49:1) and gold-dark on its tint (4.34:1).

## The wordmark

Design-system option D: **Aladdin** in DM Sans 800 at −0.03 em, Aladdin Blue, over the
**loop bar**. The bar has four equal segments (blue, red, green, gold), is as wide as the word,
0.125 em high and fully rounded. On a blue ground the word is white and the first segment
turns white. Never recolour single letters. The Aladin display font (options G and H) was
explored and not chosen.

There is no pictorial mark yet. The design system adds one only when an original exists. The
icon is the on-blue wordmark cut to its initial: a white **A** over the loop bar on a blue tile.
The 16 px cut is drawn on whole pixels (cap y 2–10, bar y 12–14 in 3 px segments). The site's
lamp is the illustration ROADMAP §4.11 asks for, drawn flat in the design-system colours. It
is not part of the wordmark or the icon.

## Type and voice

DM Sans: 800 for display, 700 for headings, 400 for body at 16/1.55. JetBrains Mono for
commands, artifacts and gate names. Caveat, in blue, for margin notes only.

The voice is short declaratives in pairs: *Models explore. Gates decide.* · *Plan. Test. Build.
Improve. Repeat.* · *Smaller steps. Better software.* Numbers carry their provenance (the
[evidence ledger](../evidence/README.md)). **No emoji** on brand surfaces, the README included.
The semantic glyphs ● ◆ ✓ ✕ → take their place.

## Files

| File | Where it goes |
|---|---|
| `banner-light.png` / `banner-dark.png` | README header, in a `<picture>` that switches on `prefers-color-scheme` (1280×320, PNG at 2×; the SVGs are the sources) |
| `wordmark.svg` / `wordmark-on-blue.svg` | anywhere the name is shown as a logo |
| `signature.svg` | the four dots, as a section marker |
| `icon.svg`, `app-icon-180.png`, `app-icon-512.png` | app and touch icons |
| `favicon.svg` (= `icon-16.svg`), `favicon-16.png`, `favicon-32.png` | the site's `<link rel="icon">` |
| `social-preview.png` | GitHub → Settings → General → Social preview (2560×1280, under 1 MB; GitHub refuses SVG) |
| `og-card.png` | the site's `og:image` and `twitter:image` (1200×630, large type for a phone feed) |
| `design-system/social-preview.html`, `og-card.html` | the HTML sources of the two cards |

The social preview is the owner's design. Four changes keep its claims true: the RED card names
the real agent (`red-worker`), a grey evidence box gives the measured cost (8 intents, each
under $1 and 15 min in its latest run, from the ledger), the feature list adds the mission
layer, and the footer no longer breaks words across lines. The genie illustration from the
earlier infographic is not used; the design system keeps the lamp metaphor light and original.

```html
<link rel="icon" href="brand/favicon.svg" type="image/svg+xml" />
<link rel="icon" href="brand/favicon-32.png" sizes="32x32" type="image/png" />
<link rel="apple-touch-icon" href="brand/app-icon-180.png" />
<meta property="og:image" content="https://raw.githubusercontent.com/adeelahmad/aloop/master/docs/brand/og-card.png">
```
