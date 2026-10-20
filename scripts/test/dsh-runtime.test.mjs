// The aloop plugin on the REAL DeepSeek Harness runtime (dsh), driven by a scripted model.
// `aloop install dsh` renders the profile into a throw-away DSH_HOME, then `dsh --profile aloop
// --json` runs a supervisor that dispatches a red-worker (subagent) and an intake (subagent_planning)
// against fixtures/fake-deepseek.mjs. The REAL bin/ scripts run in a throw-away git repo. No
// network, no tokens. It proves on dsh itself what the emulated test (dsh-plugin.test.mjs) assumes:
// the handoff reaches the model, a deny / post-execute block reaches it, the profile's roles, tools
// and models, worker confinement, and the per-role stop gate (a block steers the child and is
// reported to the supervisor after 3 rounds). It also proves what the capability matrix claims for
// dsh: a role is not offered send_message (so dsh adds no return guidance), the supervisor's
// send_message reaches a running role (live messaging), the time soft-stop reaches a running worker,
// and dsh compacts a session (a context overflow here) and the plugin records it.
//   DSH_BIN=<dsh's lib/bin.js or a dsh executable> node scripts/test/dsh-runtime.test.mjs
// Without DSH_BIN it uses `dsh` on PATH, and skips when there is none (ALOOP_REQUIRE_DSH=1 fails
// instead: CI installs the pinned @deepseek-ai/dsh and sets it).
import { execFileSync, spawn, spawnSync } from "node:child_process"
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { delimiter, join, relative } from "node:path"
import { fileURLToPath } from "node:url"
import assert from "node:assert/strict"

const HERE = join(fileURLToPath(import.meta.url), "..")
const ROOT = join(HERE, "..", "..", "plugin")
const BIN = join(ROOT, "bin")

function findDsh() {
  if (process.env.DSH_BIN) return process.env.DSH_BIN
  for (const d of (process.env.PATH || "").split(delimiter)) if (d && existsSync(join(d, "dsh"))) return join(d, "dsh")
  return ""
}
const DSH = findDsh()
if (!DSH) {
  if (process.env.ALOOP_REQUIRE_DSH === "1") { console.error("FAIL: ALOOP_REQUIRE_DSH=1 but no dsh (set DSH_BIN)"); process.exit(1) }
  console.log("skip - dsh not installed (npm install @deepseek-ai/dsh@0.2.1-rc.2, then DSH_BIN=<its lib/bin.js>)")
  process.exit(0)
}
const dshCmd = DSH.endsWith(".js") ? [process.execPath, DSH] : [DSH]

const T = mkdtempSync(join(tmpdir(), "aloop-dsh-rt-"))
process.on("exit", () => rmSync(T, { recursive: true, force: true }))
const P = join(T, "repo")
mkdirSync(P)
const HOME = join(T, "home")
const env = { ...process.env, HOME, DSH_HOME: join(T, "dsh-home"), AGENTIC_NO_AUTOINSTALL: "1", CLAUDE_PLUGIN_DATA: join(T, "pdata"),
  AGENTIC_STUCK_BLOCKS: "99", PATH: `${join(HOME, ".local", "bin")}${delimiter}${process.env.PATH || ""}` }
delete env.AGENTIC_HOST
mkdirSync(join(env.CLAUDE_PLUGIN_DATA, "bin"), { recursive: true })
for (const t of ["md-db", "ctx-symbols"]) {     // fake backends: gate-tooling passes without a Rust build
  const f = join(env.CLAUDE_PLUGIN_DATA, "bin", t)
  writeFileSync(f, "#!/bin/sh\nexit 0\n"); chmodSync(f, 0o755)
}
const sh = (cmd, args) => execFileSync(cmd, args, { cwd: P, env, encoding: "utf8" })
sh("git", ["init", "-q"]); sh("git", ["config", "user.email", "ada@x.io"]); sh("git", ["config", "user.name", "Ada"])
sh("git", ["commit", "-q", "--allow-empty", "-m", "init"])
// planning on the strongest route, workers on the economical one: two models, two tools
sh(join(BIN, "aloop-init"), ["--apply", "TOKENS_SOFT=100000", "TOKENS_HARD=200000", "MODEL_PLANNING=strongest", "MODEL_WORKER=economical"])
const story = join(P, "docs/agents/sprint1/s1-01-x")
mkdirSync(story, { recursive: true })
writeFileSync(join(P, "docs/agents/NEXT.md"), "# handoff\nNEXT-MARKER plan sprint 2\n")
const installed = sh(join(BIN, "aloop"), ["install", "dsh", "--project", P])
assert.ok(existsSync(join(env.DSH_HOME, "profiles/aloop/cordis.patch.yml")) && existsSync(join(P, ".dsh/skills/aloop/SKILL.md")), installed)
mkdirSync(join(P, ".aladdin/state/tasks"), { recursive: true })
// time soft limit 0 min: the soft-stop is due at the worker's first metered tool call
writeFileSync(join(P, ".aladdin/state/tasks/S1-01-T1.env"), `TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=red-worker\nSTORY_DIR=${story}\nBUDGET_TIME_SOFT_MIN=0\n`)
const wt = sh(join(BIN, "task-worktree"), ["add", "S1-01-T1"]).trim()

