// The DeepSeek Harness (dsh) plugin against an emulated Cordis context: the REAL plugin
// (plugin/hosts/dsh/aloop.mjs) and the REAL bin/ scripts in a throw-away git repo. No dsh, no
// network, no tokens.   node scripts/test/dsh-plugin.test.mjs
// dsh-runtime.test.mjs runs the same plugin on dsh itself; this one covers what a scripted model
// cannot reach cheaply: the budget limits, the STUCK release, the cancel, the fallbacks.
import { execFileSync } from "node:child_process"
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import assert from "node:assert/strict"

const ROOT = join(fileURLToPath(import.meta.url), "..", "..", "..", "plugin")
const BIN = join(ROOT, "bin")
const T = mkdtempSync(join(tmpdir(), "aloop-dsh-"))
process.on("exit", () => rmSync(T, { recursive: true, force: true }))
const sh = (cmd, args) => execFileSync(cmd, args, { cwd: T, encoding: "utf8" })

// isolated HOME + fake backends so gate-tooling passes without a Rust build
process.env.HOME = join(T, "home")
process.env.DSH_HOME = join(T, "dsh-home")
process.env.AGENTIC_NO_AUTOINSTALL = "1"
process.env.CLAUDE_PLUGIN_DATA = join(T, "pdata")
delete process.env.AGENTIC_HOST
mkdirSync(join(process.env.CLAUDE_PLUGIN_DATA, "bin"), { recursive: true })
for (const t of ["md-db", "ctx-symbols"]) {
  const f = join(process.env.CLAUDE_PLUGIN_DATA, "bin", t)
  writeFileSync(f, "#!/bin/sh\nexit 0\n"); chmodSync(f, 0o755)
}
sh("git", ["init", "-q"]); sh("git", ["config", "user.email", "ada@x.io"]); sh("git", ["config", "user.name", "Ada"])
sh("git", ["commit", "-q", "--allow-empty", "-m", "init"])
sh(join(BIN, "aloop-init"), ["--apply", "TOKENS_SOFT=100", "TOKENS_HARD=200", "MODEL_WORKER=economical"])
const story = join(T, "docs/agents/sprint1/s1-01-x")
mkdirSync(story, { recursive: true })
writeFileSync(join(T, "docs/agents/NEXT.md"), "# handoff\nNEXT-MARKER plan sprint 2\n")
const OTHER = mkdtempSync(join(tmpdir(), "aloop-dsh-other-"))       // not an aloop project
process.on("exit", () => rmSync(OTHER, { recursive: true, force: true }))

// the emulated Cordis context: one listener per event, a working-directory service
function host(config) {
  const on = {}, wd = new Map()
  const ctx = {
    on: (ev, fn) => { on[ev] = fn },
    get: (name) => name === "workingDirectory"
      ? { get: (session) => wd.get(session?.header?.id), set: async (agent, p) => { wd.set(agent.session.header.id, p); return p } } : undefined,
  }
  return { on, wd, ctx, config }
}
const agentOf = (id, header = {}) => ({ session: { header: { id, cwd: T, ...header } }, injected: [], steered: [], cancelled: [],
  inject(m) { this.injected.push(m) }, steer(m) { this.steered.push(m) }, cancel(c) { this.cancelled.push(c) } })
const childOf = (id, role, extra = {}) => agentOf(id, { origin: "subagent", parentSession: "root", agentPreset: `aloop-${role}`, ...extra })
const userMsg = (text, kind = "user") => ({ role: "user", id: "u1", content: [{ type: "text", text }], source: { kind } })
let call = 0
const pre = (h, agent, name, args) => h.on["tools/pre-execute"]({ name, arguments: args, agent, callId: `c${++call}` }, async () => ({ kind: "allow" }))
const post = (h, agent, name, args, content = [{ type: "text", text: "ok" }]) =>
  h.on["tools/post-execute"]({ name, arguments: args, agent, callId: `c${++call}` }, { isError: false, content }, async () => ({ kind: "accept" }))
