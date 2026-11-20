# Aladdin Design System

> **Vendored into aloop** from the owner's *Aladdin Design System* (built 2026-09-23 from the
> agentic-agile README). This folder keeps the tokens, the guideline cards and the two social cards
> (`social-preview.html`, `og-card.html`); the React components and the UI kit stay in the design
> system itself. `../gen_brand.py` reads `tokens/colors.css`. The brand-illustration card and the
> genie infographic are left out. How the repo applies these rules: [`../README.md`](../README.md).

**Aladdin** — *Intent is all you need.* · *Models explore. Gates decide.*

Aladdin is the rebrand of **agentic-agile**: an intent-driven, test-driven, cross-harness software-engineering system. A human states intent; planning agents (intake → standards → planner) produce a sprint contract and stop for approval; then per wave the system runs **RED → SCAFFOLD → GREEN → STRUCTURAL-REVIEW**, and once per sprint a **FINAL-GATE**. Each sub-agent runs in its own git worktree; every stop is intercepted by a deterministic hook gate. A failed gate never becomes accepted state. It runs on Claude Code, Codex and OpenCode, backed by `ctx-symbols`, `md-db` and budget/stats tracking.

Product surfaces: a CLI plugin (no GUI in the repo), a GitHub README/docs, and explainer graphics (the "Aladdin's loop (aloop)" infographic).

## Sources
- GitHub: https://github.com/adeelahmad/agentic-agile (master) — README read in full. `docs/` (ROADMAP.md, DESIGN.md, ARCHITECTURE.md, agentic-agile-design.html, diagrams/) could **not** be fetched this session; explore them for deeper product context.
- Roadmap referenced by the brief: https://github.com/adeelahmad/agentic-agile/blob/master/docs/ROADMAP.md
- Uploaded infographic → `assets/aladdins-loop-infographic.png`
- The user's color brief (Google-derived palette with semantic roles).

## Index
- `styles.css` — entry; `@import`s only
- `tokens/` — `colors.css`, `typography.css`, `spacing.css`, `effects.css`, `fonts.css`
- `guidelines/` — foundation specimen cards (Colors, Type, Spacing, Brand)
- `components/` — React primitives (see below), one `*.card.html` per folder
- `ui_kits/aloop/` — click-through run console
- `assets/` — reference infographic
- `thumbnail.html`, `SKILL.md`, `github.md`

## Components
- core: **Button**, **IconButton**, **Badge**, **PhaseDot**, **Signature**
- surfaces: **Panel**, **ArtifactList**, **Terminal**
- loop: **LoopStepper**, **GateVerdict**, **AgentChip**
- forms: **Input**, **Select**, **Checkbox**, **Switch**
- navigation: **Tabs**
- feedback: **Dialog**, **Toast**

