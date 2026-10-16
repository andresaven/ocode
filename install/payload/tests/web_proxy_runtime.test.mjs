import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import net from "node:net";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

function request(options, body) {
  return new Promise((resolve, reject) => {
    const req = http.request(options, res => {
      let data = "";
      res.on("data", chunk => { data += chunk; });
      res.on("error", reject);
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: data }));
    });
    req.on("error", reject);
    req.setTimeout(3000, () => req.destroy(new Error("request timeout")));
    req.end(body);
  });
}

async function eventually(check, description) {
  for (let attempt = 0; attempt < 120; attempt++) {
    try {
      const value = await check();
      if (value) return value;
    } catch {}
    await delay(250);
  }
  throw new Error(description);
}

test("real runtime: login, catalog, interrupted events and backend recovery", {
  skip: process.env.OCODE_RUNTIME_TEST !== "1",
  timeout: 120000,
}, async t => {
  // Run only inside an explicitly selected disposable container. No host
  // project, existing configuration, persistent volume or real account is used.
  assert.equal(process.platform, "linux");
  const directory = mkdtempSync(join(process.cwd(), "runtime-test-"));
  const publicPort = await freePort();
  let backendPort = await freePort();
  while (backendPort === publicPort) backendPort = await freePort();
  const controlPath = join(directory, "control.sock");
  const child = spawn(process.execPath, [resolve(here, "../src/ocode/web_proxy.mjs")], {
    cwd: directory,
    env: {
      ...process.env,
      HOME: directory,
      XDG_CONFIG_HOME: join(directory, "config"),
      XDG_DATA_HOME: join(directory, "data"),
      XDG_STATE_HOME: join(directory, "state"),
      XDG_CACHE_HOME: join(directory, "cache"),
      OCODE_DIRECTORY: directory,
      OCODE_PUBLIC_PORT: String(publicPort),
      OCODE_BACKEND_PORT: String(backendPort),
      OCODE_CONTROL_PATH: controlPath,
      OCODE_WEB_TOKEN: "fixture-browser-session",
      OPENCODE_SERVER_PASSWORD: "fixture-backend-only",
      OPENCODE_SERVER_USERNAME: "opencode",
    },
    stdio: ["ignore", "pipe", "ignore"],
  });
  let backendPid = null;
  let output = "";
  child.stdout.on("data", chunk => {
    output += chunk.toString();
    let end;
    while ((end = output.indexOf("\n")) >= 0) {
      const line = output.slice(0, end);
      output = output.slice(end + 1);
      const match = /^\[ocode-proxy\] backend started pid=(\d+)/.exec(line);
      if (match) backendPid = Number(match[1]);
    }
  });
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) {
      const exited = new Promise(resolve => child.once("exit", resolve));
      child.kill("SIGTERM");
      await exited;
    }
    rmSync(directory, { recursive: true, force: true });
  });
  const browser = path => ({ hostname: "127.0.0.1", port: publicPort, path });
  const register = handle => request({ socketPath: controlPath, path: "/login", method: "POST" }, handle);
  await eventually(async () => (await register("fixture-runtime-login-context")).status === 204,
    "real backend did not become healthy through the proxy control socket");
  assert.equal((await request(browser("/api/info"))).status, 401);
  const login = await request(browser("/?ocode_login=fixture-runtime-login-context"));
  assert.equal(login.status, 302);
  const cookie = login.headers["set-cookie"][0].split(";")[0];
  const authenticated = path => ({ ...browser(path), headers: { cookie } });
  assert.equal((await request(authenticated("/"))).status, 200, "Web UI should load");
  assert.equal((await request(authenticated("/api/info"))).status, 200);
  const contract = await request(authenticated("/openapi.json"));
  assert.equal(contract.status, 200);
  const paths = JSON.parse(contract.body).paths;
  const candidates = Object.keys(paths).filter(path => paths[path].get && !path.includes("{"));
  const catalogPath = candidates.find(path => /\/(?:provider|providers)$/.test(path));
  const eventsPath = candidates.find(path => /\/(?:event|events)$/.test(path));
  assert.ok(catalogPath, "runtime contract should expose a provider catalog");
  assert.ok(eventsPath, "runtime contract should expose events");
  const before = await request(authenticated(catalogPath));
  assert.equal(before.status, 200);
  const catalog = JSON.parse(before.body);
  t.diagnostic(`Web UI, API and provider catalog responded (${catalogPath})`);

  const stream = await new Promise((resolve, reject) => {
    const req = http.get(authenticated(eventsPath), resolve);
    req.on("error", reject);
    req.setTimeout(10000, () => req.destroy(new Error("event stream timeout")));
    t.after(() => req.destroy());
  });
  assert.equal(stream.statusCode, 200);
  assert.match(stream.headers["content-type"], /text\/event-stream/);
  stream.on("error", () => {});
  stream.resume();
  t.after(() => stream.destroy());
  const closed = new Promise(resolve => stream.once("close", resolve));
  const initialPid = backendPid;
  assert.ok(initialPid, "proxy should identify its managed backend process");
  process.kill(initialPid, "SIGTERM");
  await closed;
  assert.equal(stream.complete, false, "backend interruption must reach the browser");
  await eventually(async () => backendPid !== initialPid &&
    (await request(authenticated("/api/info"))).status === 200,
    "backend did not recover with the existing browser session");
  const after = await request(authenticated(catalogPath));
  assert.equal(after.status, 200);
  assert.deepEqual(JSON.parse(after.body), catalog, "catalog should survive backend restart");
  assert.equal(child.exitCode, null, "proxy should remain alive across backend restart");
  assert.equal((await register("fixture-runtime-reopen-context")).status, 204);
  assert.equal((await request(browser("/?ocode_login=fixture-runtime-reopen-context"))).status, 302);
  t.diagnostic("Interrupted events closed; backend restarted; catalog and browser access recovered");
});
