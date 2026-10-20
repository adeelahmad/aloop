// End-to-end test of the OpenCode plugin against a mock OpenCode client.
// Runs the REAL generated plugin (aloop install opencode) and the REAL bin/ scripts in a
// throw-away git repo. No OpenCode, no network, no tokens.   node scripts/test/opencode-plugin.test.mjs
import { execFileSync, spawnSync } from "node:child_process"
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"
import assert from "node:assert/strict"
import { approvePlan } from "./fixtures/plan-fixture.mjs"

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
delete process.env.AGENTIC_AUTONOMY_MODE  // a live run is armed on its own plugin instance below
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
  // the MCP servers OpenCode runs (their names as configured; a tool is <server>_<tool>, sanitized)
  mcp: { status: async () => ({ data: { github: { status: "connected" }, "Google Drive": { status: "connected" },
    notion: { status: "connected" }, drive: { status: "failed", error: "x" }, "drive_x": { status: "connected" } } }) },
}
const { AgenticAgile } = await import(pathToFileURL(pluginFile).href)
process.env.AGENTIC_STUCK_BLOCKS = "99"      // the plugin captures its env at load; the stuck release is tested on a second instance
const hooks = await AgenticAgile({ client, directory: T, worktree: T, project: {}, $: null })
let pass = 0
const ok = (name) => { pass++; console.log("ok -", name) }
const throws = async (fn, re, name) => { await assert.rejects(fn, re); ok(name) }
const CORE = "Core principles (every aloop agent):"     // the core block's head (mission principles show --core)
const count = (s, x) => s.split(x).length - 1
const systemOf = async (h, sessionID) => {
  const out = { system: [] }
  await h["experimental.chat.system.transform"]({ sessionID, model: {} }, out)
  return out.system.join("\n")
}

// SessionStart: NEXT.md reaches the root session's system prompt
await hooks.event({ event: { type: "session.created", properties: { info: { id: "root" } } } })
const sys = { system: [] }
await hooks["experimental.chat.system.transform"]({ sessionID: "root", model: {} }, sys)
assert.ok(sys.system.join("\n").includes("NEXT-MARKER")); ok("SessionStart handoff injected into system prompt")
assert.equal(count(sys.system.join("\n"), CORE), 1, sys.system.join("\n")); ok("the root gets the core principles exactly once")
// a resumed root session (no session.created in this process, so no SessionStart context) still carries them
assert.equal(count(await systemOf(hooks, "resumed"), CORE), 1); ok("a resumed root session gets the core principles once")

// shell.env puts bin/ on PATH and marks the shell as an agent's (`mission doors authorize` refuses it)
const envOut = { env: { PATH: "/usr/bin" } }
await hooks["shell.env"]({ cwd: T }, envOut)
assert.ok(envOut.env.PATH.startsWith(BIN)); ok("shell.env prepends plugin bin/")
assert.equal(envOut.env.ALOOP_AGENT_SHELL, "1"); ok("shell.env exports ALOOP_AGENT_SHELL=1 into the agent's shell")

// UserPromptSubmit capture
await hooks["chat.message"]({ sessionID: "root" }, { message: {}, parts: [{ type: "text", text: "build X" }] })
assert.ok(readFileSync(join(T, ".aladdin/transcripts/global.jsonl"), "utf8").includes('"prompt"')); ok("human prompt captured")

// supervisor may not write source once a sprint is live (arm the lock via a docs write)
await hooks["tool.execute.before"]({ tool: "write", sessionID: "root", callID: "1" }, { args: { filePath: join(T, "docs/agents/sprint1/s1-01-x/init.md"), content: "x" } })
await throws(() => hooks["tool.execute.before"]({ tool: "write", sessionID: "root", callID: "2" }, { args: { filePath: join(T, "src/main.rs"), content: "x" } }),
  /may not write production source/, "gate-supervisor-scope blocks the supervisor")

// commit authorship
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "root", callID: "3" },
  { args: { command: 'git commit -m "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"' } }), /co-author/i, "gate-commit-author blocks Claude trailer")

// the door rule (M9.30, M9.31): mission doors hook is a PreToolUse gate on OpenCode too
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "root", callID: "d1" }, { args: { command: "git push --force origin main" } }),
  /ASK\[door\] push-default-branch/, "doors: a force-push to the default branch stops at ASK[door]")
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "root", callID: "d2" },
  { args: { command: "aloop mission doors authorize push-default-branch --by x --reason r" } }),
  /BLOCK\[door\] forge-human-record/, "doors: an agent authorizing a door is a wall (forge-human-record)")
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "root", callID: "d3" }, { args: { command: "git reset --hard" } }),
  /ASK\[door\] discard-local-work/, "doors: discarding work in the main checkout stops at ASK[door]")
