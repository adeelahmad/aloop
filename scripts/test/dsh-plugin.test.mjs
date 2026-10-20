// The DeepSeek Harness (dsh) plugin against an emulated Cordis context: the REAL plugin
// (plugin/hosts/dsh/aloop.mjs) and the REAL bin/ scripts in a throw-away git repo. No dsh, no
// network, no tokens.   node scripts/test/dsh-plugin.test.mjs
// dsh-runtime.test.mjs runs the same plugin on dsh itself; this one covers what a scripted model
// cannot reach cheaply: the budget limits, the STUCK release, the cancel, the fallbacks.
import { execFileSync } from "node:child_process"
import { createHash } from "node:crypto"
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs"
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
      ? { get: (session) => wd.get(session?.header?.id), set: async (agent, p) => { wd.set(agent.session.header.id, p); return p },
          // dsh's ensure(): a directory that vanished is replaced by the session's first one (header.cwd)
          ensure: async (agent) => { const h = agent.session.header, c = wd.get(h.id); if (c && existsSync(c)) return c; wd.set(h.id, h.cwd); return h.cwd } }
      : undefined,
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
assert.ok(denied(await pre(h, root, "write", { file_path: join(T, "src/main.rs"), content: "x", agent_type: "red-worker" }), /may not write production source/))
assert.ok(denied(await pre(h, root, "write", { file_path: join(T, "src/main.rs"), content: "x", x: { file_path: join(story, "init.md") } }), /may not write production source/))
assert.ok(denied(await pre(h, root, "write", { file_path: join(story, "tasks.md"), content: "x", agent_type: "planner" }), /dispatch the planning activity/))
ok("arguments the model adds (agent_type, a nested file_path) never reach a gate as hook fields")
assert.ok(denied(await pre(h, root, "bash", { command: 'git commit -m "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"' }), /co-author/i))
ok("gate-commit-author blocks a Claude trailer")

// the door rule (M9.30): `mission doors hook` runs last on every tool that can cross a door
assert.ok(denied(await pre(h, root, "bash", { command: "gh pr merge 12 --squash" }), /ASK\[door\] push-default-branch/))
assert.ok(denied(await pre(h, root, "bash", { command: "git push origin main" }), /ASK\[door\] push-default-branch/))
assert.ok(allowed(await pre(h, root, "bash", { command: "git push origin s1-01-x" })))
ok("a one-way door (a merge, a push to the default branch) stops at ASK[door]; a push to a branch is two-way")
assert.ok(denied(await pre(h, root, "bash", { command: "aloop mission doors authorize push-default-branch --by me --reason r" }), /BLOCK\[door\] forge-human-record/))
ok("an agent authorizing a door itself hits a wall (BLOCK[door] forge-human-record)")
const pushFiles = { owner: "o", repo: "r", branch: "main", files: [{ path: "a.txt", content: "x" }], message: "m" }
assert.ok(denied(await pre(h, root, "mcp__github__push_files", pushFiles), /ASK\[door\] push-default-branch/))
assert.ok(denied(await pre(h, root, "mcp__github__push_files", JSON.stringify(pushFiles)), /ASK\[door\] push-default-branch/))
assert.ok(denied(await pre(h, root, "mcp__github__push_files", { ...pushFiles, arguments: JSON.stringify({ ...pushFiles, branch: "x" }) }), /ASK\[door\] push-default-branch/))
assert.ok(allowed(await pre(h, root, "mcp__github__push_files", { ...pushFiles, branch: "s1-01-x" })))
ok("an MCP tool's arguments reach the door gate as its input: push_files to the default branch is asked (a nested `arguments` key hides nothing), to a branch not")
// a once-door's digest is the MCP tool's own arguments wherever the agent runs, the digest any host prints
// for them: the human's `--once` opens the action the ASK named, from any directory
const share = { fileId: "f1", role: "reader", type: "anyone" }
const digestOf = (d) => (JSON.stringify(d).match(/--once ([0-9a-f]{64})/) || [])[1]
const shareHere = digestOf(await pre(h, root, "mcp__Google_Drive__share_file", share))
mkdirSync(join(T, "elsewhere"), { recursive: true }); h.wd.set("root", join(T, "elsewhere"))
const shareThere = digestOf(await pre(h, root, "mcp__Google_Drive__share_file", share))
h.wd.delete("root")
const want = createHash("sha256").update("mcp__Google_Drive__share_file\0" + JSON.stringify(share)).digest("hex")
assert.ok(shareHere === want && shareThere === want, `${shareHere} ${shareThere} ${want}`)
ok("an MCP tool's once digest is its own arguments, the same from any directory")
// a live run: a question to the human must name a one-way door the gate stopped ([door:<id>])
process.env.AGENTIC_AUTONOMY_MODE = "autonomous"
const ask = (question, extra = {}) => ({ questions: [{ id: "q1", question, header: "Merge", multi_select: false,
  options: [{ label: "Wait (Recommended)", description: "keep the draft" }, { label: "Merge" }] }], ...extra })
assert.ok(denied(await pre(h, root, "ask_user_question", ask("Which name do you prefer?")), /two-way question/))
assert.ok(denied(await pre(h, root, "ask_user_question", ask("Which name?", { note: "[door:push-default-branch]" })), /two-way question/))
assert.ok(allowed(await pre(h, root, "ask_user_question", ask("[door:push-default-branch] Merge PR 12 into main?"))))
delete process.env.AGENTIC_AUTONOMY_MODE
assert.ok(allowed(await pre(h, root, "ask_user_question", ask("Which name do you prefer?"))))
ok("ask_user_question is the question tool: in a live run an untagged one is denied (a tag only in an undeclared field counts for nothing); a door the gate stopped may be asked")

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

