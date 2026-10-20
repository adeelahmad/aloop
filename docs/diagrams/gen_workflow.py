#!/usr/bin/env python3
"""Generate docs/diagrams/workflow.svg — the aloop sprint loop as it actually runs.

    python3 docs/diagrams/gen_workflow.py

Everything drawn here maps to a real file, script or hook in plugin/ (see labels).
Self-contained SVG (own background, system fonts) so it renders the same on GitHub in
light and dark mode.
"""
import os
from xml.sax.saxutils import escape

W, H = 1600, 1158
BG, CARD, CARD2, LINE = "#0b1220", "#111a2e", "#0f1729", "#26344f"
TXT, MUTED, DIM = "#e6edf7", "#9fb0c8", "#6b7c96"
C = {"init": "#a78bfa", "plan": "#60a5fa", "orch": "#fbbf24", "exec": "#34d399",
     "final": "#f472b6", "clear": "#22d3ee", "warn": "#fbbf24", "hard": "#f87171"}
FONT = "ui-sans-serif, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

out = []


def add(s):
    out.append(s)


def text(x, y, s, size=14, fill=TXT, weight=400, anchor="start", mono=False, italic=False):
    add('<text x="%g" y="%g" font-size="%g" fill="%s" font-weight="%d" text-anchor="%s" font-family="%s"%s>%s</text>'
        % (x, y, size, fill, weight, anchor, MONO if mono else FONT, ' font-style="italic"' if italic else "", escape(s)))


def rect(x, y, w, h, fill=CARD, stroke=LINE, r=14, sw=1.2, dash=None, opacity=1):
    add('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="%s" stroke="%s" stroke-width="%g"%s%s/>'
        % (x, y, w, h, r, fill, stroke, sw, ' stroke-dasharray="%s"' % dash if dash else "",
           ' opacity="%g"' % opacity if opacity != 1 else ""))


def arrow(d, color=MUTED, sw=2, dash=None, marker="ah"):
    add('<path d="%s" fill="none" stroke="%s" stroke-width="%g"%s marker-end="url(#%s-%s)"/>'
        % (d, color, sw, ' stroke-dasharray="%s"' % dash if dash else "", marker, color.strip("#")))


def pill(x, y, label, color, w=None, size=12, mono=False):
    w = w or (len(label) * (7.4 if mono else 6.9) + 22)
    rect(x, y, w, 24, fill=color + "22", stroke=color + "88", r=12, sw=1)
    text(x + w / 2, y + 16.5, label, size=size, fill=color, weight=600, anchor="middle", mono=mono)
    return w


def stage(x, y, w, h, n, title, sub, color, lines, foot=None):
    rect(x, y, w, h, fill=CARD, stroke=color + "99", sw=1.6)
    rect(x, y, w, 6, fill=color, stroke=color, r=3, sw=0)
    add('<circle cx="%g" cy="%g" r="15" fill="%s"/>' % (x + 30, y + 38, color))
    text(x + 30, y + 43.5, str(n), size=15, fill=BG, weight=800, anchor="middle")
    text(x + 54, y + 36, title, size=17, weight=750)
    text(x + 54, y + 55, sub, size=12, fill=color, weight=600)
    yy = y + 88
    for kind, s in lines:
        if kind == "b":
            add('<circle cx="%g" cy="%g" r="2.6" fill="%s"/>' % (x + 22, yy - 4.5, color))
            text(x + 32, yy, s, size=13, fill=TXT)
            yy += 21
        elif kind == "m":
            text(x + 32, yy, s, size=12, fill=MUTED, mono=True)
            yy += 19
        elif kind == "h":
            yy += 4
            text(x + 18, yy, s.upper(), size=10.5, fill=DIM, weight=700)
            yy += 18
        elif kind == "sp":
            yy += 8
    if foot:
        rect(x + 12, y + h - 42, w - 24, 30, fill=color + "18", stroke=color + "55", r=8, sw=1)
        text(x + w / 2, y + h - 22, foot, size=12, fill=color, weight=650, anchor="middle")


# ── canvas ──────────────────────────────────────────────────────────────────
add('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
    'aria-labelledby="t d">' % (W, H, W, H))
add('<title id="t">aloop — the deterministic sprint loop</title>')
add('<desc id="d">Init, human-gated planning, an orchestrator that dispatches time-boxed TDD workers in '
    'isolated worktrees, hook-enforced gates, final gate with harness-written stats and handoff, clear '
    'context, repeat. Runs on Claude Code, Codex and OpenCode.</desc>')
