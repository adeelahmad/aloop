// End-to-end test of the OpenCode plugin against a mock OpenCode client.
// Runs the REAL generated plugin (aloop install opencode) and the REAL bin/ scripts in a
// throw-away git repo. No OpenCode, no network, no tokens.   node scripts/test/opencode-plugin.test.mjs
import { execFileSync } from "node:child_process"
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs"
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
assert.ok(readFileSync(join(T, ".aladdin/transcripts/global.jsonl"), "utf8").includes('"prompt"')); ok("human prompt captured")

// commit authorship (before the sprint lock: once it is live, the supervisor's commit on main is refused first)
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "root", callID: "3" },
  { args: { command: 'git commit -m "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"' } }), /co-author/i, "gate-commit-author blocks Claude trailer")

// supervisor may not write source once a sprint is live (arm the lock via a docs write)
await hooks["tool.execute.before"]({ tool: "write", sessionID: "root", callID: "1" }, { args: { filePath: join(T, "docs/agents/sprint1/s1-01-x/init.md"), content: "x" } })
await throws(() => hooks["tool.execute.before"]({ tool: "write", sessionID: "root", callID: "2" }, { args: { filePath: join(T, "src/main.rs"), content: "x" } }),
  /may not write production source/, "gate-supervisor-scope blocks the supervisor")
// a plugin gets the model's raw arguments, extra keys included (OpenCode 1.19: the AI SDK input is
// not validated before tool.execute.before), so tool_input holds each tool's declared ones only
await throws(() => hooks["tool.execute.before"]({ tool: "write", sessionID: "root", callID: "2a" },
  { args: { filePath: join(T, "src/main.rs"), content: "x", agent_type: "green-worker", agent_id: "w1" } }),
  /may not write production source/, "an `agent_type` argument does not make the supervisor a dispatched agent")
await throws(() => hooks["tool.execute.before"]({ tool: "write", sessionID: "root", callID: "2b" },
  { args: { filePath: join(T, "src/main.rs"), content: "x", file_path: join(T, "docs/agents/sprint1/s1-01-x/init.md") } }),
  /may not write production source/, "a `file_path` argument does not hide the path OpenCode writes (filePath)")
await hooks["tool.execute.after"]({ tool: "edit", sessionID: "root", callID: "2c",
  args: { filePath: join(T, "docs/agents/sprint1/s1-01-x/init.md"), oldString: "a", newString: "b", agent_type: "green-worker", cwd: "/" } }, { output: "", metadata: {} })
const rec = readFileSync(join(T, ".aladdin/transcripts/session/events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l)).at(-1)
assert.deepEqual(rec.payload.tool_input, { file_path: join(T, "docs/agents/sprint1/s1-01-x/init.md"), old_string: "a", new_string: "b", replace_all: false })
assert.ok(!("agent_type" in rec.payload) && rec.payload.cwd === T, JSON.stringify(rec.payload)); ok("tool_input is built from the tool's declared arguments (Claude-shaped), nothing else")
// the supervisor's shell is judged as on every other host: no commit on main, no block in an agent's output.md
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "root", callID: "3a" }, { args: { command: "git add README && git commit -m 'Track README'" } }),
  /never commits on main/, "gate-supervisor-scope refuses the supervisor's commit on main (bash)")
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "root", callID: "3b" },
  { args: { command: "echo '## S1-01-T1 · attempt 1 · final-gate' >> docs/agents/sprint1/s1-01-x/output.md" } }),
  /never writes a block in it/, "gate-supervisor-scope refuses the supervisor's append to an agent's output.md (bash)")


// worker isolation: must bind first, then commands/edits are confined to its worktree
await hooks.event({ event: { type: "session.created", properties: { info: { id: "child", parentID: "root" } } } })
await hooks["chat.message"]({ sessionID: "child", agent: "red-worker" }, { message: {}, parts: [{ type: "text", text: "task" }] })
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "child", callID: "4" }, { args: { command: "ls" } }),
  /bind your worktree first/, "unbound worker is refused")
