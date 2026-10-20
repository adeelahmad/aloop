"""_host.py — the HostAdapter interface (ROADMAP §4.9) and its four adapters.

Claude Code is the reference implementation; Codex, OpenCode and DeepSeek Harness (dsh) are
adapters behind the same interface. Every method names the ONE place the harness does that thing on that host,
so a new host is a new adapter class, not another `if host == ...` in a gate.

Interface (see HostAdapter): capabilities · spawn / wait / send / cancel (the dispatch
vocabulary the supervisor must use; the model performs them) · subscribe · resolve_model ·
resolve_transcript · usage · context_pressure · compact · rotate_session · worktree_create /
worktree_remove / worktree_diff · install_gate / enforce_gate · tool_policy.

Gate equivalence: enforce_gate is `native` (the host blocks on the hook's exit code) or
`outer` (the harness blocks around the host). It is never `warn`: a BLOCK is never
downgraded (ROADMAP §7).
"""
import json
import os
import subprocess

import _paths
import _routes
import _usage

HERE = os.path.dirname(os.path.realpath(__file__))
METHODS = ("capabilities", "spawn", "wait", "send", "cancel", "subscribe", "resolve_model", "resolve_transcript",
           "usage", "context_pressure", "compact", "rotate_session", "worktree_create", "worktree_remove",
           "worktree_diff", "install_gate", "enforce_gate", "tool_policy")


class Unsupported(Exception):
    """The host has no way to do this; the caller takes the correctness path, never a guess."""


def _caps():
    with open(os.path.join(HERE, "..", "hosts", "capabilities.json")) as f:
        return json.load(f)["hosts"]


def _sh(*args, cwd=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or "exit %d" % r.returncode)
    return r.stdout.strip()


class HostAdapter:
    name = ""
    # the supervisor's dispatch vocabulary on this host (the model performs these)
    SPAWN = WAIT = SEND = CANCEL = ""
    SUBSCRIBE = "sweep wait --timeout S   (token-free: wakes on the next event or attempt change)"
    ISOLATION = ""          # how a worker is confined to its worktree
    INSTALL_GATE = ""       # how the hooks reach the host
    ENFORCE_GATE = "native"  # native | outer — never warn
    TOOL_POLICY = ""        # what the hooks deny a worker

    # ── capabilities ────────────────────────────────────────────────
    def capabilities(self):
        return _caps()[self.name]

    def advertises(self, cap):
        return self.capabilities().get(cap) == "advertised"

    # ── lifecycle vocabulary ────────────────────────────────────────
    def spawn(self):
        return self.SPAWN

    def wait(self):
        return self.WAIT

    def send(self):
        if not self.advertises("live_messaging"):
            raise Unsupported("%s has no live messaging to a running worker (the soft time stop is telemetry)" % self.name)
        return self.SEND

    def cancel(self):
        return self.CANCEL

    def subscribe(self):
        return self.SUBSCRIBE

    # ── models, transcripts, usage ──────────────────────────────────
    def resolve_model(self, group, cwd=None):
        return _routes.resolve(self.name, group, cwd=cwd)

    def resolve_transcript(self, payload, agent_id):
        return _usage.agent_transcript(payload, agent_id)

    def usage(self, transcript):
        c = _usage.classes(transcript)
        return c if c is not None else {"accuracy": "none", "unreported": ["all"]}

    def context_pressure(self, transcript):
        """Context in use on the latest call; the window share needs the model's window (open)."""
        c = _usage.classes(transcript)
        return None if c is None else c["context"]

    # ── context boundary ────────────────────────────────────────────
    def compact(self):
        if not self.advertises("compaction"):
            raise Unsupported("%s does not advertise compaction: rotate instead" % self.name)
        return "compact the session (advertised by %s)" % self.name

    def rotate_session(self):
        return self.capabilities().get("rotate_command") or "/clear"

    # ── worktrees (shared: task-worktree, the fixed layout on every host) ──
    def worktree_create(self, task, cwd=None, base="HEAD"):
        return _sh(os.path.join(HERE, "task-worktree"), "add", task, base, cwd=cwd)

    def worktree_remove(self, task, cwd=None):
        return _sh(os.path.join(HERE, "task-worktree"), "remove", task, cwd=cwd)

    def worktree_diff(self, task, cwd=None):
        """Paths the task's branch changed against the main tree's HEAD (committed or not)."""
        wt = _sh(os.path.join(HERE, "task-worktree"), "path", task, cwd=cwd)
        root = _paths.project(cwd)
        base = _sh("git", "-C", wt, "merge-base", "HEAD", _sh("git", "-C", root, "rev-parse", "HEAD"))
        out = set(_sh("git", "-C", wt, "diff", "--name-only", base, "HEAD").split())
        out |= set(_sh("git", "-C", wt, "diff", "--name-only").split())
        out |= set(_sh("git", "-C", wt, "ls-files", "--others", "--exclude-standard").split())
        return sorted(p for p in out if p)

    # ── gates ───────────────────────────────────────────────────────
    def install_gate(self):
        return self.INSTALL_GATE

    def enforce_gate(self):
        assert self.ENFORCE_GATE in ("native", "outer"), "a BLOCK is never downgraded to WARN"
        return self.ENFORCE_GATE

    def tool_policy(self):
        return self.TOOL_POLICY

    def describe(self, cwd=None):
        d = {"host": self.name, "capabilities": self.capabilities(), "isolation": self.ISOLATION}
        for m in ("spawn", "wait", "send", "cancel", "subscribe", "compact", "rotate_session", "install_gate",
                  "enforce_gate", "tool_policy"):
            try:
                d[m] = getattr(self, m)()
            except Unsupported as e:
                d[m] = {"unsupported": str(e)}
        d["models"] = {g: self.resolve_model(g, cwd) for g in ("planning", "worker")}
        return d


