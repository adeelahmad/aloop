// aloop for DeepSeek Harness (dsh).
// Loaded by the `aloop` dsh profile that `aloop install dsh` writes
// ($DSH_HOME/profiles/aloop/cordis.patch.yml, row `aloop`, config {root, project, models, maxStopBlocks}).
// Maps dsh's plugin events onto the SAME scripts Claude Code runs from hooks/hooks.json, so gates,
// time boxes, the token ledger, stats and the handoff behave identically on every host:
//
//   Claude Code hook   dsh point used here
//   SessionStart       agent/created of a root agent → ensure-tools, session-start; the context is
//                      injected into the session (its first step claims it). dsh compacts a session
//                      in place (near its context limit, or /compact), so its compaction/end runs
//                      them again as source `compact`: context.compacted, and the handoff returns
//   UserPromptSubmit   agent/pre-step of a root agent with a user message → transcripts prompt,
//                      intent-trigger; exit 2 rejects the step, context joins it
//   PreToolUse         tools/pre-execute → host-adapter, gate-supervisor-scope, gate-mission-scope,
//                      gate-commit-author, gate-dispatch, budget pre; a deny hands the reason to the model. dsh cannot
//                      rewrite a call, so a bound worker is confined by denial (host-adapter)
//   PostToolUse        tools/post-execute → gate-validate-artifact, transcripts record, budget post;
//                      exit 2 turns the result into the gate's feedback, context joins the next
//                      request, `continue: false` cancels the agent
//   SubagentStart      agent/created of a child → send_message scoped out of its tools,
//                      host-adapter, transcripts stage-in, worktree-hygiene, a mission role's
//                      gate-mission-artifact start; gate-tooling on an execution role's first tool call
//   SubagentStop       the child's own agent/turn-stopping → its role's gate. A block steers the child:
//                      it keeps working and dsh does not settle it (native, like Claude's exit 2), up
//                      to maxStopBlocks rounds. A STUCK or NEEDS-HUMAN release, or a block past them,
//                      is injected into the supervisor as such, never a silent pass
//   Stop               agent/turn-stopping of a root agent → transcripts snapshot, stats hook; a
//                      `decision: block` reason steers the session
// Roles are agent presets `aloop-<role>` (a child carries its preset in session.header.agentPreset).
// Models: the one profile's delegation tools start a role on the models of the project it was rendered
// for; agent/request routes each of the role's requests to its OWN project's model (`aloop models resolve
// <role>` in the session's project, the answer gate-dispatch checks), so one install serves every project.
// Token usage: every assistant/message session event is appended, Claude-shaped, to
// <run-data>/host/dsh/<root>.jsonl (children: <root>/subagents/agent-<id>.jsonl), so the ledger
// and the time box read it like any Claude transcript.
// Self-contained (node: built-ins only): a module outside the dsh profile cannot import dsh packages.
import { spawn } from "node:child_process"
import { randomUUID } from "node:crypto"
import { appendFileSync, existsSync, mkdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, isAbsolute, join, resolve, sep } from "node:path"
import { parseEnv } from "node:util"

export const name = "aloop"
export const inject = []