await hooks["tool.execute.before"]({ tool: "question", sessionID: "root", callID: "d4" },
  { args: { questions: [{ question: "Which name?", header: "Name", options: [{ label: "a", description: "a" }] }] } })
ok("doors: outside a live run a question is asked")

// worker isolation: must bind first, then commands/edits are confined to its worktree
await hooks.event({ event: { type: "session.created", properties: { info: { id: "child", parentID: "root" } } } })
await hooks["chat.message"]({ sessionID: "child", agent: "red-worker" }, { message: {}, parts: [{ type: "text", text: "task" }] })
// SubagentStart: the sub-agent carries the core principles too (once), never the root's handoff
const childSys = await systemOf(hooks, "child")
assert.ok(count(childSys, CORE) === 1 && !childSys.includes("NEXT-MARKER"), childSys); ok("a sub-agent's system prompt gets the core principles once")
assert.equal(count(await systemOf(hooks, "root"), CORE), 1); ok("the root still gets them exactly once")
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "child", callID: "4" }, { args: { command: "ls" } }),
  /bind your worktree first/, "unbound worker is refused")
const wt = sh(join(BIN, "task-worktree"), ["add", "S1-01-T1"]).trim()
writeFileSync(join(wt, ".aladdin/task.env"), `TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=red-worker\nSTORY_DIR=${join(T, "docs/agents/sprint1/s1-01-x")}\n`)
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
const looseArgs = { patchText: `*** Begin Patch\n  *** Add File:${join(T, "src/b.rs")}\n+x\n*** End Patch` }
await hooks["tool.execute.before"]({ tool: "apply_patch", sessionID: "child", callID: "8b" }, { args: looseArgs })
assert.ok(looseArgs.patchText.includes(join(wt, "src/b.rs")), looseArgs.patchText); ok("a loosely spaced patch header is rewritten too")
// the door gate reads a bound worker's command where it runs (tool_input.workdir, its worktree): a reset
// there is two-way, the same reset in the main checkout (above) is not
await hooks["tool.execute.before"]({ tool: "bash", sessionID: "child", callID: "d5" }, { args: { command: "git reset --hard" } })
ok("doors: a bound worker's reset in its own worktree is two-way")

// token ledger + time box: usage recorded per assistant message; soft token telemetry then hard stop
const usage = (id, input, output) => ({ type: "message.updated", properties: { info: {
  id, sessionID: "child", role: "assistant", mode: "red-worker", providerID: "anthropic", modelID: "claude-sonnet-5",
  time: { created: Date.now(), completed: Date.now() }, tokens: { input, output, reasoning: 0, cache: { read: 0, write: 0 } } } } })
await hooks.event({ event: usage("m1", 120, 10) })
const sub = join(T, ".aladdin/host/opencode/root/subagents/agent-child.jsonl")
assert.ok(existsSync(sub)); ok("sub-agent usage written Claude-shaped")
const out1 = { title: "", output: "done", metadata: {} }
await hooks["tool.execute.after"]({ tool: "bash", sessionID: "child", callID: "9", args: { command: `cd ${wt} && ls` } }, out1)
assert.ok(!/BUDGET/.test(out1.output)); ok("soft token limit: nothing appended to the tool output (telemetry only)")
assert.ok(/"event": "task.token_soft_limit"/.test(readFileSync(join(T, ".aladdin/logs/events.jsonl"), "utf8"))); ok("task.token_soft_limit recorded")
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
execFileSync("rm", ["-f", join(wt, ".aladdin/budget-exceeded")])
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

// mission roles (ROADMAP §4.12): a start record, the role path guard, and the artifact gate at stop
parents.msn = "root"
await hooks.event({ event: { type: "session.created", properties: { info: { id: "msn", parentID: "root" } } } })
assert.ok(!existsSync(join(T, ".aladdin/state/mission-runs/msn.json")))       // its role is not known yet
await hooks["chat.message"]({ sessionID: "msn", agent: "evaluator" }, { message: {}, parts: [{ type: "text", text: "evaluate" }] })
const msnRun = JSON.parse(readFileSync(join(T, ".aladdin/state/mission-runs/msn.json"), "utf8"))
assert.equal(msnRun.role, "evaluator")
parents.bld = "root"
await hooks.event({ event: { type: "session.created", properties: { info: { id: "bld", parentID: "root" } } } })
await hooks["chat.message"]({ sessionID: "bld", agent: "green-worker" }, { message: {}, parts: [{ type: "text", text: "build" }] })
assert.ok(!existsSync(join(T, ".aladdin/state/mission-runs/bld.json")))
ok("a mission sub-agent's start records what exists once its role is known (a builder's never)")
await throws(() => hooks["tool.execute.before"]({ tool: "write", sessionID: "msn", callID: "m1" }, { args: { filePath: join(T, "src/app.py"), content: "x" } }),
  /BLOCK\[mission-scope\].*evaluator writes only/s, "gate-mission-scope: an evaluator never writes source")
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "msn", callID: "m2" }, { args: { command: "echo x > src/app.py" } }),
  /BLOCK\[mission-scope\]/, "gate-mission-scope judges the evaluator's bash too")
