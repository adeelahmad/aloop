"""mission principles — the standing principles, in two variants (ROADMAP §4.12, M9.7).

Every goal carries the mission layer's leadership principles, rendered for the mission's
mode: autonomous (set and forget) or spoon-fed (the human feeds the asks). The source is
plugin/mission/principles.md; this module parses it, refuses an incomplete edit, renders
one block per mode with the tension pairs and the invariant clause, and hashes the block,
so a goal names the exact principles it was derived under.

The `## Core` section is separate: the core principles every aloop agent carries on every turn,
the one-way / two-way door rule first (M9.30). They render apart from the goal blocks, so editing
them never stales a goal's stamp, and the SessionStart hook injects them into an aloop project;
`principles hook` hands the same block to every dispatched role (M9.31).

Usage:
  mission principles show [--mode autonomous|spoon-fed] [--json] [--file PATH]
        the block for a mode (default: the mission's own mode); --json adds sha256 and ids
  mission principles show --core [--file PATH]
        the core principles block (every agent, every turn)
  mission principles hook [--file PATH]
        the SubagentStart hook (the payload on stdin): in an aloop project, the core block as the
        role's additionalContext; silent outside one. Always exit 0: it never blocks a dispatch
  mission principles hash [--mode autonomous|spoon-fed] [--file PATH]
        the sha256 of exactly that block (what a goal's stamp records)
  mission principles check [--file PATH]
        principles.md (or a draft at PATH) is complete: every standing principle and tension
        pair, both variants and different, known ids only, each variant at most 280 chars; every
        core principle (the door rule first, naming its gate), the core block at most 800 chars.
        Exit 0 ok · 2 invalid (BLOCK[principles] lines) · 64 usage

From Python (the goal module): stamp(mode) -> {"mode", "sha256", "ids"}; verify(stamp) ->
problems ([] when the stamp is the current block for its mode); verify(stamp, stale_ok=True) and
stale(stamp) for a goal derived under an older block, which keeps its stamp as provenance.
"""
import hashlib
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "bin"))
import _mission as M  # noqa: E402
import _paths  # noqa: E402

SOURCE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "principles.md")
MODES = ("autonomous", "spoon-fed")

# the standing set: ROADMAP §4.12's list, the IBM loop first as the engine. Dropping one fails
# the check; adding one ("more to come") does not.
REQUIRED = ("autonomic-loop", "own-the-outcome", "ship-small", "verify-dont-assume", "raise-the-bar",
            "disagree-then-commit", "leave-it-better", "know-when-to-ask", "grow-adoption", "keep-it-simple")
REQUIRED_TENSIONS = ("customer-obsession-vs-frugality", "bias-for-action-vs-dive-deep",
                     "think-big-vs-deliver-results", "grow-adoption-vs-keep-it-simple")
MAX_VARIANT = 280   # the block is read with every goal: short keeps it cheap (Keep It Simple)
# the core principles every agent carries (M9.30): read on every session, so the block is capped
REQUIRED_CORE = ("doors", "evidence", "earn-trust", "ownership", "frugality", "highest-standards",
                 "disagree-and-commit")
MAX_CORE = 800
CORE_HEAD = "Core principles (every aloop agent):"
DOORS_NAMES = ("ASK[door]", "aloop mission doors")   # the doors line names the gate that enforces it
INVARIANT_CLAUSE = ("No principle relaxes an invariant, a gate or a budget cap (ROADMAP §7); "
                    "where one seems to, the gate decides.")

HEAD = re.compile(r"^## ([a-z0-9]+(?:-[a-z0-9]+)*): (\S.*)$")
VARIANT = re.compile(r"^- (autonomous|spoon-fed): (\S.*)$")
CORE = re.compile(r"^- ([a-z0-9]+(?:-[a-z0-9]+)*): (\S.*)$")
TENSION = re.compile(r"^- ([a-z0-9]+(?:-[a-z0-9]+)*): (\S.*?) \(([a-z0-9, -]+)\) vs (\S.*?) \(([a-z0-9, -]+)\): (\S.*)$")