const red = "TASK_ID: S1-01-T1\nYour worktree: " + wt + ". First command: `aloop bind " + wt + "`."
const script = {
  supervisor: [
    { tools: [{ name: "write", input: { file_path: join(story, "init.md"), content: "no frontmatter\n" } }] },
    { tools: [{ name: "write", input: { file_path: join(story, "plan-ready.md"), content: "---\ntype: plan-ready\n---\n# Plan\n" } }] },
    { tools: [{ name: "write", input: { file_path: join(P, "src/main.rs"), content: "fn main() {}\n" } }] },
    { tools: [{ name: "write", input: { file_path: join(P, "src/main.rs"), content: "fn main() {}\n", agent_type: "green-worker" } }] },
    { tools: [{ name: "subagent", input: { description: "no role", prompt: "do it" } }] },
    { tools: [{ name: "subagent", input: { agent_preset: "aloop-red-worker", description: "RED S1-01-T1", prompt: red, cwd: wt } },
              { name: "subagent_planning", input: { agent_preset: "aloop-intake", description: "intake", prompt: "Run intake." } }] },
    // live messaging: sent once the red-worker is mid-turn (its step 13 is held until this arrives)
    { after: { role: "red-worker", step: 13 }, tools: [{ name: "send_message", input: { agent_id: "$CHILD0", message: "LIVE-MARKER wrap up" } }] },
    { overflow: true, text: "waiting for the roles" },     // the context overflows once: dsh compacts, then retries
  ],
  "red-worker": [
    { tools: [{ name: "bash", input: { command: "ls", description: "list" } }] },
    { tools: [{ name: "bash", input: { command: `aloop bind ${wt}`, description: "bind" } }] },
    { tools: [{ name: "bash", input: { command: 'echo "pwd=$(pwd) host=$AGENTIC_HOST key=${DEEPSEEK_API_KEY:-none}"', description: "where" } }] },
    { tools: [{ name: "write", input: { file_path: join(P, "src/escape.rs"), content: "x\n" } }] },
    { tools: [{ name: "write", input: { file_path: "tests/a.rs", content: "#[test] fn a() { assert!(false) }\n" } }] },
    { tools: [{ name: "working_directory", input: { cd: P } }] },
    { tools: [{ name: "bash", input: { command: "touch WORKDIR_ESCAPE", description: "main tree", workdir: P } }] },
    { tools: [{ name: "bash", input: { command: `ln -s ${join(P, "docs")} lnk`, description: "link" } }] },
    { tools: [{ name: "write", input: { file_path: "lnk/../SYMLINK_ESCAPE.txt", content: "x\n" } }] },
    { tools: [{ name: "bash", input: { command: "touch BIND_ESCAPE # aloop bind x", description: "comment", workdir: P } }] },
    { tools: [{ name: "bash", input: { command: `cd ${P} && touch CD_ESCAPE`, description: "cd out" } }] },
    { tools: [{ name: "bash", input: { command: "touch REL_ESCAPE", description: "relative", workdir: relative(wt, P) } }] },
    { tools: [{ name: "send_message", input: { agent_id: "parent", message: "RED done, ready to merge" } }] },
    { after: { role: "supervisor", step: 7 }, tools: [{ name: "bash", input: { command: "mkdir gone", description: "a dir" } }] },
    { tools: [{ name: "working_directory", input: { cd: join(wt, "gone") } }] },
    { tools: [{ name: "bash", input: { command: "cd .. && rmdir gone", description: "remove it" } },
              { name: "write", input: { file_path: "VANISH_ESCAPE.txt", content: "x\n" } }] },
    { tools: [{ name: "bash", input: { command: `aloop bind ${wt};echo>${join(P, "CHAIN_ESCAPE")}`, description: "chained", workdir: P } }] },
  ],
  intake: [
    { tools: [{ name: "write", input: { file_path: join(P, "docs/agents/sprint1/intake.md"), content:
      "# Intake\n## What\nA semver parser.\n## Constraints\nstd only.\n## Failure scenarios\nbad input errors.\n" +
      "## Success scenarios\n1.2.3 parses.\n## Connections\nnone; depends on nothing.\n" } }] },
  ],
}
writeFileSync(join(T, "script.json"), JSON.stringify(script))