const wt = sh(join(BIN, "task-worktree"), ["add", "S1-01-T1"]).trim()
writeFileSync(join(wt, ".aladdin/task.env"), `TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=red-worker\nSTORY_DIR=${join(T, "docs/agents/sprint1/s1-01-x")}\n`)
await hooks["tool.execute.before"]({ tool: "bash", sessionID: "child", callID: "5" }, { args: { command: `aloop bind ${wt}` } })
const bashArgs = { command: "cargo test" }
await hooks["tool.execute.before"]({ tool: "bash", sessionID: "child", callID: "6" }, { args: bashArgs })
assert.equal(bashArgs.workdir, wt); ok("bound worker's bash runs in its worktree")
await hooks["tool.execute.before"]({ tool: "bash", sessionID: "root", callID: "6a" }, { args: { command: "git commit -qm wip", workdir: wt } })
ok("the supervisor's commit in a task worktree (its workdir) is a worker's, not judged as main")
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "child", callID: "6b" },
  { args: { command: "sed -i s/ATTEMPT=1/ATTEMPT=9/ .aladdin/task.env" } }),
  /rewrites the supervisor's task contract/, "a role's bash may not rewrite its task contract")
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
await throws(() => hooks["tool.execute.before"]({ tool: "write", sessionID: "msn", callID: "m1b" },
  { args: { filePath: join(T, "src/app.py"), content: "x", file_path: join(T, "docs/agents/mission/eval/e.json") } }),
  /BLOCK\[mission-scope\]/, "gate-mission-scope judges the path OpenCode writes, not a `file_path` argument beside it")
await throws(() => hooks["tool.execute.before"]({ tool: "bash", sessionID: "msn", callID: "m2" }, { args: { command: "echo x > src/app.py" } }),
  /BLOCK\[mission-scope\]/, "gate-mission-scope judges the evaluator's bash too")
await hooks["tool.execute.before"]({ tool: "write", sessionID: "msn", callID: "m3" }, { args: { filePath: join(T, "docs/agents/mission/eval/e.json"), content: "{}" } })
ok("gate-mission-scope: the evaluator writes its own eval/ dir")
const msnOut = { title: "", output: "evaluated", metadata: { sessionId: "msn" } }
await hooks["tool.execute.after"]({ tool: "task", sessionID: "root", callID: "m4", args: {} }, msnOut)
assert.ok(/BLOCKED after 3 fix rounds/.test(msnOut.output) && /BLOCK\[mission-artifact\]/.test(msnOut.output), msnOut.output)
ok("gate-mission-artifact runs at the evaluator's stop and blocks a run that wrote no artifact")

