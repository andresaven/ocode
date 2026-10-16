from dataclasses import dataclass
import json
import secrets
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

from .model import GlobalConfig, MAVEN_VERSION, ProjectConfig, normalize_build_path
from .paths import Paths


class DockerError(RuntimeError):
    pass


@dataclass
class Engine:
    kind: str
    command: list[str]

    @property
    def is_wsl(self) -> bool:
        return self.kind == "wsl-docker"

    def host_path(self, path: Path) -> str:
        value = str(path.resolve())
        if not self.is_wsl:
            return value

        p = PureWindowsPath(value)
        if not p.drive:
            raise DockerError(f"No se puede traducir la ruta a WSL: {value}")

        drive = p.drive.rstrip(":").lower()
        tail = "/".join(p.parts[1:])
        return f"/mnt/{drive}/{tail}".replace("\\", "/")


@dataclass
class CommandPreview:
    args: list[str]
    web_token: str | None = None
    server_password: str | None = None
    reused: bool = False
    foreground_args: list[str] | None = None

    def powershell(self):
        def q(value):
            if (
                self.web_token
                and value == f"OCODE_WEB_TOKEN={self.web_token}"
            ):
                value = "OCODE_WEB_TOKEN=<redacted>"
            if (
                self.server_password
                and value
                == f"OPENCODE_SERVER_PASSWORD={self.server_password}"
            ):
                value = "OPENCODE_SERVER_PASSWORD=<redacted>"
            if not value or any(ch in value for ch in ' \t"&|<>'):
                return '"' + value.replace('"', '`"') + '"'
            return value
        return " ".join(q(x) for x in self.args)

    def web_login_url(self, host: str, port: int) -> str:
        if not self.web_token:
            return f"http://{host}:{port}/"
        return (
            f"http://{host}:{port}/"
            f"?ocode_login={quote(self.web_token, safe='')}"
        )