// the door gate runs last (it records a crossing, using up a once-authorization), never for a
// read-only tool, and fails closed: a hang, a kill, a crash or an exit but 0/2 denies
const FD = join(T, "fake-doors")
mkdirSync(join(FD, "bin"), { recursive: true })
const CALLS = join(FD, "calls.log"), MODE = join(FD, "mode")
for (const g of ["host-adapter", "gate-supervisor-scope", "gate-mission-scope", "gate-commit-author", "gate-dispatch", "budget"]) {
  writeFileSync(join(FD, "bin", g), `#!/bin/sh\necho ${g} >> "${CALLS}"\n[ "$(cat "${MODE}")" = "budget-deny" ] && [ ${g} = budget ] && { echo "budget says no" >&2; exit 2; }\nexit 0\n`)
  chmodSync(join(FD, "bin", g), 0o755)
}
writeFileSync(join(FD, "bin", "mission"), `#!/bin/sh
echo "mission $*" >> "${CALLS}"
case "$(cat "${MODE}")" in
  hang) exec sleep 30 ;;
  crash) echo "Traceback: boom" >&2; exit 1 ;;
  kill) kill -9 $$ ;;
  odd) exit 3 ;;
  block) echo "BLOCK[doors]: the door gate could not decide" >&2; exit 2 ;;
  deny) echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"doors: ASK[door] x"}}' ;;
esac
exit 0
`)
chmodSync(join(FD, "bin", "mission"), 0o755)
const fd = host({ root: FD, doorsTimeoutMs: 500 })
plugin.apply(fd.ctx, fd.config)
const fdRoot = agentOf("root8")
const doorsRun = async (mode, name = "bash", args = { command: "ls" }) => {
  writeFileSync(MODE, mode); writeFileSync(CALLS, "")
  const d = await pre(fd, fdRoot, name, args)
  return { d, calls: readFileSync(CALLS, "utf8").trim().split("\n") }
}
let r = await doorsRun("ok")
assert.ok(allowed(r.d) && r.calls.at(-1) === "mission doors hook" && r.calls.at(-2) === "budget", r.calls.join(","))
r = await doorsRun("ok", "mcp__github__push_files", pushFiles)
assert.ok(allowed(r.d) && r.calls.at(-1) === "mission doors hook", r.calls.join(","))
ok("the door gate runs on every tool that can cross a door, last (after budget)")
r = await doorsRun("budget-deny")
assert.ok(denied(r.d, /budget says no/) && !r.calls.includes("mission doors hook"), r.calls.join(","))
ok("a call another gate refused never reaches the door gate (no crossing recorded for it)")
for (const [name, args] of [["read", { file_path: "README.md" }], ["glob", { pattern: "*" }], ["grep", { pattern: "x" }], ["read_image", { file_path: "a.png" }], ["todo_write", { todos: [] }], ["web_fetch", { url: "https://x.io" }], ["web_search", { query: "x" }]]) {
  r = await doorsRun("hang", name, args)
  assert.ok(allowed(r.d) && !r.calls.includes("mission doors hook"), `${name}: ${r.calls.join(",")}`)
}
ok("a read-only tool (read, glob, grep, todo, web) skips the door gate's spawn")
const t0 = Date.now()
r = await doorsRun("hang")
assert.ok(denied(r.d, /door gate.*did not answer.*fail closed/) && Date.now() - t0 < 10000, JSON.stringify(r.d))
for (const [mode, re] of [["crash", /door gate.*exited 1.*boom.*fail closed/], ["kill", /door gate.*(SIGKILL|killed).*fail closed/], ["odd", /door gate.*exited 3.*fail closed/],
  ["block", /BLOCK\[doors\]: the door gate could not decide/], ["deny", /doors: ASK\[door\] x/]]) {
  r = await doorsRun(mode)
  assert.ok(denied(r.d, re), `${mode}: ${JSON.stringify(r.d)}`)
}
rmSync(join(FD, "bin", "mission"))
assert.ok(denied((await doorsRun("ok")).d, /door gate.*could not run.*fail closed/))
ok("the door gate fails closed: a hang (timeout), a crash, a kill, an odd exit or a missing gate denies; its own block or deny is the reason")

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

// the one dsh profile serves one project: a session in another is told how to switch it
const fo = host({ root: ROOT, project: "/elsewhere", models: { subagent: "deepseek-flash", subagent_planning: "deepseek-v4-pro" } })
plugin.apply(fo.ctx, fo.config)
const r6 = agentOf("root6")
await fo.on["agent/created"]({ agent: r6, source: "startup" })
assert.ok(/rendered for \/elsewhere.*aloop install dsh --project/s.test(textOf(r6.injected[0])), textOf(r6.injected[0]))
const fs = host({ root: ROOT, project: realpathSync(T), models: {} })
plugin.apply(fs.ctx, fs.config)
const r7 = agentOf("root7")
await fs.on["agent/created"]({ agent: r7, source: "startup" })
assert.ok(!/rendered for/.test(textOf(r7.injected[0])))
ok("a session in a project the dsh profile does not serve is told to run `aloop install dsh` there (its role models are another project's)")

// an interrupted supervisor Stop still delivers the sprint-close notice (stats announces it once)
const r5 = agentOf("root5")
writeFileSync(join(T, ".aladdin/state/pending-close"), "Sprint 2 closed. Tokens — sprint: 1k\n")
await h.on["agent/turn-stopping"]({ agent: r5, turn: 1, signal: AbortSignal.abort() })
assert.ok(!r5.steered.length && r5.injected.some((m) => /Sprint 2 closed/.test(textOf(m))), JSON.stringify(r5.injected))
ok("an interrupted supervisor Stop queues the sprint-close notice for its next turn instead of dropping it")

console.log(`\n${pass} checks passed`)
