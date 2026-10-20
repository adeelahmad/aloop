"""mission principles — the standing principles, in two variants (ROADMAP §4.12, M9.7).

Every goal carries the mission layer's leadership principles, rendered for the mission's
mode: autonomous (set and forget) or spoon-fed (the human feeds the asks). The source is
plugin/mission/principles.md; this module parses it, refuses an incomplete edit, renders
one block per mode with the tension pairs and the invariant clause, and hashes the block,
so a goal names the exact principles it was derived under.

Usage:
  mission principles show [--mode autonomous|spoon-fed] [--json] [--file PATH]
        the block for a mode (default: the mission's own mode); --json adds sha256 and ids
  mission principles hash [--mode autonomous|spoon-fed] [--file PATH]
        the sha256 of exactly that block (what a goal's stamp records)
  mission principles check [--file PATH]
        principles.md (or a draft at PATH) is complete: every standing principle and tension
        pair, both variants and different, known ids only, each variant at most 280 chars.
        Exit 0 ok · 2 invalid (BLOCK[principles] lines) · 64 usage

From Python (the goal module): stamp(mode) -> {"mode", "sha256", "ids"}; verify(stamp) ->
problems ([] when the stamp is the current block for its mode).
"""
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "bin"))
import _mission as M  # noqa: E402

SOURCE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "principles.md")
MODES = ("autonomous", "spoon-fed")

# the standing set: ROADMAP §4.12's list, the IBM loop first as the engine. Dropping one fails
# the check; adding one ("more to come") does not.
REQUIRED = ("autonomic-loop", "own-the-outcome", "ship-small", "verify-dont-assume", "raise-the-bar",
            "disagree-then-commit", "leave-it-better", "know-when-to-ask", "grow-adoption", "keep-it-simple")
REQUIRED_TENSIONS = ("customer-obsession-vs-frugality", "bias-for-action-vs-dive-deep",
                     "think-big-vs-deliver-results", "grow-adoption-vs-keep-it-simple")
MAX_VARIANT = 280   # the block is read with every goal: short keeps it cheap (Keep It Simple)
INVARIANT_CLAUSE = ("No principle relaxes an invariant, a gate or a budget cap (ROADMAP §7); "
                    "where one seems to, the gate decides.")

HEAD = re.compile(r"^## ([a-z0-9]+(?:-[a-z0-9]+)*): (\S.*)$")
VARIANT = re.compile(r"^- (autonomous|spoon-fed): (\S.*)$")
TENSION = re.compile(r"^- ([a-z0-9]+(?:-[a-z0-9]+)*): (\S.*?) \(([a-z0-9, -]+)\) vs (\S.*?) \(([a-z0-9, -]+)\): (\S.*)$")


def parse(text):
    """(doc, problems): doc = {"principles": [{id, title, autonomous, spoon-fed}],
    "tensions": [{id, a: {label, ids}, b: {label, ids}, resolves}]}."""
    principles, tensions, bad = [], [], []
    section, cur = None, None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if line.startswith("## "):
            if line == "## Tensions":
                section, cur = "tensions", None
                continue
            m = HEAD.match(line)
            if not m:
                bad.append("line %d: a heading must be `## <id>: <Title>` or `## Tensions`" % n)
                section, cur = None, None
                continue
            section, cur = "principle", {"id": m.group(1), "title": m.group(2).strip(), "line": n}
            principles.append(cur)
            continue
        if not line.startswith("- ") or section is None:
            continue   # commentary
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
            for i in t[side]["ids"]:
                if i not in ids:
                    bad.append("line %d: tension %s names an unknown principle %s" % (t["line"], t["id"], i))
    for need in REQUIRED_TENSIONS:
        if need not in tids:
            bad.append("the tension pair %s is missing" % need)
    for x in principles + tensions:
        x.pop("line", None)
    return {"principles": principles, "tensions": tensions}, bad


def load(path=None):
    """The parsed principles (+ "problems": [] when complete)."""
    with open(path or SOURCE) as f:
        doc, bad = parse(f.read())
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


def digest(mode, path=None):
    return hashlib.sha256(render(mode, path).encode()).hexdigest()


def stamp(mode, path=None):
    """What a goal records: the mode, the block's sha256, and the principle ids in order."""
    return {"mode": mode, "sha256": digest(mode, path), "ids": [p["id"] for p in load(path)["principles"]]}


def verify(st, path=None):
    """Problems with a goal's stamp: [] when it is the current block for its mode."""
    if not isinstance(st, dict) or not st.get("mode") or not st.get("sha256"):
        return ["the principles stamp needs mode and sha256 (principles.stamp(mode))"]
    if st["mode"] not in MODES:
        return ["the principles stamp's mode %r must be one of %s" % (st["mode"], ", ".join(MODES))]
    try:
        cur = {m: digest(m, path) for m in MODES}
    except ValueError as e:
        return ["principles.md is invalid: %s" % e]
    if st["sha256"] == cur[st["mode"]]:
        return []
    other = [m for m in MODES if m != st["mode"] and cur[m] == st["sha256"]]
    if other:
        return ["the principles stamp's sha256 is the %s block, not the %s one (mode mismatch)" % (other[0], st["mode"])]
    return ["the principles changed since this stamp (sha256 %s, now %s): re-stamp the goal"
            % (str(st["sha256"])[:12], cur[st["mode"]][:12])]


def mission_mode(cwd):
    doc, _ = M.load(M.path_of(cwd, "mission"))
    return doc.get("mode") if isinstance(doc, dict) and doc.get("mode") in MODES else None


def parse_args(argv):
    opts, pos, k = {}, [], None
    for a in argv:
        if k:
            opts[k], k = a, None
        elif a == "--json":
            opts["json"] = True
        elif a.startswith("--"):
            k = a[2:].replace("-", "_")
        else:
            pos.append(a)
    if k:
        opts[k] = ""
    return opts, pos


def main(argv, cwd):
    cmd, (o, _) = argv[0], parse_args(argv[1:])
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
        print("mission principles: unknown command %r (show, hash, check)" % cmd, file=sys.stderr)
        return 64
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
