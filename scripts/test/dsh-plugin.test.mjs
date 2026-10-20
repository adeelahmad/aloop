// The DeepSeek Harness (dsh) plugin against an emulated Cordis context: the REAL plugin
// (plugin/hosts/dsh/aloop.mjs) and the REAL bin/ scripts in a throw-away git repo. No dsh, no
// network, no tokens.   node scripts/test/dsh-plugin.test.mjs
// dsh-runtime.test.mjs runs the same plugin on dsh itself; this one covers what a scripted model
// cannot reach cheaply: the budget limits, the STUCK release, the cancel, the fallbacks.
import { execFileSync } from "node:child_process"
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import assert from "node:assert/strict"
import { approvePlan } from "./fixtures/plan-fixture.mjs"

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
      ? { get: (session) => wd.get(session?.header?.id), set: async (agent, p) => { wd.set(agent.session.header.id, p); return p },
          // dsh's ensure(): a directory that vanished is replaced by the session's first one (header.cwd)
          ensure: async (agent) => { const h = agent.session.header, c = wd.get(h.id); if (c && existsSync(c)) return c; wd.set(h.id, h.cwd); return h.cwd } }
      : undefined,
  }
  return { on, wd, ctx, config }
}
// session.append records what a plugin writes straight into the log (as dsh's own plugins do)
const agentOf = (id, header = {}) => ({ session: { header: { id, cwd: T, ...header }, appended: [], append(type, data, intent) { this.appended.push({ type, data, intent }) } },
  injected: [], steered: [], cancelled: [],
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
// dsh's tools service as one agent's scope sees it (send_message visible); restrict() calls recorded
const scoped = (agent) => {
  const restricted = []
  agent.ctx = { get: (n) => n === "tools" ? { get: (name, a) => (a === agent && name === "send_message" ? { name } : undefined), restrict: (f) => restricted.push(f) } : undefined }
  return restricted
}
const compaction = (agent, data = {}) => h.on["session/event"](agent.session, { type: "compaction/end", seq: 9, time: Date.now(), data: { compactionId: "c1", turn: 1, ...data } })

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
const rootTools = scoped(root)
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
assert.ok(denied(await pre(h, root, "write", { file_path: join(T, "src/main.rs"), content: "x", agent_type: "red-worker" }), /may not write production source/))
assert.ok(denied(await pre(h, root, "write", { file_path: join(T, "src/main.rs"), content: "x", x: { file_path: join(story, "init.md") } }), /may not write production source/))
assert.ok(denied(await pre(h, root, "write", { file_path: join(story, "tasks.md"), content: "x", agent_type: "planner" }), /dispatch the planning activity/))
ok("arguments the model adds (agent_type, a nested file_path) never reach a gate as hook fields")
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
assert.ok(denied(await pre(h, root, "subagent", { ...red, cwd: wt }), /has not approved the plan of S1/))
approvePlan(T, BIN)                     // the human's go on sprint 1 (M0.9)
assert.ok(allowed(await pre(h, root, "subagent", { ...red, cwd: wt })))
assert.ok(readFileSync(join(wt, ".aladdin/task.env"), "utf8").includes("STORY_DIR="), "gate-dispatch copies the contract into the worktree")
ok("dispatch: no role, no TASK_ID line, no task contract or no plan approval is refused; a complete, approved one is admitted")
// the one profile's tool models are another project's (or from before MODEL_WORKER changed): a role
// still runs on THIS project's model, so gate-dispatch admits it and every request of the child uses it
const stale = host({ root: ROOT, project: "/elsewhere", models: { subagent: "deepseek-v4-pro", subagent_planning: "deepseek-flash" } })
plugin.apply(stale.ctx, stale.config)
const sroot = agentOf("sroot")
await stale.on["agent/created"]({ agent: sroot, source: "startup" })
assert.ok(allowed(await pre(stale, sroot, "subagent", { ...red, cwd: wt })))
const request = (hh, agent, config) => hh.on["agent/request"]({ agent, turn: 1, step: 1, signal: new AbortController().signal }, async () => Object.freeze({ ...config }))
const profiled = { provider: "deepseek-official", model: "deepseek-v4-pro", reasoningEffort: "high", maxTokens: 9000 }
const sred = childOf("sred", "red-worker", { parentSession: "sroot" }), splan = childOf("splan", "planner", { parentSession: "sroot" })
assert.deepEqual(await request(stale, sred, profiled), { provider: "deepseek-official", model: "deepseek-flash", maxTokens: 9000 })
assert.deepEqual(await request(stale, splan, { provider: "deepseek-official", model: "deepseek-flash" }), { provider: "deepseek-official", model: "deepseek-v4-pro" })
assert.deepEqual(await request(stale, sroot, profiled), profiled)
ok("a role runs on its project's model (`aloop models resolve`), not the profile's: dispatch admitted, every request routed to it (a new route takes its own effort); the supervisor's own route is untouched")

// SubagentStart, delegation, isolation
const child = childOf("child", "red-worker")
const childTools = scoped(child)
await h.on["agent/created"]({ agent: child, source: "startup" })
const reg = () => JSON.parse(readFileSync(join(T, ".aladdin/state/agents/child.json"), "utf8"))
assert.ok(reg().agent_type === "red-worker" && reg().host === "dsh" && reg().started); ok("SubagentStart: the role is registered from its agent preset")
assert.deepEqual([childTools, rootTools], [[{ deny: ["send_message"] }], []])
ok("SubagentStart: send_message is scoped out of the role's own tools (so dsh adds no return guidance); the supervisor keeps it")

// a compaction dsh committed on the supervisor's session is SessionStart(compact): recorded, and the
// handoff rejoins the session; a failed one, or a role's, is not
const sup = agentOf("root-compacted")
await h.on["agent/created"]({ agent: sup, source: "startup" })
compaction(child); compaction(sup, { error: { name: "Error", message: "summary failed" } }); compaction(sup)
// the next request after it (dsh's overflow retry, or the step a pressure compaction ran before) carries it,
// appended to the log before dsh builds that request; a later request does not repeat it
await request(h, sup, profiled); await request(h, sup, profiled)
const compacts = readFileSync(join(T, ".aladdin/logs/events.jsonl"), "utf8").split("\n").filter((l) => /"event": "context.compacted"/.test(l))
const handed = sup.session.appended.filter((e) => e.type === "user/message")
assert.ok(sup.injected.length === 1 && handed.length === 1 && handed[0].intent?.surfaceOp === "append" && handed[0].data.source.kind === "aloop"
  && /NEXT-MARKER/.test(textOf(handed[0].data)) && /call `aloop <tool>`/.test(textOf(handed[0].data))
  && !child.injected.length && !child.session.appended.length && compacts.length === 1,
  JSON.stringify([sup.injected.length, sup.session.appended, child.injected.length, compacts]))
ok("compaction/end of the supervisor's session: context.compacted recorded once, the handoff and tools note join the first request after it, once")

// dsh's own calls outside the agent loop (a `purpose`: the compaction summary, the session title) are billed
// but reach no assistant/message: each one's usage is read from its own stream into the session's log (aux)
const chunks = (u) => (async function* () {
  yield { type: "text-delta", index: 0, text: "t" }
  if (u) yield { type: "usage", usage: u }
  yield { type: "finish", reason: { kind: "stop" } }
})()
const llm = async (options, u) => {
  const seen = []
  for await (const c of h.on["llm/stream"]({ provider: "deepseek-official", model: "deepseek-flash", ...options }, () => chunks(u))) seen.push(c)
  return seen
}
const callUsage = { inputTokens: 300, outputTokens: 20, cacheReadTokens: 40, cacheWriteTokens: 0, totalTokens: 360 }
const relayed = await llm({ purpose: "session-title", sessionId: "root-compacted" }, callUsage)
await llm({ purpose: "compaction", sessionId: "root-compacted" }, callUsage)
await llm({ sessionId: "root-compacted" }, callUsage)                              // a loop request: its assistant/message logs it
await llm({ purpose: "session-title", sessionId: "root-compacted" }, null)        // a stream that reported no usage
await llm({ purpose: "session-title", sessionId: "not-an-aloop-session" }, callUsage)
const auxRows = readFileSync(join(T, ".aladdin/host/dsh/root-compacted.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l))
assert.ok(relayed.map((c) => c.type).join() === "text-delta,usage,finish" && auxRows.map((r) => r.aux).join() === "session-title,compaction"
  && auxRows.every((r) => r.message.model === "deepseek-flash" && r.message.id.startsWith("root-compacted:") && r.timestamp)
  && new Set(auxRows.map((r) => r.message.id)).size === 2 && !existsSync(join(T, ".aladdin/host/dsh/not-an-aloop-session.jsonl")), JSON.stringify(auxRows))
assert.deepEqual(auxRows[0].message.usage, { input_tokens: 300, output_tokens: 20, cache_read_input_tokens: 40, cache_creation_input_tokens: 0 })
ok("a compaction or session-title call's usage is logged Claude-shaped from its own stream (aux), its chunks passed on unchanged; a loop request is not logged twice")
assert.ok(denied(await pre(h, child, "subagent", red), /does not delegate/)); ok("a role may not delegate")
assert.ok(denied(await pre(h, child, "send_message", { agent_id: "root", message: "RED done, ready to merge" }), /does not message the supervisor/))
assert.ok(allowed(await pre(h, root, "send_message", { agent_id: "child", message: "status?" })))
ok("a role's result reaches the supervisor only through its stop gate (no send_message); the supervisor may message a role")
assert.ok(denied(await pre(h, child, "bash", { command: "ls" }), /bind your worktree first/)); ok("an unbound writer is refused")
assert.ok(allowed(await pre(h, child, "bash", { command: `aloop bind ${wt}` })))
await post(h, child, "bash", { command: `aloop bind ${wt}` }, [{ type: "text", text: "bound" }])
assert.ok(reg().worktree === wt && h.wd.get("child") === wt); ok("after `aloop bind` the worker's working directory moves into its worktree")
assert.ok(allowed(await pre(h, child, "bash", { command: "cargo test" })))
assert.ok(allowed(await pre(h, child, "write", { file_path: "tests/a.rs", content: "x" })))
assert.ok(denied(await pre(h, child, "write", { file_path: join(T, "src/lib.rs"), content: "x" }), /inside your worktree/))
assert.ok(allowed(await pre(h, child, "read", { file_path: join(story, "init.md") })))
ok("a bound writer: commands and relative edits in the worktree; edits outside it denied; reads anywhere")
mkdirSync(join(wt, "gone")); h.wd.set("child", join(wt, "gone")); rmSync(join(wt, "gone"), { recursive: true })
assert.ok(denied(await pre(h, child, "write", { file_path: "VANISH.txt", content: "x" }), /edit only files inside your worktree/))
assert.ok(denied(await pre(h, child, "bash", { command: "touch VANISH" }), /inside your worktree/) && h.wd.get("child") === T)
ok("a working directory that vanished is judged where dsh's tools will run (the session's first one, the main tree)")
h.wd.set("child", wt)

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
process.env.AGENTIC_STUCK_BLOCKS = "99"
await step(h, child, [userMsg("once more")])
const steers = child.steered.length
await h.on["agent/turn-stopping"]({ agent: child, turn: 1, signal: AbortSignal.abort() })
assert.ok(child.steered.length === steers && /red-worker child: BLOCKED \(interrupted while its gate ran\)/.test(textOf(root.injected.at(-1))), textOf(root.injected.at(-1)))
ok("a worker interrupted while its gate ran is released with the gate's verdict, never steered back to work")
delete process.env.AGENTIC_STUCK_BLOCKS

// a child whose turn ended without a stop boundary (cancelled) still gets its bookkeeping
const c2 = childOf("child2", "planner")
await h.on["agent/created"]({ agent: c2, source: "startup" })
await h.on["subagent/end"]({ id: "child2", provider: "spawn", local: true, stopReason: "aborted" })
assert.ok(JSON.parse(readFileSync(join(T, ".aladdin/state/agents/child2.json"), "utf8")).stopped); ok("subagent/end without a stop boundary: SubagentStop bookkeeping still runs")

// a mission role (ROADMAP §4.12): its start is recorded, its path guard denies, its stop gate checks its artifacts
const scout = childOf("scout", "market-researcher")
await h.on["agent/created"]({ agent: scout, source: "startup" })
assert.equal(JSON.parse(readFileSync(join(T, ".aladdin/state/mission-runs/scout.json"), "utf8")).role, "market-researcher")
ok("SubagentStart: a mission role's start is recorded (gate-mission-artifact start)")
assert.ok(denied(await pre(h, scout, "write", { file_path: join(T, "src/main.rs"), content: "x" }), /market-researcher writes only/))
assert.ok(denied(await pre(h, scout, "bash", { command: "echo x > src/main.rs" }), /market-researcher writes only/))
assert.ok(denied(await pre(h, scout, "bash", { command: "dsh --profile aloop 'approve msn_x 1234abcd'" }), /starts a dsh session/))
assert.ok(allowed(await pre(h, scout, "write", { file_path: join(T, "docs/agents/mission/research/landscape.md"), content: "x" })))
ok("gate-mission-scope: a mission role writes only its own paths and never starts a dsh session")
await stop(h, scout)
assert.ok(scout.steered.length === 1 && /\[aloop gate gate-mission-artifact\] Your stop was blocked/.test(textOf(scout.steered[0])), JSON.stringify(scout.steered))
await h.on["agent/turn-stopping"]({ agent: scout, turn: 1, signal: AbortSignal.abort() })
assert.ok(/market-researcher scout: BLOCKED/.test(textOf(root.injected.at(-1))), textOf(root.injected.at(-1)))
ok("SubagentStop: a mission role that wrote no artifact is steered back by gate-mission-artifact, then reported BLOCKED")

// an intake that stops to ask the human (ROADMAP §4.9): released at once as NEEDS-HUMAN with its questions,
// never steered for rounds it cannot use; a malformed Intent Contract is still steered back
writeFileSync(join(T, "docs/agents/sprint1/intake.md"), "# Intent\nWhat: a parser\n## Constraints\n## Failure scenarios\n## Success scenarios\n## Connections\n")
const ic = sh(join(BIN, "intent"), ["new", "--source", "prompt", "--raw", "a semver parser"]).trim()
const contract = { ...JSON.parse(readFileSync(ic, "utf8")), actor: "dev", want: "parse semver", why: "releases", constraints: ["std only"],
  success_scenarios: ["1.2.3 parses"], failure_scenarios: ["bad input errors"], connections: ["none"], done_looks_like: "parse() works",
  open_questions: [{ question: "accept a leading v (v1.2.3)?", class: "ask" },
    { question: "Should orders STUCK in review (NOT verified by finance) be exported?", class: "ask" }] }
writeFileSync(ic, JSON.stringify(contract))
const ask = childOf("ask", "intake")
await h.on["agent/created"]({ agent: ask, source: "startup" })
await stop(h, ask)
const askTold = textOf(root.injected.at(-1))
assert.ok(!ask.steered.length && /intake ask: NEEDS-HUMAN/.test(askTold) && /accept a leading v/.test(askTold) && /not a pass/.test(askTold), askTold)
// the release is read from the gate's own marker line, not from a question that quotes STUCK … NOT verified, and the
// record is claimed once told (a later Claude Code or Codex session is not told again)
assert.ok(!/STUCK — released/.test(askTold) && /"Should orders STUCK in review \(NOT verified by finance\) be exported\?"/.test(askTold), askTold)
assert.deepEqual(readdirSync(join(T, ".aladdin/state/needs-human")).filter((n) => n.endsWith(".json")), [])
ok("an intake whose contract only waits on the human is released at once; the supervisor is told NEEDS-HUMAN with its questions (quoted, claimed once)")
writeFileSync(ic, JSON.stringify({ ...contract, want: "" }))
await step(h, ask, [userMsg("again")])
await stop(h, ask)
assert.ok(ask.steered.length === 1 && /is invalid/.test(textOf(ask.steered[0])), JSON.stringify(ask.steered))
ok("a malformed Intent Contract still steers the intake back to work")

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
assert.ok(denied(await pre(broken, agentOf("root2"), "subagent", { ...red, cwd: wt }), /could not resolve red-worker's model/))
await assert.rejects(request(broken, childOf("bchild", "red-worker", { parentSession: "root2" }), profiled), /no model for red-worker/)
ok("a role whose project's model cannot be resolved is neither dispatched nor run on the profile's model (fail closed)")

// a gate runs the worker's code, so it gets the worker shell's environment: dsh's credential scrub,
// aloop's own settings kept, and the plugin root the gates read their schemas from
const FR = join(T, "fake-root")
mkdirSync(join(FR, "bin"), { recursive: true })
writeFileSync(join(FR, "bin", "host-adapter"), `#!/bin/sh\nenv > "${join(FR, "env.txt")}"\n`); chmodSync(join(FR, "bin", "host-adapter"), 0o755)
Object.assign(process.env, { DEEPSEEK_API_KEY: "sk-secret", GITHUB_TOKEN: "ghp-secret", DSH_SESSION_ID: "s1", TOKENS_SOFT: "5", AGENTIC_TOKENS_X: "1",
  GIT_CONFIG_COUNT: "1", GIT_CONFIG_KEY_0: "core.pager", GIT_CONFIG_VALUE_0: "cat" })
const fr = host({ root: FR })
plugin.apply(fr.ctx, fr.config)
await pre(fr, agentOf("root3"), "bash", { command: "ls" })
const genv = readFileSync(join(FR, "env.txt"), "utf8")
for (const k of ["DEEPSEEK_API_KEY", "GITHUB_TOKEN", "DSH_SESSION_ID", "DSH_HOME"]) assert.ok(!new RegExp(`^${k}=`, "m").test(genv), k)
for (const kv of ["AGENTIC_HOST=dsh", `CLAUDE_PLUGIN_ROOT=${FR}`, "TOKENS_SOFT=5", "AGENTIC_TOKENS_X=1", `HOME=${process.env.HOME}`, "GIT_CONFIG_KEY_0=core.pager"]) assert.ok(genv.includes(`\n${kv}\n`) || genv.startsWith(`${kv}\n`), kv)
for (const k of ["DEEPSEEK_API_KEY", "GITHUB_TOKEN", "DSH_SESSION_ID", "TOKENS_SOFT", "AGENTIC_TOKENS_X", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"]) delete process.env[k]
ok("a gate never sees the API key (dsh's shell scrub), keeps aloop's settings and git's config set whole, and gets the plugin root")

// a gate switch dsh loaded from the launch directory's .env (it may come with a clone) never reaches a gate;
// one the user exported at launch does
const LD = join(T, "launch"); mkdirSync(LD)
writeFileSync(join(LD, ".env"), "SKIP_HOOKS=1\nGATE_RUN_TESTS=0\nREPO_DIR=/elsewhere\nAGENTIC_NO_BUDGET=1\nDATABASE_URL=pg://x\nSCOPE_GLOBS=*\n")
Object.assign(process.env, { SKIP_HOOKS: "1", GATE_RUN_TESTS: "0", REPO_DIR: "/elsewhere", AGENTIC_NO_BUDGET: "1", DATABASE_URL: "pg://x", SCOPE_GLOBS: "src/**" })
const here = process.cwd(); process.chdir(LD)
const fe = host({ root: FR })
plugin.apply(fe.ctx, fe.config)
process.chdir(here)
await pre(fe, agentOf("root4"), "bash", { command: "ls" })
const fenv = readFileSync(join(FR, "env.txt"), "utf8")
for (const k of ["SKIP_HOOKS", "GATE_RUN_TESTS", "REPO_DIR", "AGENTIC_NO_BUDGET"]) assert.ok(!new RegExp(`^${k}=`, "m").test(fenv), k)
assert.ok(/^DATABASE_URL=pg:\/\/x$/m.test(fenv) && /^SCOPE_GLOBS=src\/\*\*$/m.test(fenv), fenv)
for (const k of ["SKIP_HOOKS", "GATE_RUN_TESTS", "REPO_DIR", "AGENTIC_NO_BUDGET", "DATABASE_URL", "SCOPE_GLOBS"]) delete process.env[k]
ok("a gate switch from the project's .env (SKIP_HOOKS, GATE_RUN_TESTS, REPO_DIR, AGENTIC_*) never reaches a gate; an exported one and other names do")

// the one dsh profile was installed for another project: its role models are this one's anyway, but the
// aloop skills dsh loads live in each project's .dsh/skills (or $DSH_HOME/skills with --global)
const fo = host({ root: ROOT, project: "/elsewhere", models: { subagent: "deepseek-flash", subagent_planning: "deepseek-v4-pro" } })
plugin.apply(fo.ctx, fo.config)
const r6 = agentOf("root6")
await fo.on["agent/created"]({ agent: r6, source: "startup" })
assert.ok(/no aloop skills for dsh.*aloop install dsh --project/s.test(textOf(r6.injected[0])) && !/role models are that project's/.test(textOf(r6.injected[0])), textOf(r6.injected[0]))
for (const [i, skills] of [join(T, ".dsh/skills/aloop"), join(process.env.DSH_HOME, "skills/aloop")].entries()) {
  mkdirSync(skills, { recursive: true }); writeFileSync(join(skills, "SKILL.md"), "x\n")
  const r6b = agentOf(`root6-${i}`)
  await fo.on["agent/created"]({ agent: r6b, source: "startup" })
  assert.ok(!/aloop install dsh/.test(textOf(r6b.injected[0])), textOf(r6b.injected[0]))
  rmSync(skills, { recursive: true })
}
const fs = host({ root: ROOT, project: realpathSync(T), models: {} })
plugin.apply(fs.ctx, fs.config)
const r7 = agentOf("root7")
await fs.on["agent/created"]({ agent: r7, source: "startup" })
assert.ok(!/aloop install dsh/.test(textOf(r7.injected[0])))
ok("a session in a project the dsh profile was not installed for is told to install the aloop skills there only when it has none (its own or --global)")

// a project whose route is a Claude id resolves to "" on dsh (the session's own model): its role runs on
// the supervisor's route, never on the profile's (another project's); a pinned provider/model splits at the first "/"
const U = mkdtempSync(join(tmpdir(), "aloop-dsh-u-"))
process.on("exit", () => rmSync(U, { recursive: true, force: true }))
for (const c of [["init", "-q"], ["config", "user.email", "ada@x.io"], ["config", "user.name", "Ada"], ["commit", "-q", "--allow-empty", "-m", "init"]])
  execFileSync("git", c, { cwd: U })
execFileSync(join(BIN, "aloop-init"), ["--apply", "MODEL_PLANNING=opus", "MODEL_WORKER_DSH=openrouter/deepseek/deepseek-chat-v3"], { cwd: U })
const uroot = agentOf("uroot", { cwd: U })
uroot.session.requestHeader = () => ({ config: { provider: "deepseek-official", model: "deepseek-flash", reasoningEffort: "max" } })
await stale.on["agent/created"]({ agent: uroot, source: "startup" })
const uplan = childOf("uplan", "intake", { parentSession: "uroot", cwd: U }), ugreen = childOf("ugreen", "green-worker", { parentSession: "uroot", cwd: U })
assert.deepEqual(await request(stale, uplan, profiled), { provider: "deepseek-official", model: "deepseek-flash", reasoningEffort: "max", maxTokens: 9000 })
assert.deepEqual(await request(stale, ugreen, profiled), { provider: "openrouter", model: "deepseek/deepseek-chat-v3", maxTokens: 9000 })
ok("a role whose project leaves its model to the session runs on the supervisor's route; a provider-qualified pin is routed to that provider")

// an interrupted supervisor Stop still delivers the sprint-close notice (stats announces it once)
const r5 = agentOf("root5")
writeFileSync(join(T, ".aladdin/state/pending-close"), "Sprint 2 closed. Tokens — sprint: 1k\n")
await h.on["agent/turn-stopping"]({ agent: r5, turn: 1, signal: AbortSignal.abort() })
assert.ok(!r5.steered.length && r5.injected.some((m) => /Sprint 2 closed/.test(textOf(m))), JSON.stringify(r5.injected))
ok("an interrupted supervisor Stop queues the sprint-close notice for its next turn instead of dropping it")

console.log(`\n${pass} checks passed`)
