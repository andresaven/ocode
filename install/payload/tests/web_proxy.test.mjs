import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import net from "node:net";
import { spawn } from "node:child_process";
import { chmodSync, mkdtempSync, rmSync } from "node:fs";
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
      res.on("data", chunk => data += chunk);
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: data }));
    });
    req.on("error", reject);
    req.setTimeout(2000, () => req.destroy(new Error("request timeout")));
    req.end(body);
  });
}

test("persistent Web can reopen without exposing stored authentication", async t => {
  const directory = mkdtempSync(join(here, "proxy-test-"));
  const socketPath = join(directory, "control.sock");
  const port = await freePort();
  const backendPort = await freePort();
  chmodSync(join(here, "fixtures", "opencode"), 0o755);
  const child = spawn(process.execPath, [resolve(here, "../src/ocode/web_proxy.mjs")], {
    env: {
      ...process.env,
      PATH: `${join(here, "fixtures")}:${process.env.PATH}`,
      OCODE_PUBLIC_PORT: String(port), OCODE_BACKEND_PORT: String(backendPort),
      OCODE_CONTROL_PATH: socketPath, OCODE_WEB_TOKEN: "fixture-browser-session",
      OPENCODE_SERVER_PASSWORD: "fixture-backend-only",
      OPENCODE_SERVER_USERNAME: "opencode",
    },
    stdio: "ignore",
  });
  t.after(async () => {
    if (child.exitCode === null) {
      const exited = new Promise(resolve => child.once("exit", resolve));
      child.kill("SIGTERM");
      await exited;
    }
    rmSync(directory, { recursive: true, force: true });
  });
  const browser = path => ({ hostname: "127.0.0.1", port, path });
  const register = handle => request({ socketPath, path: "/login", method: "POST" }, handle);
  let ready = false;
  for (let attempt = 0; attempt < 50; attempt++) {
    try {
      ready = (await register("fixture-first-login-context")).status === 204;
    } catch {}
    if (ready) break;
    await delay(100);
  }
  assert.ok(ready, "fake backend and proxy should become ready");
  assert.equal((await request(browser("/"))).status, 401);
  assert.equal((await request(browser("/login"))).status, 401, "control is not publicly exposed");
  const login = await request(browser("/?ocode_login=fixture-first-login-context"));
  assert.equal(login.status, 302);
  assert.equal(login.headers.location, "/?_ocode_verify=1");
  const cookie = login.headers["set-cookie"][0];
  assert.ok(cookie.startsWith(`ocode_autologin_${port}=`));
  assert.ok(cookie.includes("HttpOnly"));
  const response = await request({ ...browser("/api/info"), headers: { cookie: cookie.split(";")[0] } });
  assert.equal(response.status, 200);
  const info = JSON.parse(response.body);
  assert.equal(info.directory, "/workspace");
  assert.equal(info.forwardedCookie, false);
  assert.equal((await request(browser("/?ocode_login=fixture-first-login-context"))).status, 401,
    "login contexts are single-use");
  assert.equal((await register("fixture-second-login-context")).status, 204);
  const reopened = await request(browser("/?ocode_login=fixture-second-login-context"));
  assert.equal(reopened.status, 302, "new ocode session can reopen the same container");
  const registration = await register("fixture-verified-login-context");
  assert.equal(registration.status, 204);
  const explicitLogin = await request(browser("/?ocode_login=fixture-verified-login-context"));
  assert.equal(explicitLogin.status, 302);
  assert.equal(explicitLogin.headers.location, "/?_ocode_verify=1");
  assert.equal(explicitLogin.headers["cache-control"], "no-store");
  assert.equal(explicitLogin.headers["referrer-policy"], "no-referrer");
  const missingCookie = await request(browser("/?_ocode_verify=1"));
  assert.equal(missingCookie.status, 401);
  assert.match(missingCookie.body, /login no se ha completado/);
  const verifiedCookie = explicitLogin.headers["set-cookie"][0].split(";")[0];
  const verified = await request({
    ...browser("/?_ocode_verify=1"), headers: { cookie: verifiedCookie },
  });
  assert.equal(verified.status, 302);
  assert.equal(verified.headers.location, "/");
  const verifiedInfo = await request({
    ...browser("/api/info"), headers: { cookie: verifiedCookie },
  });
  assert.equal(verifiedInfo.status, 200);
  const reusedLogin = await request({
    ...browser("/?ocode_login=fixture-verified-login-context"),
    headers: { cookie: verifiedCookie },
  });
  assert.equal(reusedLogin.status, 401, "login links remain single-use even with a valid cookie");
  assert.equal((await register("invalid")).status, 400);
});
