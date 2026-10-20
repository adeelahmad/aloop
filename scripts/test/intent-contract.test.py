#!/usr/bin/env python3
"""ROADMAP §4.1: the internal Intent Contract. The harness issues the intent_id (never
model text), the contract is validated field by field, the clarification policy is
enforced (assume / default / spike are recorded; an unanswered `ask` stops both modes),
the approval summary surfaces defaults, and gate-intake runs the check.
    python3 scripts/test/intent-contract.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-intent-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, env=None):
    return subprocess.run(args, cwd=T, env=env or ENV, capture_output=True, text=True)


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
I = os.path.join(BIN, "intent")

# 1) identity is issued by the harness, and the skeleton is not yet a valid contract
r = run(I, "new", "--source", "prompt", "--raw", "add CSV export to the report page")
assert r.returncode == 0, r.stderr
path = r.stdout.strip()
doc = json.load(open(path))
assert doc["intent_id"].startswith("int_") and os.path.basename(path) == doc["intent_id"] + ".json"
assert len(doc["provenance"]["raw_sha256"]) == 64 and doc["provenance"]["received_at"]
assert run(I, "new", "--source", "telepathy").returncode == 64
r = run(I, "check", path)
assert r.returncode == 2 and "want is empty" in r.stderr and "failure_scenarios needs at least one" in r.stderr, r.stderr
ok("intent new: harness-issued id + provenance; the empty skeleton fails the check")

# 2) a complete contract passes and emits intent.normalized
doc.update({"actor": "analyst", "want": "export the report table as CSV", "why": "share numbers with finance",
            "constraints": ["no new dependencies"], "success_scenarios": ["click Export → a .csv with every visible row"],
            "failure_scenarios": ["an empty table exports a header-only CSV, not an error"],
            "connections": ["report page", "table component"], "done_looks_like": "Export button downloads the CSV",
            "open_questions": [
                {"question": "delimiter?", "class": "default", "default": "comma"},
                {"question": "file name?", "class": "assume", "assumption": "report-<date>.csv"},
                {"question": "is the 10k-row limit fast enough?", "class": "spike", "spike_task": "S1-01-T0 measure export time"}]})
json.dump(doc, open(path, "w"))
r = run(I, "check", path)
assert r.returncode == 0, r.stderr
evs = [json.loads(l)["event"] for l in open(os.path.join(T, ".aladdin", "logs", "events.jsonl"))]
assert evs == ["intent.received", "intent.normalized"], evs
ok("a complete contract passes; intent.received → intent.normalized in the event stream")

# 3) the clarification policy
doc["open_questions"].append({"question": "may we drop the legacy XLS export?", "class": "ask"})
json.dump(doc, open(path, "w"))
for mode in ("reviewed", "autonomous"):
    r = run(I, "check", path, "--mode", mode)
    assert r.returncode == 3 and "ASK[intent]: may we drop the legacy XLS export?" in r.stderr, (mode, r.stderr)
doc["open_questions"][-1]["answer"] = "no, keep it"
doc["open_questions"].append({"question": "colour?", "class": "default"})
json.dump(doc, open(path, "w"))
r = run(I, "check", path)
assert r.returncode == 2 and "(default) must record its default" in r.stderr, r.stderr
doc["open_questions"].pop()
json.dump(doc, open(path, "w"))
assert run(I, "check", path, "--mode", "autonomous").returncode == 0
ok("an unanswered ask stops BOTH modes; once answered autonomous proceeds; a default must be recorded")

# 4) the approval summary surfaces what the planner decided on the human's behalf
s = run(I, "summary", path).stdout
assert "DEFAULT picked: delimiter? → comma" in s and "ASSUMED: file name? → report-<date>.csv" in s
assert "SPIKE: is the 10k-row limit fast enough?" in s and "ASKED: may we drop the legacy XLS export? → no, keep it" in s
ok("intent summary lists defaults, assumptions, spikes and answers for the approval")

# 5) ids are frozen and harness-issued: a renamed or hand-made id is refused
forged = os.path.join(os.path.dirname(path), "int_000000000000deadbeef.json")
json.dump(doc, open(forged, "w"))
assert "does not match the file name" in run(I, "check", forged).stderr
doc2 = dict(doc, intent_id="INTENT-1")
json.dump(doc2, open(forged, "w"))
assert "was not issued by `intent new`" in run(I, "check", forged).stderr
os.remove(forged)
ok("intent ids are harness-issued and frozen")

# 6) gate-intake runs the contract check
sprint = os.path.join(T, "docs", "agents", "sprint1")
os.makedirs(sprint)
open(os.path.join(sprint, "intake.md"), "w").write(
    "# Intent\nWhat: x\n## Constraints\n## Failure scenarios\n## Success scenarios\n## Connections\n")
gate = lambda: run(os.path.join(BIN, "gate-intake"), os.path.join(sprint, "intake.md"))  # noqa: E731
assert gate().returncode == 0
doc["open_questions"].append({"question": "delete user data on export?", "class": "ask"})
json.dump(doc, open(path, "w"))
r = gate()
assert r.returncode == 2 and "only the human can answer" in r.stderr, r.stderr
ok("gate-intake blocks on an unanswered ask in the newest Intent Contract")

# 7) ROADMAP §4.9: a host's stop that only waits on the human is released at once as NEEDS-HUMAN, as a
# STUCK release is: not a pass (gate.failed, released), its questions recorded for the supervisor. A
# self-check, a malformed contract and an incomplete intake still block that stop.
stop = {"hook_event_name": "SubagentStop", "session_id": "s1", "agent_id": "ag-intake", "agent_type": "aloop:intake", "cwd": T}
host = lambda env=None: subprocess.run([os.path.join(BIN, "gate-intake"), os.path.join(sprint, "intake.md")],  # noqa: E731
                                       cwd=T, env=env or ENV, capture_output=True, text=True, input=json.dumps(stop))
r = host()
assert r.returncode == 0 and "NEEDS-HUMAN" in r.stderr and "NOT verified" in r.stderr, r.stderr
assert "ASK[intent]: delete user data on export?" in r.stderr, r.stderr
rec = os.path.join(T, ".aladdin", "state", "needs-human", doc["intent_id"] + ".json")
held = json.load(open(rec))
assert held["agent_id"] == "ag-intake" and held["questions"] == ["delete user data on export?"] and os.path.samefile(held["contract"], path), held
last = json.loads(open(os.path.join(T, ".aladdin", "logs", "events.jsonl")).read().splitlines()[-1])
assert last["event"] == "gate.failed" and last["data"].get("released") == "needs-human" and last["role"] == "intake", last
r = host(dict(ENV, AGENTIC_SELFCHECK="1"))
assert r.returncode == 2 and "only the human can answer" in r.stderr and "never answer" in r.stderr, r.stderr
ok("a host's stop on an unanswered ask is released at once as NEEDS-HUMAN (recorded, gate.failed), a self-check still blocks")
bad = dict(doc, want="")
json.dump(bad, open(path, "w"))
r = host()
assert r.returncode == 2 and "is invalid" in r.stderr and "NEEDS-HUMAN" not in r.stderr, r.stderr
json.dump(doc, open(path, "w"))
open(os.path.join(sprint, "intake.md"), "w").write("# Intent\nWhat: x\n## Constraints\n## Failure scenarios\n## Success scenarios\n")
r = host()
assert r.returncode == 2 and "five-part intent incomplete" in r.stderr and "NEEDS-HUMAN" not in r.stderr, r.stderr
open(os.path.join(sprint, "intake.md"), "w").write(
    "# Intent\nWhat: x\n## Constraints\n## Failure scenarios\n## Success scenarios\n## Connections\n")
ok("a malformed contract or an incomplete five-part intake still blocks a host's stop (no release)")

# the supervisor is told on Claude Code and Codex by its next tool result (PostToolUse), once, while the
# questions are still open; a role's own tool call never carries it
I_HOOK = lambda p: subprocess.run([I, "hook"], cwd=T, env=ENV, capture_output=True, text=True, input=json.dumps(p))  # noqa: E731
post = {"hook_event_name": "PostToolUse", "session_id": "s1", "cwd": T, "tool_name": "Agent", "tool_input": {}, "tool_response": "intake done"}
assert I_HOOK(dict(post, agent_id="ag-red", agent_type="red-worker")).stdout.strip() == ""
r = I_HOOK(post)
told = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
assert r.returncode == 0 and "intake ag-intake: NEEDS-HUMAN" in told and "delete user data on export?" in told and "not a pass" in told, r.stdout
assert I_HOOK(post).stdout.strip() == "" and os.path.isfile(rec + ".reported")
ok("the supervisor's next tool result carries NEEDS-HUMAN with the questions, once (a role's never)")
host()
doc["open_questions"][-1]["answer"] = "never"
json.dump(doc, open(path, "w"))
assert I_HOOK(post).stdout.strip() == "" and gate().returncode == 0
ok("a release whose questions were answered since is not reported; the answered contract passes")

print("\n%d checks passed" % PASS[0])