def parse(text):
    """(doc, problems): doc = {"principles": [{id, title, autonomous, spoon-fed}],
    "tensions": [{id, a: {label, ids}, b: {label, ids}, resolves}], "core": [{id, text}]}."""
    principles, tensions, core, bad = [], [], [], []
    section, cur = None, None
    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip()
        if section and line and any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in line):
            bad.append("line %d: a control or invisible character (only plain text renders the same everywhere)" % n)
        if line.startswith("## "):
            if line in ("## Tensions", "## Core"):
                section, cur = line[3:].lower(), None
                continue
            m = HEAD.match(line)
            if not m:
                bad.append("line %d: a heading must be `## <id>: <Title>` or `## Tensions`" % n)
                section, cur = None, None
                continue
            section, cur = "principle", {"id": m.group(1), "title": m.group(2).strip(), "line": n}
            principles.append(cur)
            continue
        if section is None or not line:
            continue   # commentary comes before the first section
        if not line.startswith("- "):
            bad.append("line %d: only bullet lines go inside a section (a variant is one line; no wrapped text)" % n)
            continue
        if section == "core":
            m = CORE.match(line)
            if not m:
                bad.append("line %d: unknown line in Core (`- <id>: <text>`, one line each)" % n)
            else:
                core.append({"id": m.group(1), "text": m.group(2).strip(), "line": n})
            continue
        if section == "principle":
            m = VARIANT.match(line)
            if not m:
                bad.append("line %d: unknown line in %s (only `- autonomous:` and `- spoon-fed:`)" % (n, cur["id"]))
            elif m.group(1) in cur:
                bad.append("line %d: %s has a second %s variant" % (n, cur["id"], m.group(1)))
            else:
                cur[m.group(1)] = m.group(2).strip()
        else:
            m = TENSION.match(line)
            if not m:
                bad.append("line %d: unknown line in Tensions (`- <id>: <Label> (<ids>) vs <Label> (<ids>): <how>`)" % n)
                continue
            split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
            tensions.append({"id": m.group(1), "a": {"label": m.group(2).strip(), "ids": split(m.group(3))},
                             "b": {"label": m.group(4).strip(), "ids": split(m.group(5))},
                             "resolves": m.group(6).strip(), "line": n})
    ids = [p["id"] for p in principles]
    seen = set()
    for p in principles:
        if p["id"] in seen:
            bad.append("line %d: duplicate principle %s" % (p["line"], p["id"]))
        seen.add(p["id"])
        for mode in MODES:
            if not p.get(mode):
                bad.append("line %d: %s has no %s variant" % (p["line"], p["id"], mode))
            elif len(p[mode]) > MAX_VARIANT:
                bad.append("line %d: %s's %s variant is longer than %d characters (%d)"
                           % (p["line"], p["id"], mode, MAX_VARIANT, len(p[mode])))
        if p.get("autonomous") and p.get("autonomous") == p.get("spoon-fed"):
            bad.append("line %d: %s: the two variants are the same (write one per mode)" % (p["line"], p["id"]))
    for need in REQUIRED:
        if need not in ids:
            bad.append("the standing principle %s is missing" % need)
    if ids and ids[0] != REQUIRED[0]:
        bad.append("%s (the engine) must be listed first" % REQUIRED[0])
    tids, seen = [t["id"] for t in tensions], set()
    for t in tensions:
        if t["id"] in seen:
            bad.append("line %d: duplicate tension pair %s" % (t["line"], t["id"]))
        seen.add(t["id"])
        for side in ("a", "b"):
            if not t[side]["ids"]:
                bad.append("line %d: tension %s names no principle for %s" % (t["line"], t["id"], t[side]["label"]))
            for i in t[side]["ids"]:
                if i not in ids:
                    bad.append("line %d: tension %s names an unknown principle %s" % (t["line"], t["id"], i))
    for need in REQUIRED_TENSIONS:
        if need not in tids:
            bad.append("the tension pair %s is missing" % need)
    seen = set()
    for c in core:
        if c["id"] in seen:
            bad.append("line %d: duplicate core principle %s" % (c["line"], c["id"]))
        seen.add(c["id"])
        if len(c["text"]) > MAX_VARIANT:
            bad.append("line %d: core principle %s is longer than %d characters (%d)"
                       % (c["line"], c["id"], MAX_VARIANT, len(c["text"])))
        if c["id"] == "doors" and not all(x in c["text"] for x in DOORS_NAMES):
            bad.append("line %d: the doors principle names %s (the gate that enforces it)"
                       % (c["line"], " and ".join("`%s`" % x if " " in x else x for x in DOORS_NAMES)))
    for need in REQUIRED_CORE:
        if need not in seen:
            bad.append("the core principle %s is missing" % need)
    if core and core[0]["id"] != REQUIRED_CORE[0]:
        bad.append("the core principle %s (the door rule) must be listed first" % REQUIRED_CORE[0])
    size = len(_core_text(core))
    if size > MAX_CORE:
        bad.append("the core block is longer than %d characters (%d)" % (MAX_CORE, size))
    for x in principles + tensions + core:
        x.pop("line", None)
    return {"principles": principles, "tensions": tensions, "core": core}, bad


