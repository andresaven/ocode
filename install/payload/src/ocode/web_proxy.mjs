import http from "node:http";
import { spawn } from "node:child_process";
import { Buffer } from "node:buffer";
import { chmodSync, existsSync, lstatSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

const publicPort = Number(process.env.OCODE_PUBLIC_PORT || "4096");
const backendPort = Number(process.env.OCODE_BACKEND_PORT || "4097");
const directory = process.env.OCODE_DIRECTORY || "/workspace";
// Identify the script actually loaded by Node, not just the installed UI.
const proxyRevision = "2026-10-08.1";
const webToken = process.env.OCODE_WEB_TOKEN || "";
const backendPassword = process.env.OPENCODE_SERVER_PASSWORD || "";
const backendUsername = process.env.OPENCODE_SERVER_USERNAME || "opencode";
const cookieName = `ocode_autologin_${publicPort}`;
const controlPath = process.env.OCODE_CONTROL_PATH || "/tmp/ocode-web-control.sock";
const pendingLogins = new Map();

if (!webToken) {
  console.error("[ocode-proxy] missing OCODE_WEB_TOKEN");
  process.exit(2);
}
if (!backendPassword) {
  console.error("[ocode-proxy] missing OPENCODE_SERVER_PASSWORD");
  process.exit(2);
}
pendingLogins.set(webToken, Date.now() + 60000);

let shuttingDown = false;
let backend = null;
let restartCount = 0;
let restartTimer = null;
let shutdownTimer = null;
let stopInstructions = null;

// Older containers have neither these sources nor the helper mount. Keep them
// usable after an application update; their mounts require manual recreation.
async function configureInstructions() {
  if (!process.env.OCODE_GLOBAL_AGENTS) return;
  const { syncInstructions } = await import("./ocode-instructions.mjs");
  stopInstructions = syncInstructions({
    globalSource: process.env.OCODE_GLOBAL_AGENTS,
    projectSource: process.env.OCODE_PROJECT_AGENTS,
    target: join(process.env.XDG_CONFIG_HOME || join(homedir(), ".config"),
      "opencode", "AGENTS.md"),
    onError: () => {
      console.error("[ocode-proxy] cannot synchronize instructions; stopping backend");
      shutdown("instructions-sync-failed", 2);
    },
  });
}

function startBackend() {
  if (shuttingDown || backend) return;

  backend = spawn(
    "opencode",
    [
      "serve",
      "--hostname",
      "127.0.0.1",
      "--port",
      String(backendPort),
    ],
    {
      env: process.env,
      // Do not pipe stdout/stderr through Node. The 0.4.20 proxy captured
      // them to discover a generated password; that extra coupling is gone.
      stdio: ["ignore", "inherit", "inherit"],
    },
  );

  console.log(
    `[ocode-proxy] backend started pid=${backend.pid ?? ""} ` +
    `port=${backendPort}`,
  );

  backend.on("exit", (code, signal) => {
    backend = null;

    if (shuttingDown) {
      console.log(
        `[ocode-proxy] backend exited during shutdown code=${code ?? ""} ` +
        `signal=${signal ?? ""}`,
      );
      return;
    }

    restartCount += 1;
    const delayMs = Math.min(5000, 500 * Math.max(1, restartCount));

    console.error(
      `[ocode-proxy] backend exited code=${code ?? ""} ` +
      `signal=${signal ?? ""}; restart in ${delayMs}ms`,
    );

    restartTimer = setTimeout(() => {
      restartTimer = null;
      startBackend();
    }, delayMs);
  });

  backend.on("error", (error) => {
    console.error(`[ocode-proxy] backend spawn error: ${error.message}`);
  });
}

function parseCookies(header) {
  const result = {};
  if (!header) return result;

  for (const pair of header.split(";")) {
    const index = pair.indexOf("=");
    if (index < 0) continue;
    const key = pair.slice(0, index).trim();
    const value = pair.slice(index + 1).trim();
    result[key] = value;
  }
  return result;
}

function authorizeBrowser(req, res) {
  const url = new URL(
    req.url || "/",
    `http://${req.headers.host || "localhost"}`,
  );
  const queryToken = url.searchParams.get("ocode_login");
  // Keep the browser on the application's root route throughout login.
  // Accept the short-lived URLs emitted by earlier ocode builds as well.
  const loginPage = queryToken !== null || url.pathname === "/_ocode/login";
  const verifyLogin = (url.pathname === "/" && url.searchParams.has("_ocode_verify")) ||
    url.pathname === "/_ocode/login/verify";

  const expires = pendingLogins.get(queryToken);
  if (expires && expires > Date.now()) {
    pendingLogins.delete(queryToken);
    url.searchParams.delete("ocode_login");
    const location = "/?_ocode_verify=1";

    res.writeHead(302, {
      "set-cookie":
        `${cookieName}=${webToken}; Path=/; HttpOnly; SameSite=Strict`,
      location: location || "/",
      "cache-control": "no-store",
      "referrer-policy": "no-referrer",
    });
    res.end();
    return false;
  }

  const cookies = parseCookies(req.headers.cookie);
  if (!loginPage && cookies[cookieName] === webToken) {
    if (verifyLogin) {
      res.writeHead(302, {
        location: "/",
        "cache-control": "no-store",
        "referrer-policy": "no-referrer",
      });
      res.end();
      return false;
    }
    return true;
  }

  res.writeHead(401, {
    "content-type": "text/plain; charset=utf-8",
    "cache-control": "no-store",
    "x-ocode-auth-source": "proxy",
  });
  res.end(verifyLogin
    ? "OpenCode Web: el navegador no ha devuelto una cookie de acceso válida. " +
      "El login no se ha completado. Comprueba el bloqueo de cookies y que " +
      "la dirección no cambie entre localhost y 127.0.0.1."
    : loginPage
      ? "OpenCode Web: el enlace de acceso ha caducado o ya se ha utilizado. " +
        "Pulsa Abrir Web de nuevo en ocode."
      : "OpenCode Web: abre esta instancia desde ocode.");
  return false;
}

function backendHeaders(headers) {
  const result = { ...headers };

  // The browser authenticates only to the local ocode proxy. Do not forward
  // the proxy cookie to OpenCode itself.
  delete result.cookie;

  result.host = `127.0.0.1:${backendPort}`;
  result.authorization =
    "Basic " +
    Buffer.from(
      `${backendUsername}:${backendPassword}`,
      "utf8",
    ).toString("base64");
  result["x-opencode-directory"] = directory;
  return result;
}

function retireServiceWorker(req, res) {
  if (req.headers["service-worker"] !== "script" ||
      !["GET", "HEAD"].includes(req.method)) return false;

  // A cached navigation can bypass login entirely. Serve a replacement worker
  // without a fetch handler, rather than letting OpenCode install an offline
  // shell on this authenticated origin. It also retires existing registrations
  // when the browser checks for an update, even if the login cookie has expired.
  // This fixed public script contains no backend or browser authentication.
  const script = `
self.addEventListener("install", event => {
  event.waitUntil(self.skipWaiting());
});
self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    await self.clients.claim();
    await self.registration.unregister();
  })());
});
`;
  res.writeHead(200, {
    "content-type": "application/javascript; charset=utf-8",
    "cache-control": "no-store",
    "service-worker-allowed": "/",
    "x-content-type-options": "nosniff",
  });
  res.end(req.method === "HEAD" ? undefined : script);
  return true;
}

function proxyHttp(req, res) {
  if (retireServiceWorker(req, res)) return;
  if (!authorizeBrowser(req, res)) return;

  const documentRequest = req.headers["sec-fetch-dest"] === "document" ||
    (req.headers.accept || "").includes("text/html");
  const requestHeaders = backendHeaders(req.headers);
  if (documentRequest) {
    // A 304 could revive a previously cached application shell after login.
    delete requestHeaders["if-none-match"];
    delete requestHeaders["if-modified-since"];
  }

  let upstreamResponse = null;
  function unavailable(error) {
    if (res.destroyed || res.writableEnded) return;
    if (res.headersSent) {
      // A partial response (including SSE) must be interrupted, not ended
      // successfully or appended to. Let the browser detect the disconnect.
      res.destroy();
      return;
    }
    res.writeHead(503, {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
      "retry-after": "1",
    });
    res.end(`OpenCode backend unavailable: ${error.message}`);
  }

  const upstream = http.request(
    {
      hostname: "127.0.0.1",
      port: backendPort,
      method: req.method,
      path: req.url,
      headers: requestHeaders,
    },
    (upstreamRes) => {
      upstreamResponse = upstreamRes;
      upstreamRes.on("error", unavailable);
      upstreamRes.on("aborted", () => {
        unavailable(new Error("backend response interrupted"));
      });
      if (res.destroyed) {
        upstreamRes.destroy();
        return;
      }
      const responseHeaders = { ...upstreamRes.headers };
      if (documentRequest ||
          (upstreamRes.headers["content-type"] || "").includes("text/html")) {
        // Do not reuse the application document across container/login changes.
        // Static assets retain their backend cache policy.
        responseHeaders["cache-control"] = "no-store";
        delete responseHeaders.etag;
        delete responseHeaders["last-modified"];
      }
      delete responseHeaders["x-ocode-auth-source"];
      if (upstreamRes.statusCode === 401) {
        responseHeaders["x-ocode-auth-source"] = "backend";
        responseHeaders["cache-control"] = "no-store";
      }
      res.writeHead(upstreamRes.statusCode ?? 502, responseHeaders);
      upstreamRes.pipe(res);
    },
  );

  upstream.on("error", unavailable);
  const disconnect = () => {
    upstreamResponse?.destroy();
    upstream.destroy();
  };
  res.on("close", disconnect);
  res.on("error", disconnect);
  req.on("aborted", () => res.destroy());
  req.on("error", () => res.destroy());

  // Never replay requests: a failed prompt may already have been accepted.
  req.pipe(upstream);
}

function proxyUpgrade(req, socket, head) {
  const fakeResponse = {
    writeHead(statusCode, headers) {
      let text = `HTTP/1.1 ${statusCode} Unauthorized\r\n`;
      for (const [name, value] of Object.entries(headers || {})) {
        text += `${name}: ${value}\r\n`;
      }
      text += "\r\n";
      socket.write(text);
    },
    end(body = "") {
      if (body) socket.write(body);
      socket.destroy();
    },
  };

  if (!authorizeBrowser(req, fakeResponse)) return;

  let upgradedSocket = null;
  const upstream = http.request({
    hostname: "127.0.0.1",
    port: backendPort,
    method: req.method,
    path: req.url,
    headers: backendHeaders(req.headers),
  });

  const disconnect = () => {
    upstream.destroy();
    upgradedSocket?.destroy();
  };
  socket.on("close", disconnect);
  socket.on("error", disconnect);
  // Upgraded HTTP sockets can be half-open: EOF alone does not emit close.
  socket.on("end", () => socket.destroy());

  upstream.on("upgrade", (upstreamRes, upstreamSocket, upstreamHead) => {
    upgradedSocket = upstreamSocket;
    upstreamSocket.on("error", () => socket.destroy());
    upstreamSocket.on("end", () => socket.destroy());
    upstreamSocket.on("close", () => socket.destroy());
    if (socket.destroyed) {
      upstreamSocket.destroy();
      return;
    }
    let response =
      `HTTP/1.1 ${upstreamRes.statusCode} ${upstreamRes.statusMessage}\r\n`;

    for (const [name, value] of Object.entries(upstreamRes.headers)) {
      if (Array.isArray(value)) {
        for (const item of value) response += `${name}: ${item}\r\n`;
      } else if (value !== undefined) {
        response += `${name}: ${value}\r\n`;
      }
    }
    response += "\r\n";

    socket.write(response);
    if (upstreamHead.length) socket.write(upstreamHead);
    if (head.length) upstreamSocket.write(head);

    upstreamSocket.pipe(socket).pipe(upstreamSocket);
  });

  // A rejected upgrade is an ordinary HTTP response. Do not leave the
  // browser's upgrade socket waiting forever for a handshake.
  upstream.on("response", (upstreamRes) => {
    upstreamRes.on("error", () => socket.destroy());
    upstreamRes.resume();
    socket.end(
      `HTTP/1.1 ${upstreamRes.statusCode ?? 502} ` +
      `${upstreamRes.statusMessage || "Bad Gateway"}\r\n` +
      "Connection: close\r\nContent-Length: 0\r\n\r\n",
    );
  });
  upstream.on("error", () => socket.destroy());
  upstream.end();
}

const proxyServer = http.createServer(proxyHttp);
proxyServer.on("upgrade", proxyUpgrade);

// Only docker exec inside this container can register a new browser login.
// Never return the backend authentication or the session cookie to ocode.
const controlServer = http.createServer((req, res) => {
  if (req.method !== "POST" || req.url !== "/login") {
    res.writeHead(404).end();
    return;
  }
  let body = "";
  req.setTimeout(2000, () => req.destroy());
  req.on("data", chunk => {
    body += chunk.toString("utf8");
    if (body.length > 128) req.destroy();
  });
  req.on("end", () => {
    if (!/^[A-Za-z0-9_-]{20,128}$/.test(body)) {
      res.writeHead(400).end();
      return;
    }
    const probe = http.get({
      hostname: "127.0.0.1", port: backendPort,
      path: "/api/info", headers: backendHeaders({}),
    }, upstream => {
      upstream.resume();
      if (upstream.statusCode !== 200) {
        res.writeHead(503).end();
        return;
      }
      for (const [key, expiry] of pendingLogins) {
        if (expiry <= Date.now()) pendingLogins.delete(key);
      }
      if (pendingLogins.size >= 20) {
        pendingLogins.delete(pendingLogins.keys().next().value);
      }
      pendingLogins.set(body, Date.now() + 60000);
      res.writeHead(204).end();
    });
    probe.setTimeout(1000, () => probe.destroy());
    probe.on("error", () => {
      if (!res.headersSent) res.writeHead(503).end();
    });
  });
});

function startServers() {
  console.log(`[ocode-proxy] revision=${proxyRevision} node=${process.version}`);
  if (existsSync(controlPath)) {
    if (!lstatSync(controlPath).isSocket()) {
      throw new Error("Unexpected file at proxy control socket path");
    }
    unlinkSync(controlPath);
  }
  controlServer.listen(controlPath, () => chmodSync(controlPath, 0o600));

  proxyServer.listen(publicPort, "0.0.0.0", () => {
    console.log(
      `[ocode-proxy] listening 0.0.0.0:${publicPort} -> ` +
      `127.0.0.1:${backendPort} directory=${directory}`,
    );
    startBackend();
  });
}

configureInstructions().then(startServers).catch(() => {
  console.error("[ocode-proxy] initialization failed; backend not started");
  process.exit(2);
});

function shutdown(reason, exitCode = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`[ocode-proxy] shutdown reason=${reason} exit=${exitCode}`);
  stopInstructions?.();

  if (restartTimer) {
    clearTimeout(restartTimer);
    restartTimer = null;
  }

  if (backend) {
    backend.kill("SIGTERM");
  }

  controlServer.close();

  proxyServer.close(() => {
    if (shutdownTimer) {
      clearTimeout(shutdownTimer);
      shutdownTimer = null;
    }
    process.exit(exitCode);
  });

  // Do not remain stuck forever if a connection refuses to close.
  shutdownTimer = setTimeout(() => {
    console.log(`[ocode-proxy] shutdown deadline reached exit=${exitCode}`);
    process.exit(exitCode);
  }, 3000);
  shutdownTimer.unref();
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("exit", (code) => {
  console.log(`[ocode-proxy] process exit=${code} shutdown=${shuttingDown}`);
});

process.on("uncaughtException", (error) => {
  console.error(`[ocode-proxy] uncaught exception: ${error.stack || error}`);
});

process.on("unhandledRejection", (reason) => {
  console.error(`[ocode-proxy] unhandled rejection: ${String(reason)}`);
});
