// Isolated model fixture: no external requests, tools, accounts or real model.
import http from "node:http";
import { writeFileSync } from "node:fs";

const model = "ocode-fixture:latest";
let calls = 0;
const paths = [];
http.createServer((req, res) => {
  paths.push(`${req.method} ${req.url}`);
  writeFileSync("/workspace/fixture-status.json", JSON.stringify({ calls, paths }));
  const json = value => {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(value));
  };
  if (req.url === "/fixture/status") return json({ calls });
  if (req.url === "/api/tags") return json({ models: [{
    name: model, model, size: 1, digest: "fixture",
    modified_at: "2026-10-07T00:00:00Z",
    details: { family: "llama", parameter_size: "1B" },
  }] });
  if (req.url === "/v1/models") return json({ object: "list", data: [{
    id: model, object: "model", owned_by: "fixture",
  }] });
  let data = "";
  req.on("data", chunk => { data += chunk; });
  req.on("end", () => {
    if (req.url === "/api/show") return json({
      capabilities: ["completion"], parameters: "num_ctx 32768",
      model_info: { "general.architecture": "llama", "llama.context_length": 32768 },
      details: { family: "llama", parameter_size: "1B" },
    });
    if (req.url !== "/v1/chat/completions") return res.writeHead(404).end();
    calls++;
    const body = JSON.parse(data);
    const base = { id: "chatcmpl-fixture", object: "chat.completion.chunk", created: 1, model };
    if (!body.stream) return json({ ...base, object: "chat.completion", choices: [{
      index: 0, message: { role: "assistant", content: "OCODE_FIXTURE_OK" }, finish_reason: "stop",
    }], usage: { prompt_tokens: 10, completion_tokens: 4, total_tokens: 14 } });
    res.writeHead(200, { "content-type": "text/event-stream" });
    for (const value of [
      { ...base, choices: [{ index: 0, delta: { role: "assistant", content: "OCODE_FIXTURE_OK" }, finish_reason: null }] },
      { ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
        usage: { prompt_tokens: 10, completion_tokens: 4, total_tokens: 14 } },
    ]) res.write(`data: ${JSON.stringify(value)}\n\n`);
    res.end("data: [DONE]\n\n");
  });
}).listen(44097, "127.0.0.1");
