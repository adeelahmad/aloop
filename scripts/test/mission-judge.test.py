#!/usr/bin/env python3
"""ROADMAP §4.12 (M9) M9.5: LLM judges. The gate checks the setup, not the verdict: a pinned
model id (no alias, route or "latest"), a hashed rubric, at least 3 runs and a majority
agreement threshold, a scale with in-range pass and anchors, and research sources that
resolve. The verdict is re-derived from the RECORDED runs only (first `runs` valid runs of the
current rubric content; extra runs are ignored), so nobody can shop for a verdict. Runs use a
fake judge command (ALOOP_JUDGE_CMD): no network, no tokens.
    python3 scripts/test/mission-judge.test.py
"""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
AREAS = os.path.join(ROOT, "mission")
T = tempfile.mkdtemp(prefix="aloop-judge-")
FAKE_DIR = tempfile.mkdtemp(prefix="aloop-judge-fake-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
ENV.pop("ALOOP_JUDGE_CMD", None)
PASS = [0]
sys.path[:0] = [BIN, AREAS]
import _mission as M  # noqa: E402
import judge as J  # noqa: E402

MODEL = "claude-opus-5-5"


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, env=None):
    return subprocess.run(args, cwd=T, env=env or ENV, capture_output=True, text=True)


def events(kind=None):
    p = os.path.join(T, ".aladdin", "logs", "events.jsonl")
    evs = [json.loads(ln) for ln in open(p)] if os.path.exists(p) else []
    return [e for e in evs if kind is None or e["event"] == kind]


