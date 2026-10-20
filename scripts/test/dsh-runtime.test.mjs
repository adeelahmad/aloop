// The aloop plugin on the REAL DeepSeek Harness runtime (dsh), driven by a scripted model.
// `aloop install dsh` renders the profile into a throw-away DSH_HOME, then `dsh --profile aloop
// --json` runs a supervisor that dispatches a red-worker and a state-assessor (subagent), and an
// intake, an archivist and a market-researcher (subagent_planning) against fixtures/fake-deepseek.mjs.
// The REAL bin/ scripts run in a throw-away git repo. No network, no tokens. It proves on dsh itself
// what the emulated test (dsh-plugin.test.mjs) assumes: the handoff reaches the model, a deny /
// post-execute block reaches it, the profile's roles, tools and models, worker confinement, the
// per-role stop gate (a block steers the child and is reported to the supervisor after 3 rounds; an
// intake that stops to ask is released at once as NEEDS-HUMAN; a role that passes stops at once), a
// mission role's two gates (gate-mission-artifact's start record and stop check, gate-mission-scope),
// and that the one install runs a second project's roles on that project's own models. It also proves
// what the capability matrix claims for dsh: a role is not offered send_message (so dsh adds no return
// guidance), the supervisor's send_message reaches a running role (live messaging), the time
// soft-stop reaches a running worker, and dsh compacts a session (a context overflow here), the
// plugin records it and the handoff (the core principles with it) joins the first request after
// it; every call dsh made (its compaction and session-title calls too) is priced, to the micro-dollar.
//   DSH_BIN=<dsh's lib/bin.js or a dsh executable> node scripts/test/dsh-runtime.test.mjs
// Without DSH_BIN it uses `dsh` on PATH, and skips when there is none (ALOOP_REQUIRE_DSH=1 fails
// instead: CI installs the pinned @deepseek-ai/dsh and sets it).
import { execFileSync, spawn, spawnSync } from "node:child_process"
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { delimiter, dirname, join, relative } from "node:path"
import { fileURLToPath } from "node:url"
import assert from "node:assert/strict"
import { approvePlan } from "./fixtures/plan-fixture.mjs"

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
// stand-in host CLIs: a session a role starts from a tool call would land in sessions.log
const HOSTS = join(T, "hosts")
mkdirSync(HOSTS)
for (const h of ["claude", "dsh"]) { writeFileSync(join(HOSTS, h), `#!/bin/sh\necho "${h} $*" >> ${join(T, "sessions.log")}\n`); chmodSync(join(HOSTS, h), 0o755) }
const env = { ...process.env, HOME, DSH_HOME: join(T, "dsh-home"), AGENTIC_NO_AUTOINSTALL: "1", CLAUDE_PLUGIN_DATA: join(T, "pdata"),
  AGENTIC_STUCK_BLOCKS: "99", PATH: [join(HOME, ".local", "bin"), HOSTS, process.env.PATH || ""].join(delimiter) }
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
approvePlan(P, BIN, 1, env)             // the human's go on sprint 1 (M0.9): gate-dispatch admits nothing before it

const red = "TASK_ID: S1-01-T1\nYour worktree: " + wt + ". First command: `aloop bind " + wt + "`."
// the Scout's sourced report, with the harness's own commands: a saved page archived, one claim quoting it
const page = "docs/agents/mission/research/pages/todo.html"
const report = `mkdir -p ${dirname(page)} && printf '<p>Todoist is the most used to-do app among teams.</p>\\n' > ${page}` +
  ` && r=$(aloop mission research new "To-do rivals" --question "Which to-do apps lead?") && id=$(basename "$r" .json)` +
  ` && aloop mission research archive "$id" https://example.com/todo --file ${page}` +
  ` && aloop mission research claim "$id" "Todoist leads among teams" --kind rival --source s1 --quote "Todoist is the most used to-do app among teams"`
