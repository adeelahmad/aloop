// A scripted stand-in for DeepSeek's Anthropic-compatible endpoint (DEEPSEEK_BASE_URL), so the
// real dsh runtime can be driven with no network and no tokens.
//   FAKE_SCRIPT=<json> FAKE_LOG=<jsonl> node fake-deepseek.mjs     → prints the port it listens on
// The script maps a role to its steps: {"supervisor": [step, ...], "red-worker": [...], ...}. A
// request's role is read from its system prompt (`You are aloop's \`<role>\` role.`, the line
// `aloop install dsh` opens every role's persona with; none → supervisor), and its step is the
// number of assistant messages it already holds. A step is {"text": "..."} (ends the turn) or
// {"tools": [{"name", "input"}, ...]}; past the end of a role's steps the reply is "<role> done"
// (the supervisor's is "E2E-DONE"). Every request is logged with the text it carried, so a test
// can assert what the model was shown (a deny reason, an injected gate verdict, the handoff).
import { createServer } from "node:http"
import { appendFileSync, readFileSync } from "node:fs"

const SCRIPT = JSON.parse(readFileSync(process.env.FAKE_SCRIPT, "utf8"))
const LOG = process.env.FAKE_LOG || "/dev/null"
const USAGE = { input_tokens: 100, output_tokens: 10, cache_read_input_tokens: 50, cache_creation_input_tokens: 0 }

const text = (c) => typeof c === "string" ? c
  : (c || []).map((b) => b.type === "text" ? b.text : b.type === "tool_result" ? `[tool_result${b.is_error ? " error" : ""}] ${text(b.content)}`
    : b.type === "tool_use" ? `[tool_use ${b.name}] ${JSON.stringify(b.input)}` : "").join("\n")
let seq = 0

function sse(res, blocks, stop) {
  res.writeHead(200, { "content-type": "text/event-stream" })
  const send = (o) => res.write(`event: ${o.type}\ndata: ${JSON.stringify(o)}\n\n`)
  send({ type: "message_start", message: { id: `msg_fake_${++seq}`, type: "message", role: "assistant", model: "fake", content: [],
    usage: { ...USAGE, output_tokens: 0 } } })
  blocks.forEach((b, i) => {
    if (b.text !== undefined) {
      send({ type: "content_block_start", index: i, content_block: { type: "text", text: "" } })
      send({ type: "content_block_delta", index: i, delta: { type: "text_delta", text: b.text } })
    } else {
      send({ type: "content_block_start", index: i, content_block: { type: "tool_use", id: `toolu_fake_${seq}_${i}`, name: b.name, input: {} } })
      send({ type: "content_block_delta", index: i, delta: { type: "input_json_delta", partial_json: JSON.stringify(b.input || {}) } })
    }
    send({ type: "content_block_stop", index: i })
  })
  send({ type: "message_delta", delta: { stop_reason: stop, stop_sequence: null }, usage: { output_tokens: USAGE.output_tokens } })
  send({ type: "message_stop" })
  res.end()
}

const server = createServer((req, res) => {
  let body = ""
  req.on("data", (d) => (body += d))
  req.on("end", () => {
    let j = {}
    try { j = JSON.parse(body) } catch {}
    if (!req.url.endsWith("/v1/messages")) { res.writeHead(404); return res.end("{}") }
    const system = text(j.system)
    const msgs = j.messages || []
    const tools = (j.tools || []).map((t) => t.name)
    if (!tools.length) return sse(res, [{ text: "aloop session" }], "end_turn")     // dsh's session-title call
    const role = (/You are aloop's `([a-z-]+)` role\./.exec(system) || [])[1] || "supervisor"
    const step = msgs.filter((m) => m.role === "assistant").length
    appendFileSync(LOG, JSON.stringify({ role, step, model: j.model, tools, system,
      last: text(msgs.at(-1)?.content), all: msgs.map((m) => text(m.content)).join("\n---\n") }) + "\n")
    const s = (SCRIPT[role] || [])[step]
    if (s?.tools) return sse(res, s.tools, "tool_use")
    return sse(res, [{ text: s?.text ?? (role === "supervisor" ? "E2E-DONE" : `${role} done`) }], "end_turn")
  })
})
server.listen(0, "127.0.0.1", () => console.log(server.address().port))