class Claude(HostAdapter):
    """The reference implementation: the host gives hooks, sub-agents and worktrees natively."""
    name = "claude"
    SPAWN = "Agent tool: subagent_type: <role>, model: <aloop models resolve group>, run_in_background: true, isolation: \"worktree\""
    WAIT = "task notification (run_in_background) or `budget watch`"
    SEND = ""
    CANCEL = "TaskStop <agent>, then `budget mark-killed <agent_id>`"
    ISOLATION = "host-native: isolation: \"worktree\" creates .claude/worktrees/agent-<id> at dispatch"
    INSTALL_GATE = "plugin/hooks/hooks.json, loaded by the plugin install (/plugin install)"
    ENFORCE_GATE = "native"
    TOOL_POLICY = "gate-supervisor-scope · gate-commit-author · budget hook pre (PreToolUse, exit 2 denies)"


class Codex(HostAdapter):
    name = "codex"
    SPAWN = "spawn_agent: agent_type: <role>; the prompt's first line names the task and its worktree"
    WAIT = "wait_agent (several spawn_agent, then wait_agent)"
    CANCEL = "close_agent, then `aloop budget mark-killed <agent_id>`"
    ISOLATION = "aloop task-worktree add <TASK>; the worker's first command is `aloop bind <path>` (host-adapter confines it)"
    INSTALL_GATE = "codex plugin add aloop@aloop-marketplace, then trust the hooks once in /hooks"
    ENFORCE_GATE = "native"
    TOOL_POLICY = "host-adapter hook: writers denied until bound; then every command/patch must stay under the worktree"


class OpenCode(HostAdapter):
    name = "opencode"
    SPAWN = "task tool: subagent_type: <role>; the prompt names the task and its worktree"
    WAIT = "several task calls in one message; the plugin reports each result"
    CANCEL = "the plugin aborts the session, then `aloop budget mark-killed <agent_id>`"
    ISOLATION = "aloop task-worktree add <TASK>; `aloop bind <path>`; the plugin rewrites commands/edits into it"
    INSTALL_GATE = "aloop install opencode writes .opencode/plugins/aloop.js, agents and skills"
    ENFORCE_GATE = "outer"      # the plugin runs the gates around the host's tool calls and sub-agent ends
    TOOL_POLICY = "aloop.js: tool.execute.before rewrites or denies; a gate's exit 2 denies the call"


class Dsh(HostAdapter):
    """DeepSeek Harness: roles are agent presets aloop-<role>; hosts/dsh/aloop.mjs runs the gates."""
    name = "dsh"
    SPAWN = ("subagent (red-worker, scaffolder, green-worker, structural-reviewer, final-gate) or subagent_planning "
             "(intake, standards, planner, archivist): agent_preset: aloop-<role>, description, prompt opening with "
             "`TASK_ID: <id>` and the worktree path, cwd: <worktree> for a writer")
    WAIT = "start the delegations together, then end your turn: each `subagent finished` notice wakes you (no wait tool)"
    SEND = ("send_message agent_id: <the role's id from `started subagent <id>`>, message: <text>: a working role reads "
            "it at its next step, an idle one starts a turn with it (a role itself is not offered send_message)")
    CANCEL = "interrupt_agent <agent_id>, then `aloop budget mark-killed <agent_id>`"
    ISOLATION = ("aloop task-worktree add <TASK>; dispatch with cwd: <worktree>; the worker's first command is "
                 "`aloop bind <path>`; host-adapter then denies any command or path outside it (dsh cannot rewrite a call)")
    INSTALL_GATE = "aloop install dsh writes the aloop profile ($DSH_HOME/profiles/aloop): run `dsh --profile aloop`"
    ENFORCE_GATE = "native"     # a block is dsh's own deny / post-execute block / steer before the child settles
    TOOL_POLICY = "aloop.mjs: tools/pre-execute denies on a gate's exit 2; a writer is denied until bound, then confined"


ADAPTERS = {"claude": Claude, "codex": Codex, "opencode": OpenCode, "dsh": Dsh}


def adapter(name=None):
    name = name or os.environ.get("AGENTIC_HOST") or "claude"
    if name not in ADAPTERS:
        raise ValueError("unknown host %r (known: %s)" % (name, ", ".join(sorted(ADAPTERS))))
    return ADAPTERS[name]()


def conforms(cls):
    """Every method of the interface is present and callable on the adapter."""
    return [m for m in METHODS if not callable(getattr(cls, m, None))]