const script = {
  supervisor: [
    { tools: [{ name: "write", input: { file_path: join(story, "init.md"), content: "no frontmatter\n" } }] },
    { tools: [{ name: "write", input: { file_path: join(story, "plan-ready.md"), content: "---\ntype: plan-ready\n---\n# Plan\n" } }] },
    { tools: [{ name: "write", input: { file_path: join(P, "src/main.rs"), content: "fn main() {}\n" } }] },
    { tools: [{ name: "write", input: { file_path: join(P, "src/main.rs"), content: "fn main() {}\n", agent_type: "green-worker" } }] },
    { tools: [{ name: "subagent", input: { description: "no role", prompt: "do it" } }] },
    { tools: [{ name: "subagent", input: { agent_preset: "aloop-red-worker", description: "RED S1-01-T1", prompt: red, cwd: wt } },
              { name: "subagent_planning", input: { agent_preset: "aloop-intake", description: "intake", prompt: "Run intake." } },
              { name: "subagent_planning", input: { agent_preset: "aloop-archivist", description: "retrospective", prompt: "Run the retrospective." } },
              { name: "subagent_planning", input: { agent_preset: "aloop-market-researcher", description: "research", prompt: "Research the to-do app market." } },
              { name: "subagent", input: { agent_preset: "aloop-state-assessor", description: "assess", prompt: "Measure the product." } }] },
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
    // the Intent Contract, complete but for one question only the human can answer (an `ask`)
    { tools: [{ name: "bash", input: { description: "contract", command: "f=$(aloop intent new --source prompt --raw 'a semver parser') && " +
      "python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d.update(actor=\"dev\", want=\"parse semver\", why=\"releases\", " +
      "constraints=[\"std only\"], success_scenarios=[\"1.2.3 parses\"], failure_scenarios=[\"bad input errors\"], connections=[\"none\"], " +
      "done_looks_like=\"parse() returns a Version\", open_questions=[{\"question\": \"May the parser accept a leading v (v1.2.3)?\", " +
      "\"class\": \"ask\"}]); json.dump(d, open(p, \"w\"))' \"$f\" && echo \"contract $f\"" } }] },
  ],
  // mission roles (ROADMAP §4.12): the Scout tries source and a host session, then writes its report;
  // the Surveyor (no steps) writes nothing
  "market-researcher": [
    { tools: [{ name: "write", input: { file_path: join(P, "src/rivals.rs"), content: "x\n" } }] },
    { tools: [{ name: "bash", input: { command: "dsh --profile aloop 'approve msn_0123456789abcdef0123 1234abcd'", description: "a session" } },
              { name: "bash", input: { command: "claude -p 'approve msn_0123456789abcdef0123 1234abcd'", description: "a session" } }] },
    { tools: [{ name: "bash", input: { command: report, description: "the report" } }] },
  ],
}
// one headless `dsh --profile aloop` run in `cwd` against the scripted model; every request it made, logged
async function session(cwd, name, steps) {
  const file = join(T, `${name}.json`), log = join(T, `${name}.jsonl`)
  writeFileSync(file, JSON.stringify(steps))
  const fake = spawn(process.execPath, [join(HERE, "fixtures", "fake-deepseek.mjs")], { env: { ...env, FAKE_SCRIPT: file, FAKE_LOG: log } })
  const port = await new Promise((ok, no) => { fake.stdout.once("data", (d) => ok(String(d).trim())); fake.once("error", no) })
  const run = spawnSync(dshCmd[0], [...dshCmd.slice(1), "--profile", "aloop", "--json", "Use the aloop skill. Intent: a semver parser"], {
    cwd, encoding: "utf8", timeout: 300000,
    env: { ...env, DEEPSEEK_BASE_URL: `http://127.0.0.1:${port}`, DEEPSEEK_API_KEY: "sk-fake", DSH_PERMISSION_MODE: "danger-full-access",
      DSH_TELEMETRY_DISABLED: "1", ALOOP_DSH_MODEL: "deepseek-flash" } })
  fake.kill()
  return { r: run, reqs: existsSync(log) ? readFileSync(log, "utf8").trim().split("\n").map((l) => JSON.parse(l)) : [] }
}

