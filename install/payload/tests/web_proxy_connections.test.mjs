import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import net from "node:net";
import { EventEmitter } from "node:events";
import { readFileSync, mkdtempSync, rmSync, chmodSync, existsSync, lstatSync, unlinkSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "../src/ocode/web_proxy.mjs"), "utf8")
  .replace(/^import .*;\r?\n/gm, "");

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

async function listen(server) {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  return server.address().port;
}

async function fixture(t, handler, { session = "fixture-session" } = {}) {
  const backend = http.createServer(handler);
  const backendPort = await listen(backend);
  const temporary = mkdtempSync(join(here, "connections-test-"));
  const sockets = new Set();
  const children = [];
  const childEvents = new EventEmitter();
  const logs = [];
  const exits = [];
  const signals = new Map();
  backend.on("connection", socket => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
  });
  // Run the actual proxy with a controlled backend. No OpenCode process,
  // persistent data or installed configuration is loaded by these tests.
  const context = vm.createContext({
    http, Buffer, URL, setTimeout, clearTimeout,
    chmodSync: process.platform === "win32" ? () => {} : chmodSync,
    existsSync, lstatSync, unlinkSync,
    console: {
      log: message => logs.push(message),
      error: message => logs.push(message),
    },
    spawn: () => {
      const child = Object.assign(new EventEmitter(), {
        pid: children.length + 1, kill() {},
      });
      children.push(child);
      childEvents.emit("started", child);
      return child;
    },
    process: {
      version: "fixture-node",
      env: {
        OCODE_PUBLIC_PORT: "0",
        OCODE_BACKEND_PORT: String(backendPort),
        OCODE_WEB_TOKEN: session,
        OPENCODE_SERVER_PASSWORD: "fixture-only",
        OCODE_CONTROL_PATH: process.platform === "win32"
          ? `\\\\.\\pipe\\ocode-connections-${process.pid}-${temporary.split(/[\\/]/).pop()}`
          : join(temporary, "control.sock"),
      },
      on: (name, listener) => signals.set(name, listener),
      exit: code => exits.push(code),
    },
  });
  vm.runInContext(source, context);
  const proxy = vm.runInContext("proxyServer", context);
  const control = vm.runInContext("controlServer", context);
  const clients = new Set();
  proxy.on("connection", socket => {
    clients.add(socket);
    socket.on("close", () => clients.delete(socket));
  });
  t.after(async () => {
    vm.runInContext("if (restartTimer) clearTimeout(restartTimer)", context);
    vm.runInContext("if (shutdownTimer) clearTimeout(shutdownTimer)", context);
    for (const socket of clients) socket.destroy();
    for (const socket of sockets) socket.destroy();
    await Promise.all([proxy, control, backend].map(server =>
      new Promise(resolve => server.close(resolve))));
    rmSync(temporary, { recursive: true, force: true });
  });
  if (!proxy.listening) await new Promise(resolve => proxy.once("listening", resolve));
  if (!control.listening) await new Promise(resolve => control.once("listening", resolve));
  return {
    backend, context, control, children, childEvents, logs, exits, signals,
    options: {
      hostname: "127.0.0.1", port: proxy.address().port,
      headers: { cookie: `ocode_autologin_0=${session}` },
    },
  };
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
    req.end(body);
  });
}

test("interrupted events close the browser connection and allow a fresh request", { timeout: 5000 }, async t => {
  let count = 0;
  const emitted = deferred();
  let stream;
  const { options } = await fixture(t, (req, res) => {
    count++;
    if (count > 1) return res.end("reconnected");
    stream = res;
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write("data: connected\n\n");
  });
  const closed = deferred();
  const req = http.get(options, res => {
    res.on("data", () => emitted.resolve());
    res.on("error", () => {});
    res.on("close", () => closed.resolve(res.complete));
  });
  req.on("error", () => {});
  t.after(() => req.destroy());
  await emitted.promise;
  stream.destroy();
  assert.equal(await closed.promise, false, "interruption must not appear as a successful response");
  assert.equal((await request(options)).body, "reconnected");
});