add("<defs>")
for col in set(list(C.values()) + [MUTED, DIM]):
    add('<marker id="ah-%s" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="%s"/></marker>' % (col.strip("#"), col))
add('<linearGradient id="bar" x1="0" x2="1"><stop offset="0" stop-color="%s"/><stop offset="0.6" stop-color="%s"/>'
    '<stop offset="1" stop-color="%s"/></linearGradient>' % (C["exec"], C["warn"], C["hard"]))
add("</defs>")
rect(0, 0, W, H, fill=BG, stroke=BG, r=0, sw=0)

# ── header ──────────────────────────────────────────────────────────────────
text(48, 64, "aloop", size=40, weight=800)
text(48, 94, "You state the intent. Deterministic hooks — not model goodwill — drive every sprint to done.",
     size=16, fill=MUTED)
x = 48
for i, s in enumerate(["PLAN", "EXECUTE", "RECORD", "CLEAR", "REPEAT"]):
    x += pill(x, 112, s, [C["plan"], C["exec"], C["final"], C["clear"], C["init"]][i], size=11.5) + 8
    if i < 4:
        text(x - 2, 128.5, "→", size=14, fill=DIM)
        x += 14
text(W - 48, 60, "runs on", size=12, fill=DIM, anchor="end")
hx = W - 48
for h in reversed(["Claude Code", "Codex", "OpenCode"]):
    w = len(h) * 7.6 + 26
    hx -= w
    pill(hx, 70, h, TXT, w=w, size=12.5)
    hx -= 8
text(W - 48, 122, "planning: Opus 5.5 · orchestrator + workers: Sonnet", size=12.5, fill=MUTED, anchor="end")

# ── the six stages ──────────────────────────────────────────────────────────
Y, SH = 162, 420
xs = [40, 286, 532, 778, 1092, 1338]
ws = [222, 222, 222, 290, 222, 222]

stage(xs[0], Y, ws[0], SH, 1, "Init", "human confirms · once", C["init"], [
    ("m", "/aloop:init"), ("sp", ""),
    ("b", "tokens 150k / 250k"), ("b", "time 20 / 30 min"), ("b", "models per role"),
    ("b", ".gitignore → .agentic/"), ("b", "docs/agents/ tracked? (no)"), ("b", "commit author = you"),
    ("h", "written by the harness"), ("m", "docs/agents/defaults.md"),
], foot="aloop init --apply")

stage(xs[1], Y, ws[1], SH, 2, "Plan", "human-gated · Opus 5.5", C["plan"], [
    ("b", "retrospective → memory"), ("b", "intake (5-part intent)"), ("b", "standards (gate matrix)"),
    ("b", "planner: stories → tasks"), ("b", "  → validate → tests-only"), ("b", "  → waves + deps"),
    ("h", "gate-stage2-complete"), ("m", "every story planned"), ("m", "self-contained sprint"),
], foot="human says “go”")

stage(xs[2], Y, ws[2], SH, 3, "Orchestrate", "autonomous · Sonnet", C["orch"], [
    ("b", "dispatch wave workers"), ("b", "sleep on budget watch"), ("b", "sweep: merge · retry"),
    ("b", "  · re-plan · escalate"), ("sp", ""),
    ("h", "owns only"), ("m", "init.md · plan-ready.md"), ("m", "execution.log"),
    ("h", "never writes code"), ("m", "gate-supervisor-scope"),
], foot="one sprint per run")

# execute stage: custom body with the TDD chain
ex, ew = xs[3], ws[3]
rect(ex, Y, ew, SH, fill=CARD, stroke=C["exec"] + "99", sw=1.6)
rect(ex, Y, ew, 6, fill=C["exec"], stroke=C["exec"], r=3, sw=0)
add('<circle cx="%g" cy="%g" r="15" fill="%s"/>' % (ex + 30, Y + 38, C["exec"]))
text(ex + 30, Y + 43.5, "4", size=15, fill=BG, weight=800, anchor="middle")
text(ex + 54, Y + 36, "Execute", size=17, weight=750)
text(ex + 54, Y + 55, "one worktree per task · Sonnet", size=12, fill=C["exec"], weight=600)
chain = [("RED", "tests fail by assertion", "gate-red-verify"),
         ("SCAFFOLD", "one stub per symbol", "gate-scaffold-verify"),
         ("GREEN", "least code to pass", "gate-green-verify"),
         ("REVIEW", "orphans · duplicates", "gate-structural-integrity")]