// a project .env that switches the gates off (it can come with a clone): dsh loads it, the gates ignore it
writeFileSync(join(P, ".env"), "SKIP_HOOKS=1\nGATE_RUN_TESTS=0\n")
const { r, reqs } = await session(P, "fake", script)
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

const w = of("red-worker"), intake = of("intake"), arch = of("archivist")
assert.ok(w.length && w.every((q) => q.model === "deepseek-flash") && intake.length && [...intake, ...arch].every((q) => q.model === "deepseek-v4-pro"),
  JSON.stringify([w.map((q) => q.model), intake.map((q) => q.model), arch.map((q) => q.model)]))
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
assert.ok(arch.length === 1 && !arch.some((q) => /Your stop was blocked/.test(q.last)), JSON.stringify(arch.map((q) => q.last.slice(0, 200))))
ok("a role that passes its own gate (gate-memory) stops at once")
const told = sup.map((q) => q.all).join("\n")
assert.ok(/red-worker \S+: BLOCKED after 3 fix rounds/.test(told) && !/(intake|archivist) \S+: BLOCKED/.test(told) && !/archivist \S+: NEEDS-HUMAN/.test(told), told.slice(-1500))
ok("a gate still blocking after 3 rounds is reported to the supervisor, never a silent pass")
assert.ok(/contract \S+int_\w+\.json/.test(after("intake", 1)), after("intake", 1))
assert.ok(intake.length === 3 && !intake.some((q) => /Your stop was blocked/.test(q.last)), JSON.stringify(intake.map((q) => q.last.slice(0, 200))))
assert.ok(/intake \S+: NEEDS-HUMAN — released UNVERIFIED \(not a pass\)/.test(told) && /May the parser accept a leading v \(v1\.2\.3\)\?/.test(told), told.slice(-1500))
ok("an intake that stops to ask is released at once, never steered: the supervisor is told NEEDS-HUMAN with its question (ROADMAP §4.9)")
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
// ...on the FIRST request after it, the overflow retry itself (dsh admits inbox messages only on a step's
// first attempt, so an injected handoff would arrive a step late), and once: no later request repeats it
const tail = (q) => q.all.split("</compacted-summary>")[1] || ""
const later = sup.filter((q) => q.step >= 7 && !q.overflow)
assert.ok(/NEXT-MARKER/.test(tail(retry)) && /aloop tools: call `aloop <tool>`/.test(tail(retry))
  && /Core principles \(every aloop agent\):\n1\. Two-way door/.test(tail(retry))
  && later.every((q) => (tail(q).match(/NEXT-MARKER/g) || []).length === 1 && (tail(q).match(/Core principles/g) || []).length === 1),
  JSON.stringify(later.map((q) => [q.step, tail(q).slice(0, 400)])))
ok("the first request after a compaction (the overflow retry) carries the handoff, the core principles and the tools note, once")

// a mission role on each route (ROADMAP §4.12): the Scout through subagent_planning, the Surveyor through subagent
const scout = of("market-researcher"), surveyor = of("state-assessor")
assert.ok(scout.length && scout.every((q) => q.model === "deepseek-v4-pro") && surveyor.length && surveyor.every((q) => q.model === "deepseek-flash"),
  JSON.stringify([scout.map((q) => q.model), surveyor.map((q) => q.model)]))
