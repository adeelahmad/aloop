// aloop for OpenCode.
// Installed by `aloop install opencode` (it replaces __AGENTIC_ROOT__ with this plugin's
// absolute path). Maps OpenCode's plugin hooks onto the SAME scripts Claude Code and Codex
// run from hooks/hooks.json, so gates, time boxes, the token ledger, stats and the handoff
// behave identically on every host:
//
//   Claude Code hook   OpenCode equivalent used here
//   SessionStart       event session.created (root session) → ensure-tools, session-start;
//                      the context is added to that session's system prompt (a resumed root,
//                      which has none, still gets the core principles)
//   UserPromptSubmit   chat.message on a root session → transcripts prompt, intent-trigger
//                      (its context goes into the next turn's system prompt)
//   PreToolUse         tool.execute.before → host-adapter, gate-supervisor-scope,
//                      gate-mission-scope, gate-commit-author, budget pre, then mission doors
//                      hook LAST (it records door.crossed, so only once every other gate admitted
//                      the call; not for read-only tools; a hang, crash or odd exit denies); a
//                      deny THROWS (the tool call fails with the reason). Bound workers'
//                      commands/edits are REWRITTEN into their worktree (bash workdir, file
//                      paths, patch headers); the gates read bash's workdir as tool_input.workdir.
//   PostToolUse        tool.execute.after → transcripts record, budget post; context is
//                      appended to the tool output; `continue: false` aborts the session
//   SubagentStart      event session.created (child session) → host-adapter, transcripts
//                      stage-in; the core principles go into its system prompt; gate-tooling
//                      on the worker's first tool call (role known);
//                      a mission role's gate-mission-artifact start on its first chat.message
//   SubagentStop       tool.execute.after of the `task` tool → the role's gate; a block is
//                      sent back to the sub-agent (it keeps working) up to 3 rounds, then
//                      reported in the task result
//   Stop               event session.idle (root session) → transcripts snapshot, stats hook;
//                      a `decision: block` reason is prompted back into the session
// Token usage: every completed assistant message is appended, Claude-shaped, to
// .agentic/host/opencode/<root>.jsonl (sub-agents: <root>/subagents/agent-<id>.jsonl), so
// the ledger and the time box read it like any Claude transcript.
import { spawnSync } from "node:child_process"
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, isAbsolute, join, relative, resolve } from "node:path"

const ROOT = "__AGENTIC_ROOT__"
const BIN = join(ROOT, "bin")
const WRITERS = new Set(["red-worker", "scaffolder", "green-worker"])
const GATES = {
  "red-worker": "gate-red-verify", scaffolder: "gate-scaffold-verify", "green-worker": "gate-green-verify",
  "structural-reviewer": "gate-structural-integrity", "final-gate": "gate-final",
  intake: "gate-intake", standards: "gate-standards-cited", planner: "gate-stage2-complete", archivist: "gate-memory",
  // the mission roles (ROADMAP §4.12): each one's artifacts are checked when it stops
  "market-researcher": "gate-mission-artifact", "product-tester": "gate-mission-artifact",
  "state-assessor": "gate-mission-artifact", "evaluator": "gate-mission-artifact",
  "mission-planner": "gate-mission-artifact", "ethics-reviewer": "gate-mission-artifact",
}
// OpenCode's `question` is the door gate's question tool (AskUserQuestion)
const TOOL = { bash: "Bash", write: "Write", edit: "Edit", apply_patch: "apply_patch", read: "Read", task: "Agent", question: "AskUserQuestion" }
const EDITS = new Set(["write", "edit", "apply_patch"])
// read-only built-ins (the door gate's QUIET set): they cross no door, so no spawn
const QUIET = new Set(["read", "grep", "glob", "list", "todowrite", "todoread", "webfetch", "websearch", "codesearch"])
const GATE_ROUNDS = 3
// the door gate's timeout: hooks.json's 15 s; ALOOP_DOORS_TIMEOUT_MS only shortens it (tests)
const DOORS_MS = ((t) => (t > 0 && t < 15000 ? t : 15000))(Number(process.env.ALOOP_DOORS_TIMEOUT_MS))