const WRITERS = new Set(["red-worker", "scaffolder", "green-worker"])
const EXECUTION = new Set([...WRITERS, "structural-reviewer", "final-gate"])
const GATES = {
  "red-worker": "gate-red-verify", scaffolder: "gate-scaffold-verify", "green-worker": "gate-green-verify",
  "structural-reviewer": "gate-structural-integrity", "final-gate": "gate-final",
  intake: "gate-intake", standards: "gate-standards-cited", planner: "gate-stage2-complete", archivist: "gate-memory",
  // the mission roles (ROADMAP §4.12): each one's artifacts are checked when it stops
  "market-researcher": "gate-mission-artifact", "product-tester": "gate-mission-artifact",
  "state-assessor": "gate-mission-artifact", evaluator: "gate-mission-artifact",
  "mission-planner": "gate-mission-artifact", "ethics-reviewer": "gate-mission-artifact",
}
const MISSION = new Set(Object.keys(GATES).filter((r) => GATES[r] === "gate-mission-artifact"))
const DISPATCH = new Set(["subagent", "subagent_planning"])
const TOOL = { bash: "Bash", write: "Write", edit: "Edit", read: "Read", glob: "Glob", grep: "Grep", subagent: "Agent", subagent_planning: "Agent" }
// a gate's release (exit 0, NOT a pass): the same block N times (STUCK), or questions only the human can answer
// questions only the intake can raise. Read from the gate's own marker line (`[<gate>] STUCK …` at a line start, the
// last one), never from model text it quotes (an intake's question, a test's output); NEEDS-HUMAN only at the intake's stop.
const RELEASE = /^\[[\w-]+\] (STUCK|NEEDS-HUMAN)\b[^\n]*NOT verified/gm
const releaseOf = (stderr, role) => {
  const kind = [...String(stderr || "").matchAll(RELEASE)].at(-1)?.[1]
  return kind === "NEEDS-HUMAN" && role !== "intake" ? null : kind || null
}
const TOOLS_NOTE = "aloop tools: call `aloop <tool>` (e.g. `aloop selfcheck`, `aloop stats show`); never search for them."
const PROVIDER = "deepseek-official"      // bin/install's DSH_PROVIDER: a bare model id is DeepSeek's own

// A gate runs the worker's own code (its tests), so it gets the environment dsh gives the worker's
// shell: credential-shaped names and DSH_* dropped (dsh-subprocess's scrub), aloop's own settings
// (TOKENS_SOFT…) and git's GIT_CONFIG_COUNT/KEY_n/VALUE_n set kept whole (a dropped KEY_n makes
// every git command fail), plus the plugin root the gates read their schemas from.
const SCRUB = /KEY|PASSWORD|SECRET|TOKEN/i
const KEEP = /^(AGENTIC_|ALOOP_|ALADDIN_|CLAUDE_PLUGIN_)|^(SPRINT_)?TOKENS_(SOFT|HARD)$|^TOKEN_METER$|^TASK_ENV_KEYS$|^GIT_CONFIG_(COUNT|KEY_\d+|VALUE_\d+)$/
// dsh also loads the launch directory's .env into its environment (loadLayeredEnv: a name nobody
// exported), and that file can come with a clone or a worker's commit. A gate switch from it
// (SKIP_HOOKS, GATE_RUN_TESTS=0, a REPO_DIR or AGENTIC_* override) would turn a gate off, so it never
// reaches a gate; the same setting exported at launch still does.
const GATE_SWITCH = /^(SKIP_HOOKS|GATE_\w+|REPO_DIR|STORY_DIR|SPRINT_DIR|BASE_REF|SCOPE_GLOBS|TEST_GLOBS|SCAFFOLD_SYMBOLS|STANDARDS_FILE|INTAKE_FILE|MEMORY_FILE|TASK_\w+|ATTEMPT|AGENT_ROLE|TOOL_FILE_PATH|TOKEN_METER|(SPRINT_)?TOKENS_\w+|(AGENTIC|ALADDIN|ALOOP|CLAUDE|CODEX|XDG)_\w+)$/i
function fileSwitches(dir) {
  let values = {}
  try { values = parseEnv(readFileSync(join(dir, ".env"), "utf8")) } catch {}
  return new Set(Object.keys(values).filter((k) => GATE_SWITCH.test(k) && process.env[k] === values[k]))
}
function gateEnv(root, drop = new Set()) {
  const env = {}
  for (const [k, v] of Object.entries(process.env))
    if (v !== undefined && !drop.has(k) && !/^DSH_/i.test(k) && (KEEP.test(k) || !SCRUB.test(k))) env[k] = v
  return { ...env, AGENTIC_HOST: "dsh", ...(root ? { CLAUDE_PLUGIN_ROOT: root } : {}) }
}

