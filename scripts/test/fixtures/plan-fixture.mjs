// plan-fixture.mjs — the human's approval of a sprint's plan in a test project (ROADMAP M0.9), for
// the host tests that dispatch an execution role: gate-dispatch admits none before it.
//   import { approvePlan } from "./fixtures/plan-fixture.mjs"
//   approvePlan(projectDir, binDir)    // sprint 1: a plan record when none, then the human types the line asked for
import { spawnSync } from "node:child_process"
import { existsSync, mkdirSync, readFileSync } from "node:fs"
import { join } from "node:path"

export function approvePlan(cwd, bin, sprint = 1, env = process.env) {
  const run = (cmd, args, input) => spawnSync(join(bin, cmd), args, { cwd, env, input, encoding: "utf8" })
  mkdirSync(join(cwd, "docs/agents", `sprint${sprint}`), { recursive: true })
  const log = join(cwd, ".aladdin/logs/events.jsonl")
  const planned = existsSync(log) && readFileSync(log, "utf8").split("\n").filter(Boolean).some((l) => {
    try { const e = JSON.parse(l); return /^plan\.(created|revised)$/.test(e.event) && e.data?.sprint === `S${sprint}` } catch { return false }
  })
  if (!planned) {                       // what `ids freeze` records when gate-stage2-complete passes
    const r = run("events", ["emit", "plan.created", "--role", "planner", "--data", `sprint=S${sprint}`])
    if (r.status !== 0) throw new Error(`plan.created: ${r.stderr}`)
  }
  const args = ["authority", "--initiated-by", "tester", "--mode", "reviewed", "--scope", `sprint ${sprint} as approved`, "--sprint", String(sprint)]
  let r = run("events", args)
  const m = r.status === 3 && /they reply {2}(.+?) {2}\(the code/.exec(r.stderr)
  if (m) {                              // the human types the line; the UserPromptSubmit hook captures it
    run("transcripts", ["prompt"], JSON.stringify({ hook_event_name: "UserPromptSubmit", session_id: "s-human", prompt: m[1] }))
    r = run("events", args)
  }
  if (r.status !== 0) throw new Error(`events authority: ${r.status} ${r.stderr}`)
  return r.stdout.trim()
}