const stop = (h, agent) => h.on["agent/turn-stopping"]({ agent, turn: 1 })
const step = (h, agent, messages) => h.on["agent/pre-step"]({ agent, messages, turn: 1, step: 1 }, async () => ({ kind: "enter", messages }))
const usage = (h, agent, id, inputTokens, outputTokens, extra = { cacheReadTokens: 0, cacheWriteTokens: 0 }) =>
  h.on["session/event"](agent.session, { type: "assistant/message", seq: 1, time: Date.now(),
    data: { turn: 1, step: 1, message: { id, role: "assistant", content: [], source: { kind: "model", model: "deepseek-flash" } },
      usage: { inputTokens, outputTokens, ...extra } } })
const textOf = (m) => m.content.map((b) => b.text).join("\n")

const plugin = await import(join(ROOT, "hosts", "dsh", "aloop.mjs"))
assert.equal(plugin.name, "aloop"); assert.deepEqual(plugin.inject, [])
process.env.AGENTIC_STUCK_BLOCKS = "99"      // the stuck release is tested on its own below
const h = host({ root: ROOT, maxStopBlocks: 3, models: { subagent: "deepseek-flash", subagent_planning: "deepseek-v4-pro" } })
plugin.apply(h.ctx, h.config)
let pass = 0
const ok = (name) => { pass++; console.log("ok -", name) }
const allowed = (d) => d.kind === "allow"
const denied = (d, re) => d.kind === "deny" && re.test(d.reason)

assert.equal(process.env.AGENTIC_HOST, "dsh"); ok("the plugin names its host for every shell the model runs (AGENTIC_HOST=dsh)")

// SessionStart: the handoff is injected into the root session as an aloop message
const root = agentOf("root")
await h.on["agent/created"]({ agent: root, source: "startup" })
assert.equal(root.injected.length, 1)
const start = root.injected[0]
assert.ok(/NEXT-MARKER/.test(textOf(start)) && /call `aloop <tool>`/.test(textOf(start)) && start.source.kind === "aloop" && Object.isFrozen(start.content))
ok("SessionStart: the handoff and the tools note are injected into the session (a frozen, aloop-sourced message)")

// UserPromptSubmit: only a root step that carries the human's message
await step(h, root, [userMsg("[aloop gate] notice", "aloop")])
assert.ok(!existsSync(join(T, ".aladdin/transcripts/global.jsonl")))
const d0 = await step(h, root, [userMsg("build X")])
assert.equal(d0.kind, "enter")
assert.ok(readFileSync(join(T, ".aladdin/transcripts/global.jsonl"), "utf8").includes('"prompt"')); ok("human prompt captured (and only the human's)")

// PreToolUse on the supervisor
assert.ok(allowed(await pre(h, root, "write", { file_path: join(story, "init.md"), content: "x" })))
assert.ok(denied(await pre(h, root, "write", { file_path: join(T, "src/main.rs"), content: "x" }), /may not write production source/))
assert.ok(denied(await pre(h, root, "edit", { file_path: "src/main.rs", old_string: "a", new_string: "b" }), /may not write production source/))
ok("gate-supervisor-scope blocks the supervisor's source edits (relative paths too)")
assert.ok(denied(await pre(h, root, "bash", { command: 'git commit -m "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"' }), /co-author/i))
ok("gate-commit-author blocks a Claude trailer")

// PostToolUse: gate-validate-artifact's block becomes the call's result
writeFileSync(join(story, "init.md"), "no frontmatter\n")
const v = await post(h, root, "write", { file_path: join(story, "init.md"), content: "no frontmatter\n" })
assert.ok(v.kind === "block" && /missing top YAML frontmatter/.test(v.feedback[0].text), JSON.stringify(v))
writeFileSync(join(story, "init.md"), "---\ntype: init\n---\n")
assert.equal((await post(h, root, "write", { file_path: join(story, "init.md"), content: "---\ntype: init\n---\n" })).kind, "accept")
ok("PostToolUse: an invalid artifact write is blocked with the gate's reason; a valid one is accepted")