ok("a mission role runs on its route's model: market-researcher (subagent_planning) deepseek-v4-pro, state-assessor (subagent) deepseek-flash")
const agents = readdirSync(join(P, ".aladdin/state/agents")).map((f) => JSON.parse(readFileSync(join(P, ".aladdin/state/agents", f), "utf8")))
const idOf = (role) => agents.find((a) => a.agent_type === role)?.agent_id || "-"
const runOf = (role) => { try { return JSON.parse(readFileSync(join(P, ".aladdin/state/mission-runs", `${idOf(role)}.json`), "utf8")) } catch { return {} } }
assert.ok(["market-researcher", "state-assessor"].every((role) => runOf(role).role === role && runOf(role).agent_id === idOf(role)),
  JSON.stringify([agents.map((a) => [a.agent_type, a.agent_id]), existsSync(join(P, ".aladdin/state/mission-runs")) && readdirSync(join(P, ".aladdin/state/mission-runs"))]))
ok("SubagentStart: gate-mission-artifact start records each mission role's run (.aladdin/state/mission-runs/<agent>.json)")
assert.ok(/market-researcher writes only/.test(after("market-researcher", 0)) && !existsSync(join(P, "src/rivals.rs")), after("market-researcher", 0))
ok("PreToolUse: gate-mission-scope denies a mission role's write outside its own paths")
assert.ok(/starts a dsh session from a tool call/.test(after("market-researcher", 1)) && /starts a claude session from a tool call/.test(after("market-researcher", 1))
  && !existsSync(join(T, "sessions.log")), after("market-researcher", 1))
ok("gate-mission-scope denies a dsh or claude session started from a mission role's tool call (its prompt would pass for the human's)")
const reports = readdirSync(join(P, "docs/agents/mission/research")).filter((f) => /^rsr_[0-9a-f]+\.json$/.test(f))
const by = (event, role) => events.filter((e) => e.event === event && e.role === role && e.ids?.agent === idOf(role))
assert.ok(reports.length === 1 && by("artifact.created", "market-researcher").some((e) => e.refs?.artifact === `docs/agents/mission/research/${reports[0]}`)
  && by("gate.passed", "market-researcher").length === 1 && scout.length === 4 && !scout.some((q) => /Your stop was blocked/.test(q.last))
  && !/market-researcher \S+: BLOCKED/.test(told),
  JSON.stringify([reports, scout.map((q) => q.last.slice(0, 300)), events.filter((e) => e.role === "market-researcher").map((e) => e.event)]))
ok("SubagentStop: a mission role that writes a valid artifact (a sourced report) passes gate-mission-artifact and stops at once")
const nudged = surveyor.filter((q) => /\[aloop gate gate-mission-artifact\] Your stop was blocked:[\s\S]*you wrote no artifact/.test(q.last))
assert.ok(nudged.length === 3 && /state-assessor \S+: BLOCKED after 3 fix rounds[\s\S]*you wrote no artifact/.test(told),
  JSON.stringify([surveyor.map((q) => q.last.slice(0, 200)), told.slice(-1500)]))
ok("SubagentStop: a mission role that wrote no artifact is steered back by gate-mission-artifact (3 rounds), then reported BLOCKED")

const gates = readFileSync(join(P, ".aladdin/logs/gates.jsonl"), "utf8")
assert.ok(/"gate":"gate-red-verify".*"exit":2/.test(gates) && /"selfcheck":false/.test(gates), gates.slice(0, 600))
ok("the host ran the worker's gate (gates.jsonl)")
assert.ok(agents.some((a) => a.agent_type === "red-worker" && a.worktree === wt && a.host === "dsh" && a.stopped)
  && agents.some((a) => a.agent_type === "intake" && a.host === "dsh" && a.stopped && a.transcript), JSON.stringify(agents))