test("closing the browser cancels its backend event stream", { timeout: 5000 }, async t => {
  const upstreamClosed = deferred();
  const { options } = await fixture(t, (req, res) => {
    res.on("close", () => upstreamClosed.resolve());
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write("data: connected\n\n");
  });
  const req = http.get(options, res => {
    res.on("error", () => {});
    res.once("data", () => res.destroy());
  });
  req.on("error", () => {});
  t.after(() => req.destroy());
  await upstreamClosed.promise;
});

test("normal responses retain their body and status", { timeout: 5000 }, async t => {
  const { options } = await fixture(t, (req, res) => {
    res.writeHead(201, { "content-type": "application/json" });
    res.end('{"ready":true}');
  });
  const result = await request(options);
  assert.equal(result.status, 201);
  assert.equal(result.body, '{"ready":true}');
});

test("fresh login replaces another instance's cookie and verifies access", { timeout: 5000 }, async t => {
  const first = await fixture(t, (req, res) => res.end("first"));
  const second = await fixture(t, (req, res) => res.end("second"), {
    session: "fixture-other-session",
  });
  const staleCookie = first.options.headers.cookie;
  const rejected = await request({
    ...second.options, path: "/api/info", headers: { cookie: staleCookie },
  });
  assert.equal(rejected.status, 401);
  assert.equal(rejected.headers["x-ocode-auth-source"], "proxy");

  const handle = "fixture-new-login-context";
  const registered = await request({
    socketPath: second.control.address(), method: "POST", path: "/login",
  }, handle);
  assert.equal(registered.status, 204);
  const login = await request({
    ...second.options, path: `/?ocode_login=${handle}`,
    headers: { cookie: staleCookie },
  });
  assert.equal(login.status, 302);
  const cookie = login.headers["set-cookie"][0].split(";")[0];
  assert.notEqual(cookie, staleCookie);
  assert.equal(cookie.split("=")[0], staleCookie.split("=")[0]);
  const options = { ...second.options, headers: { cookie } };
  const verified = await request({ ...options, path: login.headers.location });
  assert.equal(verified.status, 302);
  assert.equal(verified.headers.location, "/");
  assert.equal((await request({ ...options, path: "/api/info" })).body, "second");
  assert.equal((await request({
    ...options, path: `/?ocode_login=${handle}`,
  })).status, 401, "links remain single-use after replacing a stale cookie");
});

test("backend exit restarts only the backend and preserves browser access", { timeout: 5000 }, async t => {
  const { backend, options, children, childEvents, logs, exits } = await fixture(t, (req, res) => res.end("ready"));
  assert.equal(children.length, 1);
  const backendPort = backend.address().port;
  assert.equal((await request({ ...options, path: "/api/info" })).status, 200);
  await new Promise(resolve => backend.close(resolve));
  const restarted = new Promise(resolve => childEvents.once("started", resolve));
  children[0].emit("exit", 1, null);
  const unavailable = await request({ ...options, path: "/api/info" });
  assert.equal(unavailable.status, 503, "proxy keeps listening while the backend is down");
  await restarted;
  await new Promise(resolve => backend.listen(backendPort, "127.0.0.1", resolve));
  assert.equal(children.length, 2);
  assert.equal((await request({ ...options, path: "/api/info" })).status, 200);
  assert.deepEqual(exits, []);
  assert.ok(logs.some(line => line.includes("backend exited code=1")));
  assert.ok(logs.some(line => line.includes("revision=2026-10-08.1")));
});

for (const signal of ["SIGTERM", "SIGINT"]) {
  test(`${signal} records the shutdown reason without exposing access data`, { timeout: 5000 }, async t => {
    const { context, signals, logs, exits } = await fixture(t, (req, res) => res.end());
    const closed = new Promise(resolve => vm.runInContext("proxyServer", context).once("close", resolve));
    signals.get(signal)();
    signals.get(signal)();
    await closed;
    assert.deepEqual(exits, [0]);
    assert.equal(logs.filter(line => line.includes(`shutdown reason=${signal} exit=0`)).length, 1);
    signals.get("exit")(0);
    assert.ok(logs.some(line => line.includes("process exit=0 shutdown=true")));
    assert.ok(logs.every(line => !line.includes("fixture-session") && !line.includes("fixture-only")));
  });
}