const safe = (s) => String(s || "").replace(/[^A-Za-z0-9_.-]/g, "_")
const freeze = (o) => { Object.freeze(o); for (const v of Object.values(o)) if (v && typeof v === "object" && !Object.isFrozen(v)) freeze(v); return o }
// a dsh UserMessage (what @deepseek-ai/dsh-llm's createUserMessage builds); `plugin` is a reserved source kind
const msg = (text) => freeze({ role: "user", id: randomUUID(), content: [{ type: "text", text }], source: { kind: "aloop" } })
const textOf = (content) => typeof content === "string" ? content : (content || []).map((b) => b?.text || "").filter(Boolean).join("\n")
const under = (p, root) => { const r = resolve(p), w = resolve(root); return r === w || r.startsWith(w + sep) }
// a model id as the profile routes it (bin/install's dsh_agent_options): `<provider>/<model>`, or a bare DeepSeek id
const routeOf = (m) => { const i = m.indexOf("/"); return i < 0 ? { provider: PROVIDER, model: m } : { provider: m.slice(0, i), model: m.slice(i + 1) } }
// the aloop skills dsh loads for a project: its own .dsh/skills, or $DSH_HOME/skills (`--global`)
const hasSkills = (project) => [join(project, ".dsh"), process.env.DSH_HOME?.trim() || join(homedir(), ".dsh")]
  .some((d) => existsSync(join(d, "skills", "aloop", "SKILL.md")))

// The project a directory belongs to: the main tree, also from inside a linked worktree.
function projectOf(dir) {
  for (let d = resolve(dir); ; d = dirname(d)) {
    const g = join(d, ".git")
    try {
      if (statSync(g).isDirectory()) return d
      const m = /^gitdir:\s*(.+)$/m.exec(readFileSync(g, "utf8"))     // <main>/.git/worktrees/<name>
      if (m) { const gd = resolve(d, m[1].trim()); return gd.includes(`${sep}.git${sep}worktrees${sep}`) ? gd.split(`${sep}.git${sep}`)[0] : d }
      return d
    } catch {}
    if (dirname(d) === d) return resolve(dir)
  }
}
// the run-data dir (bin/_paths.sh): .aladdin/, or the legacy .agentic/ until `aloop layout migrate`
function homeOf(project) {
  const nu = join(project, ".aladdin")
  if (["transcripts", "budget", "ledger", "logs", "state", "host", "worktrees"].some((d) => existsSync(join(nu, d)))) return nu
  return existsSync(join(project, ".agentic")) ? join(project, ".agentic") : nu
}