def _core_text(core):
    return "\n".join([CORE_HEAD] + ["%d. %s" % (i, c["text"]) for i, c in enumerate(core, 1)]) + "\n"


def load(path=None):
    """The parsed principles (+ "problems": [] when complete)."""
    try:
        with open(path or SOURCE, encoding="utf-8") as f:
            doc, bad = parse(f.read())
    except (OSError, UnicodeDecodeError) as e:
        doc, bad = {"principles": [], "tensions": [], "core": []}, ["cannot read %s: %s" % (path or SOURCE, e)]
    doc["problems"] = bad
    return doc


def render(mode, path=None):
    """The block a goal carries for one mode. Raises ValueError when the source is invalid."""
    if mode not in MODES:
        raise ValueError("mode must be one of %s" % ", ".join(MODES))
    doc = load(path)
    if doc["problems"]:
        raise ValueError("; ".join(doc["problems"]))
    out = ["Standing principles (%s mode): apply them to every goal and intent of this mission." % mode]
    out += ["%d. %s: %s" % (i, p["title"], p[mode]) for i, p in enumerate(doc["principles"], 1)]
    out.append("Tensions (they pull against each other on purpose; settle each this way):")
    out += ["- %s vs %s: %s" % (t["a"]["label"], t["b"]["label"], t["resolves"]) for t in doc["tensions"]]
    out.append(INVARIANT_CLAUSE)
    return "\n".join(out) + "\n"


def render_core(path=None):
    """The core principles block every aloop agent carries (M9.30). Raises ValueError when the
    source is invalid."""
    doc = load(path)
    if doc["problems"]:
        raise ValueError("; ".join(doc["problems"]))
    return _core_text(doc["core"])


def digest(mode, path=None):
    return hashlib.sha256(render(mode, path).encode()).hexdigest()


def stamp(mode, path=None):
    """What a goal records: the mode, the block's sha256, and the principle ids in order."""
    return {"mode": mode, "sha256": digest(mode, path), "ids": [p["id"] for p in load(path)["principles"]]}


def verify(st, path=None, stale_ok=False):
    """Problems with a goal's stamp: [] when it is the current block for its mode. With stale_ok, a
    well-formed stamp of an older block is not a problem (the goal keeps it as provenance; stale() says so),
    but a malformed stamp or the other mode's block still is."""
    if not isinstance(st, dict) or not st.get("mode") or not st.get("sha256"):
        return ["the principles stamp needs mode and sha256 (principles.stamp(mode))"]
    if st["mode"] not in MODES:
        return ["the principles stamp's mode %r must be one of %s" % (st["mode"], ", ".join(MODES))]
    if not isinstance(st["sha256"], str) or not re.match(r"^[0-9a-f]{64}$", st["sha256"]):
        return ["the principles stamp's sha256 is not a sha256"]
    if not isinstance(st.get("ids"), list) or not all(isinstance(i, str) for i in st["ids"]):
        return ["the principles stamp needs ids, the principle ids it carries"]
    try:
        cur = {m: digest(m, path) for m in MODES}
    except ValueError as e:
        return ["principles.md is invalid: %s" % e]
    if st["sha256"] == cur[st["mode"]]:
        ids = [p["id"] for p in load(path)["principles"]]
        return [] if st.get("ids") == ids else ["the principles stamp's ids %r are not the block's %r"
                                                % (st.get("ids"), ids)]
    other = [m for m in MODES if m != st["mode"] and cur[m] == st["sha256"]]
    if other:
        return ["the principles stamp's sha256 is the %s block, not the %s one (mode mismatch)" % (other[0], st["mode"])]
    if not stale_ok:
        return ["the principles changed since this stamp (sha256 %s, now %s)" % (st["sha256"][:12], cur[st["mode"]][:12])]
    missing = [i for i in REQUIRED if i not in st["ids"]]   # an older block still carried the standing set
    if missing or st["ids"][:1] != [REQUIRED[0]]:
        return ["the principles stamp's ids lack the standing set (%s) or do not start with %s"
                % (", ".join(missing) or "order", REQUIRED[0])]
    return []


