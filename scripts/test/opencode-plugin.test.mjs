// End-to-end test of the OpenCode plugin against a mock OpenCode client.
// Runs the REAL generated plugin (aloop install opencode) and the REAL bin/ scripts in a
// throw-away git repo. No OpenCode, no network, no tokens.   node scripts/test/opencode-plugin.test.mjs
import { execFileSync } from "node:child_process"
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"
import assert from "node:assert/strict"

const ROOT = join(fileURLToPath(import.meta.url), "..", "..", "..", "plugin")
const BIN = join(ROOT, "bin")
const T = mkdtempSync(join(tmpdir(), "agentic-oc-"))
process.on("exit", () => rmSync(T, { recursive: true, force: true }))   // a leaked dir held ~500MB
const sh = (cmd, args, opts = {}) => execFileSync(cmd, args, { cwd: T, encoding: "utf8", ...opts })

// isolated HOME + fake backends so gate-tooling passes without a Rust build; the SessionStart
// hook would otherwise start a real background cargo build over the fakes (350MB per run)
process.env.HOME = join(T, "home")
process.env.AGENTIC_NO_AUTOINSTALL = "1"
process.env.CLAUDE_PLUGIN_DATA = join(T, "pdata")
process.env.AGENTIC_NO_AUTOINSTALL = "1"  // no background cargo build into the temp dir
mkdirSync(join(process.env.CLAUDE_PLUGIN_DATA, "bin"), { recursive: true })
for (const t of ["md-db", "ctx-symbols"]) {
  const f = join(process.env.CLAUDE_PLUGIN_DATA, "bin", t)
  writeFileSync(f, "#!/bin/sh\nexit 0\n"); chmodSync(f, 0o755)
}
sh("git", ["init", "-q"]); sh("git", ["config", "user.email", "ada@x.io"]); sh("git", ["config", "user.name", "Ada"])
sh("git", ["commit", "-q", "--allow-empty", "-m", "init"])
sh(join(BIN, "aloop-init"), ["--apply", "TOKENS_SOFT=100", "TOKENS_HARD=200"])
mkdirSync(join(T, "docs/agents/sprint1/s1-01-x"), { recursive: true })
writeFileSync(join(T, "docs/agents/NEXT.md"), "# handoff\nNEXT-MARKER plan sprint 2\n")
sh(join(BIN, "aloop"), ["install", "opencode"])
const pluginFile = join(T, ".opencode/plugins/aloop.js")
assert.ok(!readFileSync(pluginFile, "utf8").includes("__AGENTIC_ROOT__"), "root substituted")

// mock OpenCode client
const parents = { root: null, child: "root" }
const calls = { prompt: [], abort: [], toast: [] }
const client = {
  session: {
    get: async ({ path }) => ({ data: { id: path.id, parentID: parents[path.id] ?? null } }),
    prompt: async (a) => { calls.prompt.push(a); return { data: {} } },
    abort: async (a) => { calls.abort.push(a); return { data: true } },
  },
  tui: { showToast: async (a) => { calls.toast.push(a) } },
}
const { AgenticAgile } = await import(pathToFileURL(pluginFile).href)
process.env.AGENTIC_STUCK_BLOCKS = "99"      // the plugin captures its env at load; the stuck release is tested on a second instance
const hooks = await AgenticAgile({ client, directory: T, worktree: T, project: {}, $: null })
let pass = 0
const ok = (name) => { pass++; console.log("ok -", name) }
const throws = async (fn, re, name) => { await assert.rejects(fn, re); ok(name) }

// SessionStart: NEXT.md reaches the root session's system prompt
await hooks.event({ event: { type: "session.created", properties: { info: { id: "root" } } } })
const sys = { system: [] }
await hooks["experimental.chat.system.transform"]({ sessionID: "root", model: {} }, sys)
assert.ok(sys.system.join("\n").includes("NEXT-MARKER")); ok("SessionStart handoff injected into system prompt")