// dispatch: a role is required, then gate-dispatch's contract and model checks
assert.ok(denied(await pre(h, root, "subagent", { description: "x", prompt: "TASK_ID: S1-01-T1" }), /pass agent_preset/))
assert.ok(denied(await pre(h, root, "subagent", { agent_preset: "aloop-red-worker", description: "x", prompt: "go" }), /TASK_ID: <id>/))
const red = { agent_preset: "aloop-red-worker", description: "RED", prompt: "TASK_ID: S1-01-T1\nworktree …" }
assert.ok(denied(await pre(h, root, "subagent", red), /write the task contract/))
mkdirSync(join(T, ".aladdin/state/tasks"), { recursive: true })
writeFileSync(join(T, ".aladdin/state/tasks/S1-01-T1.env"), `TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=red-worker\nSTORY_DIR=${story}\n`)
const wt = sh(join(BIN, "task-worktree"), ["add", "S1-01-T1"]).trim()
assert.ok(allowed(await pre(h, root, "subagent", { ...red, cwd: wt })))
assert.ok(readFileSync(join(wt, ".aladdin/task.env"), "utf8").includes("STORY_DIR="), "gate-dispatch copies the contract into the worktree")
ok("dispatch: no role, no TASK_ID line or no task contract is refused; a complete one is admitted")
const stale = host({ root: ROOT, models: { subagent: "deepseek-v4-pro" } })     // rendered before MODEL_WORKER changed
plugin.apply(stale.ctx, stale.config)
await stale.on["agent/created"]({ agent: agentOf("root"), source: "startup" })
assert.ok(denied(await pre(stale, agentOf("root"), "subagent", { ...red, cwd: wt }), /dispatch red-worker with model: deepseek-flash/))
ok("gate-dispatch refuses a delegation tool whose model is not the project's (re-run `aloop install dsh`)")

// SubagentStart, delegation, isolation
const child = childOf("child", "red-worker")
await h.on["agent/created"]({ agent: child, source: "startup" })
const reg = () => JSON.parse(readFileSync(join(T, ".aladdin/state/agents/child.json"), "utf8"))
assert.ok(reg().agent_type === "red-worker" && reg().host === "dsh" && reg().started); ok("SubagentStart: the role is registered from its agent preset")
assert.ok(denied(await pre(h, child, "subagent", red), /does not delegate/)); ok("a role may not delegate")
assert.ok(denied(await pre(h, child, "bash", { command: "ls" }), /bind your worktree first/)); ok("an unbound writer is refused")
assert.ok(allowed(await pre(h, child, "bash", { command: `aloop bind ${wt}` })))
await post(h, child, "bash", { command: `aloop bind ${wt}` }, [{ type: "text", text: "bound" }])
assert.ok(reg().worktree === wt && h.wd.get("child") === wt); ok("after `aloop bind` the worker's working directory moves into its worktree")
assert.ok(allowed(await pre(h, child, "bash", { command: "cargo test" })))
assert.ok(allowed(await pre(h, child, "write", { file_path: "tests/a.rs", content: "x" })))
assert.ok(denied(await pre(h, child, "write", { file_path: join(T, "src/lib.rs"), content: "x" }), /inside your worktree/))
assert.ok(allowed(await pre(h, child, "read", { file_path: join(story, "init.md") })))
ok("a bound writer: commands and relative edits in the worktree; edits outside it denied; reads anywhere")

// token ledger + time box, Claude-shaped per role
await usage(h, child, "m1", 120, 10, { cacheReadTokens: 0 })              // no cache-write count reported
const sub = join(T, ".aladdin/host/dsh/root/subagents/agent-child.jsonl")
const rec = JSON.parse(readFileSync(sub, "utf8").trim().split("\n")[0])
assert.deepEqual(rec.message.usage, { input_tokens: 120, output_tokens: 10, cache_read_input_tokens: 0 })
assert.equal(JSON.parse(readFileSync(sub.replace(/\.jsonl$/, ".meta.json"), "utf8")).agentType, "red-worker")
ok("usage written Claude-shaped per role; a class dsh did not report stays absent (unreported), not zero")
const p1 = await post(h, child, "bash", { command: "ls" })
assert.ok(!(p1.additionalContexts || []).length); ok("soft token limit: nothing added for the model (telemetry only)")
assert.ok(/"event": "task.token_soft_limit"/.test(readFileSync(join(T, ".aladdin/logs/events.jsonl"), "utf8"))); ok("task.token_soft_limit recorded")
await usage(h, child, "m2", 400, 10)
const p2 = await post(h, child, "bash", { command: "ls" })
assert.ok((p2.additionalContexts || []).some((m) => /HARD BUDGET/.test(textOf(m))), JSON.stringify(p2)); ok("hard limit: the report instruction joins the next request")
for (let i = 0; i < 3; i++) assert.ok(denied(await pre(h, child, "bash", { command: "ls" }), /HARD BUDGET/))
ok("hard limit: 3 refusals")
assert.ok(allowed(await pre(h, child, "bash", { command: "ls" })))
await post(h, child, "bash", { command: "ls" })
await new Promise((r) => setImmediate(r))
assert.equal(child.cancelled.at(-1)?.kind, "hook"); ok("hard limit: `continue: false` cancels the agent")