def _probe(command: list[str]) -> bool:
    try:
        cp = subprocess.run(
            command + ["version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
        return cp.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def resolve_engine(cfg: GlobalConfig) -> Engine:
    runtime = cfg.container_runtime
    kind = runtime.kind

    if kind == "unset":
        raise DockerError(
            "El motor de contenedores aún no está configurado. "
            "Ábrelo desde la configuración de ocode."
        )

    if kind == "custom":
        if not runtime.command:
            raise DockerError("container_runtime.command está vacío.")
        return Engine(kind="custom", command=list(runtime.command))

    if kind == "docker":
        return Engine(kind="docker", command=["docker"])
    if kind == "podman":
        return Engine(kind="podman", command=["podman"])
    if kind == "wsl-docker":
        return Engine(kind="wsl-docker", command=["wsl", "docker"])

    # Auto: no modifica aliases ni perfiles. Solo prueba CLIs reales.
    candidates = [
        Engine("docker", ["docker"]),
        Engine("podman", ["podman"]),
        Engine("wsl-docker", ["wsl", "docker"]),
    ]
    for engine in candidates:
        if _probe(engine.command):
            return engine

    raise DockerError(
        "No se encontró un motor de contenedores utilizable. "
        "Configura container_runtime en config\\config.json."
    )


def run_capture(engine: Engine, args, check=True):
    full = engine.command + list(args)
    try:
        return subprocess.run(
            full,
            check=check,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        raise DockerError(
            f"No se encuentra el runtime: {' '.join(engine.command)}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise DockerError(exc.stdout or str(exc)) from exc


def docker_available(cfg: GlobalConfig):
    try:
        engine = resolve_engine(cfg)
        cp = run_capture(engine, ["version"], check=False)
        if cp.returncode != 0:
            return False, f"{' '.join(engine.command)}: {cp.stdout.strip()}"
        first = cp.stdout.strip().splitlines()
        detail = first[0] if first else "OK"
        return True, f"{engine.kind} ({' '.join(engine.command)}): {detail}"
    except DockerError as exc:
        return False, str(exc)


def image_exists(cfg: GlobalConfig, image):
    try:
        engine = resolve_engine(cfg)
        cp = subprocess.run(
            engine.command + ["image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return cp.returncode == 0
    except (DockerError, FileNotFoundError):
        return False


def container_running(cfg: GlobalConfig, name):
    try:
        engine = resolve_engine(cfg)
        cp = subprocess.run(
            engine.command
            + ["ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return name in cp.stdout.splitlines()
    except (DockerError, FileNotFoundError):
        return False


def build_runtime(paths: Paths, cfg: GlobalConfig, profile_name: str):
    engine = resolve_engine(cfg)
    profile = cfg.runtimes[profile_name]
    if not profile.buildable:
        return "El runtime no necesita construcción."

    package = {
        "8": "openjdk8-jdk",
        "11": "openjdk11-jdk",
        "17": "openjdk17-jdk",
        "21": "openjdk21-jdk",
    }.get(profile.java)

    if not package:
        raise DockerError(f"Java no soportado: {profile.java}")

    build_dir = paths.runtimes_dir / profile_name
    build_dir.mkdir(parents=True, exist_ok=True)

    # Alpine 3.21 community publishes Maven 3.9.9-r0. Maven is a Java package;
    # keep Java, Node and the other OS packages on the base image's branch.
    dockerfile = f"""FROM {cfg.opencode_image}
USER root
RUN apk add --no-cache {package} nodejs npm bash
RUN apk add --no-cache --repository=https://dl-cdn.alpinelinux.org/alpine/v3.21/community maven={MAVEN_VERSION}-r0
RUN mvn -version
ENTRYPOINT ["opencode"]
"""
    (build_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")

    cp = run_capture(
        engine,
        ["build", "-t", profile.image, engine.host_path(build_dir)]
    )
    return cp.stdout


def _mount_file(args, engine, source, target, readonly=True):
    spec = f"type=bind,source={engine.host_path(source)},target={target}"
    if readonly:
        spec += ",readonly"
    args.extend(["--mount", spec])


def _mount_directory(args, engine, source, target, readonly=True):
    spec = f"type=bind,source={engine.host_path(source)},target={target}"
    if readonly:
        spec += ",readonly"
    args.extend(["--mount", spec])


def _mount_volume(args, volume, target):
    args.extend(
        ["--mount", f"type=volume,source={volume},target={target}"]
    )


def validate_build_paths(
    workspace: Path,
    paths: list[str],
    sensitive_paths: list[str] | None = None,
) -> None:
    """Reject build directories outside the working copy or overlapping protection.

    Redirecting a directory to a Linux volume is what lets Maven set POSIX
    permissions on the build output of a Windows working copy. These checks keep
    the mount inside the working copy and away from the protection masks.
    """
    root = workspace.resolve()

    for raw in paths:
        try:
            relative = normalize_build_path(raw)
        except ValueError as exc:
            raise DockerError(f"Directorio de build no válido: {exc}") from exc

        if not relative:
            raise DockerError(
                "Indica un directorio de build relativo a la working copy."
            )

        host = root / Path(relative)
        if host.exists() and not host.is_dir():
            raise DockerError(
                f"El directorio de build no es una carpeta: {relative}"
            )
        if host.exists() and (host / "pom.xml").is_file():
            raise DockerError(
                f"{relative} es la raíz de un módulo Maven, no una carpeta de "
                "salida. Indica su target u otra carpeta de build."
            )
        if not host.exists() and not host.parent.is_dir():
            raise DockerError(
                "No existe el directorio padre del build dentro de la working "
                f"copy: {relative}"
            )

        for sensitive in sensitive_paths or []:
            protected = str(sensitive).replace("\\", "/").strip("/")
            if (
                relative == protected
                or relative.startswith(f"{protected}/")
                or protected.startswith(f"{relative}/")
            ):
                raise DockerError(
                    f"El directorio de build {relative} se solapa con la "
                    f"protección {protected}. Elige un directorio fuera de ella."
                )


def maven_repository_path(value: str) -> Path | None:
    """Allow only an existing Maven artifact directory, never its parent."""
    if not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    if not path.is_absolute() or not path.is_dir() or path.name.lower() != "repository":
        raise DockerError("Indica una carpeta repository de Maven existente y absoluta.")
    resolved = path.resolve()
    if resolved.name.lower() != "repository" or "," in str(resolved) or "\n" in str(resolved):
        raise DockerError("La ruta del repositorio Maven no es válida para el montaje.")
    return resolved


def _relative(value):
    value = value.replace("\\", "/").lstrip("/")
    parts = [p for p in value.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise DockerError(f"Ruta sensible no válida: {value}")
    return "/".join(parts)


def _maven_module_roots(workspace: Path) -> list[Path]:
    """Follow only explicit Maven <module> declarations; no repo-wide scan."""
    roots: list[Path] = [workspace]
    seen: set[Path] = {workspace.resolve()}
    queue: list[Path] = [workspace]

    while queue:
        current = queue.pop(0)
        pom = current / "pom.xml"
        if not pom.is_file():
            continue

        try:
            tree = ET.parse(pom)
            root = tree.getroot()
        except (ET.ParseError, OSError):
            continue

        for elem in root.iter():
            if elem.tag.rsplit("}", 1)[-1] != "module" or not elem.text:
                continue

            candidate = (current / elem.text.strip()).resolve()
            try:
                candidate.relative_to(workspace.resolve())
            except ValueError:
                continue

            if candidate.is_dir() and candidate not in seen:
                seen.add(candidate)
                roots.append(candidate)
                queue.append(candidate)

    return roots


def _candidate_roots(workspace: Path) -> list[Path]:
    """
    Candidate module roots without a recursive repository sweep.

    Includes:
    - the working-copy root;
    - Maven modules declared in pom.xml;
    - immediate child directories (e.g. wseganadero, decovi).
    """
    result: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        try:
            resolved = path.resolve()
            resolved.relative_to(workspace.resolve())
        except (OSError, ValueError):
            return

        if not resolved.is_dir() or resolved in seen:
            return

        seen.add(resolved)
        result.append(resolved)

    add(workspace)

    for module in _maven_module_roots(workspace):
        add(module)

    try:
        for child in workspace.iterdir():
            if child.is_dir():
                add(child)
    except OSError:
        pass

    return result


def _resolve_sensitive_paths(
    workspace: Path,
    configured: list[str],
) -> tuple[list[tuple[str, Path]], list[str]]:
    """
    Resolve exact paths and **/suffix rules.

    Returns (resolved, unresolved_required_rules).
    """
    result: list[tuple[str, Path]] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    candidates = _candidate_roots(workspace)

    for raw in configured:
        value = _relative(raw)
        suffix_mode = value.startswith("**/")
        suffix = value[3:] if suffix_mode else value

        matches = 0
        bases = candidates if suffix_mode else [workspace]

        for base in bases:
            host_path = base.joinpath(*suffix.split("/"))
            if not host_path.exists():
                continue

            try:
                relative = host_path.resolve().relative_to(workspace.resolve()).as_posix()
            except ValueError:
                continue

            matches += 1
            if relative in seen:
                continue

            seen.add(relative)
            result.append((relative, host_path))

        # Built-in suffix rules are security expectations. If they cannot be
        # resolved at all, stop instead of silently launching unprotected.
        if suffix_mode and matches == 0:
            unresolved.append(raw)

    return result, unresolved

def build_run_command(
    *,
    paths: Paths,
    global_cfg: GlobalConfig,
    project: ProjectConfig,
    mode: str,
    detached: bool,
):
    if mode not in ("tui", "serve"):
        raise DockerError(f"Modo desconocido: {mode}")
    engine = resolve_engine(global_cfg)
    workspace = Path(project.workspace)
    if not workspace.is_dir():
        raise DockerError(f"No existe la working copy: {workspace}")

    profile = global_cfg.runtimes.get(project.runtime)
    if not profile:
        raise DockerError(f"Runtime desconocido: {project.runtime}")

    args = engine.command + ["run"]

    # The server owns the container lifecycle; Web and TUI are just clients.
    args.append("-d")
    args.extend(["--name", project.container_name])
    args.extend(["--label", "io.ocode.mode=shared"])
    args.extend(["--workdir", "/workspace"])

    if global_cfg.security_no_new_privileges:
        args.extend(["--security-opt", "no-new-privileges:true"])
    if global_cfg.cap_drop_all:
        args.extend(["--cap-drop", "ALL"])

    args.extend(
        [
            "--mount",
            f"type=bind,source={engine.host_path(workspace)},target=/workspace",
        ]
    )

    _mount_file(
        args, engine, paths.opencode_config,
        "/root/.config/opencode/opencode.jsonc",
    )
    _mount_file(
        args, engine, paths.global_agents,
        "/etc/ocode/global-AGENTS.md",
    )
    paths.global_skills.mkdir(parents=True, exist_ok=True)
    project_skills = paths.project_skills(project.project_id)
    project_skills.mkdir(parents=True, exist_ok=True)
    _mount_directory(args, engine, paths.global_skills,
                     "/root/.config/opencode/skills", readonly=True)
    _mount_directory(args, engine, project_skills,
                     "/etc/ocode/project-skills", readonly=False)
    args.extend(["-e", "OCODE_GLOBAL_AGENTS=/etc/ocode/global-AGENTS.md"])

    project_agents = paths.project_agents(project.project_id)
    if project.project_agents_enabled and project_agents.exists():
        _mount_file(args, engine, project_agents, "/etc/ocode/project-AGENTS.md",
                    readonly=False)
        args.extend(["-e", "OCODE_PROJECT_AGENTS=/etc/ocode/project-AGENTS.md"])

    project_config = paths.project_config(project.project_id)
    if project.project_config_enabled and project_config.exists():
        # V2 discovers config in ancestors, including above the repository root.
        # Keep ocode's config outside the working copy and do not shadow its files.
        _mount_file(args, engine, project_config, "/opencode.jsonc")

    _mount_volume(args, project.data_volume, "/root/.local/share/opencode")
    _mount_volume(args, project.state_volume, "/root/.local/state/opencode")

    repository = maven_repository_path(project.maven_repository)
    if repository is not None:
        _mount_file(args, engine, repository, "/root/.m2/repository")

    resolved_sensitive, unresolved_sensitive = _resolve_sensitive_paths(
        workspace, project.sensitive_paths
    )

    # Use a known-empty host directory to shadow sensitive directories.
    # Binding /dev/null (a file) over a directory is not portable and can fail
    # with "not a directory" errors on Docker/Podman.
    empty_dir = paths.sensitive_empty_dir
    empty_dir.mkdir(parents=True, exist_ok=True)
    try:
        if any(empty_dir.iterdir()):
            raise DockerError(
                f"El directorio de máscara sensible no está vacío: {empty_dir}"
            )
    except OSError as exc:
        raise DockerError(
            f"No se puede validar el directorio de máscara sensible: {empty_dir}"
        ) from exc

    for relative, host_path in resolved_sensitive:
        target = f"/workspace/{relative}"
        if host_path.is_dir():
            args.extend([
                "--mount",
                "type=bind,"
                f"source={engine.host_path(empty_dir)},"
                f"target={target},readonly",
            ])
        elif host_path.is_file():
            args.extend(["--volume", f"/dev/null:{target}:ro"])

    # Build output can leave the Windows mount: the volume is a Linux
    # filesystem, so plugins that copy POSIX permissions onto the output work.
    build_volumes = project.build_volumes
    if build_volumes:
        validate_build_paths(
            workspace,
            [relative for relative, _ in build_volumes],
            [relative for relative, _ in resolved_sensitive],
        )
        for relative, volume in build_volumes:
            _mount_volume(args, volume, f"/workspace/{relative}")

    web_token = None
    server_password = None
    if mode in ("tui", "serve"):
        proxy_script = paths.web_proxy_script
        if not proxy_script.is_file():
            raise DockerError(
                f"No existe el proxy Web requerido: {proxy_script}"
            )
        if not paths.instructions_script.is_file():
            raise DockerError(
                f"No existe el sincronizador de instrucciones: {paths.instructions_script}"
            )
        _mount_file(
            args, engine, paths.instructions_script,
            "/usr/local/lib/ocode-instructions.mjs",
        )

        web_token = secrets.token_urlsafe(32)
        server_password = secrets.token_urlsafe(32)
        backend_port = 4097 if project.port != 4097 else 4098

        args.extend(
            [
                "-p",
                f"{global_cfg.bind_address}:{project.port}:{project.port}",
                "-e",
                f"OCODE_PUBLIC_PORT={project.port}",
                "-e",
                f"OCODE_BACKEND_PORT={backend_port}",
                "-e",
                "OCODE_DIRECTORY=/workspace",
                "-e",
                f"OCODE_WEB_TOKEN={web_token}",
                "-e",
                f"OPENCODE_SERVER_PASSWORD={server_password}",
                "-e",
                "OPENCODE_SERVER_USERNAME=opencode",
                "--mount",
                "type=bind,"
                f"source={engine.host_path(proxy_script)},"
                "target=/usr/local/lib/ocode-web-proxy.mjs,readonly",
                "--entrypoint",
                "node",
            ]
        )

    args.append(profile.image)

    args.append("/usr/local/lib/ocode-web-proxy.mjs")

    return CommandPreview(
        args,
        web_token=web_token,
        server_password=server_password,
        foreground_args=(
            _tui_client_command(engine, project.container_name)
            if mode == "tui" else None
        ),
    )



def sensitive_applied_paths(project: ProjectConfig) -> list[str]:
    workspace = Path(project.workspace)
    resolved, _ = _resolve_sensitive_paths(
        workspace, project.sensitive_paths
    )
    return [relative for relative, _ in resolved]



def sensitive_mount_report(
    paths: Paths,
    cfg: GlobalConfig,
    project: ProjectConfig,
) -> str:
    workspace = Path(project.workspace)
    resolved, _ = _resolve_sensitive_paths(
        workspace, project.sensitive_paths
    )

    engine = resolve_engine(cfg)
    empty_dir = paths.sensitive_empty_dir

    lines = [
        f"Working copy: {workspace}",
        "",
        f"Sensitive paths bloqueados: {len(resolved)}",
    ]

    if not resolved:
        lines.extend([
            "",
            "No se ha encontrado ninguna ruta configurada para bloquear "
            "en esta working copy.",
        ])
    else:
        for relative, host_path in resolved:
            target = f"/workspace/{relative}"
            lines.append("")
            if host_path.is_dir():
                lines.extend([
                    f"  DIRECTORIO  {relative}",
                    f"  destino     {target}",
                    "  protección  directorio vacío · solo lectura",
                ])
            else:
                lines.extend([
                    f"  FICHERO     {relative}",
                    f"  destino     {target}",
                    "  protección  /dev/null · solo lectura",
                ])

        lines.extend([
            "",
            f"Máscara de directorios: {engine.host_path(empty_dir)}",
        ])

    return "\n".join(lines)


def _tui_client_command(engine: Engine, name: str) -> list[str]:
    # Resolve the port and authentication inside the container. Passing a shell
    # expansion through Docker on Windows can turn a set port into its fallback.
    # V2 clients use OPENCODE_PASSWORD; do not pass it through host arguments.
    script = '''
const { spawn } = require("node:child_process");
const port = Number(process.env.OCODE_BACKEND_PORT || 4097);
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  console.error("[ocode] Puerto interno de OpenCode inválido.");
  process.exit(1);
}
const url = "http://127.0.0.1:" + port;
console.log("[ocode] TUI: servidor=" + url);
const client = spawn("opencode", ["--server", url, "/workspace"], {
  stdio: "inherit",
  env: { ...process.env, OPENCODE_PASSWORD: process.env.OPENCODE_SERVER_PASSWORD },
});
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => client.kill(signal));
}
client.on("error", (error) => {
  console.error("[ocode] No se pudo iniciar la TUI: " + error.message);
  process.exitCode = 1;
});
client.on("close", (code, signal) => {
  process.exitCode = code ?? (signal === "SIGINT" ? 130 : 1);
});
'''
    return engine.command + ["exec", "-it", name, "node", "-e", script]


def launch_foreground(preview):
    try:
        if preview.foreground_args is not None:
            if preview.args:
                launch_detached(preview)
            wait_for_backend(preview.foreground_args)
            return subprocess.call(preview.foreground_args)
        return subprocess.call(preview.args)
    except FileNotFoundError as exc:
        raise DockerError("No se encuentra el motor de contenedores.") from exc


def wait_for_backend(client_args: list[str]) -> None:
    """Wait for the shared server without exposing its authentication."""
    script = '''
const http = require("node:http");
const deadline = Date.now() + 15000;
function probe() {
  const req = http.get({
    hostname: "127.0.0.1", port: Number(process.env.OCODE_BACKEND_PORT || 4097),
    path: "/api/info",
    auth: "opencode:" + process.env.OPENCODE_SERVER_PASSWORD,
  }, res => {
    res.resume();
    if (res.statusCode === 200) process.exit(0);
    retry();
  });
  req.on("error", retry);
  req.setTimeout(1000, () => req.destroy());
}
function retry() {
  if (Date.now() >= deadline) process.exit(1);
  setTimeout(probe, 250);
}
probe();
'''
    # Preserve the engine prefix (Docker, Podman, WSL or a custom command).
    index = len(client_args) - 6
    args = client_args[:index] + ["exec", client_args[index + 2], "node", "-e", script]
    try:
        cp = subprocess.run(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise DockerError("El servidor no está listo. El contenedor se conserva.") from exc
    if cp.returncode != 0:
        raise DockerError("El servidor no está listo. El contenedor se conserva.")


def launch_detached(preview):
    try:
        cp = subprocess.run(
            preview.args,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return cp.stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise DockerError(exc.stdout or str(exc)) from exc


def container_status(cfg: GlobalConfig, name: str) -> str:
    """Read container state without treating engine failures as absence."""
    engine = resolve_engine(cfg)
    try:
        cp = subprocess.run(
            engine.command
            + ["ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.State}}"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DockerError("No se pudo consultar el estado de la instancia.") from exc
    return cp.stdout.strip() or "absent"


def container_published_ports(cfg: GlobalConfig, name: str) -> set[int]:
    """Read only port bindings, including those retained by stopped containers."""
    engine = resolve_engine(cfg)
    cp = run_capture(engine, [
        "inspect", name, "--format", "{{json .HostConfig.PortBindings}}",
    ])
    try:
        bindings = json.loads(cp.stdout) or {}
        return {
            int(binding["HostPort"])
            for entries in bindings.values()
            for binding in (entries or [])
            if binding.get("HostPort")
        }
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise DockerError("No se pudieron consultar los puertos de la instancia.") from exc


def container_exists(cfg: GlobalConfig, name: str) -> bool:
    engine = resolve_engine(cfg)
    cp = subprocess.run(
        engine.command
        + ["ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return name in cp.stdout.splitlines()


def remove_container(cfg: GlobalConfig, name: str) -> None:
    engine = resolve_engine(cfg)
    run_capture(engine, ["rm", name])


def recreate_container(*, paths: Paths, global_cfg: GlobalConfig, project: ProjectConfig) -> None:
    """Replace only the container, retaining project files and named volumes."""
    # Prepare the command and check requirements before touching the old instance.
    preview = build_run_command(
        paths=paths, global_cfg=global_cfg, project=project,
        mode="serve", detached=True,
    )
    required_files = [paths.opencode_config, paths.global_agents]
    if project.project_agents_enabled:
        required_files.append(paths.project_agents(project.project_id))
    if project.project_config_enabled:
        required_files.append(paths.project_config(project.project_id))
    for source in required_files:
        if not source.is_file():
            raise DockerError(f"Falta un fichero de configuración requerido: {source}")
    profile = global_cfg.runtimes[project.runtime]
    if not image_exists(global_cfg, profile.image):
        raise DockerError(
            f"No está disponible la imagen {profile.image}. "
            "Prepara el runtime antes de recrear el contenedor. "
            "La instancia actual no se ha modificado."
        )

    status = container_status(global_cfg, project.container_name)
    if status not in ("absent", "running", "restarting", "exited", "created", "dead"):
        raise DockerError(
            f"No se puede recrear un contenedor en estado '{status}'. "
            "La instancia actual no se ha modificado."
        )
    if status in ("running", "restarting"):
        stop_container(global_cfg, project.container_name)
    if status != "absent":
        # No --volumes or --force: removal must succeed normally, and the same
        # named data/state volumes are reused by the prepared run command.
        remove_container(global_cfg, project.container_name)
    try:
        launch_detached(preview)
        wait_for_backend(_tui_client_command(resolve_engine(global_cfg), project.container_name))
    except Exception as exc:
        raise DockerError(
            "No se pudo completar la recreación. Se conservan la configuración "
            "y los volúmenes de sesiones y datos. Si se creó un contenedor, "
            "se conserva para diagnóstico. Revisa el error y vuelve a intentarlo.\n\n"
            f"{exc}"
        ) from exc


def build_launch_command(*, paths, global_cfg, project, mode, detached):
    """Reuse an existing container without changing its configuration."""
    name = project.container_name
    if not container_exists(global_cfg, name):
        return build_run_command(
            paths=paths, global_cfg=global_cfg, project=project,
            mode=mode, detached=detached,
        )

    engine = resolve_engine(global_cfg)
    # Inspect only non-sensitive metadata, never the container environment.
    cp = run_capture(engine, [
        "inspect", name, "--format",
        '{{index .Config.Labels "io.ocode.mode"}}|'
        '{{.HostConfig.AutoRemove}}|{{json .Config.Cmd}}',
    ])
    existing_mode, auto_remove, command = cp.stdout.strip().split("|", 2)
    if existing_mode not in ("tui", "serve", "shared"):
        existing_mode = "serve" if "ocode-web-proxy.mjs" in command else "tui"
    if auto_remove == "true":
        raise DockerError(
            "El contenedor anterior usa --rm y no puede reutilizarse como "
            "persistente. Detén esa instancia antigua antes de iniciar otra vez."
        )
    if existing_mode == "tui" and mode == "serve":
        raise DockerError(
            "Este contenedor TUI antiguo no tiene el servidor Web ni el puerto "
            "publicado. Los nuevos contenedores admiten ambas interfaces. "
            "No se ha eliminado ni modificado el contenedor antiguo."
        )

    running = container_running(global_cfg, name)
    if existing_mode == "serve" and not running:
        raise DockerError(
            "Este contenedor Web antiguo no tiene garantizada la configuración "
            "de autenticación que necesita el proxy actual. Reiniciarlo no "
            "actualiza su configuración.\n\n"
            "Abre Ajustes de esta instancia y pulsa Guardar y recrear contenedor, "
            "confirmando el aviso. Se reutilizarán los volúmenes de sesiones "
            "y datos; no uses Eliminar instancia.\n\n"
            "No se ha iniciado este contenedor ni detenido ninguna otra instancia."
        )
    if existing_mode == "tui":
        args = ["attach", name] if running else ["start", "-ai", name]
    else:
        args = [] if running else ["start", name]
    return CommandPreview(
        engine.command + args if args else [], reused=True,
        foreground_args=(
            _tui_client_command(engine, name)
            if mode == "tui" and existing_mode != "tui" else None
        ),
    )


def prepare_web_login(cfg: GlobalConfig, name: str, preview: CommandPreview):
    """Register a fresh login through a container-local Unix socket.

    The handle travels over stdin, not command arguments or captured output.
    No existing authentication material is read from Docker.
    """
    engine = resolve_engine(cfg)
    handle = secrets.token_urlsafe(32)
    script = '''
const http = require("node:http");
let body = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", chunk => body += chunk);
process.stdin.on("end", () => {
  const req = http.request({
    socketPath: "/tmp/ocode-web-control.sock",
    path: "/login", method: "POST",
  }, res => {
    res.resume();
    res.on("end", () => process.exit(res.statusCode === 204 ? 0 : 1));
  });
  req.on("error", () => process.exit(1));
  req.setTimeout(1500, () => { req.destroy(); process.exit(1); });
  req.end(body);
});
'''
    for attempt in range(20):
        try:
            cp = subprocess.run(
                engine.command + ["exec", "-i", name, "node", "-e", script],
                input=handle, text=True, encoding="utf-8",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=3,
            )
        except subprocess.TimeoutExpired:
            cp = None
        if cp is not None and cp.returncode == 0:
            preview.web_token = handle
            return
        if attempt < 19:
            time.sleep(0.5)
    raise DockerError(
        "No se pudo preparar el acceso Web. El contenedor se conserva. "
        "Comprueba que el proxy haya arrancado; los contenedores antiguos "
        "pueden necesitar detenerse e iniciarse para cargar el proxy actualizado."
    )


def container_exit_report(
    cfg: GlobalConfig,
    name: str,
    tail: int = 120,
) -> str:
    """Return exit status and recent logs for a stopped container."""
    engine = resolve_engine(cfg)

    inspect_cp = subprocess.run(
        engine.command
        + [
            "inspect",
            name,
            "--format",
            "status={{.State.Status}} exit={{.State.ExitCode}} "
            "oom={{.State.OOMKilled}} started={{.State.StartedAt}} "
            "finished={{.State.FinishedAt}} "
            "error={{.State.Error}}",
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    logs_cp = subprocess.run(
        engine.command + ["logs", "--tail", str(tail), name],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    status = inspect_cp.stdout.strip() or "estado no disponible"
    logs = logs_cp.stdout.strip() or "(sin salida en logs)"

    return f"{status}\\n\\nLogs:\\n{logs}"


def stop_container(cfg: GlobalConfig, name):
    engine = resolve_engine(cfg)
    cp = run_capture(engine, ["stop", name])
    return cp.stdout.strip()


def container_logs(cfg: GlobalConfig, name, tail=200):
    if not container_exists(cfg, name):
        return "La instancia todavía no tiene contenedor."
    engine = resolve_engine(cfg)
    return run_capture(
        engine, ["logs", "--tail", str(tail), name], check=False
    ).stdout


def inspect_volumes(cfg: GlobalConfig, project: ProjectConfig) -> str:
    engine = resolve_engine(cfg)
    rows = []
    volumes = [
        project.data_volume,
        project.state_volume,
        *[name for _, name in project.build_volumes],
    ]
    for volume in volumes:
        cp = subprocess.run(
            engine.command + ["volume", "inspect", volume, "--format", "{{.Name}}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        state = "existe" if cp.returncode == 0 else "se creará al lanzar"
        rows.append(f"{volume}: {state}")
    return "\n".join(rows)


def remove_state(cfg: GlobalConfig, project: ProjectConfig) -> str:
    if container_running(cfg, project.container_name):
        raise DockerError("Detén la instancia antes de eliminar su estado.")

    engine = resolve_engine(cfg)
    outputs = []

    for volume in (
        project.data_volume,
        project.state_volume,
        *[name for _, name in project.build_volumes],
    ):
        cp = subprocess.run(
            engine.command + ["volume", "rm", volume],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        output = cp.stdout.strip()

        if cp.returncode == 0:
            outputs.append(output or f"{volume}: eliminado")
            continue

        # Missing volumes are already in the desired final state.
        lowered = output.lower()
        if (
            "no such volume" in lowered
            or "not found" in lowered
            or "does not exist" in lowered
        ):
            outputs.append(f"{volume}: no existía")
            continue

        raise DockerError(
            f"No se pudo eliminar el volumen {volume}:\n{output}"
        )

    return "\n".join(outputs)