No source component library exists (the product is a CLI), so this is an authored set. Intentional additions specific to Aladdin: PhaseDot/Signature (the four-dot grammar), LoopStepper (the per-wave loop), GateVerdict (a hook gate's decision), AgentChip (sub-agent + model), ArtifactList (evidence), Terminal (CLI transcripts).

## UI kits
- `ui_kits/aloop/` — Intent → Plan approval → loop → accepted state. Derived from the infographic + README; not a recreation of shipped UI.

---

## CONTENT FUNDAMENTALS
- **Voice:** precise, engineering-literal, confident. Short declaratives in pairs: "Models explore. Gates decide." "Plan. Test. Build. Improve. Repeat." "Smaller steps. Better software."
- **Person:** "you" for the human ("Your approval", "Your commits — merges authored by you", "Your intent summons possibility"). The system is referred to by role nouns (supervisor, planner, green-worker), never "I".
- **Casing:** Sentence case for headings ("Red — Write failing tests" uses Title-ish phase labels). Phase names are proper nouns in caps in prose: RED, GREEN, SCAFFOLD, FINAL-GATE. Uppercase micro-labels for states (FAIL, PASS).
- **Structure words:** arrows for flow (intake → standards → planner), em-dashes for asides, middle dot `·` as a separator.
- **Code-literal nouns:** artifacts and gates are always monospace and exact: `plan.md`, `tasks.md`, `validate.md`, `gate-red`, `ctx-symbols`, `/aladdin:init`.
- **Claims are evidenced:** numbers with provenance ("~1.55M output tokens", "322 tests", "99.9% cache hits"). "Determinism is structural, not vibes." / "never a false block, never a silent pass".
- **Magic, controlled:** the lamp/genie metaphor is light ("The genie takes it from here") — never "AI magic ✨". Intent becomes controlled execution.
- **Emoji:** not used in brand surfaces (the old README's 🤖 is retired). Unicode glyphs are used as semantic markers: ● ◆ ✓ ✕ →.
- **Handwritten asides** (Caveat, blue, slightly rotated) for margin notes only: "Same intent. Greater outcomes." "More progress every loop."

## VISUAL FOUNDATIONS
- **Color is semantic, never decorative.** Blue = intent/orchestration/system; Red = proof something is missing; Green = proof it now holds / accepted; Gold = deterministic decision, gate, budget boundary; Gray = evidence, history, provenance. The mark is blue; the four colors appear when the system is doing something. Signature order: Intent → RED → GREEN → Gate.
- **Surfaces:** white canvas, `#F8F9FA` app surface. Panels are pale tints (`#E8F0FE`, `#FCE8E6`, `#E6F4EA`, `#FEF7E0`, `#F1F3F4`) with a matching light tinted border — flat + "glass-light". Nested evidence boxes are translucent white (rgba 255/.7) with hairline border.
- **Type:** DM Sans (substitute) — 800 display with −0.03em tracking, 700 headings, 400 body at 16/1.55. JetBrains Mono for commands and artifacts. Caveat for blue margin annotations only.
- **Backgrounds:** no gradients as fills, no full-bleed photos. The infographic uses illustration (genie mascot, lamp, scroll) — treat as reference; per roadmap the lamp motif must be original and abstract, no Disney language.
- **Corner radii:** 16px panels, 12px nested boxes/terminal, 8px inputs, 24px dialogs, pill buttons/badges/chips. Numbered phase discs are circles.
- **Cards:** tinted bg + 1px tinted border, no shadow at rest. Plain cards: white + `#DADCE0` hairline.
- **Shadows:** neutral gray, low (`--shadow-1..3`), used only for floating things: toasts, dialogs, switch thumbs. Never colored glows.
- **Borders:** 1px `#DADCE0` everywhere; semantic panels use their tint border. Focus = 3px `#E8F0FE`/`#A8C7FA` ring + blue border.
- **Layout:** generous whitespace, grids of panels, flow connected by thin arrows. Max container ~1200px. Top bar is plain white with hairline bottom.
- **Hover:** buttons darken to the `*-dark` variant; ghost/secondary gain a pale tint. **Press:** scale(.98). No bounces.
- **Motion:** calm and deterministic — 120–320ms, `cubic-bezier(.2,0,0,1)`, fades and opacity steps; step dots light up in order. No spring/bounce.
- **Transparency/blur:** only the modal scrim (rgba 32/33/36 .32 + 2px blur) and glass overlays over busy content.
- **Imagery vibe:** bright, cool, clean white with saturated primaries; friendly but engineered.
- **Gates** are drawn as gold diamonds ◆; accepted state as green ✓; failures as red ✕.

## ICONOGRAPHY
- The repo ships no icon font or SVG set; the infographic uses simple filled pictograms (shield, clock, layers, flag, person, git-branch).
- Primary approach in this system: **unicode semantic glyphs** (● ◆ ✓ ✕ → ⋯) — they're part of the brand grammar.
- For UI pictograms, use **Lucide** from CDN (`https://unpkg.com/lucide@latest`) — ⚠️ substitution; closest match to the infographic's rounded pictograms. Stroke 2px, 20px default, color `--text-secondary` or the semantic color of the thing it labels.
- No emoji. Harness logos (Claude Code, Codex, OpenCode) are third-party marks — don't redraw; use names in text.

## Logo / wordmark
**Chosen: option D.** "Aladdin" set in DM Sans 800, −0.03em, Aladdin Blue, with a 4-segment **loop bar** underneath (blue → red → green → gold = Intent → RED → GREEN → Gate), bar width = word width, height ≈ 0.12× cap size, fully rounded. On blue backgrounds: white word, first bar segment white. Never recolor individual letters. No pictorial lamp mark yet — add one only when an original is provided. See `guidelines/brand-wordmark.html`.

## Fonts
No font files supplied. Using Google Fonts substitutes: **DM Sans**, **JetBrains Mono**, **Caveat** (loaded via `tokens/fonts.css`). Replace with brand files when available.
