#!/usr/bin/env python3
"""ROADMAP §4.12 (M9) M9.7: the standing principles, in two variants (autonomous and
spoon-fed) with tension pairs. `plugin/mission/principles.md` is the source; the module
parses it, refuses a malformed or incomplete edit, renders one block per mode and hashes
it, so a goal can carry the exact principles it was derived under (stamp / verify).
    python3 scripts/test/mission-principles.test.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
MD = os.path.join(ROOT, "mission", "principles.md")
T = tempfile.mkdtemp(prefix="aloop-principles-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
sys.path.insert(0, BIN)
sys.path.insert(0, os.path.join(ROOT, "mission"))
import principles as P  # noqa: E402


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args):
    return subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True)


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
MI = os.path.join(BIN, "mission")

# 1) the shipped file passes its check: every standing principle, both variants, the four tension pairs
r = run(MI, "principles", "check")
assert r.returncode == 0, r.stderr
doc = P.load()
ids = [p["id"] for p in doc["principles"]]
for need in ("autonomic-loop", "own-the-outcome", "ship-small", "verify-dont-assume", "raise-the-bar",
             "disagree-then-commit", "leave-it-better", "know-when-to-ask", "grow-adoption", "keep-it-simple"):
    assert need in ids, (need, ids)
assert set(P.REQUIRED) <= set(ids) and ids[0] == "autonomic-loop", ids   # the IBM loop is the engine, listed first
for p in doc["principles"]:
    assert p["autonomous"] and p["spoon-fed"] and p["autonomous"] != p["spoon-fed"], p["id"]
tens = {t["id"]: t for t in doc["tensions"]}
assert set(tens) == {"customer-obsession-vs-frugality", "bias-for-action-vs-dive-deep",
                     "think-big-vs-deliver-results", "grow-adoption-vs-keep-it-simple"}, sorted(tens)
for t in tens.values():
    assert t["a"]["ids"] and t["b"]["ids"] and set(t["a"]["ids"] + t["b"]["ids"]) <= set(ids), t
    assert t["resolves"].strip(), t
ok("principles.md: the standing set, both variants (different) per principle, four tension pairs over known ids")

# 2) one block per mode; Raise the Bar is the spec's main difference (own backlog vs ranked options)
auto = run(MI, "principles", "show", "--mode", "autonomous")
spoon = run(MI, "principles", "show", "--mode", "spoon-fed")
assert auto.returncode == 0 and spoon.returncode == 0, auto.stderr + spoon.stderr
assert auto.stdout != spoon.stdout
for p in doc["principles"]:
    assert p["title"] in auto.stdout and p["title"] in spoon.stdout, p["title"]
    assert p["autonomous"] in auto.stdout and p["spoon-fed"] in spoon.stdout, p["id"]
    assert p["spoon-fed"] not in auto.stdout and p["autonomous"] not in spoon.stdout, p["id"]
rb = next(p for p in doc["principles"] if p["id"] == "raise-the-bar")
assert "backlog" in rb["autonomous"] and "options" in rb["spoon-fed"], rb
for out in (auto.stdout, spoon.stdout):
    assert out.rstrip("\n").splitlines()[-1] == P.INVARIANT_CLAUSE, out.splitlines()[-1]
    assert "Customer Obsession" in out and "Frugality" in out
assert "autonomous mode" in auto.stdout.splitlines()[0] and "spoon-fed mode" in spoon.stdout.splitlines()[0]
assert run(MI, "principles", "show", "--mode", "yolo").returncode == 64
ok("show --mode: one block per mode, only that mode's variants, tensions, and the invariant clause last")

# 3) the hash is the sha256 of exactly the rendered block, stable across runs
for mode, out in (("autonomous", auto.stdout), ("spoon-fed", spoon.stdout)):
    h = run(MI, "principles", "hash", "--mode", mode)
    assert h.returncode == 0, h.stderr
    assert h.stdout.strip() == hashlib.sha256(out.encode()).hexdigest() == P.digest(mode), mode
    assert P.render(mode) == out and P.render(mode) == P.render(mode)
assert P.digest("autonomous") != P.digest("spoon-fed")
js = json.loads(run(MI, "principles", "show", "--mode", "spoon-fed", "--json").stdout)
assert js["mode"] == "spoon-fed" and js["sha256"] == P.digest("spoon-fed") and js["text"] == spoon.stdout
assert js["ids"] == ids
ok("hash --mode = sha256(show --mode); --json carries mode, sha256, ids and text")

# 4) without --mode, the mission's mode is used; with no mission it is a usage error
r = run(MI, "principles", "show")
assert r.returncode == 64 and "--mode" in r.stderr, r.stderr
assert run(MI, "init", "the world's best to-do app", "--mode", "spoon-fed", "--budget-usd", "5").returncode == 0
r = run(MI, "principles", "show")
assert r.returncode == 0 and r.stdout == spoon.stdout, r.stderr
assert run(MI, "principles", "hash").stdout.strip() == P.digest("spoon-fed")
ok("no --mode: the mission's own mode (spoon-fed here); none and no mission is a usage error")

# 5) stamp / verify: what a goal carries, and how a changed principle or mode is caught
st = P.stamp("autonomous")
assert st == {"mode": "autonomous", "sha256": P.digest("autonomous"), "ids": ids}, st
assert P.verify(st) == []
assert any("changed since" in x for x in P.verify(dict(st, sha256="0" * 64)))
assert any("mode" in x for x in P.verify(dict(st, mode="sometimes")))
assert any("mode" in x for x in P.verify(dict(st, mode="spoon-fed")))   # the other mode's block has another hash
assert P.verify(None) and P.verify({"mode": "autonomous"})
assert any("ids" in x for x in P.verify(dict(st, ids=[])))      # the stamp names the principles it carries
# a goal keeps the stamp it was derived under as provenance: an older block is stale, not invalid
# (re-stamping would change the goal's sha256 and void its ethics review and approval)
assert P.verify(dict(st, sha256="ab" * 32), stale_ok=True) == []
assert P.stale(dict(st, sha256="ab" * 32)) and not P.stale(st)
assert any("mode" in x for x in P.verify(dict(st, mode="spoon-fed"), stale_ok=True))
for b in (dict(st, sha256="nope"), dict(st, ids="x"), {"mode": "autonomous"},
          dict(st, sha256="ab" * 32, ids=st["ids"][1:]), dict(st, sha256="ab" * 32, ids=st["ids"][1:] + st["ids"][:1])):
    assert P.verify(b, stale_ok=True), b
ok("stamp(mode) = {mode, sha256, ids}; verify() names a changed block, a wrong mode, a missing field")

# 6) a malformed or incomplete edit is refused (exit 2) with a line per problem; --file checks a draft
text = open(MD).read()


def check_variant(new_text):
    p = os.path.join(T, "draft.md")
    open(p, "w").write(new_text)
    return run(MI, "principles", "check", "--file", p)


ks = next(p for p in doc["principles"] if p["id"] == "keep-it-simple")
cases = [
    (text.replace("- spoon-fed: " + ks["spoon-fed"], ""), "keep-it-simple has no spoon-fed variant"),
    (text.replace("- spoon-fed: " + ks["spoon-fed"], "- spoon-fed: " + ks["autonomous"]),
     "keep-it-simple: the two variants are the same"),
    (text.replace("- spoon-fed: " + ks["spoon-fed"], "- spoonfed: " + ks["spoon-fed"]), "unknown line"),
    (text.replace("(keep-it-simple)", "(keep-it-simpler)"), "names an unknown principle keep-it-simpler"),
    (text.replace("## grow-adoption:", "## grow-adoptions:"), "the standing principle grow-adoption is missing"),
    (text + "\n## keep-it-simple: Keep It Simple Again\n- autonomous: a\n- spoon-fed: b\n", "duplicate principle keep-it-simple"),
    (text.replace("- autonomous: " + ks["autonomous"], "- autonomous: " + "x" * (P.MAX_VARIANT + 1)), "longer than"),
    ("\n".join(l for l in text.splitlines() if not l.startswith("- think-big-vs-deliver-results:")) + "\n",
     "the tension pair think-big-vs-deliver-results is missing"),
]
ga = next(p for p in doc["principles"] if p["id"] == "grow-adoption")
cut = ga["autonomous"].index(" Never")
cases += [
    (text.replace(ga["autonomous"], ga["autonomous"][:cut] + "\n  " + ga["autonomous"][cut + 1:]), "only bullet lines"),
    (text.replace(ga["autonomous"], ga["autonomous"].replace(" Never", "\u2028Never")), "control or invisible"),
    (text.replace("(ship-small) vs", "( ) vs"), "names no principle for Bias for Action"),
    (text.replace("- spoon-fed: " + ks["spoon-fed"], "- autonomous: again\n- spoon-fed: " + ks["spoon-fed"]),
     "keep-it-simple has a second autonomous variant"),
    (text.replace("## Tensions\n", "## Tensions\n" + [l for l in text.splitlines() if l.startswith("- think-big")][0] + "\n"),
     "duplicate tension pair think-big-vs-deliver-results"),
    (text.replace("## keep-it-simple: Keep It Simple", "## Keep It Simple"), "a heading must be"),
]
al = text.index("## autonomic-loop:")
oo = text.index("## own-the-outcome:")
sm = text.index("## ship-small:")
cases.append((text[:al] + text[oo:sm] + text[al:oo] + text[sm:], "(the engine) must be listed first"))
for new_text, want in cases:
    assert new_text != text, want
    r = check_variant(new_text)
    assert r.returncode == 2 and want in r.stderr and "BLOCK[principles]" in r.stderr, (want, r.stderr)
assert check_variant(text).returncode == 0
open(os.path.join(T, "draft.md"), "wb").write(text.encode("latin-1", "replace"))   # an editor's latin-1 save
r = run(MI, "principles", "check", "--file", os.path.join(T, "draft.md"))
assert r.returncode == 2 and "cannot read" in r.stderr and "Traceback" not in r.stderr, r.stderr
r = run(MI, "principles", "show", "--mode", "autonomous", "--file", os.path.join(T, "draft.md"))
assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr
assert run(MI, "principles", "check", "--file", "").returncode == 64        # an empty --file is not the shipped file
assert run(MI, "principles", "show", "--mode", "").returncode == 64
check_variant(text)
r = run(MI, "principles", "show", "--mode", "autonomous", "--file", os.path.join(T, "draft.md"))
assert r.returncode == 0 and r.stdout == auto.stdout
check_variant(cases[0][0])
assert run(MI, "principles", "show", "--mode", "autonomous", "--file", os.path.join(T, "draft.md")).returncode == 2
ok("check refuses a missing or duplicated variant, unknown ids, a dropped standing principle or pair, overlong text")

# 8) the core principles every aloop agent carries (M9.30), the one-way / two-way door rule first; they
#    are single-sourced in principles.md, rendered apart from the goal blocks (so editing them never
#    stales a goal's stamp), and injected at session start in an aloop project
r = run(MI, "principles", "show", "--core")
assert r.returncode == 0, r.stderr
core = r.stdout
assert core.startswith("Core principles") and len(core) <= P.MAX_CORE and core == P.render_core(), core
assert "ASK[door]" in core and "aloop mission doors" in core and "Two-way door" in core, core
assert [c["id"] for c in P.load()["core"]] == list(P.REQUIRED_CORE) and P.REQUIRED_CORE[0] == "doors"
assert "Core principles" not in auto.stdout and "Two-way door" not in auto.stdout       # not in a goal block
dl = next(l for l in text.splitlines() if l.startswith("- doors: "))
ev = next(l for l in text.splitlines() if l.startswith("- evidence: "))
core_cases = [
    (text.replace(dl + "\n", ""), "the core principle doors is missing"),
    (text.replace(ev, "- evidence: " + "x" * (P.MAX_VARIANT + 1)), "longer than"),
    (text.replace(ev, ev + "\n" + ev), "duplicate core principle evidence"),
    (text.replace(dl, dl.replace("ASK[door]", "ASK")), "the doors principle names ASK[door] and `aloop mission doors`"),
    (text.replace(ev, ev + "\n- extra-a: " + "y" * 270 + "\n- extra-b: " + "z" * 270), "core block is longer than"),
    (text.replace("## Core\n", "## Core\n- Bad Id: text\n"), "unknown line in Core"),
]
for new_text, want in core_cases:
    assert new_text != text, want
    r = check_variant(new_text)
    assert r.returncode == 2 and want in r.stderr and "BLOCK[principles]" in r.stderr, (want, r.stderr)
check_variant(text.replace(ev, "- evidence: Claim only what a test or a source shows."))
draft = os.path.join(T, "draft.md")
for mode in P.MODES:   # a core edit leaves both goal blocks, and so every goal's stamp, as they were
    assert run(MI, "principles", "hash", "--mode", mode, "--file", draft).stdout == run(MI, "principles", "hash", "--mode", mode).stdout
assert run(MI, "principles", "show", "--core", "--file", draft).stdout != core
kw = next(p for p in P.load()["principles"] if p["id"] == "know-when-to-ask")
assert "one-way door" in kw["autonomous"] and "one-way door" in kw["spoon-fed"], kw
assert "ask whenever" not in kw["spoon-fed"] and "two-way" in kw["spoon-fed"], kw
SS = os.path.join(BIN, "session-start")
proj = os.path.join(T, "proj")
os.makedirs(os.path.join(proj, "docs", "agents"))
subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
r = subprocess.run([SS], input=json.dumps({"cwd": proj, "source": "startup"}), env=ENV, capture_output=True, text=True)
assert r.returncode == 0 and core.strip() in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"], r.stdout
bare = os.path.join(T, "bare")
os.makedirs(bare)
subprocess.run(["git", "init", "-q"], cwd=bare, check=True)
r = subprocess.run([SS], input=json.dumps({"cwd": bare, "source": "startup"}), env=ENV, capture_output=True, text=True)
assert r.returncode == 0 and r.stdout == "", r.stdout                                       # not an aloop project
ok("core principles: the door rule first, single-sourced, apart from the goal blocks, checked, injected at session start")

# 7) the area is discovered (no shared list edited) and `mission principles --help` is its docstring
assert "principles" in run(MI, "--list").stdout.split()
r = run(MI, "principles", "--help")
assert r.returncode == 0 and "two variants" in r.stdout, r.stdout
assert run(MI, "principles", "nope").returncode == 64
shutil.rmtree(T, ignore_errors=True)
ok("discovered as an area; --help is the module docstring; an unknown subcommand is a usage error")

print("mission-principles: %d checks passed" % PASS[0])