// shell.env puts bin/ on PATH
const envOut = { env: { PATH: "/usr/bin" } }
await hooks["shell.env"]({ cwd: T }, envOut)
assert.ok(envOut.env.PATH.startsWith(BIN)); ok("shell.env prepends plugin bin/")

// UserPromptSubmit capture
await hooks["chat.message"]({ sessionID: "root" }, { message: {}, parts: [{ type: "text", text: "build X" }] })
assert.ok(readFileSync(join(T, ".agentic/transcripts/global.jsonl"), "utf8").includes('"prompt"')); ok("human prompt captured")

// supervisor may not write source once a sprint is live (arm the lock via a docs write)
await hooks["tool.execute.before"]({ tool: "write", sessionID: "root", callID: "1" }, { args: { filePath: join(T, "docs/agents/sprint1/s1-01-x/init.md"), content: "x" } })
await throws(() => hooks["tool.execute.before"]({ tool: "write", sessionID: "root", callID: "2" }, { args: { filePath: join(T, "src/main.rs"), content: "x" } }),
  /may not write production source/, "gate-supervisor-scope blocks the supervisor")

// commit authorship
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "root", callID: "3" },
  { args: { command: 'git commit -m "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"' } }), /co-author/i, "gate-commit-author blocks Claude trailer")

// worker isolation: must bind first, then commands/edits are confined to its worktree
await hooks.event({ event: { type: "session.created", properties: { info: { id: "child", parentID: "root" } } } })
await hooks["chat.message"]({ sessionID: "child", agent: "red-worker" }, { message: {}, parts: [{ type: "text", text: "task" }] })
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "child", callID: "4" }, { args: { command: "ls" } }),
  /bind your worktree first/, "unbound worker is refused")
const wt = sh(join(BIN, "task-worktree"), ["add", "S1-01-T1"]).trim()
writeFileSync(join(wt, ".agentic/task.env"), `TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=red-worker\nSTORY_DIR=${join(T, "docs/agents/sprint1/s1-01-x")}\n`)
await hooks["tool.execute.before"]({ tool: "bash", sessionID: "child", callID: "5" }, { args: { command: `aloop bind ${wt}` } })
const bashArgs = { command: "cargo test" }
await hooks["tool.execute.before"]({ tool: "bash", sessionID: "child", callID: "6" }, { args: bashArgs })
assert.equal(bashArgs.workdir, wt); ok("bound worker's bash runs in its worktree")
const writeArgs = { filePath: "tests/a.rs", content: "x" }
await hooks["tool.execute.before"]({ tool: "write", sessionID: "child", callID: "7" }, { args: writeArgs })
assert.equal(writeArgs.filePath, join(wt, "tests/a.rs")); ok("bound worker's relative write lands in its worktree")
const patchArgs = { patchText: `*** Begin Patch\n*** Add File: ${join(T, "src/lib.rs")}\n+x\n*** End Patch` }
await hooks["tool.execute.before"]({ tool: "apply_patch", sessionID: "child", callID: "8" }, { args: patchArgs })
assert.ok(patchArgs.patchText.includes(`*** Add File: ${join(wt, "src/lib.rs")}`)); ok("patch paths rewritten into the worktree")

// token ledger + time box: usage recorded per assistant message; soft token telemetry then hard stop
const usage = (id, input, output) => ({ type: "message.updated", properties: { info: {
  id, sessionID: "child", role: "assistant", mode: "red-worker", providerID: "anthropic", modelID: "claude-sonnet-5",
  time: { created: Date.now(), completed: Date.now() }, tokens: { input, output, reasoning: 0, cache: { read: 0, write: 0 } } } } })