await hooks["tool.execute.before"]({ tool: "write", sessionID: "msn", callID: "m3" }, { args: { filePath: join(T, "docs/agents/mission/eval/e.json"), content: "{}" } })
ok("gate-mission-scope: the evaluator writes its own eval/ dir")
const msnOut = { title: "", output: "evaluated", metadata: { sessionId: "msn" } }
await hooks["tool.execute.after"]({ tool: "task", sessionID: "root", callID: "m4", args: {} }, msnOut)
assert.ok(/BLOCKED after 3 fix rounds/.test(msnOut.output) && /BLOCK\[mission-artifact\]/.test(msnOut.output), msnOut.output)
ok("gate-mission-artifact runs at the evaluator's stop and blocks a run that wrote no artifact")

// dispatch (the task tool) runs gate-dispatch: the TASK_ID line, the task contract, then the human's plan
// approval (ROADMAP M0.9); OpenCode names no model per call (the agent file pins it)
const task = (id, prompt, sessionID = "root") => hooks["tool.execute.before"]({ tool: "task", sessionID, callID: id },
  { args: { subagent_type: "green-worker", description: "GREEN", prompt } })
await throws(() => task("d1", "go green"), /BLOCK\[dispatch\].*TASK_ID: <id>/s, "OpenCode task: a worker dispatch without its TASK_ID line is refused")
mkdirSync(join(T, ".aladdin/state/tasks"), { recursive: true })
writeFileSync(join(T, ".aladdin/state/tasks/S1-01-T2.env"), `TASK_ID=S1-01-T2\nATTEMPT=1\nAGENT_ROLE=green-worker\nSTORY_DIR=${join(T, "docs/agents/sprint1/s1-01-x")}\n`)
await throws(() => task("d2", "TASK_ID: S1-01-T2\nGREEN"), /has not approved the plan of S1/, "OpenCode task: no worker is dispatched before the human approves the sprint's plan")
approvePlan(T, BIN)
await task("d3", "TASK_ID: S1-01-T2\nGREEN"); ok("OpenCode task: after the human's typed approval the dispatch is admitted")
await task("d4", "TASK_ID: S2-01-T1\nGREEN", "child"); ok("OpenCode task: a sub-agent's own task call is not gate-dispatch's")

// Stop: a closed sprint is announced once (toast + prompt back into the session)
mkdirSync(join(T, ".aladdin/state"), { recursive: true })
writeFileSync(join(T, ".aladdin/state/pending-close"), "Sprint 1 closed. Tokens — sprint: 1k\n")
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "root" } } })
assert.ok(calls.toast.length === 1 && /Sprint 1 closed/.test(calls.prompt.at(-1).body.parts[0].text)); ok("sprint close announced on idle")
assert.ok(existsSync(join(T, ".aladdin/ledger/root.jsonl"))); ok("token ledger written for the session")

// a live run (an autonomous run, read at plugin load): a two-way question is decided, not asked; a
// question at a door the gate stopped in this session (the force-push above) is asked
process.env.AGENTIC_AUTONOMY_MODE = "autonomous"
const live = await AgenticAgile({ client, directory: T, worktree: T, project: {}, $: null })
delete process.env.AGENTIC_AUTONOMY_MODE
const ask = (text) => ({ args: { questions: [{ question: text, header: "Q", options: [{ label: "yes", description: "recommended" }] }] } })
await throws(() => live["tool.execute.before"]({ tool: "question", sessionID: "root", callID: "q1" }, ask("Which name should the module get?")),
  /two-way question/, "doors: in a live run an untagged question is refused")
await live["tool.execute.before"]({ tool: "question", sessionID: "root", callID: "q2" },
  ask("[door:push-default-branch] Push the fix to main? Recommended: no, a draft PR."))
ok("doors: in a live run a question at a door the gate stopped is asked")