test("instruction synchronization failure closes the proxy with a failure status", { timeout: 5000 }, async t => {
  const { context, logs, exits } = await fixture(t, (req, res) => res.end());
  const closed = new Promise(resolve => vm.runInContext("proxyServer", context).once("close", resolve));
  vm.runInContext('shutdown("instructions-sync-failed", 2)', context);
  await closed;
  assert.deepEqual(exits, [2]);
  assert.ok(logs.some(line => line.includes("shutdown reason=instructions-sync-failed exit=2")));
});

test("service worker updates retire cached navigation without a browser login", { timeout: 5000 }, async t => {
  let calls = 0;
  const { options } = await fixture(t, (req, res) => {
    calls++;
    res.end("unexpected");
  });
  const result = await request({
    ...options, path: "/sw.js",
    headers: { "service-worker": "script" },
  });
  assert.equal(result.status, 200);
  assert.equal(result.headers["cache-control"], "no-store");
  assert.match(result.headers["content-type"], /application\/javascript/);
  assert.equal(result.headers["service-worker-allowed"], "/");
  assert.equal(calls, 0, "worker updates never reach the backend");

  // Execute only the fixed replacement script with a simulated worker lifecycle.
  // No real browser storage or OpenCode data is read or changed.
  const events = new Map();
  const lifecycle = [];
  vm.runInNewContext(result.body, {
    self: {
      addEventListener: (name, listener) => events.set(name, listener),
      skipWaiting: async () => lifecycle.push("skipWaiting"),
      clients: { claim: async () => lifecycle.push("claim") },
      registration: { unregister: async () => lifecycle.push("unregister") },
    },
  });
  assert.equal(events.has("fetch"), false, "replacement cannot intercept login or API requests");
  for (const name of ["install", "activate"]) {
    let work;
    events.get(name)({ waitUntil: promise => { work = promise; } });
    await work;
  }
  assert.deepEqual(lifecycle, ["skipWaiting", "claim", "unregister"]);
});

test("ordinary workers still use the authenticated backend", { timeout: 5000 }, async t => {
  const { options } = await fixture(t, (req, res) => {
    res.writeHead(200, { "content-type": "application/javascript" });
    res.end("ordinary worker");
  });
  const result = await request({
    ...options, path: "/worker.js",
    headers: { ...options.headers, "sec-fetch-dest": "worker" },
  });
  assert.equal(result.status, 200);
  assert.equal(result.body, "ordinary worker");
  const unauthenticated = await request({ ...options, path: "/api/info", headers: {} });
  assert.equal(unauthenticated.status, 401, "backend routes still require a browser login");
});

test("application documents cannot revive an old cached shell", { timeout: 5000 }, async t => {
  const { options } = await fixture(t, (req, res) => {
    assert.equal(req.headers["if-none-match"], undefined);
    assert.equal(req.headers["if-modified-since"], undefined);
    res.writeHead(200, {
      "content-type": "text/html", "cache-control": "public, max-age=86400",
      etag: '"old-shell"', "last-modified": "Wed, 07 Oct 2026 09:00:00 GMT",
    });
    res.end("application document");
  });
  const result = await request({
    ...options, path: "/",
    headers: {
      ...options.headers, accept: "text/html", "sec-fetch-dest": "document",
      "if-none-match": '"old-shell"', "if-modified-since": "Wed, 07 Oct 2026 09:00:00 GMT",
    },
  });
  assert.equal(result.status, 200);
  assert.equal(result.headers["cache-control"], "no-store");
  assert.equal(result.headers.etag, undefined);
  assert.equal(result.headers["last-modified"], undefined);
  assert.equal(result.body, "application document");
});

test("static assets retain caching without affecting login", { timeout: 5000 }, async t => {
  const { options } = await fixture(t, (req, res) => {
    assert.equal(req.headers["if-none-match"], '"asset"');
    res.writeHead(200, {
      "content-type": "application/javascript", "cache-control": "public, max-age=86400",
      etag: '"asset"',
    });
    res.end("asset");
  });
  const result = await request({
    ...options, path: "/_assets/runtime.js",
    headers: { ...options.headers, "if-none-match": '"asset"' },
  });
  assert.equal(result.headers["cache-control"], "public, max-age=86400");
  assert.equal(result.headers.etag, '"asset"');
  assert.equal(result.body, "asset");
});