export const AgenticAgile = async ({ client, directory, worktree }) => {
  const project = worktree || directory
  const active = () => existsSync(join(project, "docs", "agents"))       // an aloop project
  const env = { ...process.env, AGENTIC_HOST: "opencode", PATH: `${BIN}:${process.env.PATH || ""}` }
  const sessions = new Map()       // id -> { parentID, root, agent }
  const startCtx = new Map()       // root id -> SessionStart additionalContext
  const promptCtx = new Map()      // root id -> intent-trigger context for the next turn
  // the run-data dir (bin/_paths.sh): .aladdin/, or the legacy .agentic/ until `aloop layout migrate`
  const homeDir = (() => {
    const nu = join(project, ".aladdin")
    if (["transcripts", "budget", "ledger", "logs", "state", "host", "worktrees"].some((d) => existsSync(join(nu, d)))) return nu
    return existsSync(join(project, ".agentic")) ? join(project, ".agentic") : nu
  })()
  const homeGuard = () => {        // .aladdin/.gitignore: config.yaml is committed, run data never is
    if (homeDir.endsWith(".aladdin") && existsSync(homeDir) && !existsSync(join(homeDir, ".gitignore")))
      try { writeFileSync(join(homeDir, ".gitignore"), "# aloop run data (transcripts, budgets, ledger, logs, state) — never committed\n*\n!.gitignore\n!config.yaml\n") } catch {}
  }
  const hostDir = join(homeDir, "host", "opencode")
  const safe = (s) => String(s || "").replace(/[^A-Za-z0-9_.-]/g, "_")

  const run = (script, args, payload, cwd = project, timeout = 600000) => {
    const r = spawnSync(join(BIN, script), args, { input: JSON.stringify(payload || {}), cwd, env, encoding: "utf8", timeout })
    let json = null
    const last = (r.stdout || "").trim().split("\n").filter((l) => l.startsWith("{")).pop()
    try { if (last) json = JSON.parse(last) } catch {}
    return { code: r.status ?? 1, stdout: r.stdout || "", stderr: (r.stderr || "").trim(), json, error: r.error, signal: r.signal }
  }
  const denyOf = (res) => {
    const h = res.json?.hookSpecificOutput
    if (h?.permissionDecision === "deny") return h.permissionDecisionReason || "denied"
    if (res.code === 2) return res.stderr || "blocked"
    return null
  }
  // The door gate fails closed: a gate that cannot start, hangs, is killed or exits other than 0/2 has not
  // admitted the call (a host that read that as "no objection" would let a one-way door through)
  const doorsDenyOf = (res) => {
    const how = res.error?.code === "ETIMEDOUT" ? `timed out after ${DOORS_MS / 1000} s`
      : res.error ? `could not start (${res.error.code || res.error.message})`
        : res.signal ? `was killed (${res.signal})` : res.code !== 0 && res.code !== 2 ? `exited ${res.code}` : ""
    if (how) return `BLOCK[doors]: the door gate (aloop mission doors hook) ${how}, so the call is denied (it fails closed)${res.stderr ? `: ${res.stderr}` : ""}`
    return denyOf(res)
  }
  let core = null                  // the core principles block (mission principles show --core), read once
  const coreBlock = () => {
    if (core === null) {
      const r = run("mission", ["principles", "show", "--core"], {}, project, 15000)
      if (r.code !== 0 || !r.stdout.trim()) return ""        // retried next turn
      core = r.stdout.trim()
    }
    return core
  }

  // OpenCode names an MCP tool <server>_<tool>, each part with [^a-zA-Z0-9_-] as "_": the gates read it as
  // Claude Code's mcp__<server>__<tool> (the longest server name that prefixes it), so a door matches a
  // server and a tool, not a word inside either. The servers are re-read on a miss, at most once a second.
  let servers = [], serversAt = 0
  async function toolName(tool) {
    if (TOOL[tool]) return TOOL[tool]
    if (QUIET.has(tool) || !tool.includes("_")) return tool
    const of = () => servers.find((n) => tool.startsWith(n + "_") && tool.length > n.length + 1)
    if (!of() && Date.now() - serversAt > 1000) {
      serversAt = Date.now()
      try {
        const data = (await client.mcp.status()).data
        if (data && typeof data === "object")
          servers = Object.keys(data).map((n) => n.replace(/[^a-zA-Z0-9_-]/g, "_")).sort((a, b) => b.length - a.length)
      } catch {}
    }
    const n = of()
    return n ? `mcp__${n}__${tool.slice(n.length + 1)}` : tool
  }

  async function info(id) {
    if (sessions.has(id)) return sessions.get(id)
    let parentID = null
    try { parentID = (await client.session.get({ path: { id } })).data?.parentID || null } catch {}
    const s = { parentID, root: id, agent: null }
    sessions.set(id, s)
    if (parentID) s.root = (await info(parentID)).root
    return s
  }
  const transcriptOf = (s, id) =>
    s.parentID ? join(hostDir, s.root, "subagents", `agent-${safe(id)}.jsonl`) : join(hostDir, `${safe(id)}.jsonl`)
  async function payload(event, sessionID, extra = {}) {
    const s = await info(sessionID)
    const p = { hook_event_name: event, session_id: s.root, transcript_path: join(hostDir, `${safe(s.root)}.jsonl`), cwd: project, ...extra }
    if (s.parentID) { p.agent_id = sessionID; p.agent_type = s.agent || "" }
    return p
  }
  const bound = (id) => {
    try { return JSON.parse(readFileSync(join(homeDir, "state", "agents", `${safe(id)}.json`), "utf8")).worktree || "" } catch { return "" }
  }
  const toolInput = (tool, a) =>
    tool === "bash" ? { command: a.command, workdir: a.workdir }
      : tool === "apply_patch" ? { command: a.patchText || "" }
        : { file_path: a.filePath, ...a }
  // Map a path into the worker's worktree: relative → under wt; project-absolute → same path in wt.
  const intoWt = (p, wt) => {
    if (!p) return p
    if (!isAbsolute(p)) return join(wt, p)
    const r = resolve(p)
    if (r === wt || r.startsWith(wt + "/")) return r
    const rel = relative(project, r)
    return rel.startsWith("..") ? r : join(wt, rel)
  }

  async function subagentStop(childID, parentSessionID) {
    const s = await info(childID)
    const role = s.agent || ""
    const p = await payload("SubagentStop", childID)
    run("host-adapter", ["hook"], p)
    let verdict = null
    const gate = GATES[role]
    for (let round = 0; gate && round <= GATE_ROUNDS; round++) {
      const res = run(gate, [], p, bound(childID) || project)
      // A stuck release (the same block N times) exits 0 but is NOT a pass: hand the supervisor
      // the reason as a release, never as a silent success.
      if (res.code === 0 && /\bSTUCK\b.*NOT verified/.test(res.stderr || "")) { verdict = { stuck: res.stderr }; break }
      if (res.code !== 2) { verdict = null; break }
      verdict = { blocked: res.stderr }
      if (round === GATE_ROUNDS) break
      // Claude semantics: a blocked stop sends the reason back and the sub-agent keeps working.
      try {
        await client.session.prompt({ path: { id: childID }, body: { agent: role || undefined,
          parts: [{ type: "text", text: `[aloop gate ${gate}] Your stop was blocked:\n${verdict.blocked}\nFix it, run \`aloop selfcheck\`, then finish.` }] } })
      } catch { break }
    }
    run("budget", ["hook", "stop"], p)
    run("transcripts", ["stop"], p)
    run("stats", ["hook"], p)
    return verdict
  }

  return {
    "shell.env": async (_input, output) => {
      output.env.PATH = `${BIN}:${output.env.PATH || process.env.PATH || ""}`
      output.env.AGENTIC_HOST = "opencode"
      output.env.ALOOP_AGENT_SHELL = "1"     // an agent's shell: `mission doors authorize|revoke` refuses it
    },

    "chat.message": async (input, output) => {
      if (!input.sessionID) return
      const s = await info(input.sessionID)
      if (input.agent) s.agent = input.agent
      if (!active()) return
      if (s.parentID) {
        // A mission role's SubagentStart: record what exists once its role is known (its first message
        // comes before its first tool call); the stop gate diffs against it
        if (GATES[s.agent] === "gate-mission-artifact" && !s.missionStart) {
          s.missionStart = true
          run("gate-mission-artifact", ["start"], await payload("SubagentStart", input.sessionID))
        }
        return
      }
      const text = (output.parts || []).filter((x) => x.type === "text").map((x) => x.text).join("\n")
      if (text.startsWith("[aloop")) return                     // our own injections
      const pl = await payload("UserPromptSubmit", input.sessionID, { prompt: text })
      run("transcripts", ["prompt"], pl)
      const trig = run("intent-trigger", ["hook"], pl).json?.hookSpecificOutput?.additionalContext
      if (trig) promptCtx.set(s.root, trig)                      // natural-language auto-trigger (§4.11)
    },

    "experimental.chat.system.transform": async (input, output) => {
      output.system.push("aloop tools: call `aloop <tool>` (e.g. `aloop selfcheck`, `aloop stats show`); never search for them.")
      if (!input.sessionID) return
      const s = await info(input.sessionID)
      const ctx = startCtx.get(s.root)
      if (ctx && !s.parentID) output.system.push(ctx)
      // the core principles reach every agent: a sub-agent, and a resumed root (no SessionStart context
      // in this process); a root's SessionStart context already carries them, so never twice
      const cb = active() ? coreBlock() : ""
      if (cb && !output.system.some((x) => x.includes(cb.split("\n")[0]))) output.system.push(cb)
      const trig = promptCtx.get(s.root)
      if (trig && !s.parentID) { output.system.push(trig); promptCtx.delete(s.root) }
    },

    "tool.execute.before": async (input, output) => {
      if (!active()) return
      const s = await info(input.sessionID)
      const args = output.args || {}
      let p = await payload("PreToolUse", input.sessionID, { tool_name: await toolName(input.tool), tool_input: toolInput(input.tool, args) })
      run("host-adapter", ["hook"], p)                                   // records `aloop bind`
      const role = s.agent || ""
      if (s.parentID && GATES[role] && WRITERS.has(role) && !s.toolingOk) {     // SubagentStart preflight
        const why = denyOf(run("gate-tooling", [], p))
        if (why) throw new Error(why)
        s.toolingOk = true
      }
      const isBind = input.tool === "bash" && /\b((?:aloop|agentic)\s+bind|bin\/bind)\s+\S+/.test(args.command || "")
      if (s.parentID && WRITERS.has(role) && !isBind) {
        const wt = bound(input.sessionID)
        if (!wt && (input.tool === "bash" || EDITS.has(input.tool)))
          throw new Error("aloop isolation: bind your worktree first — your first command must be `aloop bind <worktree>` (the absolute path from your dispatch message).")
        if (wt) {                                                        // confine to the worktree
          if (input.tool === "bash") args.workdir = wt
          if (input.tool === "write" || input.tool === "edit") args.filePath = intoWt(args.filePath, wt)
          if (input.tool === "apply_patch" && args.patchText)
            args.patchText = args.patchText.replace(/^([^\S\n]*\*\*\*[^\S\n]*(?:Add|Update|Delete)[^\S\n]+File:[^\S\n]*|[^\S\n]*\*\*\*[^\S\n]*Move[^\S\n]+to:[^\S\n]*)(.+)$/gm, (_, h, f) => h + intoWt(f.trim(), wt))
          p = await payload("PreToolUse", input.sessionID, { tool_name: await toolName(input.tool), tool_input: toolInput(input.tool, args) })
        }
      }
      const checks = []
      if (EDITS.has(input.tool)) checks.push(["gate-supervisor-scope", []])
      if (EDITS.has(input.tool) || input.tool === "bash") checks.push(["gate-mission-scope", []])   // role path guards
      if (input.tool === "bash") checks.push(["gate-commit-author", []])
      checks.push(["budget", ["hook", "pre"]])
      // the door rule, LAST: it records door.crossed (a once-grant is used up), so only for a call every
      // other gate admitted. Payload cwd stays the project; a bound worker's directory is tool_input.workdir
      if (!QUIET.has(input.tool)) checks.push(["mission", ["doors", "hook"]])
      for (const [script, a] of checks) {
        const doors = script === "mission"
        const res = run(script, a, p, project, doors ? DOORS_MS : undefined)
        const why = doors ? doorsDenyOf(res) : denyOf(res)
        if (why) throw new Error(why)
      }
    },

    "tool.execute.after": async (input, output) => {
      if (!active()) return
      const p = await payload("PostToolUse", input.sessionID, {
        tool_name: await toolName(input.tool), tool_input: toolInput(input.tool, input.args || {}), tool_response: output.output,
      })
      run("transcripts", ["record"], p)
      const res = run("budget", ["hook", "post"], p)
      const ctx = res.json?.hookSpecificOutput?.additionalContext
      if (ctx) output.output = `${output.output || ""}\n\n[aloop] ${ctx}`
      if (res.json?.continue === false) {
        try { await client.session.abort({ path: { id: input.sessionID } }) } catch {}
      }
      if (input.tool === "task") {
        const child = output.metadata?.sessionId
        if (child) {
          const v = await subagentStop(child, input.sessionID)
          if (v?.stuck) output.output = `${output.output || ""}\n\n[aloop gate] STUCK — released UNVERIFIED (not a pass): the sub-agent hit the same block repeatedly:\n${v.stuck}\nDo not merge. Discard its worktree and re-dispatch the task with a Feedback block that addresses the reason, or re-plan.`
          else if (v?.blocked) output.output = `${output.output || ""}\n\n[aloop gate] BLOCKED after ${GATE_ROUNDS} fix rounds — the sub-agent did not pass its gate:\n${v.blocked}\nApply the playbook's retry/escalate rule (re-dispatch with this feedback, or escalate).`
        }
      }
    },

    event: async ({ event }) => {
      if (!active()) return
      const props = event.properties || {}
      if (event.type === "session.created" && props.info?.id) {
        const id = props.info.id
        sessions.delete(id)
        const s = await info(id)
        if (!s.parentID) {
          const start = { hook_event_name: "SessionStart", cwd: project, source: "startup" }
          const ctx = [run("ensure-tools", [], start), run("session-start", [], start)]
            .map((r) => r.json?.hookSpecificOutput?.additionalContext).filter(Boolean).join("\n\n")
          if (ctx) startCtx.set(id, ctx)
        } else {
          const p = await payload("SubagentStart", id)
          run("host-adapter", ["hook"], p)
          run("transcripts", ["stage-in"], p)
        }
      }
      if (event.type === "message.updated") {
        const m = props.info
        if (!m || m.role !== "assistant" || !m.time?.completed || !m.tokens) return
        const s = await info(m.sessionID)
        if (m.mode && s.parentID) s.agent = s.agent || m.mode
        const file = transcriptOf(s, m.sessionID)
        mkdirSync(dirname(file), { recursive: true })
        homeGuard()
        appendFileSync(file, JSON.stringify({
          timestamp: new Date(m.time.completed).toISOString(),
          message: { id: m.id, model: `${m.providerID}/${m.modelID}`, usage: {
            input_tokens: m.tokens.input || 0, cache_read_input_tokens: m.tokens.cache?.read || 0,
            cache_creation_input_tokens: m.tokens.cache?.write || 0,
            output_tokens: (m.tokens.output || 0) + (m.tokens.reasoning || 0) } },
        }) + "\n")
        if (s.parentID) writeFileSync(file.replace(/\.jsonl$/, ".meta.json"), JSON.stringify({ agentType: s.agent || "" }))
      }
      if (event.type === "session.idle" && props.sessionID) {
        const s = await info(props.sessionID)
        if (s.parentID) return                                           // sub-agents: gated via the task result
        const p = await payload("Stop", props.sessionID)
        run("transcripts", ["snapshot"], p)
        const res = run("stats", ["hook"], p)
        if (res.json?.systemMessage) {
          try { await client.tui.showToast({ body: { message: res.json.systemMessage, variant: "info" } }) } catch {}
        }
        if (res.json?.decision === "block" && res.json.reason) {
          try { await client.session.prompt({ path: { id: props.sessionID }, body: { parts: [{ type: "text", text: res.json.reason }] } }) } catch {}
        }
      }
    },
  }
}