cy = Y + 76
for i, (name, what, gate) in enumerate(chain):
    rect(ex + 16, cy, ew - 52, 58, fill=CARD2, stroke=C["exec"] + "66", r=10, sw=1)
    text(ex + 30, cy + 23, name, size=14, fill=C["exec"], weight=800)
    text(ex + 30 + len(name) * 10 + 8, cy + 23, what, size=12.5, fill=TXT)
    text(ex + 30, cy + 44, "stop ⟶ " + gate, size=11, fill=MUTED, mono=True)
    if i < 3:
        arrow("M%g %g L%g %g" % (ex + (ew - 36) / 2, cy + 58, ex + (ew - 36) / 2, cy + 70), C["exec"], sw=1.6)
    cy += 70
# retry loop: a blocked stop sends the worker back (inside the card's right margin)
arrow("M%g %g C%g %g %g %g %g %g" % (ex + ew - 36, Y + 318, ex + ew - 10, Y + 300, ex + ew - 10, Y + 122,
                                      ex + ew - 36, Y + 104), C["exec"], sw=1.4, dash="4 4")
text(ex + 30, Y + 366, "gate block → worker keeps working · retry ≤3", size=11.5, fill=C["exec"], weight=600)
rect(ex + 12, Y + SH - 42, ew - 24, 30, fill=C["exec"] + "18", stroke=C["exec"] + "55", r=8, sw=1)
text(ex + ew / 2, Y + SH - 22, "merge on pass · waves in parallel", size=12, fill=C["exec"], weight=650, anchor="middle")

stage(xs[4], Y, ws[4], SH, 5, "Final gate", "record · by the harness", C["final"], [
    ("b", "full standards matrix"), ("b", "zero suppressions"), ("b", "every box ticked"),
    ("h", "gate-final → stats"), ("m", "sprintN/stats.md"), ("m", "  tasks · attempts · gates"),
    ("m", "  tokens · human msgs"), ("m", "docs/agents/NEXT.md"), ("m", "  the handoff"),
], foot="sprint DONE")

stage(xs[5], Y, ws[5], SH, 6, "Clear", "fresh context · repeat", C["clear"], [
    ("b", "Stop hook shows totals"), ("b", "you run /clear"), ("b", "SessionStart loads"),
    ("b", "  NEXT.md into context"), ("b", "next sprint plans"), ("b", "  from disk, not memory"),
    ("h", "tokens (ledger)"), ("m", "sprint · session"), ("m", "whole project"),
], foot="aloop stats show")

# forward arrows between stages
for i in range(5):
    x1 = xs[i] + ws[i]
    x2 = xs[i + 1]
    arrow("M%g %g L%g %g" % (x1 + 4, Y + 200, x2 - 4, Y + 200), [C["plan"], C["orch"], C["exec"], C["final"], C["clear"]][i], sw=2.4)

# ── feedback loops under the stages ─────────────────────────────────────────
ly = Y + SH + 18
# re-plan loop: execute -> plan
arrow("M%g %g L%g %g L%g %g L%g %g" % (xs[3] + 80, Y + SH + 2, xs[3] + 80, ly + 22, xs[1] + 150, ly + 22, xs[1] + 150, Y + SH + 4),
      C["hard"], sw=2, dash="7 5")
rect(xs[1] + 190, ly + 8, 520, 28, fill=BG, stroke=BG, r=0, sw=0)
text(xs[1] + 200, ly + 27, "hard budget hit → worker stopped → sprint paused → planner splits the task (≤2) → resume",
     size=12.5, fill=C["hard"], weight=650)
# repeat loop: clear -> plan
arrow("M%g %g L%g %g L%g %g L%g %g" % (xs[5] + 115, Y + SH + 2, xs[5] + 115, ly + 62, xs[1] + 60, ly + 62, xs[1] + 60, Y + SH + 4),
      C["clear"], sw=2, dash="7 5")
rect(xs[3] + 60, ly + 48, 430, 28, fill=BG, stroke=BG, r=0, sw=0)
text(xs[3] + 70, ly + 67, "repeat: next sprint (planned self-contained) until the project is done",
     size=12.5, fill=C["clear"], weight=650)