// a project .env that switches the gates off (it can come with a clone): dsh loads it, the gates ignore it
writeFileSync(join(P, ".env"), "SKIP_HOOKS=1\nGATE_RUN_TESTS=0\n")
const LOG = join(T, "fake.jsonl")
const fake = spawn(process.execPath, [join(HERE, "fixtures", "fake-deepseek.mjs")], { env: { ...env, FAKE_SCRIPT: join(T, "script.json"), FAKE_LOG: LOG } })
const port = await new Promise((ok, no) => { fake.stdout.once("data", (d) => ok(String(d).trim())); fake.once("error", no) })
const r = spawnSync(dshCmd[0], [...dshCmd.slice(1), "--profile", "aloop", "--json", "Use the aloop skill. Intent: a semver parser"], {
  cwd: P, encoding: "utf8", timeout: 300000,
  env: { ...env, DEEPSEEK_BASE_URL: `http://127.0.0.1:${port}`, DEEPSEEK_API_KEY: "sk-fake", DSH_PERMISSION_MODE: "danger-full-access",
    DSH_TELEMETRY_DISABLED: "1", ALOOP_DSH_MODEL: "deepseek-flash" } })
fake.kill()
const reqs = existsSync(LOG) ? readFileSync(LOG, "utf8").trim().split("\n").map((l) => JSON.parse(l)) : []
const of = (role) => reqs.filter((q) => q.role === role)
const after = (role, step) => of(role).find((q) => q.step === step + 1)?.last || ""
const diag = () => `exit ${r.status}\n${(r.stderr || "").slice(-3000)}\n${(r.stdout || "").slice(-2000)}`
let pass = 0
const ok = (name) => { pass++; console.log("ok -", name) }

assert.equal(r.status, 0, diag())
const final = (r.stdout || "").trim().split("\n").map((l) => { try { return JSON.parse(l) } catch { return {} } }).filter((e) => e.type === "final").pop()
assert.ok(final && JSON.stringify(final).includes("E2E-DONE"), diag()); ok("dsh --profile aloop runs headless to the supervisor's final answer (exit 0)")

const sup = of("supervisor")
assert.ok(sup.length >= 7 && sup.every((q) => q.model === "deepseek-flash"), JSON.stringify(sup.map((q) => q.model)))
ok("the supervisor runs on $ALOOP_DSH_MODEL (the profile's agent-default-model)")
assert.ok(sup[0].tools.includes("subagent") && sup[0].tools.includes("subagent_planning") && !sup[0].tools.includes("subagent_fork"), sup[0].tools.join(","))
ok("the profile gives the supervisor both delegation tools (subagent, subagent_planning)")
assert.ok(/NEXT-MARKER/.test(sup[0].all) && /aloop tools: call `aloop <tool>`/.test(sup[0].all), sup[0].all.slice(0, 600))
ok("SessionStart: the handoff (NEXT.md) and the tools note reach the supervisor's first request")
assert.ok(/missing top YAML frontmatter/.test(after("supervisor", 0)), after("supervisor", 0)); ok("PostToolUse: gate-validate-artifact's block reaches the model")
assert.ok(!/BLOCK|unbound variable/.test(after("supervisor", 1)) && existsSync(join(story, "plan-ready.md")), after("supervisor", 1))
ok("a planning artifact passes gate-validate-artifact's schema check (the gates get the plugin root)")
assert.ok(/may not write production source/.test(after("supervisor", 2)), after("supervisor", 2)); ok("PreToolUse: gate-supervisor-scope's deny reaches the model")
assert.ok(/may not write production source/.test(after("supervisor", 3)) && !existsSync(join(P, "src/main.rs")), after("supervisor", 3))
ok("an argument the model adds (agent_type) never reaches a gate as a hook field")
assert.ok(/pass agent_preset/.test(after("supervisor", 4)), after("supervisor", 4)); ok("a dispatch without a role is refused")
assert.ok(/started subagent/.test(after("supervisor", 5)), after("supervisor", 5)); ok("gate-dispatch admits a role dispatched with its task contract and model")