await hooks.event({ event: usage("m1", 120, 10) })
const sub = join(T, ".agentic/host/opencode/root/subagents/agent-child.jsonl")
assert.ok(existsSync(sub)); ok("sub-agent usage written Claude-shaped")
const out1 = { title: "", output: "done", metadata: {} }
await hooks["tool.execute.after"]({ tool: "bash", sessionID: "child", callID: "9", args: { command: `cd ${wt} && ls` } }, out1)
assert.ok(!/BUDGET/.test(out1.output)); ok("soft token limit: nothing appended to the tool output (telemetry only)")
assert.ok(/"event": "task.token_soft_limit"/.test(readFileSync(join(T, ".agentic/logs/events.jsonl"), "utf8"))); ok("task.token_soft_limit recorded")
await hooks.event({ event: usage("m2", 400, 10) })
const out2 = { title: "", output: "", metadata: {} }
await hooks["tool.execute.after"]({ tool: "bash", sessionID: "child", callID: "10", args: {} }, out2)
assert.ok(/HARD BUDGET/.test(out2.output)); ok("hard limit: report instruction")
for (let i = 0; i < 3; i++)
  await assert.rejects(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "child", callID: "r" + i }, { args: { command: `cd ${wt} && ls` } }), /HARD BUDGET/)
ok("hard limit: 3 refusals")
await hooks["tool.execute.before"]({ tool: "bash", sessionID: "child", callID: "11" }, { args: { command: "ls" } })
await hooks["tool.execute.after"]({ tool: "bash", sessionID: "child", callID: "11", args: {} }, { output: "", metadata: {} })
assert.equal(calls.abort.at(-1)?.path?.id, "child"); ok("hard limit: session aborted by the hook")

// SubagentStop via the task result: gate blocks (no init.md) -> sub-agent re-prompted, then reported
const taskOut = { title: "", output: "worker done", metadata: { sessionId: "child" } }
await hooks["tool.execute.after"]({ tool: "task", sessionID: "root", callID: "12", args: {} }, taskOut)
// the budget-exceeded marker releases the gate (a stopped worker is not trapped) — so no block:
assert.ok(!/BLOCKED/.test(taskOut.output)); ok("budget-stopped worker is released by its gate")
execFileSync("rm", ["-f", join(wt, ".agentic/budget-exceeded")])
const taskOut2 = { title: "", output: "worker done", metadata: { sessionId: "child" } }
let before = calls.prompt.length
await hooks["tool.execute.after"]({ tool: "task", sessionID: "root", callID: "13", args: {} }, taskOut2)
assert.ok(/BLOCKED after 3 fix rounds/.test(taskOut2.output) && calls.prompt.length - before === 3, taskOut2.output)
ok("gate block re-prompts the sub-agent 3x, then reports to the supervisor")
// the stuck release (default threshold, a fresh plugin instance): those identical blocks are already
// logged, so the next one releases the worker UNVERIFIED and the supervisor is told so, never a silent pass
delete process.env.AGENTIC_STUCK_BLOCKS
const hooks2 = await AgenticAgile({ client, directory: T, worktree: T, project: {}, $: null })
await hooks2.event({ event: { type: "session.created", properties: { info: { id: "child", parentID: "root" } } } })
await hooks2["chat.message"]({ sessionID: "child", agent: "red-worker" }, { message: {}, parts: [{ type: "text", text: "task" }] })
const taskOut3 = { title: "", output: "worker done", metadata: { sessionId: "child" } }
before = calls.prompt.length
await hooks2["tool.execute.after"]({ tool: "task", sessionID: "root", callID: "14", args: {} }, taskOut3)
assert.ok(/STUCK — released UNVERIFIED/.test(taskOut3.output) && /Do not merge/.test(taskOut3.output) && calls.prompt.length === before,
  taskOut3.output)
ok("a worker stuck on one block is released unverified and reported as such, not re-prompted")

// Stop: a closed sprint is announced once (toast + prompt back into the session)
mkdirSync(join(T, ".agentic/state"), { recursive: true })
writeFileSync(join(T, ".agentic/state/pending-close"), "Sprint 1 closed. Tokens — sprint: 1k\n")
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "root" } } })
assert.ok(calls.toast.length === 1 && /Sprint 1 closed/.test(calls.prompt.at(-1).body.parts[0].text)); ok("sprint close announced on idle")
assert.ok(existsSync(join(T, ".agentic/ledger/root.jsonl"))); ok("token ledger written for the session")

console.log(`\n${pass} checks passed`)