def stale(st, path=None):
    """A well-formed stamp of a block that is no longer the current one for its mode."""
    return not verify(st, path, stale_ok=True) and bool(verify(st, path))


def mission_mode(cwd):
    doc, _ = M.load(M.path_of(cwd, "mission"))
    return doc.get("mode") if isinstance(doc, dict) and doc.get("mode") in MODES else None


def parse_args(argv):
    opts, pos, k = {}, [], None
    for a in argv:
        if k:
            opts[k], k = a, None
        elif a in ("--json", "--core"):
            opts[a[2:]] = True
        elif a.startswith("--"):
            k = a[2:].replace("-", "_")
        else:
            pos.append(a)
    if k:
        opts[k] = ""
    return opts, pos


def hook(cwd_default, path=None):
    """SubagentStart (Claude Code and Codex read hooks.json): a dispatched role carries the core block too,
    as the main session does from session-start. Raises on any problem; main() keeps it silent."""
    payload = json.loads(sys.stdin.read() or "{}")
    if not isinstance(payload, dict):
        raise ValueError("the hook payload is not an object")
    cwd = payload.get("cwd") or cwd_default
    if not isinstance(cwd, str) or not os.path.isdir(cwd) or not os.path.isdir(_paths.docs(cwd)):
        return 0      # not an aloop project
    if path and not os.path.isfile(path):
        raise ValueError("no such file %s" % path)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": render_core(path)}}))
    return 0


def main(argv, cwd):
    cmd, (o, _) = argv[0], parse_args(argv[1:])
    if cmd == "hook":
        try:
            return hook(cwd, o.get("file") or None)
        except Exception as e:  # noqa: BLE001 — context, not a gate: a failure never blocks the dispatch
            print("mission principles hook: no core principles for this role (%s: %s)" % (type(e).__name__, e),
                  file=sys.stderr)
            return 0
    for k in ("file", "mode"):
        if k in o and not o[k]:
            print("mission principles: --%s needs a value" % k, file=sys.stderr)
            return 64
    path = o.get("file") or None
    if path and not os.path.isfile(path):
        print("mission principles: no such file %s" % path, file=sys.stderr)
        return 64
    if cmd == "check":
        bad = load(path)["problems"]
        for b in bad:
            print("BLOCK[principles]: %s" % b, file=sys.stderr)
        if not bad:
            print("[principles] ok: %d principles, both variants, tension pairs complete"
                  % len(load(path)["principles"]), file=sys.stderr)
        return 2 if bad else 0
    if cmd not in ("show", "hash"):
        print("mission principles: unknown command %r (show, hash, check, hook)" % cmd, file=sys.stderr)
        return 64
    if o.get("core"):
        if cmd != "show" or o.get("mode"):
            print("mission principles: --core goes with show, without --mode", file=sys.stderr)
            return 64
        try:
            sys.stdout.write(render_core(path))
        except ValueError:
            for b in load(path)["problems"]:
                print("BLOCK[principles]: %s" % b, file=sys.stderr)
            return 2
        return 0
    mode = o.get("mode") or mission_mode(cwd)
    if mode not in MODES:
        print("mission principles: --mode autonomous|spoon-fed is required%s"
              % ("" if o.get("mode") else " (no mission to take it from)"), file=sys.stderr)
        return 64
    try:
        text = render(mode, path)
    except ValueError:
        for b in load(path)["problems"]:
            print("BLOCK[principles]: %s" % b, file=sys.stderr)
        return 2
    if cmd == "hash":
        print(hashlib.sha256(text.encode()).hexdigest())
    elif o.get("json"):
        print(json.dumps({"mode": mode, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                          "ids": [p["id"] for p in load(path)["principles"]], "text": text}, indent=1))
    else:
        sys.stdout.write(text)
    return 0
