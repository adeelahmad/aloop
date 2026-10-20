"""mission_fixture.py — a throwaway project for the mission-layer tests (ROADMAP §4.12).

    from mission_fixture import Project
    p = Project(mode="autonomous", veto_window_h=0)   # git repo + an approved mission
    p.research(["c1", "c2"])                         # -> "rsr_…" with those claim ids
    p.journey(draft) / p.run("journeys", "check")

The product under test is a tiny to-do "app": app.json holds its measured state, and
tools/journey.py is the pinned runner that reads it and prints the result line. Touch
`flaky` in the project to make every other run of a journey drift by 20%.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
sys.path.insert(0, BIN)
import _mission as M  # noqa: E402

RUNNER = r'''
import json, os, sys
app = json.load(open("app.json"))
j = os.environ.get("ALADDIN_JOURNEY") or os.environ.get("ALADDIN_BENCHMARK", "")
n = 0
if os.path.exists("flaky"):          # every other run of the same journey is 20% slower
    c = ".runs-" + j
    n = int(open(c).read()) if os.path.exists(c) else 0
    open(c, "w").write(str(n + 1))
secs = app["seconds_to_first_task"] * (1.2 if n % 2 else 1.0)
print("measuring", j)
print(json.dumps({"steps": {s: s in app["working_steps"] for s in ("open", "add", "see")},
                  "metrics": {"secs": secs, "tasks_shown": app["tasks_shown"]}}))
'''
PIN = {"python3": {"version": "Python 3", "probe": ["python3", "--version"]}}


class Project:
    def __init__(self, mode="autonomous", veto_window_h=None, approve=True):
        self.dir = tempfile.mkdtemp(prefix="aloop-mission-")
        self.env = dict(os.environ, HOME=os.path.join(self.dir, "home"))
        self.env.pop("AGENTIC_HOST", None)
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)
        os.makedirs(os.path.join(self.dir, "tools"))
        with open(os.path.join(self.dir, "tools", "journey.py"), "w") as f:
            f.write(RUNNER)
        self.app(seconds_to_first_task=9.0, working_steps=["open", "add"], tasks_shown=0)
        args = ["init", "the world's best to-do app with the best user experience", "--mode", mode,
                "--budget-usd", "40"]
        if veto_window_h is not None:
            args += ["--veto-window-h", str(veto_window_h)]
        r = self.run(*args)
        assert r.returncode == 0, r.stderr
        if approve:
            r = self.run("approve", "--by", "Adeel")
            assert r.returncode == 0, r.stderr

    def run(self, *args):
        return subprocess.run([os.path.join(BIN, "mission")] + list(args), cwd=self.dir, env=self.env,
                              capture_output=True, text=True)

    def app(self, **state):
        with open(os.path.join(self.dir, "app.json"), "w") as f:
            json.dump(state, f)

    def research(self, claims):
        rid = M.new_id("research")
        M.save(M.path_of(self.dir, "research", rid), {
            "research_id": rid, "status": "recorded", "created_at": M.now(),
            "claims": [{"claim_id": c, "text": "claim %s" % c, "kind": "other"} for c in claims]})
        return rid

    def draft(self, rid, **over):
        d = {"title": "Add a task and see it", "persona": "first-time user",
             "steps": [{"id": "open", "action": "open the app", "event": "app_open"},
                       {"id": "add", "action": "type a task, press Enter", "event": "task_add"},
                       {"id": "see", "action": "see the task in the list", "event": "task_seen"}],
             "metrics": [{"id": "secs", "name": "seconds to the first task", "unit": "s", "direction": "lower",
                          "query": {"kind": "script"}, "source_refs": [rid + "#c1"]},
                         {"id": "tasks_shown", "name": "tasks shown after adding one", "unit": "count",
                          "direction": "higher", "query": {"kind": "script"}, "source_refs": [rid + "#c1"]}],
             "pass_criteria": [{"id": "all", "steps": "all"},
                               {"id": "fast", "metric": "secs", "op": "<=", "value": 5}],
             "runner": {"command": ["python3", "tools/journey.py"], "pinned": PIN, "timeout_s": 60},
             "source_refs": [rid + "#c1"]}
        d.update(over)
        return d

    def journey(self, draft):
        p = os.path.join(self.dir, "draft-%d.json" % len(os.listdir(self.dir)))
        with open(p, "w") as f:
            json.dump(draft, f)
        r = self.run("journeys", "new", "--from", p)
        assert r.returncode == 0, r.stderr
        return json.load(open(r.stdout.strip()))["journey_id"]

    def path(self, kind, aid=""):
        return M.path_of(self.dir, kind, aid)

    def load(self, kind, aid=""):
        return json.load(open(self.path(kind, aid)))

    def save(self, kind, aid, doc):
        M.save(self.path(kind, aid), doc)

    def events(self, name=None):
        p = os.path.join(self.dir, ".aladdin", "logs", "events.jsonl")
        evs = [json.loads(l) for l in open(p)] if os.path.exists(p) else []
        return [e for e in evs if name is None or e["event"] == name]