// the door gate runs LAST (it records door.crossed, which uses up a once-grant) and fails closed. A stub
// plugin root: each gate logs its call and does what S/mode.<gate> says (default: admit)
const S = mkdtempSync(join(tmpdir(), "agentic-oc-stub-"))
process.on("exit", () => rmSync(S, { recursive: true, force: true }))
mkdirSync(join(S, "plugin/bin"), { recursive: true })
for (const g of ["host-adapter", "gate-tooling", "gate-supervisor-scope", "gate-mission-scope", "gate-commit-author", "budget", "mission"]) {
  writeFileSync(join(S, "plugin/bin", g), `#!/bin/sh
S="$(dirname "$0")/../.."
cat > "$S/stdin.$(basename "$0")"
echo "$(basename "$0") $*" >> "$S/calls"
case "$(cat "$S/mode.$(basename "$0")" 2>/dev/null)" in
  hang) exec sleep 30 ;;
  crash) kill -KILL $$ ;;
  exit1) exit 1 ;;
  deny) echo "BLOCK[stub] no" >&2; exit 2 ;;
esac
exit 0
`)
  chmodSync(join(S, "plugin/bin", g), 0o755)
}
writeFileSync(join(S, "aloop.mjs"), readFileSync(join(ROOT, "hosts/opencode/aloop.js"), "utf8").replaceAll("__AGENTIC_ROOT__", join(S, "plugin")))
process.env.ALOOP_DOORS_TIMEOUT_MS = "500"
const stub = await (await import(pathToFileURL(join(S, "aloop.mjs")).href)).AgenticAgile({ client, directory: T, worktree: T, project: {}, $: null })
delete process.env.ALOOP_DOORS_TIMEOUT_MS
const mode = (g, m) => writeFileSync(join(S, "mode." + g), m)
const gated = async (tool, args) => {
  writeFileSync(join(S, "calls"), "")
  let err = null
  try { await stub["tool.execute.before"]({ tool, sessionID: "root", callID: "s" }, { args }) } catch (e) { err = e }
  return { err, calls: readFileSync(join(S, "calls"), "utf8").split("\n").filter(Boolean) }
}
let g = await gated("bash", { command: "ls" })
assert.ok(!g.err && g.calls.at(-1) === "mission doors hook" && g.calls.at(-2) === "budget hook pre", g.calls.join(" | "))
ok("doors: the door gate runs last, after budget")
for (const tool of ["read", "codesearch"]) {
  g = await gated(tool, { filePath: join(T, "README.md"), query: "x" })
  assert.ok(!g.err && g.calls.length && !g.calls.some((c) => c.startsWith("mission")), `${tool}: ${g.calls.join(" | ")}`)
}
ok("doors: a read-only tool (read, codesearch) never spawns the door gate")
for (const [tool, want] of [["github_create_pull_request", "mcp__github__create_pull_request"],
  ["Google_Drive_share_file", "mcp__Google_Drive__share_file"], ["notion_search_slack_posts", "mcp__notion__search_slack_posts"],
  ["drive_get_share", "mcp__drive__get_share"], ["drive_x_send", "mcp__drive_x__send"], ["lsp_hover", "lsp_hover"], ["bash", "Bash"]]) {
  await gated(tool, { command: "ls" })
  const sent = JSON.parse(readFileSync(join(S, "stdin.mission"), "utf8"))
  assert.equal(sent.tool_name, want, tool)
}
ok("doors: an MCP tool reaches the gates as mcp__<server>__<tool> (OpenCode's servers, the longest first), so a word inside its name is no door")
mode("budget", "deny")
g = await gated("bash", { command: "ls" })
assert.ok(/BLOCK\[stub\]/.test(g.err?.message) && !g.calls.some((c) => c.startsWith("mission")), g.calls.join(" | "))
ok("doors: a call another gate denied never reaches the door gate")
mode("budget", "")
for (const [m, what] of [["hang", "hangs (past its timeout)"], ["crash", "is killed by a signal"], ["exit1", "exits 1"]]) {
  mode("mission", m)
  const t0 = Date.now()
  g = await gated("bash", { command: "ls" })
  assert.ok(/door gate/.test(g.err?.message) && Date.now() - t0 < 10000, `${m}: ${g.err?.message}`)
  ok(`doors: a door gate that ${what} denies the call (fails closed)`)
}
rmSync(join(S, "plugin/bin/mission"))
g = await gated("bash", { command: "ls" })
assert.ok(/door gate/.test(g.err?.message), g.err?.message); ok("doors: a door gate that cannot start denies the call (fails closed)")

// end to end, last (a red run may record an authorization): the agent's shell, marked only by shell.env,
// cannot authorize a door
const agentEnv = { ...process.env, ALOOP_AGENT_SHELL: envOut.env.ALOOP_AGENT_SHELL, AGENTIC_HOST: envOut.env.AGENTIC_HOST }
for (const k of ["CLAUDECODE", "DSH_SHELL", "CODEX_THREAD_ID"]) delete agentEnv[k]
const a = spawnSync(join(BIN, "aloop"), ["mission", "doors", "authorize", "publish-release", "--by", "x", "--reason", "r"],
  { cwd: T, env: agentEnv, encoding: "utf8" })
assert.ok(a.status === 2 && /agent session/.test(a.stderr), a.stderr)
ok("doors: `mission doors authorize` refuses the agent's OpenCode shell")

console.log(`\n${pass} checks passed`)