def write(name, text):
    p = os.path.join(T, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
MI = os.path.join(BIN, "mission")


def judge(*args, env=None):
    return run(MI, "judge", *args, env=env)


def research(*args):
    return run(MI, "research", *args)


def judges_dir():
    return M.mdir(T, "judges")


def rubric_files():
    d = judges_dir()
    return sorted(f for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []


def runs_file(rid):
    return os.path.join(judges_dir(), "runs", rid + ".jsonl")


def records(rid):
    p = runs_file(rid)
    return [json.loads(ln) for ln in open(p) if ln.strip()] if os.path.exists(p) else []


# ── the fake judge: reads the prompt on stdin, checks the input is in it, answers like `claude -p` ──
FAKE = r'''
import json, os, sys
d = os.path.dirname(os.path.abspath(__file__))
prompt = sys.stdin.buffer.read().decode("utf-8")
model = sys.argv[sys.argv.index("--model") + 1]
with open(os.path.join(d, "models.log"), "a") as f:
    f.write(model + "\n")
expect = open(os.path.join(d, "expect"), encoding="utf-8").read()
if expect not in prompt or "judge/1" not in prompt:
    print("the input is not in the prompt", file=sys.stderr)
    sys.exit(5)
seq = json.load(open(os.path.join(d, "seq.json")))
cpath = os.path.join(d, "counter")
n = int(open(cpath).read()) if os.path.exists(cpath) else 0
open(cpath, "w").write(str(n + 1))
step = seq[n]
if step == "fail":
    print("judge crashed", file=sys.stderr)
    sys.exit(1)
if isinstance(step, dict) and "plain" in step:          # a host that prints plain text, no JSON envelope
    print("Plain reasoning.\n" + json.dumps({"score": step["plain"], "reason": "plain text"}))
    sys.exit(0)
served = model
if step == "garbage":
    answer = "I think it is quite good overall, maybe a four?"
else:
    score = step if isinstance(step, int) else step["score"]
    served = step.get("served", model) if isinstance(step, dict) else model
    answer = ("The capture flow is clear {mostly}.\n"
              + json.dumps({"score": score, "reason": "run %d" % (n + 1)}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answer,
                  "total_cost_usd": 0.0125,
                  "usage": {"input_tokens": 100, "output_tokens": 20, "cache_creation_input_tokens": 5,
                            "cache_read_input_tokens": 7},
                  "modelUsage": {served: {"inputTokens": 100, "outputTokens": 20}}}))
'''
FAKE_PATH = os.path.join(FAKE_DIR, "fake-judge.py")
with open(FAKE_PATH, "w") as f:
    f.write(FAKE)
JENV = dict(ENV, ALOOP_JUDGE_CMD="%s %s --model {model}" % (shlex.quote(sys.executable), shlex.quote(FAKE_PATH)))


def script(seq, expect):
    """The fake judge's next answers (a list of steps) and the text it must find in the prompt."""
    with open(os.path.join(FAKE_DIR, "seq.json"), "w") as f:
        json.dump(seq, f)
    with open(os.path.join(FAKE_DIR, "expect"), "w", encoding="utf-8") as f:
        f.write(expect)
    if os.path.exists(os.path.join(FAKE_DIR, "counter")):
        os.remove(os.path.join(FAKE_DIR, "counter"))


def calls():
    p = os.path.join(FAKE_DIR, "counter")
    return int(open(p).read()) if os.path.exists(p) else 0


SCALE = {"min": 1, "max": 5}

# 1) parse_score: the LAST {"score": …} object in the answer, an integer within the scale
assert J.parse_score('Clear and fast.\n{"score": 4, "reason": "clear"}', SCALE) == (4, "clear")
assert J.parse_score('{"score": 2, "reason": "a"}\nOn reflection:\n{"score": 5, "reason": "b"}', SCALE) == (5, "b")
assert J.parse_score('{"score": 3, "reason": "uses {braces} and \\"quotes\\""} trailing', SCALE) == \
    (3, 'uses {braces} and "quotes"')
assert J.parse_score('{"score": 3, "reason": "two\\nlines"}', SCALE) == (3, "two lines")
assert J.parse_score('{"score": 4}', SCALE) == (4, "")
for bad in ("maybe a four", "", '{"score": 9, "reason": "x"}', '{"score": 0}', '{"score": 4.5}', '{"score": "4"}',
            '{"score": true}', '{"score": 4, "reason": "x"', '{"rating": 4}', '{"score": 4}\n{"score": 7}'):
    assert J.parse_score(bad, SCALE) == (None, ""), bad
assert J.parse_score('{"score": -1}', {"min": -2, "max": 2}) == (-1, "")
ok("parse_score: the last score object wins; non-integers and out-of-scale scores are no score")

# 2) the pinned model: a concrete id, never an alias, a route, "latest" or a name with no version
for good in (MODEL, "anthropic/claude-sonnet-5", "gpt-5.1-codex", "claude-haiku-4-5"):
    assert J.model_problems(good) == [], (good, J.model_problems(good))
for bad, why in (("", "empty"), ("sonnet", "alias"), ("opus", "alias"), ("fable", "alias"), ("haiku", "alias"),
                 ("default", "alias"), ("best", "alias"), ("opusplan", "alias"), ("strong", "route"),
                 ("strongest", "route"), ("economical", "route"), ("claude-3-5-sonnet-latest", "latest"),
                 ("Claude-Latest-5", "latest"), ("anthropic/sonnet", "alias"), ("gpt-codex", "version"),
                 ("claude opus 5", "whitespace")):
    p = J.model_problems(bad)
    assert p and why in " ".join(p), (bad, p)
ok("pinned model: empty, Claude Code aliases, routes, *latest* and unversioned names are refused")

# 3) agreement: the score most runs agree on (within the tolerance), ties broken by the LOWER score
assert J.agree_score([4, 4, 2], 1, 5, 0) == (4, 2)
assert J.agree_score([1, 3, 5], 1, 5, 0) == (1, 1)            # all different: a tie, the lowest
assert J.agree_score([3, 4, 5], 1, 5, 1) == (4, 3)
assert J.agree_score([2, 4, 5], 1, 5, 1) == (3, 2)            # 3, 4 and 5 each cover two: the lowest wins
assert J.agree_score([5, 5, 1, 1], 1, 5, 0) == (1, 2)         # conservative
assert J.agree_score([], 1, 5, 0) == (None, 0)
ok("agree_score: highest count within the tolerance; ties go to the lower score")

# 4) the prompt is deterministic and carries the rubric, the subject and the input between fences
RUB = {"rubric_id": "rub_000000000000aaaaaaaa", "title": "Quick capture feels effortless",
       "question": "How effortless is adding a task?", "criteria": ["one step", "no mode switch"],
       "scale": {"min": 1, "max": 5, "anchors": {"5": "one keystroke", "1": "a form with many fields"}},
       "pass": 4, "model": MODEL, "runs": 3, "agree": 2, "tolerance": 0, "sources": []}
pr = J.prompt(RUB, "todo-app@v1", "Press N, type, Enter.")
assert pr == J.prompt(dict(RUB), "todo-app@v1", "Press N, type, Enter.")
for part in ("judge/1", "Quick capture feels effortless", "How effortless is adding a task?", "- one step",
             "- no mode switch", "todo-app@v1", "```\nPress N, type, Enter.\n```", "1: a form with many fields",
             "5: one keystroke", '{"score": <integer from 1 to 5>, "reason": "<one line>"}'):
    assert part in pr, (part, pr)
assert pr.index("1: a form") < pr.index("5: one keystroke")   # anchors in scale order
fenced = J.prompt(RUB, "s", "code:\n````\nx\n````")
assert "`````\ncode:\n````\nx\n````\n`````" in fenced, fenced     # a fence longer than any in the input
assert J.prompt(RUB, "todo-app@v2", "x") != J.prompt(RUB, "todo-app@v1", "x")
ok("prompt(): deterministic judge/1 template; anchors ordered; the input is fenced safely")

# 5) a research report to cite (built through the research CLI from a local file snapshot)
page = write("page.txt", "Field study, 2026.\nMost users want to capture a task in one step, without "
                         "leaving the list they are looking at.\nForms with many fields are abandoned.\n")
r = research("new", "To-do app capture UX", "--question", "What do users expect from quick capture?")
assert r.returncode == 0, r.stderr
RSR = os.path.basename(r.stdout.strip())[:-len(".json")]
r = research("archive", RSR, "https://example.test/page", "--file", page)
assert r.returncode == 0 and r.stdout.strip() == "s1", (r.stdout, r.stderr)
r = research("claim", RSR, "Users want one-step capture", "--source", "s1", "--quote",
             "want to capture a task in one step", "--kind", "user-need")
assert r.returncode == 0 and r.stdout.strip() == "c1", (r.stdout, r.stderr)
SRC = RSR + "#c1"
r = research("check")
assert r.returncode == 0, r.stderr
ok("a research report with an archived snapshot and a quoted claim to cite (%s)" % SRC)

# 6) new: usage errors are 64, setup problems are 2 with BLOCK lines, and nothing is written
BASE = ["Quick capture feels effortless", "--question", "How effortless is adding a task?",
        "--criterion", "one step", "--criterion", "no mode switch", "--scale", "1-5", "--pass", "4",
        "--model", MODEL, "--source", SRC]


def without(args, flag):
    out, i = [], 0
    while i < len(args):
        if args[i] == flag:
            i += 2
            continue
        out.append(args[i])
        i += 1
    return out


def with_(args, flag, value):
    if flag in args:
        i = args.index(flag)
        return args[:i + 1] + [value] + args[i + 2:]
    return args + [flag, value]


for args in (without(BASE, "--model"), without(BASE, "--question"), without(BASE, "--criterion"),
             without(BASE, "--scale"), without(BASE, "--pass"), without(BASE, "--source"), BASE[1:],
             with_(BASE, "--scale", "5"), with_(BASE, "--scale", "a-b"), with_(BASE, "--pass", "four"),
             with_(BASE, "--runs", "x"), with_(BASE, "--anchor", "great"), with_(BASE, "--anchor", "x=great"),
             BASE + ["--bogus", "1"]):
    r = judge("new", *args)
    assert r.returncode == 64, (args, r.returncode, r.stderr)
for args, why in ((with_(BASE, "--model", "sonnet"), "alias"), (with_(BASE, "--model", "strong"), "route"),
                  (with_(BASE, "--model", "claude-3-5-sonnet-latest"), "latest"),
                  (with_(BASE, "--model", "opusplan"), "alias"),
                  (with_(BASE, "--runs", "2"), "runs"), (with_(BASE, "--agree", "1"), "majority"),
                  (with_(BASE, "--agree", "4"), "majority"),
                  (with_(with_(BASE, "--runs", "4"), "--agree", "2"), "majority"),
                  (with_(BASE, "--scale", "5-1"), "min"), (with_(BASE, "--scale", "3-3"), "min"),
                  (with_(BASE, "--pass", "6"), "pass"), (with_(BASE, "--pass", "0"), "pass"),
                  (with_(BASE, "--anchor", "7=perfect"), "anchor"), (with_(BASE, "--tolerance", "-1"), "tolerance"),
                  (with_(BASE, "--tolerance", "2"), "tolerance"), (with_(BASE, "--scale", "0-1000"), "span"),
                  (with_(BASE, "--source", "rsr_0000000000000000dead#c1"), "rsr_0000000000000000dead"),
                  (with_(BASE, "--source", RSR + "#c9"), "c9"), (with_(BASE, "--source", "a paper I read"), "source")):
    r = judge("new", *args)
    assert r.returncode == 2 and "BLOCK[judge]" in r.stderr and why in r.stderr, (args, r.returncode, r.stderr)
assert rubric_files() == [] and events("artifact.created") == []
ok("judge new refuses usage errors (64) and setup problems (2): models, runs, majority, ranges, sources")

# 7) a good rubric: proposed, sealed, harness id; the check passes
r = judge("new", *(BASE + ["--anchor", "5=one keystroke", "--anchor", "1=a form with many fields"]))
assert r.returncode == 0, r.stderr
rpath = r.stdout.strip()
assert os.path.realpath(os.path.dirname(rpath)) == os.path.realpath(judges_dir()), rpath
doc = json.load(open(rpath))
RID = doc["rubric_id"]
assert M.ID.match(RID) and RID.startswith("rub_") and os.path.basename(rpath) == RID + ".json"
assert doc["status"] == "proposed" and doc["runs"] == 3 and doc["agree"] == 2 and doc["tolerance"] == 0
assert doc["scale"] == {"min": 1, "max": 5, "anchors": {"1": "a form with many fields", "5": "one keystroke"}}
assert doc["pass"] == 4 and doc["model"] == MODEL and doc["sources"] == [SRC]
assert doc["criteria"] == ["one step", "no mode switch"] and doc["sha256"] == M.digest(doc)
assert J.setup_problems(T, rpath, doc) == []
ev = events("artifact.created")
assert len(ev) == 1 and ev[0]["data"]["rubric_id"] == RID and ev[0]["refs"]["rubric"].endswith(RID + ".json")
r = judge("check")
assert r.returncode == 0, r.stderr
r = judge("check", RID, "--json")
assert r.returncode == 0 and json.loads(r.stdout) == {"exit": 0, "problems": [], "checked": [RID]}, r.stdout
r = judge("new", *(BASE + ["--runs", "5"]))
assert r.returncode == 0 and json.load(open(r.stdout.strip()))["agree"] == 3, r.stderr   # floor(5/2)+1
R5 = json.load(open(r.stdout.strip()))["rubric_id"]
os.remove(r.stdout.strip())
ok("judge new writes a sealed proposed rubric (default agree 2 of 3; 3 of 5) and emits artifact.created")

# 8) only an active rubric runs; activate freezes it
inp_a = write("input-a.md", "Quick capture: press N anywhere — type the task, press Enter. Done ✓\n")
script([4, 4, 4], "press N anywhere")
r = judge("run", RID, "--subject", "todo-app@v1", "--input", inp_a, env=JENV)
assert r.returncode == 2 and "active" in r.stderr and calls() == 0, r.stderr
r = judge("activate", RID)
assert r.returncode == 0, r.stderr
doc = json.load(open(rpath))
assert doc["status"] == "active" and doc["sha256"] == M.digest(doc)
ev = events("artifact.updated")
assert len(ev) == 1 and ev[0]["data"]["action"] == "activate" and ev[0]["data"]["sha256"] == doc["sha256"]
assert judge("activate", RID).returncode == 0                     # already active: a no-op
assert len(events("artifact.updated")) == 1
ok("run needs an active rubric; activate checks the setup and records artifact.updated")

# 9) a run: the prompt goes on stdin, N records are appended, judge.run events, verdict pass 2 of 3
script([4, 4, 2], "press N anywhere — type the task")       # non-ASCII survives the round trip
r = judge("run", RID, "--subject", "todo-app@v1", "--input", inp_a, env=JENV)
assert r.returncode == 0 and "pass" in r.stdout, (r.stdout, r.stderr)
assert calls() == 3
assert open(os.path.join(FAKE_DIR, "models.log")).read().split() == [MODEL] * 3     # {model} substituted
recs = records(RID)
assert [x["run_id"] for x in recs] == [RID + "-r1", RID + "-r2", RID + "-r3"]
assert [x["score"] for x in recs] == [4, 4, 2] and all(x["valid"] for x in recs)
in_a = hashlib.sha256(open(inp_a, "rb").read()).hexdigest()
pr = J.prompt(doc, "todo-app@v1", open(inp_a, encoding="utf-8").read())
x = recs[0]
assert set(x) == {"run_id", "rubric_id", "rubric_sha256", "subject", "input_sha256", "prompt_sha256", "model",
                  "served_model", "score", "reason", "valid", "error", "output", "cost_usd", "tokens",
                  "recorded_at"}, sorted(x)
assert x["rubric_id"] == RID and x["rubric_sha256"] == doc["sha256"] and x["subject"] == "todo-app@v1"
assert x["input_sha256"] == in_a and x["prompt_sha256"] == hashlib.sha256(pr.encode()).hexdigest()
assert x["model"] == MODEL and x["served_model"] == MODEL and x["reason"] == "run 1" and not x["error"]
assert x["cost_usd"] == 0.0125 and x["tokens"] == 132 and '"score": 4' in x["output"]
ev = events("judge.run")
assert len(ev) == 3 and [e["data"]["run_id"] for e in ev] == [y["run_id"] for y in recs]
assert all("output" not in e["data"] and e["model"] == MODEL for e in ev)
assert ev[0]["data"]["score"] == 4 and ev[0]["data"]["valid"] is True and ev[0]["refs"]["runs"].endswith(RID + ".jsonl")
r = judge("verdict", RID, "--subject", "todo-app@v1", "--json")
assert r.returncode == 0, r.stderr
v = json.loads(r.stdout)
assert {k: v[k] for k in ("verdict", "score", "agreeing", "runs", "needed", "rubric_id", "subject")} == {
    "verdict": "pass", "score": 4, "agreeing": 2, "runs": 3, "needed": 2, "rubric_id": RID,
    "subject": "todo-app@v1"}, v
assert v["rubric_sha256"] == doc["sha256"] and v["input_sha256"] == in_a and v["run_ids"] == [y["run_id"] for y in recs]
assert J.verdict(T, RID, "todo-app@v1") == v
ok("judge run: prompt on stdin, records + judge.run events (no output in the event), 2 of 3 agree → pass")

# 10) a 4th extra run is ignored: re-running cannot shop for a verdict
script([1], "press N anywhere")
r = judge("run", RID, "--subject", "todo-app@v1", "--input", inp_a, "--times", "1", env=JENV)
assert r.returncode == 0 and len(records(RID)) == 4, r.stderr
v = J.verdict(T, RID, "todo-app@v1")
assert v["verdict"] == "pass" and v["run_ids"] == [RID + "-r1", RID + "-r2", RID + "-r3"], v
assert judge("run", RID, "--subject", "x", "--input", inp_a, "--times", "0", env=JENV).returncode == 64
ok("the first `runs` valid records decide; a 4th run is recorded and ignored")

# 11) fail, inconclusive, and the subject separates verdicts
inp_b = write("input-b.md", "To add a task open Settings, then Tasks, then New, then fill 9 fields.\n")
script([2, 2, 5], "fill 9 fields")
r = judge("run", RID, "--subject", "todo-app@v2", "--input", inp_b, env=JENV)
assert r.returncode == 2 and "fail" in r.stdout, (r.stdout, r.stderr)
v = J.verdict(T, RID, "todo-app@v2")
assert (v["verdict"], v["score"], v["agreeing"]) == ("fail", 2, 2), v
script([1, 3, 5], "press N anywhere")
r = judge("run", RID, "--subject", "todo-app@v3", "--input", inp_a, env=JENV)
assert r.returncode == 2 and "inconclusive" in r.stdout, r.stdout
v = J.verdict(T, RID, "todo-app@v3")
assert (v["verdict"], v["score"], v["agreeing"]) == ("inconclusive", None, 1), v
r = judge("verdict", RID, "--subject", "todo-app@v3")
assert r.returncode == 2 and "BLOCK[judge]" in r.stderr, r.stderr
v = J.verdict(T, RID, "nobody-ran-this")
assert (v["verdict"], v["runs"], v["run_ids"], v["input_sha256"]) == ("incomplete", 0, [], None), v
assert judge("verdict", RID, "--subject", "nobody-ran-this").returncode == 2
assert J.verdict(T, RID, "todo-app@v1")["verdict"] == "pass"
ok("verdicts: 2 of 3 below pass → fail; 3 different → inconclusive; no runs → incomplete (all exit 2)")

# 12) invalid runs are recorded and never count: garbage, a crash, a different served model
script(["garbage", 4, {"score": 4, "served": "claude-haiku-4-5"}, "fail"], "press N anywhere")
r = judge("run", RID, "--subject", "todo-app@v4", "--input", inp_a, "--times", "4", env=JENV)
assert r.returncode == 2 and "incomplete" in r.stdout, (r.stdout, r.stderr)
recs = [y for y in records(RID) if y["subject"] == "todo-app@v4"]
assert [y["valid"] for y in recs] == [False, True, False, False], recs
assert recs[0]["score"] is None and "no score" in recs[0]["error"]
assert recs[2]["score"] == 4 and recs[2]["served_model"] == "claude-haiku-4-5" and "served" in recs[2]["error"]
assert recs[3]["score"] is None and "exit 1" in recs[3]["error"] and recs[3]["cost_usd"] is None
v = J.verdict(T, RID, "todo-app@v4")
assert (v["verdict"], v["runs"]) == ("incomplete", 1), v
script([4, {"plain": 4}], "press N anywhere")
r = judge("run", RID, "--subject", "todo-app@v4", "--input", inp_a, "--times", "2", env=JENV)
assert r.returncode == 0 and "pass" in r.stdout, (r.stdout, r.stderr)
recs = [y for y in records(RID) if y["subject"] == "todo-app@v4"]
assert recs[-1]["served_model"] is None and recs[-1]["valid"] and recs[-1]["tokens"] is None   # plain text host
v = J.verdict(T, RID, "todo-app@v4")
assert v["verdict"] == "pass" and v["run_ids"] == [recs[1]["run_id"], recs[4]["run_id"], recs[5]["run_id"]], v
assert judge("check", RID).returncode == 0                # invalid records are well formed
ok("garbage, a crashed command and a different served model are recorded valid=false and never count")

# 13) --input picks the input; without it, the latest input judged for the subject
script([2, 2, 2], "fill 9 fields")
r = judge("run", RID, "--subject", "todo-app@v1", "--input", inp_b, env=JENV)
assert r.returncode == 2, r.stdout
in_b = hashlib.sha256(open(inp_b, "rb").read()).hexdigest()
v = J.verdict(T, RID, "todo-app@v1")
assert (v["verdict"], v["input_sha256"]) == ("fail", in_b), v
r = judge("verdict", RID, "--subject", "todo-app@v1", "--input", inp_a, "--json")
assert r.returncode == 0 and json.loads(r.stdout)["input_sha256"] == in_a, r.stdout
assert J.verdict(T, RID, "todo-app@v1", input_sha256=in_a)["verdict"] == "pass"
assert judge("verdict", RID, "--subject", "todo-app@v1", "--input", os.path.join(T, "missing.md")).returncode == 2
ok("verdict --input selects the input by sha256; without it, the latest input for the subject")

# 14) the input: text only, at most 200 KB, never recorded when refused
n0 = len(records(RID))
binp = os.path.join(T, "shot.png")
with open(binp, "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
big = write("big.md", "x" * (200 * 1024 + 1))
script([4, 4, 4], "x")
for p in (binp, big, os.path.join(T, "missing.md")):
    r = judge("run", RID, "--subject", "s", "--input", p, env=JENV)
    assert r.returncode == 2 and "BLOCK[judge]" in r.stderr, (p, r.stderr)
assert judge("run", RID, "--input", inp_a, env=JENV).returncode == 64           # --subject required
r = judge("run", RID, "--subject", "s", "--input", inp_a,
          env=dict(JENV, ALOOP_JUDGE_CMD="no-such-judge-%s --model {model}" % os.getpid()))
assert r.returncode == 2 and "not found" in r.stderr, r.stderr                     # no command: nothing recorded
assert len(records(RID)) == n0 and calls() == 0
ok("judge run refuses a binary, oversized or missing input (nothing is run or recorded)")

# 15) tolerance 1: scores within 1 agree; ties go to the lower (conservative) score
r = judge("new", *(with_(with_(BASE, "--tolerance", "1"), "--pass", "4")))
assert r.returncode == 0, r.stderr
RT = json.load(open(r.stdout.strip()))["rubric_id"]
assert judge("activate", RT).returncode == 0
script([3, 4, 5], "press N anywhere")
r = judge("run", RT, "--subject", "a", "--input", inp_a, env=JENV)
assert r.returncode == 0, r.stdout
v = J.verdict(T, RT, "a")
assert (v["verdict"], v["score"], v["agreeing"]) == ("pass", 4, 3), v
script([2, 4, 5], "press N anywhere")
r = judge("run", RT, "--subject", "b", "--input", inp_a, env=JENV)
v = J.verdict(T, RT, "b")
assert r.returncode == 2 and (v["verdict"], v["score"], v["agreeing"]) == ("fail", 3, 2), v
ok("tolerance 1: 3/4/5 agree on 4 (pass); 2/4/5 tie on 3, 4 and 5 → the lower 3 (fail)")

# 16) run records are re-checked: a score must re-parse from its output, ids stay sequential
rf = runs_file(RID)
orig = open(rf).read()
lines = orig.splitlines()
tamper = json.loads(lines[2])
tamper["score"] = 5                                            # r3 said 2; a 5 does not re-parse
with open(rf, "w") as f:
    f.write("\n".join(lines[:2] + [json.dumps(tamper)] + lines[3:]) + "\n")
r = judge("check", RID)
assert r.returncode == 2 and RID + "-r3" in r.stderr and "re-parse" in r.stderr, r.stderr
assert J.verdict(T, RID, "todo-app@v1", input_sha256=in_a)["verdict"] != "pass"
g = [json.loads(ln) for ln in lines if json.loads(ln)["valid"] is False][0]
g["valid"], g["error"] = True, ""                              # an invalid run flipped to valid
with open(rf, "w") as f:
    f.write("\n".join(json.dumps(g) if json.loads(ln)["run_id"] == g["run_id"] else ln for ln in lines) + "\n")
r = judge("check", RID)
assert r.returncode == 2 and g["run_id"] in r.stderr, r.stderr
with open(rf, "w") as f:                                       # a deleted run (shopping by deletion)
    f.write("\n".join(lines[:2] + lines[3:]) + "\n")
r = judge("check", RID)
assert r.returncode == 2 and "sequence" in r.stderr, r.stderr
other = json.loads(lines[0])
other["model"], other["rubric_sha256"], other["score"] = "claude-haiku-4-5", "nope", 4.0
with open(rf, "w") as f:
    f.write("\n".join([json.dumps(other)] + lines[1:]) + "\nnot json\n")
r = judge("check", RID)
assert r.returncode == 2 and "model" in r.stderr and "rubric_sha256" in r.stderr and "not JSON" in r.stderr, r.stderr
assert "score 4.0 does not re-parse" in r.stderr, r.stderr
with open(rf, "w") as f:
    f.write(orig)
assert judge("check", RID).returncode == 0
ok("judge check re-parses each record's score, its validity, model, rubric sha and run-id sequence")

# 17) editing an active rubric breaks the seal; a re-sealed edit is a different rubric: old runs never count
doc = json.load(open(rpath))
doc["pass"] = 2
with open(rpath, "w") as f:
    json.dump(doc, f)
r = judge("check", RID)
assert r.returncode == 2 and "sha256 does not match" in r.stderr, r.stderr
v = J.verdict(T, RID, "todo-app@v1")
assert v["verdict"] == "blocked" and v["problems"], v
assert judge("verdict", RID, "--subject", "todo-app@v1").returncode == 2
script([4, 4, 4], "press N anywhere")
r = judge("run", RID, "--subject", "todo-app@v1", "--input", inp_a, env=JENV)
assert r.returncode == 2 and calls() == 0, r.stderr
M.seal(doc)                                                    # a hand re-seal of the edited content
M.save(rpath, doc)
assert judge("check", RID).returncode == 0
v = J.verdict(T, RID, "todo-app@v1", input_sha256=in_a)
assert (v["verdict"], v["runs"], v["run_ids"]) == ("incomplete", 0, []), v
ok("an edited active rubric fails the check (2); after a re-seal its old runs no longer count")

# 18) a research source that stops resolving blocks the rubric and its verdict
page2 = write("page2.txt", "Benchmark: the fastest apps add a task in under two seconds from any screen.\n")
r = research("new", "Capture speed", "--question", "How fast is capture in the best apps?")
RSR2 = os.path.basename(r.stdout.strip())[:-len(".json")]
assert research("archive", RSR2, "https://example.test/bench", "--file", page2).returncode == 0
assert research("claim", RSR2, "Best apps capture in under 2s", "--source", "s1", "--quote",
                "add a task in under two seconds", "--kind", "benchmark").returncode == 0
r = judge("new", *with_(BASE, "--source", RSR2 + "#c1"))
assert r.returncode == 0, r.stderr
R3 = json.load(open(r.stdout.strip()))["rubric_id"]
assert judge("activate", R3).returncode == 0
script([5, 5, 4], "press N anywhere")
assert judge("run", R3, "--subject", "a", "--input", inp_a, env=JENV).returncode == 0
assert research("retire", RSR2, "--reason", "the benchmark was withdrawn").returncode == 0
r = judge("check", R3)
assert r.returncode == 2 and RSR2 in r.stderr, r.stderr
v = J.verdict(T, R3, "a")
assert v["verdict"] == "blocked" and any(RSR2 in p for p in v["problems"]), v
ok("a rubric whose research source no longer resolves fails the check and its verdict is blocked")

# 19) retire: a reason is required; a retired rubric neither runs nor passes nor re-activates
assert judge("retire", RT).returncode == 64
r = judge("retire", RT, "--reason", "replaced by a stricter rubric")
assert r.returncode == 0, r.stderr
d = json.load(open(M.path_of(T, "rubric", RT)))
assert d["status"] == "retired" and d["retired"]["reason"] == "replaced by a stricter rubric" and d["retired"]["at"]
assert d["sha256"] == M.digest(d) and judge("check", RT).returncode == 0
acts = [e["data"]["action"] for e in events("artifact.updated") if e["data"]["rubric_id"] == RT]
assert acts == ["activate", "retire"], acts
script([5, 5, 5], "press N anywhere")
assert judge("run", RT, "--subject", "a", "--input", inp_a, env=JENV).returncode == 2 and calls() == 0
assert J.verdict(T, RT, "a")["verdict"] == "blocked"
assert judge("activate", RT).returncode == 2 and judge("retire", RT, "--reason", "again").returncode == 2
ok("judge retire: reason required, sealed, artifact.updated; a retired rubric never runs or passes")

# 20) list, show, check over all, unknown ids, help
r = judge("list", "--json")
rows = json.loads(r.stdout)
assert r.returncode == 0 and [x["rubric_id"] for x in rows] == sorted([RID, RT, R3]), rows
assert {x["rubric_id"]: x["status"] for x in rows} == {RID: "active", RT: "retired", R3: "active"}
assert all({"title", "model", "runs", "agree", "records"} <= set(x) for x in rows), rows
assert RID in judge("list").stdout
r = judge("show", RID, "--json")
assert r.returncode == 0 and json.loads(r.stdout)["rubric"]["rubric_id"] == RID, r.stdout
assert "Quick capture feels effortless" in judge("show", RID).stdout
assert judge("show", "rub_000000000000ffffffff").returncode == 2
assert judge("check", "rub_000000000000ffffffff").returncode == 2
r = judge("check", "--json")
assert r.returncode == 2 and sorted(json.loads(r.stdout)["checked"]) == sorted([RID, RT, R3]), r.stdout
assert judge("nope").returncode == 64 and judge("verdict", RID).returncode == 64
r = judge("--help")
assert r.returncode == 0 and "mission judge new" in r.stdout and "verdict" in r.stdout, r.stdout
assert "judge" in run(MI, "--list").stdout.split()
ok("judge list / show / check over every rubric; unknown ids block; --help is the module docstring")

# 21) events: one artifact.created per rubric, judge.run per run (with the pinned model)
assert sorted(e["data"]["rubric_id"] for e in events("artifact.created") if e["data"].get("kind") == "rubric") == \
    sorted([RID, R5, RT, R3])
n_runs = sum(len(records(x)) for x in (RID, RT, R3))
assert len(events("judge.run")) == n_runs and all(e["role"] == "judge" for e in events("judge.run"))
assert all(e["data"]["rubric_sha256"] and "output" not in e["data"] for e in events("judge.run"))
ok("events: artifact.created / artifact.updated for rubrics, one judge.run per recorded run")

# 22) no rubrics yet is not a failure
T2 = tempfile.mkdtemp(prefix="aloop-judge-empty-")
subprocess.run(["git", "init", "-q"], cwd=T2, check=True)
r = subprocess.run([MI, "judge", "check"], cwd=T2, env=ENV, capture_output=True, text=True)
assert r.returncode == 0 and "no rubrics yet" in r.stderr, r.stderr
r = subprocess.run([MI, "judge", "list", "--json"], cwd=T2, env=ENV, capture_output=True, text=True)
assert r.returncode == 0 and json.loads(r.stdout) == [], r.stdout
ok("no rubrics yet: check passes with nothing to check")

print("mission-judge: %d checks passed" % PASS[0])