const w = of("red-worker"), intake = of("intake")
assert.ok(w.length && w.every((q) => q.model === "deepseek-flash") && intake.length && intake.every((q) => q.model === "deepseek-v4-pro"),
  JSON.stringify([w.map((q) => q.model), intake.map((q) => q.model)]))
ok("each group runs on its own model: workers deepseek-flash (economical), planning deepseek-v4-pro (strongest)")
assert.ok(/aloop bind <that path>/.test(w[0].system), w[0].system.slice(0, 400)); ok("the role's persona (its agent file + the dsh note) is the child's prompt")
assert.ok(/bind your worktree first/.test(after("red-worker", 0)), after("red-worker", 0)); ok("an unbound writer is refused (SKIP_HOOKS=1 in the project's .env does not lift it)")
assert.ok(/bound: /.test(after("red-worker", 1)), after("red-worker", 1)); ok("aloop bind runs from the worker's shell")
assert.ok(after("red-worker", 2).includes(`pwd=${wt} host=dsh key=none`), after("red-worker", 2))
ok("the worker runs in the worktree it was dispatched with (cwd), knows its host, and never sees the API key")
assert.ok(/edit only files inside your worktree/.test(after("red-worker", 3)) && !existsSync(join(P, "src/escape.rs")), after("red-worker", 3))
ok("a bound writer's edit outside its worktree is denied")
assert.ok(existsSync(join(wt, "tests/a.rs")), after("red-worker", 4)); ok("a relative path lands in the worktree")
assert.ok(/stay in your worktree/.test(after("red-worker", 5)), after("red-worker", 5)); ok("working_directory may not leave the worktree")
assert.ok(/inside your worktree/.test(after("red-worker", 6)) && !existsSync(join(P, "WORKDIR_ESCAPE")), after("red-worker", 6))
ok("bash's own workdir is the directory judged: a command sent to the main tree is denied")
assert.ok(/edit only files inside your worktree/.test(after("red-worker", 8)) && !existsSync(join(P, "SYMLINK_ESCAPE.txt")), after("red-worker", 8))
ok("a write through a worktree symlink (`lnk/..`) is judged where dsh writes it, outside the worktree")
assert.ok(/inside your worktree/.test(after("red-worker", 9)) && !existsSync(join(P, "BIND_ESCAPE")), after("red-worker", 9))
ok("a bind in a comment does not lift confinement")
assert.ok(/stay in your worktree/.test(after("red-worker", 10)) && !existsSync(join(P, "CD_ESCAPE")), after("red-worker", 10))
assert.ok(/inside your worktree/.test(after("red-worker", 11)) && !existsSync(join(P, "REL_ESCAPE")), after("red-worker", 11))
ok("a command that `cd`s out of the worktree, or a relative workdir out of it, is denied")
assert.ok(/does not message the supervisor/.test(after("red-worker", 12)) && !/RED done, ready to merge/.test(sup.map((q) => q.all).join("\n")), after("red-worker", 12))
ok("a role's result reaches the supervisor only through its stop gate (send_message is refused)")
assert.ok([...w, ...intake].every((q) => !q.tools.includes("send_message") && !/with send_message\(/.test(q.all)) && sup.every((q) => q.tools.includes("send_message")),
  JSON.stringify([w[0].tools, (/Your parent agent id[^\n]*/.exec(w[0].all) || [])[0]]))
ok("a role is not offered send_message, so dsh adds no return guidance to its task; the supervisor keeps it")
const live = w.find((q) => /Agent \S+ sent a message: \s*LIVE-MARKER wrap up/.test(q.last))
assert.ok(/message delivered to agent/.test(after("supervisor", 6)) && live?.step === 14, JSON.stringify([after("supervisor", 6), live?.step]))
ok("live messaging: the supervisor's send_message reaches a role mid-turn, at its next step")
assert.ok(w.some((q) => q.step > 0 && /SOFT TIME LIMIT reached/.test(q.last)), JSON.stringify(w.map((q) => q.last.slice(-120))))
ok("the time soft-stop reaches a running worker mid-turn (tools/post-execute context, as PostToolUse on every host)")
assert.ok(/edit only files inside your worktree/.test(after("red-worker", 15)) && !existsSync(join(P, "VANISH_ESCAPE.txt")), after("red-worker", 15))
ok("a write after its working directory vanished is judged where dsh writes it (the main tree), and denied")
assert.ok(/inside your worktree/.test(after("red-worker", 16)) && !existsSync(join(P, "CHAIN_ESCAPE")), after("red-worker", 16))
ok("a command chained to a bind with no space (`aloop bind <wt>;…`) is confined")

const steered = w.filter((q) => /\[aloop gate gate-red-verify\] Your stop was blocked/.test(q.last))
assert.equal(steered.length, 3, JSON.stringify(w.map((q) => q.last.slice(0, 80))))
ok("SubagentStop: gate-red-verify's block steers the worker back to work (native, before dsh settles it), 3 rounds (GATE_RUN_TESTS=0 in .env ignored)")
assert.ok(intake.length === 2 && !intake.some((q) => /Your stop was blocked/.test(q.last)), JSON.stringify(intake.map((q) => q.last.slice(0, 200))))
ok("a role that passes its own gate (gate-intake) stops at once")
const told = sup.map((q) => q.all).join("\n")
assert.ok(/red-worker \S+: BLOCKED after 3 fix rounds/.test(told) && !/intake \S+: BLOCKED/.test(told), told.slice(-1500))
ok("a gate still blocking after 3 rounds is reported to the supervisor, never a silent pass")
assert.ok(/Background subagent \S+ finished[^\n]*\nIts closing message:\s*intake done/.test(told), told.slice(-1500))
ok("a role's closing message reaches the supervisor in dsh's settlement notice")

const compacted = reqs.filter((q) => q.role === "compaction" && q.of === "supervisor")
const retry = sup.find((q) => q.step === 7 && !q.overflow)
assert.ok(sup.some((q) => q.step === 7 && q.overflow) && compacted.length === 1 && /<compacted-summary>[\s\S]*aloop-fake checkpoint/.test(retry?.all || ""),
  JSON.stringify([compacted, retry?.all.slice(0, 600)]))
ok("dsh compacts a session whose context overflowed (a summary checkpoint replaces the history) and retries")
const events = readFileSync(join(P, ".aladdin/logs/events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l))
assert.equal(events.filter((e) => e.event === "context.compacted").length, 1, JSON.stringify(events.map((e) => e.event)))
assert.ok(sup.some((q) => /NEXT-MARKER/.test(q.all.split("</compacted-summary>")[1] || "")), JSON.stringify(sup.map((q) => q.all.slice(-300))))
ok("the compaction is recorded (context.compacted) and the handoff rejoins the compacted session, as SessionStart(compact)")

const gates = readFileSync(join(P, ".aladdin/logs/gates.jsonl"), "utf8")
assert.ok(/"gate":"gate-red-verify".*"exit":2/.test(gates) && /"selfcheck":false/.test(gates), gates.slice(0, 600))
ok("the host ran the worker's gate (gates.jsonl)")
const agents = readdirSync(join(P, ".aladdin/state/agents")).map((f) => JSON.parse(readFileSync(join(P, ".aladdin/state/agents", f), "utf8")))
assert.ok(agents.some((a) => a.agent_type === "red-worker" && a.worktree === wt && a.host === "dsh" && a.stopped)
  && agents.some((a) => a.agent_type === "intake" && a.host === "dsh" && a.stopped && a.transcript), JSON.stringify(agents))
ok("both roles are registered with their stop and transcript, the worker with its binding (host-adapter)")
const hostDir = join(P, ".aladdin/host/dsh")
const rootLog = readdirSync(hostDir).find((f) => f.endsWith(".jsonl"))
const subs = join(hostDir, rootLog.replace(/\.jsonl$/, ""), "subagents")
const metas = readdirSync(subs).filter((f) => f.endsWith(".meta.json")).map((f) => JSON.parse(readFileSync(join(subs, f), "utf8")).agentType).sort()
assert.deepEqual(metas, ["intake", "red-worker"])
const line = JSON.parse(readFileSync(join(hostDir, rootLog), "utf8").split("\n")[0])
assert.deepEqual(line.message.usage, { input_tokens: 100, output_tokens: 10, cache_read_input_tokens: 50, cache_creation_input_tokens: 0 })
ok("token usage is written Claude-shaped per session and per role (.aladdin/host/dsh)")
assert.ok(readdirSync(join(P, ".aladdin/ledger")).some((f) => f.endsWith(".jsonl"))); ok("Stop: the token ledger is written for the session")

console.log(`\n${pass} checks passed`)