test("stale browser login is identified without reaching the backend", { timeout: 5000 }, async t => {
  let calls = 0;
  const { options } = await fixture(t, (req, res) => {
    calls++;
    res.end("unexpected");
  });
  const result = await request({
    ...options, path: "/api/info",
    headers: { cookie: "ocode_autologin=legacy-context; ocode_autologin_0=old-context" },
  });
  assert.equal(result.status, 401);
  assert.equal(result.headers["x-ocode-auth-source"], "proxy");
  assert.equal(result.headers["cache-control"], "no-store");
  assert.equal(calls, 0);
});

test("legacy cookie and stale browser authorization do not override a valid proxy login", { timeout: 5000 }, async t => {
  const { options } = await fixture(t, (req, res) => {
    assert.equal(req.headers.cookie, undefined);
    assert.equal(req.headers.authorization,
      "Basic " + Buffer.from("opencode:fixture-only").toString("base64"));
    assert.equal(req.headers["x-opencode-directory"], "/workspace");
    res.end("ready");
  });
  const result = await request({
    ...options, path: "/api/info",
    headers: {
      cookie: "ocode_autologin=legacy-context; " + options.headers.cookie,
      authorization: "Basic stale-browser-context",
    },
  });
  assert.equal(result.status, 200);
  assert.equal(result.body, "ready");
  assert.equal(result.headers["x-ocode-auth-source"], undefined);
});

test("backend rejection is identified even with an empty response", { timeout: 5000 }, async t => {
  const { options } = await fixture(t, (req, res) => res.writeHead(401).end());
  const result = await request({ ...options, path: "/api/info" });
  assert.equal(result.status, 401);
  assert.equal(result.body, "");
  assert.equal(result.headers["x-ocode-auth-source"], "backend");
  assert.equal(result.headers["cache-control"], "no-store");
});

test("a disconnected prompt is reported once, never replayed", { timeout: 5000 }, async t => {
  let count = 0;
  let body = "";
  const { options } = await fixture(t, (req, res) => {
    count++;
    req.on("data", chunk => { body += chunk; });
    req.on("end", () => res.destroy());
  });
  const result = await request({ ...options, method: "POST", path: "/prompt" }, "fixture prompt");
  assert.equal(result.status, 503);
  assert.equal(result.headers["retry-after"], "1");
  assert.equal(count, 1);
  assert.equal(body, "fixture prompt");
});

test("a rejected WebSocket upgrade returns its status and closes", { timeout: 5000 }, async t => {
  const { options } = await fixture(t, (req, res) => {
    res.writeHead(503).end();
  });
  const result = await request({
    ...options,
    headers: { ...options.headers, connection: "Upgrade", upgrade: "websocket" },
  });
  assert.equal(result.status, 503);
  assert.equal(result.headers.connection, "close");
});

for (const side of ["backend", "browser"]) {
  test(`WebSocket ${side} disconnect closes the other end`, { timeout: 5000 }, async t => {
    const { backend, options } = await fixture(t, (req, res) => res.end());
    let backendSocket;
    const upstreamClosed = deferred();
    backend.on("upgrade", (req, socket) => {
      backendSocket = socket;
      socket.on("error", () => {});
      socket.on("end", () => {
        upstreamClosed.resolve();
        socket.destroy();
      });
      socket.on("close", () => upstreamClosed.resolve());
      socket.resume();
      socket.write("HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n");
    });
    const browserClosed = deferred();
    const req = http.request({
      ...options,
      headers: { ...options.headers, connection: "Upgrade", upgrade: "websocket" },
    });
    req.on("error", () => {});
    req.on("upgrade", (res, socket) => {
      t.after(() => socket.destroy());
      socket.on("error", () => {});
      socket.on("close", () => browserClosed.resolve());
      socket.resume();
      if (side === "backend") backendSocket.destroy();
      else socket.destroy();
    });
    t.after(() => req.destroy());
    req.end();
    await Promise.all([browserClosed.promise, upstreamClosed.promise]);
  });
}