// SubagentStop on the child's own stop boundary
await stop(h, child)
assert.ok(!child.steered.length && !root.injected.slice(1).length); ok("a budget-stopped worker is released by its gate, unblocked")
execFileSync("rm", ["-f", join(wt, ".aladdin/budget-exceeded")])
await step(h, child, [userMsg("more work")])                              // a new step: its next stop is gated again
for (let i = 0; i < 3; i++) await stop(h, child)
assert.ok(child.steered.length === 3 && child.steered.every((m) => /\[aloop gate gate-red-verify\] Your stop was blocked/.test(textOf(m))))
assert.equal(root.injected.length, 1)
await stop(h, child)
assert.ok(child.steered.length === 3 && /red-worker child: BLOCKED after 3 fix rounds/.test(textOf(root.injected.at(-1))), textOf(root.injected.at(-1)))
ok("a gate block steers the worker 3x, then the supervisor is told it is BLOCKED")
assert.ok(reg().stopped); ok("SubagentStop registered (host-adapter)")

// the stuck release (default threshold): those identical blocks are logged, so the next one
// releases the worker UNVERIFIED and the supervisor is told so, never a silent pass
delete process.env.AGENTIC_STUCK_BLOCKS
await step(h, child, [userMsg("again")])
const before = child.steered.length
await stop(h, child)
assert.ok(child.steered.length === before && /STUCK — released UNVERIFIED/.test(textOf(root.injected.at(-1))) && /Do not merge/.test(textOf(root.injected.at(-1))))
ok("a worker stuck on one block is released unverified and reported as such, not steered")

// a child whose turn ended without a stop boundary (cancelled) still gets its bookkeeping
const c2 = childOf("child2", "planner")
await h.on["agent/created"]({ agent: c2, source: "startup" })
await h.on["subagent/end"]({ id: "child2", provider: "spawn", local: true, stopReason: "aborted" })
assert.ok(JSON.parse(readFileSync(join(T, ".aladdin/state/agents/child2.json"), "utf8")).stopped); ok("subagent/end without a stop boundary: SubagentStop bookkeeping still runs")

// Stop: a closed sprint is announced once, steered back into the session
mkdirSync(join(T, ".aladdin/state"), { recursive: true })
writeFileSync(join(T, ".aladdin/state/pending-close"), "Sprint 1 closed. Tokens — sprint: 1k\n")
await stop(h, root)
assert.ok(root.steered.length === 1 && /Sprint 1 closed/.test(textOf(root.steered[0])) && /dsh --profile aloop/.test(textOf(root.steered[0])))
ok("sprint close steered into the session, naming dsh's rotate (a new run)")
await stop(h, root)
assert.equal(root.steered.length, 1); ok("announced once")
assert.ok(existsSync(join(T, ".aladdin/ledger/root.jsonl"))); ok("token ledger written for the session")

// outside an aloop project nothing runs; a plugin that cannot run its gates fails closed
const plain = agentOf("plain", { cwd: OTHER })
await h.on["agent/created"]({ agent: plain, source: "startup" })
assert.ok(!plain.injected.length && allowed(await pre(h, plain, "write", { file_path: join(OTHER, "src/x.rs") }))); ok("outside an aloop project the plugin stays out of the way")
const broken = host({ root: join(T, "no-such-plugin") })
plugin.apply(broken.ctx, broken.config)
assert.ok(denied(await pre(broken, agentOf("root2"), "bash", { command: "ls" }), /could not run host-adapter/)); ok("a gate that cannot run denies (fail closed)")

console.log(`\n${pass} checks passed`)