export function apply(ctx, config = {}) {
  const ROOT = config.root
  const BIN = join(ROOT, "bin")
  const MAX = Number.isInteger(config.maxStopBlocks) ? config.maxStopBlocks : 3
  // A model's shell gets dsh's own environment (secrets and DSH_* removed), so this reaches every
  // `aloop …` the supervisor or a worker runs: bin/ scripts then know their host.
  process.env.AGENTIC_HOST = "dsh"
  const FROM_FILE = fileSwitches(process.cwd())
  if (FROM_FILE.size) process.stderr.write(`[aloop] gates ignore ${[...FROM_FILE].join(", ")} from ${join(process.cwd(), ".env")} (export a setting to use it)\n`)
  const sessions = new Map()      // session id -> state
  const dispatched = new Map()    // a delegation call's id -> the model its role runs on

  function info(x) {
    const session = x?.session || x
    const h = session?.header || {}
    const id = h.id || ""
    let s = sessions.get(id)
    if (!s) {
      const parent = h.parentSession || null
      const ps = parent ? sessions.get(parent) : null
      const cwd0 = h.cwd || process.cwd()
      s = { id, parent, child: !!parent, root: ps ? ps.root : (parent || id), cwd0,
            role: String(h.agentPreset || "").replace(/^aloop-/, ""), project: ps ? ps.project : projectOf(cwd0),
            agent: null, blocks: 0, verdict: null, released: false, toolingOk: false }
      sessions.set(id, s)
    }
    if (x?.session) s.agent = x
    return s
  }
  const active = (s) => !!s.id && existsSync(join(s.project, "docs", "agents"))    // an aloop project
  const hostDir = (s) => join(homeOf(s.project), "host", "dsh")
  const transcriptOf = (s) => s.child
    ? join(hostDir(s), safe(s.root), "subagents", `agent-${safe(s.id)}.jsonl`)
    : join(hostDir(s), `${safe(s.id)}.jsonl`)
  const homeGuard = (s) => {      // .aladdin/.gitignore: config.yaml is committed, run data never is
    const d = homeOf(s.project)
    if (d.endsWith(".aladdin") && existsSync(d) && !existsSync(join(d, ".gitignore")))
      try { writeFileSync(join(d, ".gitignore"), "# aloop run data (transcripts, budgets, ledger, logs, state) — never committed\n*\n!.gitignore\n!config.yaml\n") } catch {}
  }
  const bound = (s) => {
    try { return JSON.parse(readFileSync(join(homeOf(s.project), "state", "agents", `${safe(s.id)}.json`), "utf8")).worktree || "" } catch { return "" }
  }
  // the agent's current working directory (the model can move it with the working_directory tool)
  const cwdOf = (s) => {
    try { const c = ctx.get("workingDirectory")?.get(s.agent?.session); if (c) return c } catch {}
    return s.cwd0
  }
  // ...as dsh's tools will use it: they call ensure(), which moves a directory that vanished back to
  // the session's first one (a child's is its parent's, the main tree), so a gate judges that one
  const wdOf = async (s, agent) => {
    try { const c = await ctx.get("workingDirectory")?.ensure?.(agent); if (c) return c } catch {}
    return cwdOf(s)
  }
  function payload(event, s, extra = {}) {
    const p = { hook_event_name: event, session_id: s.root, transcript_path: join(hostDir(s), `${safe(s.root)}.jsonl`), cwd: s.project, ...extra }
    if (s.child) { p.agent_id = s.id; p.agent_type = s.role; p.agent_transcript_path = transcriptOf(s) }
    return p
  }
  const argsOf = (exec) => {
    if (typeof exec.arguments !== "string") return exec.arguments || {}
    try { return JSON.parse(exec.arguments) } catch { return {} }
  }
  // Claude-shaped tool_input, built from each tool's declared arguments only: dsh accepts extra
  // keys, and a gate must never read a field the model made up (an `agent_type` beside a
  // file_path). Any other tool's arguments travel as one JSON string. `workdir` is the directory
  // dsh really runs in (bash's own `workdir`, else the agent's). Relative paths are joined the
  // way dsh joins them (`<dir>/<path>`, `..` kept), never normalised here: like dsh,
  // host-adapter follows a symlink before `..`.
  function toolInput(tool, a, wd, model = "") {
    const str = (v) => (typeof v === "string" ? v : undefined)
    const at = (p) => (p === undefined || isAbsolute(p) ? p : `${wd}${sep}${p}`)
    if (DISPATCH.has(tool)) return { subagent_type: String(a.agent_preset || "").replace(/^aloop-/, ""), description: str(a.description),
      prompt: str(a.prompt) || "", cwd: str(a.cwd), model }
    switch (tool) {
      case "bash": return { command: str(a.command) || "", description: str(a.description), workdir: at(str(a.workdir)) ?? wd }
      case "working_directory": return { cd: at(str(a.cd)), workdir: wd }
      case "write": return { file_path: at(str(a.file_path)), content: str(a.content), workdir: wd }
      case "edit": return { file_path: at(str(a.file_path)), old_string: str(a.old_string), new_string: str(a.new_string),
        replace_all: a.replace_all === true, workdir: wd }
      case "read": return { file_path: at(str(a.file_path)), offset: a.offset, limit: a.limit, workdir: wd }
      case "read_image": return { file_path: at(str(a.file_path)), workdir: wd }
      case "glob": case "grep": return { pattern: str(a.pattern), path: at(str(a.path)), include: str(a.include), workdir: wd }
      default: return { arguments: JSON.stringify(a), workdir: wd }
    }
  }

  // Run one of this plugin's bin/ scripts with the hook JSON on stdin. Async: one Node process
  // drives every agent, so a synchronous wait would stall all of them.
  function run(script, args, input, cwd) {
    return new Promise((done) => {
      let out = "", err = "", settled = false, timer = null
      const finish = (code, error) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        let json = null
        const last = out.trim().split("\n").filter((l) => l.startsWith("{")).pop()
        try { if (last) json = JSON.parse(last) } catch {}
        done({ code, stdout: out, stderr: err.trim(), json, error })
      }
      let child
      try {
        child = spawn(join(BIN, script), args, { cwd, env: gateEnv(ROOT, FROM_FILE) })
      } catch (e) { return finish(1, e) }
      // a hung script is killed after 10 minutes (as on OpenCode); spawn's own `timeout` would keep a
      // timer alive for a script that never started
      timer = setTimeout(() => { try { child.kill("SIGKILL") } catch {} }, 600000)
      timer.unref?.()
      child.stdout.on("data", (d) => (out += d))
      child.stderr.on("data", (d) => (err += d))
      child.on("error", (e) => finish(1, e))
      child.on("close", (code) => finish(code ?? 1))
      child.stdin.on("error", () => {})
      child.stdin.end(JSON.stringify(input || {}))
    })
  }
  // A gate's verdict: the deny reason, or null. A gate that cannot run denies (fail closed).
  const denyOf = (res, script) => {
    if (res.error) return `aloop: could not run ${script} (${res.error.code || res.error.message}); re-run \`aloop install dsh\``
    const h = res.json?.hookSpecificOutput
    if (h?.permissionDecision === "deny") return h.permissionDecisionReason || "denied"
    if (res.code === 2) return res.stderr || "blocked"
    return null
  }
  const gate = async (script, args, input, cwd) => denyOf(await run(script, args, input, cwd), script)
  // The model a role runs on: its project's own ("" = the session's own model), never the profile's.
  // null when it cannot be resolved: the dispatch is refused and a request fails (fail closed).
  async function modelOf(s, role) {
    const res = await run("models", ["resolve", role], {}, s.project)
    return res.code === 0 && !res.error ? res.stdout.trim() : null
  }

  async function preTool(exec) {
    const s = info(exec.agent)
    if (!active(s)) return null
    const tool = exec.name, a = argsOf(exec)
    // as on Claude Code, the result reaches the supervisor only through its stop gate (a role is not
    // offered send_message, see agent/created; a call to it anyway is refused here)
    if (s.child && tool === "send_message")
      return "aloop: a role does not message the supervisor: your result reaches it when your stop gate passes. Write your report in your output.md block, then finish."
    if (DISPATCH.has(tool)) {
      if (s.child) return "aloop: a role does not delegate. Finish your own task and report back to the supervisor."
      if (!a.agent_preset) return "aloop: dispatch an aloop role: pass agent_preset: \"aloop-<role>\" (subagent: red-worker, scaffolder, green-worker, structural-reviewer, final-gate, product-tester, state-assessor; subagent_planning: intake, standards, planner, archivist, market-researcher, evaluator, mission-planner, ethics-reviewer)."
      const role = String(a.agent_preset).replace(/^aloop-/, ""), m = await modelOf(s, role)
      if (m === null) return `aloop: could not resolve ${role}'s model in ${s.project} (\`aloop models resolve ${role}\`)`
      dispatched.set(exec.callId, m)
    }
    const p = payload("PreToolUse", s, { tool_name: TOOL[tool] || tool, tool_input: toolInput(tool, a, await wdOf(s, exec.agent), dispatched.get(exec.callId)),
      tool_use_id: exec.callId })
    let why = await gate("host-adapter", ["hook"], p, s.project)        // records `aloop bind`, confines writers
    if (why) return why
    if (s.child && EXECUTION.has(s.role) && !s.toolingOk) {               // SubagentStart preflight
      if ((why = await gate("gate-tooling", [], p, s.project))) return why
      s.toolingOk = true
    }
    const checks = []
    if (tool === "write" || tool === "edit" || tool === "bash") checks.push(["gate-supervisor-scope", []], ["gate-mission-scope", []])
    if (tool === "bash") checks.push(["gate-commit-author", []])
    if (DISPATCH.has(tool)) checks.push(["gate-dispatch", []])
    checks.push(["budget", ["hook", "pre"]])
    for (const [script, args] of checks) if ((why = await gate(script, args, p, s.project))) return why
    return null
  }

  async function postTool(exec, result) {
    const s = info(exec.agent)
    if (!active(s)) return {}
    const tool = exec.name, a = argsOf(exec), wd = cwdOf(s)
    const p = payload("PostToolUse", s, { tool_name: TOOL[tool] || tool, tool_input: toolInput(tool, a, wd, dispatched.get(exec.callId)),
      tool_use_id: exec.callId, tool_response: textOf(result?.content) })
    dispatched.delete(exec.callId)
    const out = {}
    if (tool === "write" && !result?.isError) out.block = await gate("gate-validate-artifact", [], p, s.project)
    await run("transcripts", ["record"], p, s.project)
    const res = await run("budget", ["hook", "post"], p, s.project)
    out.ctx = res.json?.hookSpecificOutput?.additionalContext || null
    if (res.json?.continue === false) out.cancel = res.json.stopReason || "aloop: hard budget limit"
    // a bound worker works in its worktree: move its working directory there (bash then runs there)
    if (s.child && tool === "bash" && /\bbind\s+\//.test(a.command || "")) {
      const wt = bound(s)
      if (wt && !under(wd, wt)) try { await ctx.get("workingDirectory")?.set(exec.agent, wt) } catch {}
    }
    return out
  }

  // A child's stop: its role's gate decides. Returns true when the child was steered back to work.
  async function childStop(s, agent, signal) {
    const p = payload("SubagentStop", s, { stop_hook_active: s.blocks > 0 })
    await run("host-adapter", ["hook"], p, s.project)
    const g = GATES[s.role]
    if (g) {
      const res = await run(g, [], p, bound(s) || s.project)
      // A release (stuck on one block, or waiting on the human's answers) exits 0 but is NOT a pass:
      // hand the supervisor the reason as a release, never as a silent success.
      const rel = res.code === 0 && releaseOf(res.stderr, s.role)
      if (rel === "STUCK") s.verdict = { stuck: res.stderr }
      else if (rel) {   // the notice `intent hook` builds from the record `intent release` wrote (and claims it: told once)
        const told = await run("intent", ["hook"], { hook_event_name: "PostToolUse", session_id: s.root, cwd: s.project }, s.project)
        s.verdict = { human: told.json?.hookSpecificOutput?.additionalContext || "" }
      } else if (res.code === 2 || res.error) {
        const why = res.code === 2 ? res.stderr || "blocked" : denyOf(res, g)
        if (s.blocks < MAX && !signal?.aborted) {     // an interrupted child is released, never revived
          s.blocks++
          // Claude semantics: a blocked stop sends the reason back and the sub-agent keeps working.
          agent.steer(msg(`[aloop gate ${g}] Your stop was blocked:\n${why}\nFix it, run \`aloop selfcheck\`, then finish.`))
          return true
        }
        s.verdict = { blocked: why, interrupted: !!signal?.aborted }
      } else s.verdict = null
    }
    await release(s, p)
    return false
  }
  async function release(s, p) {
    if (s.released) return
    s.released = true
    s.blocks = 0
    await run("budget", ["hook", "stop"], p, s.project)
    await run("transcripts", ["stop"], p, s.project)
    await run("stats", ["hook"], p, s.project)
    const v = s.verdict, root = sessions.get(s.root)?.agent
    s.verdict = null
    if (!v || !root) return
    root.inject(msg(v.stuck
      ? `[aloop gate] ${s.role} ${s.id}: STUCK — released UNVERIFIED (not a pass): the sub-agent hit the same block repeatedly:\n${v.stuck}\nDo not merge. Discard its worktree and re-dispatch the task with a Feedback block that addresses the reason, or re-plan.`
      : "human" in v
        ? v.human || `[aloop gate] ${s.role} ${s.id}: NEEDS-HUMAN — released UNVERIFIED (not a pass): its Intent Contract has questions only the human can answer (\`aloop intent check\` on the newest docs/agents/intents/int_*.json lists them). Ask the human each one, then re-dispatch ${s.role} with the answers in a ### Feedback block. Do not plan from it until gate-intake passes.`
        : `[aloop gate] ${s.role} ${s.id}: BLOCKED ${v.interrupted ? "(interrupted while its gate ran)" : `after ${MAX} fix rounds`} — the sub-agent did not pass its gate:\n${v.blocked}\nApply the playbook's retry/escalate rule (re-dispatch with this feedback, or escalate).`))
  }
  async function rootStop(s, agent, signal) {
    const p = payload("Stop", s, { stop_hook_active: s.blocks > 0 })
    await run("transcripts", ["snapshot"], p, s.project)
    const res = await run("stats", ["hook"], p, s.project)
    if (res.json?.systemMessage) process.stderr.write(`[aloop] ${res.json.systemMessage}\n`)
    if (res.json?.decision === "block" && res.json.reason && s.blocks < MAX) {
      if (signal?.aborted) { agent.inject(msg(res.json.reason)); s.blocks = 0; return }   // stats announced it once: queue it for the next turn
      s.blocks++
      agent.steer(msg(res.json.reason))
      return
    }
    s.blocks = 0
  }
  // SessionStart of a root agent: the context joins the session's next step
  async function sessionStart(s, agent, source) {
    const start = { hook_event_name: "SessionStart", session_id: s.id, transcript_path: transcriptOf(s), cwd: s.project, source }
    const parts = [TOOLS_NOTE]
    // the one dsh profile was installed for another project (`aloop install dsh` records which): its
    // roles run on this project's models anyway, but dsh loads the aloop skills per project
    let here = s.project
    try { here = realpathSync(s.project) } catch {}
    if (config.project && config.project !== "(global)" && config.project !== here && !hasSkills(s.project)) {
      const note = `aloop: this project has no aloop skills for dsh (the dsh profile was installed for ${config.project}). Run \`aloop install dsh --project ${s.project}\` (or \`--global\`) first.`
      parts.push(note)
      process.stderr.write(`[${note}]\n`)
    }
    for (const script of ["ensure-tools", "session-start"]) {
      const c = (await run(script, [], start, s.project)).json?.hookSpecificOutput?.additionalContext
      if (c) parts.push(c)
    }
    agent.inject(msg(parts.join("\n\n")))
  }

  ctx.on("agent/created", async ({ agent, source }) => {
    try {
      const s = info(agent)
      if (!active(s)) return
      if (!s.child) await sessionStart(s, agent, source || "startup")
      else {
        // dsh adds "send your result to the parent with send_message" to a child's task whenever
        // the child can see send_message (dsh-subagent startLocal, right after this event); a role
        // reports through its stop gate, so the tool is scoped out of its own tools (agent-local,
        // dropped with the agent) and that guidance never comes. The supervisor keeps it.
        try {
          const tools = agent.ctx?.get("tools")
          if (tools?.get("send_message", agent)) tools.restrict({ deny: ["send_message"] })
        } catch {}
        const p = payload("SubagentStart", s)
        await run("host-adapter", ["hook"], p, s.project)
        await run("transcripts", ["stage-in"], p, s.project)
        if (EXECUTION.has(s.role)) await run("worktree-hygiene", [], { ...p, cwd: s.cwd0 }, s.project)
        if (MISSION.has(s.role)) await run("gate-mission-artifact", ["start"], p, s.project)   // what exists before it works
      }
    } catch {}                    // a throw would fail the agent's creation
  })

  ctx.on("agent/pre-step", async (step, next) => {
    let extra = null
    try {
      const s = info(step.agent)
      if (s.child) s.released = false                                     // a new step: the next stop is gated again
      else if (active(s)) {
        const text = (step.messages || []).filter((m) => m?.source?.kind === "user").map((m) => textOf(m.content)).join("\n").trim()
        if (text) {
          const p = payload("UserPromptSubmit", s, { prompt: text })
          await run("transcripts", ["prompt"], p, s.project)
          const res = await run("intent-trigger", ["hook"], p, s.project)
          if (res.code === 2) return { kind: "reject" }
          extra = res.json?.hookSpecificOutput?.additionalContext || null   // natural-language auto-trigger (§4.11)
        }
      }
    } catch {}
    const d = await next()
    return extra && d?.kind === "enter" ? { ...d, messages: [...d.messages, msg(extra)] } : d
  }, { prepend: true })

  // A role's every request goes to its project's model (the tool started it on the profile's); "" is the
  // route a child inherits, its parent's latest. A new route takes its own default effort, as on a dsh switch.
  ctx.on("agent/request", async (req, next) => {
    const proposed = await next()
    const s = info(req.agent)
    if (!s.child || !GATES[s.role] || !active(s)) return proposed
    s.model ??= await modelOf(s, s.role)          // once per child; an unresolved one is tried again
    let route = s.model ? routeOf(s.model) : null
    if (s.model === "") {
      let c
      try { c = sessions.get(s.parent)?.agent?.session?.requestHeader?.()?.config } catch {}
      if (c?.provider && c.model) route = { provider: c.provider, model: c.model, ...(c.reasoningEffort === undefined ? {} : { reasoningEffort: c.reasoningEffort }) }
    }
    // never the profile's model by default: a role whose model cannot be told fails its request
    if (!route) throw new Error(`aloop: no model for ${s.role} in ${s.project} (\`aloop models resolve ${s.role}\`)`)
    if (route.provider === proposed.provider && route.model === proposed.model) return proposed
    const { reasoningEffort: _effort, ...rest } = proposed
    return { ...rest, ...route }
  }, { prepend: true })

  ctx.on("tools/pre-execute", async (exec, next) => {
    let why
    try { why = await preTool(exec) } catch (e) { why = `aloop: the gate itself failed (${e?.message || e})` }
    if (why) dispatched.delete(exec.callId)       // a denied call has no post-execute
    return why ? { kind: "deny", reason: why } : next()
  }, { prepend: true })

  ctx.on("tools/post-execute", async (exec, result, next) => {
    let r = {}
    try { r = await postTool(exec, result) } catch {}
    if (r.cancel) setImmediate(() => { try { exec.agent?.cancel({ kind: "hook", reason: r.cancel }) } catch {} })
    const d = await next()
    const extra = r.ctx ? [msg(`[aloop] ${r.ctx}`)] : []
    if (r.block) return { kind: "block", feedback: [{ type: "text", text: r.block }], additionalContexts: [...(d.additionalContexts || []), ...extra] }
    return extra.length ? { ...d, additionalContexts: [...(d.additionalContexts || []), ...extra] } : d
  }, { prepend: true })

  ctx.on("agent/turn-stopping", async ({ agent, signal }) => {
    try {
      const s = info(agent)
      if (!active(s)) return
      if (s.child) await childStop(s, agent, signal)
      else await rootStop(s, agent, signal)
    } catch {}                    // a throw would fail the turn
  })

  // A child whose turn ended without a stop boundary (cancelled, errored) still gets its bookkeeping.
  ctx.on("subagent/end", async (end) => {
    try {
      const s = sessions.get(end?.id)
      if (!s || !active(s) || s.released) return
      const p = payload("SubagentStop", s, { stop_hook_active: false })
      await run("host-adapter", ["hook"], p, s.project)
      await release(s, p)
    } catch {}
  })

  ctx.on("session/event", (session, event) => {
    try {
      // a compaction that committed (a failed one ends with `error`): Claude Code's SessionStart(compact)
      if (event?.type === "compaction/end") {
        const s = info(session)
        if (!s.child && s.agent && active(s) && !event.data?.error) sessionStart(s, s.agent, "compact").catch(() => {})
        return
      }
      if (event?.type !== "assistant/message" || !event.data?.usage) return
      const s = info(session)
      if (!active(s)) return
      const u = event.data.usage, m = event.data.message || {}
      const usage = { input_tokens: u.inputTokens || 0, output_tokens: u.outputTokens || 0 }
      // a class the host did not report stays absent (named as unreported), never a silent zero
      if (u.cacheReadTokens !== undefined) usage.cache_read_input_tokens = u.cacheReadTokens
      if (u.cacheWriteTokens !== undefined) usage.cache_creation_input_tokens = u.cacheWriteTokens
      const file = transcriptOf(s)
      mkdirSync(dirname(file), { recursive: true })
      homeGuard(s)
      appendFileSync(file, JSON.stringify({ timestamp: new Date(event.time || Date.now()).toISOString(),
        message: { id: m.id || `${s.id}:${event.seq}`, model: m.source?.model || "", usage } }) + "\n")
      if (s.child) writeFileSync(file.replace(/\.jsonl$/, ".meta.json"), JSON.stringify({ agentType: s.role }))
    } catch {}
  })
}