ok("both roles are registered with their stop and transcript, the worker with its binding (host-adapter)")
const hostDir = join(P, ".aladdin/host/dsh")
const rootLog = readdirSync(hostDir).find((f) => f.endsWith(".jsonl"))
const subs = join(hostDir, rootLog.replace(/\.jsonl$/, ""), "subagents")
const metas = readdirSync(subs).filter((f) => f.endsWith(".meta.json")).map((f) => JSON.parse(readFileSync(join(subs, f), "utf8")).agentType).sort()
assert.deepEqual(metas, ["archivist", "intake", "market-researcher", "red-worker", "state-assessor"])
const line = JSON.parse(readFileSync(join(hostDir, rootLog), "utf8").split("\n")[0])
assert.deepEqual(line.message.usage, { input_tokens: 100, output_tokens: 10, cache_read_input_tokens: 50, cache_creation_input_tokens: 0 })
ok("token usage is written Claude-shaped per session and per role (.aladdin/host/dsh)")
assert.ok(readdirSync(join(P, ".aladdin/ledger")).some((f) => f.endsWith(".jsonl"))); ok("Stop: the token ledger is written for the session")
// dsh prints no cost: run-summary prices these logs by the model id dsh sent (the price table's key),
// every call at the scripted usage, between its off-peak and its peak price (the band is the call's time)
writeFileSync(join(T, "out.jsonl"), r.stdout || "")
const rs = spawnSync(join(BIN, "run-summary"), [T, "--host", "dsh"], { env, encoding: "utf8" })
assert.equal(rs.status, 0, rs.stderr)
const sum = JSON.parse(readFileSync(join(T, "summary.json"), "utf8"))
assert.deepEqual(Object.keys(sum.cost_by_model || {}).sort(), ["deepseek-flash", "deepseek-v4-pro"], JSON.stringify(sum))
for (const [m, [miss, hit, out]] of Object.entries({ "deepseek-flash": [0.15, 0.003, 0.6], "deepseek-v4-pro": [0.66, 0.022, 1.98] })) {
  const c = sum.cost_by_model[m], low = c.calls * (100 * miss + 50 * hit + 10 * out) / 1e6
  assert.ok(c.calls > 0 && c.in === 100 * c.calls && c.cache_read === 50 * c.calls && c.out === 10 * c.calls, JSON.stringify(c))
  assert.ok(c.usd >= low - 1e-6 && c.usd <= 2 * low + 1e-6, JSON.stringify([m, c, low]))
}
assert.ok(sum.cost_usd !== null, JSON.stringify(sum))
ok("run-summary prices the runtime's usage per model id dsh sent (deepseek-flash, deepseek-v4-pro), never unmeasured")
// every reply the endpoint served is a priced call: the role turns, dsh's compaction and its session title
const served = reqs.filter((q) => !q.overflow), priced = Object.values(sum.cost_by_model).reduce((n, c) => n + c.calls, 0)
assert.ok(served.some((q) => q.role === "title") && served.some((q) => q.role === "compaction") && priced === served.length,
  JSON.stringify([priced, served.length, served.filter((q) => ["title", "compaction"].includes(q.role))]))
ok("every call dsh made is priced, its compaction and session-title calls included (never a partial sum as the total)")
// a DeepSeek run costs a fraction of a cent here: kept to the micro-dollar (the total and each model's
// sum are each rounded to 6 places), and the evidence row shows it to four
const usd = Object.values(sum.cost_by_model).reduce((n, c) => n + c.usd, 0), places = 1e-6 * Object.keys(sum.cost_by_model).length
const cell = (rs.stdout.split("\n").filter((l) => l.startsWith("| ")).pop() || "").split("|").map((c) => c.trim())[6] || ""
assert.ok(sum.cost_usd > 0 && Math.abs(sum.cost_usd - usd) <= places && /^\$\d+\.\d{4}$/.test(cell) && Math.abs(Number(cell.slice(1)) - sum.cost_usd) < 5e-5,
  JSON.stringify([sum.cost_usd, usd, cell]))
ok("a sub-cent dsh cost is kept: cost_usd is the sum of cost_by_model (> 0), and the evidence row shows it, never $0.00")