# ── time box panel ──────────────────────────────────────────────────────────
ty = 704
rect(40, ty, 760, 186, fill=CARD, stroke=LINE)
text(62, ty + 32, "Per-attempt time box", size=16, weight=750)
text(262, ty + 32, "tokens = context in use + all output · hooks measure it from the worker's own log", size=12, fill=MUTED)
bx, bw, by = 62, 716, ty + 58
rect(bx, by, bw, 16, fill="url(#bar)", stroke="none", r=8, sw=0)
sx = bx + bw * 150 / 250
add('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="2"/>' % (sx, by - 8, sx, by + 24, TXT))
add('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="2"/>' % (bx + bw, by - 8, bx + bw, by + 24, TXT))
text(bx, by + 40, "0", size=12, fill=MUTED)
text(sx, by + 40, "soft 150k tok · 20 min", size=12.5, fill=C["warn"], weight=700, anchor="middle")
text(bx + bw, by + 40, "hard 250k · 30 min", size=12.5, fill=C["hard"], weight=700, anchor="end")
text(bx, by + 70, "soft → warning injected into the worker's tool result: finish or end gracefully", size=12.5, fill=TXT)
text(bx, by + 92, "hard → one report write, 3 refused calls, then the hook stops it (continue:false / abort) — never merged",
     size=12.5, fill=TXT)
text(bx, by + 116, "budget source: tasks.md budget: line  >  worktree task.env  >  defaults.md  >  built-in",
     size=12, fill=MUTED, mono=True)

# ── state / artifacts panel ─────────────────────────────────────────────────
rect(820, ty, 740, 186, fill=CARD, stroke=LINE)
text(842, ty + 32, "State on disk — the repo is the memory", size=16, weight=750)
col1 = [("docs/agents/", C["plan"]), ("defaults.md", TXT), ("NEXT.md", TXT), ("memory.md", TXT),
        ("sprintN/stories.md · plan.md", TXT), ("sprintN/sNN-*/tasks · validate · plan", TXT),
        ("sprintN/execution.log · stats.md", TXT)]
col2 = [(".aladdin/  (run data, git-ignored)", C["clear"]), ("transcripts/  full capture", TXT),
        ("budget/  one file per attempt", TXT), ("ledger/  tokens per session", TXT),
        ("logs/gates.jsonl  every verdict", TXT), ("worktrees/  one per task", TXT), ("state/  sprint closes · locks", TXT)]
for ci, col in enumerate((col1, col2)):
    for j, (s, fill) in enumerate(col):
        text(842 + ci * 360 + (0 if j == 0 else 14), ty + 60 + j * 18.5, s, size=12.5 if j else 13,
             fill=fill, weight=700 if j == 0 else 400, mono=True)

# ── harness band ────────────────────────────────────────────────────────────
hy = 912
rect(40, hy, 1520, 196, fill=CARD2, stroke=LINE)
text(62, hy + 32, "Harness — deterministic hooks, outside model control", size=16, weight=750)
text(492, hy + 32, "same scripts on every host: hooks.json (Claude Code, Codex) · generated plugin (OpenCode)", size=12, fill=MUTED)
hooks = [
    ("SessionStart", C["clear"], ["ensure-tools: build + PATH", "load NEXT.md handoff", "re-apply .gitignore"]),
    ("PreToolUse", C["orch"], ["supervisor-scope", "commit author = you", "worktree isolation", "budget: refuse at hard"]),
    ("PostToolUse", C["warn"], ["budget: warn / stop", "transcripts: record"]),
    ("SubagentStop", C["exec"], ["role gate (red/green/…)", "comms: output.md", "block → keep working"]),
    ("Stop", C["final"], ["token ledger", "announce sprint totals", "ask for /clear"]),
]
hw = (1520 - 24 - 4 * 14) / 5
for i, (ev, col, items) in enumerate(hooks):
    x0 = 52 + i * (hw + 14)
    rect(x0, hy + 50, hw, 132, fill=CARD, stroke=col + "77", r=10, sw=1.2)
    text(x0 + 16, hy + 76, ev, size=14, fill=col, weight=800, mono=True)
    for j, it in enumerate(items):
        text(x0 + 16, hy + 100 + j * 19, "· " + it, size=12.5, fill=TXT)

text(W / 2, H - 16, "tools on PATH as `aloop <tool>` · md-db + ctx-symbols auto-built · commits authored by you · open source (MIT)",
     size=12, fill=DIM, anchor="middle")
add("</svg>")

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflow.svg")
with open(path, "w") as f:
    f.write("\n".join(out) + "\n")
print("wrote", path)