// a resumed mission child (the task tool's session_id continues it): each task is its own run, with a fresh
// start record that is open while it works; the first task's record never judges (or excuses) the second's work
const mission = (...a) => sh(join(BIN, "mission"), a, { stdio: ["ignore", "pipe", "pipe"] }).trim().split("\n").pop()
mission("init", "the best to-do app", "--mode", "autonomous", "--budget-usd", "40")
try { mission("approve", "--by", "Ada") } catch (e) {        // M9.17: the human types the line the ASK names
  const prompt = /they reply {2}(.+?) {2}\(the code/.exec(e.stderr)[1]
  sh(join(BIN, "transcripts"), ["prompt"], { input: JSON.stringify({ hook_event_name: "UserPromptSubmit", session_id: "s-human", prompt }) })
  mission("approve", "--by", "Ada")
}
const runOf = (id) => JSON.parse(readFileSync(join(T, ".aladdin/state/mission-runs", `${id}.json`), "utf8"))
parents.eth = "root"
await hooks.event({ event: { type: "session.created", properties: { info: { id: "eth", parentID: "root" } } } })
await hooks["chat.message"]({ sessionID: "eth", agent: "ethics-reviewer" }, { message: {}, parts: [{ type: "text", text: "review the mission" }] })
const run1 = runOf("eth")
const review = mission("ethics", "new", "--subject", "mission")
writeFileSync(review, JSON.stringify({ ...JSON.parse(readFileSync(review, "utf8")), findings: [], tier: "clear" }))
mission("ethics", "record", review)
const ethOut = { title: "", output: "reviewed", metadata: { sessionId: "eth" } }
await hooks["tool.execute.after"]({ tool: "task", sessionID: "root", callID: "e1", args: {} }, ethOut)
assert.ok(!/BLOCK|STUCK/.test(ethOut.output) && runOf("eth").stopped, ethOut.output)
await hooks["chat.message"]({ sessionID: "eth", agent: "ethics-reviewer" }, { message: {}, parts: [{ type: "text", text: "review it again" }] })
const run2 = runOf("eth")
assert.ok(!run2.stopped && run2.started > run1.started, JSON.stringify(run2).slice(0, 300))
ok("a resumed mission child starts a new run per task: a fresh start record, open while it works")

// an intake that stops to ask the human (ROADMAP §4.9): released at once as NEEDS-HUMAN with its questions in
// the task result, never re-prompted for rounds it cannot use; a malformed Intent Contract is still re-prompted
writeFileSync(join(T, "docs/agents/sprint1/intake.md"), "# Intent\nWhat: a parser\n## Constraints\n## Failure scenarios\n## Success scenarios\n## Connections\n")
const ic = sh(join(BIN, "intent"), ["new", "--source", "prompt", "--raw", "a semver parser"]).trim()
const contract = { ...JSON.parse(readFileSync(ic, "utf8")), actor: "dev", want: "parse semver", why: "releases", constraints: ["std only"],
  success_scenarios: ["1.2.3 parses"], failure_scenarios: ["bad input errors"], connections: ["none"], done_looks_like: "parse() works",
  open_questions: [{ question: "accept a leading v (v1.2.3)?", class: "ask" },
    { question: "Should orders STUCK in review (NOT verified by finance) be exported?", class: "ask" }] }
writeFileSync(ic, JSON.stringify(contract))
parents.itk = "root"
await hooks.event({ event: { type: "session.created", properties: { info: { id: "itk", parentID: "root" } } } })
await hooks["chat.message"]({ sessionID: "itk", agent: "intake" }, { message: {}, parts: [{ type: "text", text: "intake" }] })
const askOut = { title: "", output: "one question needs the human", metadata: { sessionId: "itk" } }
before = calls.prompt.length
await hooks["tool.execute.after"]({ tool: "task", sessionID: "root", callID: "i1", args: {} }, askOut)
assert.ok(/NEEDS-HUMAN — released UNVERIFIED/.test(askOut.output) && /accept a leading v/.test(askOut.output) && calls.prompt.length === before, askOut.output)
// the release is read from the gate's own marker line, not from a question that quotes STUCK … NOT verified, and the
// record is claimed once told (a later Claude Code or Codex session is not told again)
assert.ok(!/STUCK — released/.test(askOut.output) && /"Should orders STUCK in review \(NOT verified by finance\) be exported\?"/.test(askOut.output), askOut.output)
assert.deepEqual(readdirSync(join(T, ".aladdin/state/needs-human")).filter((n) => n.endsWith(".json")), [])
ok("an intake whose contract only waits on the human is released at once; the task result says NEEDS-HUMAN with its questions (quoted, claimed once)")
writeFileSync(ic, JSON.stringify({ ...contract, want: "" }))
const badOut = { title: "", output: "done", metadata: { sessionId: "itk" } }
await hooks["tool.execute.after"]({ tool: "task", sessionID: "root", callID: "i2", args: {} }, badOut)
assert.ok(/BLOCKED after 3 fix rounds/.test(badOut.output) && /is invalid/.test(badOut.output) && calls.prompt.length - before === 3, badOut.output)
ok("a malformed Intent Contract still re-prompts the intake, then reports it BLOCKED")
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

console.log(`\n${pass} checks passed`)