// One install serves every project: a second project with other models, where `aloop install dsh` never
// ran (the one profile still records the first project and its models), runs each role on ITS models.
const Q = join(T, "repo-q")
mkdirSync(Q)
const shq = (cmd, args) => execFileSync(cmd, args, { cwd: Q, env, encoding: "utf8" })
shq("git", ["init", "-q"]); shq("git", ["config", "user.email", "ada@x.io"]); shq("git", ["config", "user.name", "Ada"])
shq("git", ["commit", "-q", "--allow-empty", "-m", "init"])
const PROFILE = join(env.DSH_HOME, "profiles/aloop/cordis.patch.yml"), rendered = readFileSync(PROFILE, "utf8")
shq(join(BIN, "aloop-init"), ["--apply", "TOKENS_SOFT=100000", "TOKENS_HARD=200000", "MODEL_PLANNING_DSH=deepseek-flash", "MODEL_WORKER_DSH=deepseek-v4-pro"])
assert.ok(readFileSync(PROFILE, "utf8") === rendered && rendered.includes(`# aloop-project: ${realpathSync(P)}\n`)
  && /toolName: subagent, .*model: "deepseek-flash"/.test(rendered) && /toolName: subagent_planning, .*model: "deepseek-v4-pro"/.test(rendered), rendered.slice(0, 1200))
ok("a second project's init leaves the one profile as the first project rendered it (its owner and models)")
const qstory = join(Q, "docs/agents/sprint1/s1-01-x")
mkdirSync(qstory, { recursive: true })
mkdirSync(join(Q, ".aladdin/state/tasks"), { recursive: true })
writeFileSync(join(Q, ".aladdin/state/tasks/S1-01-T1.env"), `TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=red-worker\nSTORY_DIR=${qstory}\n`)
approvePlan(Q, BIN, 1, env)             // the second project's own human go (M0.9): approval never carries across projects
const q = await session(Q, "fake-q", {
  supervisor: [
    { tools: [{ name: "subagent", input: { agent_preset: "aloop-red-worker", description: "RED S1-01-T1", prompt: "TASK_ID: S1-01-T1\nWrite the failing tests." } },
              { name: "subagent_planning", input: { agent_preset: "aloop-intake", description: "intake", prompt: "Run intake." } }] },
    { text: "waiting for the roles" },
  ],
  intake: script.intake.map((s) => ({ tools: s.tools.map((t) => ({ ...t, input: { ...t.input, file_path: join(Q, "docs/agents/sprint1/intake.md") } })) })),
})
const qof = (role) => q.reqs.filter((x) => x.role === role)
assert.equal(q.r.status, 0, `exit ${q.r.status}\n${(q.r.stderr || "").slice(-3000)}`)
const qsup = qof("supervisor"), dispatched = qsup.find((x) => x.step === 1)?.last || ""
assert.ok((dispatched.match(/started subagent/g) || []).length === 2 && !/BLOCK\[dispatch\]/.test(dispatched), dispatched)
ok("gate-dispatch admits the second project's roles with no `aloop install dsh` there")
const qw = qof("red-worker"), qi = qof("intake")
assert.ok(qw.length && qw.every((x) => x.model === "deepseek-v4-pro") && qi.length && qi.every((x) => x.model === "deepseek-flash"),
  JSON.stringify([qw.map((x) => x.model), qi.map((x) => x.model)]))
assert.ok(of("red-worker").every((x) => x.model === "deepseek-flash") && of("intake").every((x) => x.model === "deepseek-v4-pro"))
ok("each project's roles run on its own models from one profile: the second's workers deepseek-v4-pro (MODEL_WORKER_DSH), planning deepseek-flash (MODEL_PLANNING_DSH)")
assert.ok(/no aloop skills for dsh.*aloop install dsh --project/s.test(qsup[0].all) && !/role models are that project's/.test(qsup[0].all), qsup[0].all.slice(0, 1500))
ok("a session in a project without aloop's dsh skills is told to install them there, never that its role models are another project's")

console.log(`\n${pass} checks passed`)
