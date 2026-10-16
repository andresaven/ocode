"""Opt-in integration: real Textual launcher and Docker, local model fixture.

Run directly with the project's Python environment; unittest discovery does
not select this file. Only resources created by this test are removed.
"""
import asyncio
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import sys
from tempfile import TemporaryDirectory
import time
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

PAYLOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PAYLOAD / "src"))

from ocode import docker
from ocode.model import ContainerRuntime
from ocode.paths import Paths
from ocode.store import Store
from ocode.tui import OcodeApp, ProjectScreen

BROWSER_CHECK = "--browser" in sys.argv
if BROWSER_CHECK:
    sys.argv.remove("--browser")


def free_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


class WebLauncherIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_web_button_session_and_complete_prompt(self):
        with TemporaryDirectory(prefix="launcher-test-", dir=PAYLOAD / "tests") as temporary:
            paths = Paths(Path(temporary))
            shutil.copytree(PAYLOAD / "defaults", paths.defaults_dir)
            paths.web_proxy_script.parent.mkdir(parents=True)
            shutil.copy2(PAYLOAD / "src/ocode/web_proxy.mjs", paths.web_proxy_script)
            store = Store(paths)
            cfg = store.initialize()
            cfg.container_runtime = ContainerRuntime(kind="docker")
            # Keep the integration hermetic: no GitHub calls or update prompts.
            cfg.check_updates = False
            store.save_global(cfg)
            workspace = paths.home / "workspace"
            workspace.mkdir()
            shutil.copy2(PAYLOAD / "tests/fixtures/model_server.mjs", workspace / "model_server.mjs")
            (workspace / "opencode.jsonc").write_text(json.dumps({
                "$schema": "https://opencode.ai/config.json",
                "snapshots": False,
                "model": "ollama/ocode-fixture:latest",
                "providers": {"ollama": {
                    "package": "@opencode/ai/providers/openai-compatible",
                    "settings": {"baseURL": "http://127.0.0.1:44097/v1"},
                    "models": {"ocode-fixture:latest": {
                        "package": "@opencode/ai/providers/openai-compatible",
                        "name": "Isolated fixture", "limit": {"context": 32768, "output": 256},
                    }},
                }},
            }), encoding="utf-8")
            project = store.create_project(
                name="web-integration", branch="isolated", workspace=str(workspace),
                runtime="java8", default_mode="serve", port=free_port(),
            )
            engine = docker.resolve_engine(cfg)
            try:
                app = OcodeApp(store, paths)
                async with app.run_test(size=(120, 45)) as pilot:
                    app.open_project(project.project_id)
                    await pilot.pause()
                    screen = app.screen
                    self.assertIsInstance(screen, ProjectScreen)
                    # Exercise the actual button handler, replacing only opening
                    # a user browser. Docker launch and login are not mocked.
                    with patch("ocode.tui.webbrowser.open", return_value=True) as opened:
                        await screen.start_serve()
                    self.assertTrue(opened.called, "Iniciar Web should reach browser opening")
                    login_url = opened.call_args.args[0]
                self.assertTrue(docker.container_running(cfg, project.container_name))
                docker.run_capture(engine, ["exec", "-d", project.container_name,
                                           "node", "/workspace/model_server.mjs"])
                origin = f"http://127.0.0.1:{project.port}"

                def request(method, path, data=None, cookie=None, browser=True):
                    connection = http.client.HTTPConnection("127.0.0.1", project.port, timeout=10)
                    headers = {"content-type": "application/json"}
                    if browser:
                        headers["origin"] = origin
                        headers["sec-fetch-site"] = "same-origin"
                    if cookie:
                        headers["cookie"] = cookie
                    try:
                        connection.request(method, path, json.dumps(data) if data is not None else None, headers)
                        response = connection.getresponse()
                        body = response.read().decode()
                        return response.status, dict(response.getheaders()), json.loads(body) if body.startswith(("{", "[")) else body
                    finally:
                        connection.close()

                login = urlsplit(login_url)
                status, headers, _ = request("GET", login.path + "?" + login.query, browser=False)
                self.assertEqual(status, 302)
                cookie = headers["set-cookie"].split(";")[0]
                status, _, _ = request("GET", "/", cookie=cookie)
                self.assertEqual(status, 200)
                print("Iniciar Web: real Textual handler, Docker launch, host port and login OK")

                # The fixture starts after the real launcher. Reload only
                # before creating sessions, so no live session is evicted.
                status, _, reloaded = request("POST", "/api/location/reload", {}, cookie)
                self.assertEqual(status, 204, f"fixture configuration reload failed: {reloaded}")

                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    status, _, models = request("GET", "/api/model", cookie=cookie)
                    if status == 200 and "ocode-fixture" in json.dumps(models):
                        break
                    await asyncio.sleep(0.25)
                else:
                    self.fail("local fixture model did not become available")
                status, _, created = request("POST", "/api/session", {
                    "title": "isolated complete prompt",
                    "agent": "build",
                    "model": {"providerID": "ollama", "id": "ocode-fixture:latest"},
                }, cookie)
                self.assertEqual(status, 200, f"session creation failed: {created}")
                session = created["data"]
                self.assertEqual(session["location"]["directory"], "/workspace")
                print("Session creation with browser headers and ocode defaults: OK")
                status, _, selected = request("POST", f"/api/session/{session['id']}/model", {
                    "model": {"providerID": "ollama", "id": "ocode-fixture:latest"},
                }, cookie)
                self.assertEqual(status, 204, f"model selection failed: {selected}")
                events = []
                event_types = []
                event_connection = http.client.HTTPConnection("127.0.0.1", project.port, timeout=10)
                event_connection.request("GET", "/api/event", headers={"cookie": cookie})
                event_stream = event_connection.getresponse()
                self.assertEqual(event_stream.status, 200)

                def collect_errors():
                    try:
                        while line := event_stream.readline():
                            if line.startswith(b"data:"):
                                event = json.loads(line[5:].strip())
                                if isinstance(event, dict):
                                    event = event.get("payload", event)
                                if not isinstance(event, dict):
                                    continue
                                category = event.get("type", event.get("_tag", ""))
                                event_types.append(category)
                                if "error" in category.lower() or "failed" in category.lower():
                                    events.append(event)
                    except (OSError, ValueError):
                        pass

                event_thread = threading.Thread(target=collect_errors, daemon=True)
                event_thread.start()
                self.addCleanup(event_connection.close)
                status, _, admitted = request("POST", f"/api/session/{session['id']}/prompt", {
                    "text": "Reply OCODE_FIXTURE_OK. Do not use tools or read files.",
                    "resume": True,
                }, cookie)
                self.assertEqual(status, 200, f"prompt admission failed: {admitted}")
                deadline = time.monotonic() + 30
                completed = []
                while time.monotonic() < deadline:
                    status, _, messages = request("GET", f"/api/session/{session['id']}/message", cookie=cookie)
                    if status == 200:
                        completed = [entry for entry in messages["data"]
                                     if entry.get("type") == "assistant"
                                     and entry.get("finish") == "stop"
                                     and any(part.get("type") == "text"
                                             and part.get("text") == "OCODE_FIXTURE_OK"
                                             for part in entry.get("content", []))]
                        if completed:
                            break
                        if any(entry.get("type") == "idle" and entry.get("outcome") == "failed"
                               for entry in messages["data"]):
                            break
                    await asyncio.sleep(0.25)
                if not completed:
                    states = [{"type": entry.get("type"), "finish": entry.get("finish"),
                               "error": entry.get("error"), "outcome": entry.get("outcome")}
                              for entry in messages.get("data", [])]
                    activity = workspace / "fixture-status.json"
                    requests = json.loads(activity.read_text()) if activity.exists() else {}
                    errors = [event.get("data", {}).get("error") for event in events]
                    self.fail(f"no completed fixture response; states={states}; fixture requests={requests}; errors={errors}; event types={event_types[-10:]}")
                self.assertTrue(docker.container_running(cfg, project.container_name))
                print("Prompt admission and completed local fixture response: OK")
                if BROWSER_CHECK:
                    finished = threading.Event()

                    class BrowserBridge(BaseHTTPRequestHandler):
                        def log_message(self, *args):
                            pass

                        def do_GET(self):
                            if self.path == "/start":
                                preview = docker.CommandPreview([])
                                docker.prepare_web_login(cfg, project.container_name, preview)
                                self.send_response(302)
                                self.send_header("Location", preview.web_login_url("127.0.0.1", project.port))
                                self.end_headers()
                            elif self.path == "/finish":
                                self.send_response(200)
                                self.end_headers()
                                self.wfile.write(b"Browser check finished; disposable resources will be removed.")
                                finished.set()
                            else:
                                self.send_error(404)

                    bridge = ThreadingHTTPServer(("127.0.0.1", 44095), BrowserBridge)
                    bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
                    bridge_thread.start()
                    print("Browser fixture ready: http://127.0.0.1:44095/start", flush=True)
                    try:
                        self.assertTrue(await asyncio.to_thread(finished.wait, 300), "browser check timed out")
                    finally:
                        bridge.shutdown()
                        bridge.server_close()
            finally:
                if docker.container_exists(cfg, project.container_name):
                    if docker.container_running(cfg, project.container_name):
                        docker.stop_container(cfg, project.container_name)
                    docker.remove_container(cfg, project.container_name)
                # These two names were generated uniquely by this fixture.
                for volume in (project.data_volume, project.state_volume):
                    docker.run_capture(engine, ["volume", "rm", volume], check=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
