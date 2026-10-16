"""Describe storage mappings without reading files or container configuration."""

import json
from pathlib import PureWindowsPath
import subprocess

from .docker import DockerError, resolve_engine
from .model import GlobalConfig, ProjectConfig
from .paths import Paths


def host_mount_path(source: str) -> str:
    """Translate known Windows/WSL bind paths, keeping other engine paths intact."""
    for prefix in ("/run/desktop/mnt/host/", "/host_mnt/", "/mnt/"):
        if source.startswith(prefix):
            tail = source[len(prefix):]
            drive, separator, rest = tail.partition("/")
            if separator and len(drive) == 1 and drive.isalpha():
                return str(PureWindowsPath(f"{drive.upper()}:/") / rest)
    return source


def storage_report(paths: Paths, cfg: GlobalConfig, project: ProjectConfig) -> str:
    lines = [
        f"Proyecto: {project.name} / {project.branch}",
        f"Contenedor: {project.container_name}",
        "",
        "MONTAJES REALES DE LA INSTANCIA",
        "Se omite /workspace (código del proyecto). No se leen contenidos.",
        "Los montajes se conservan al reiniciar; pueden diferir de la configuración actual.",
        "",
    ]
    try:
        engine = resolve_engine(cfg)
        result = subprocess.run(
            engine.command + ["inspect", "--type", "container", "--format",
                              "{{json .Mounts}}", project.container_name],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        if result.returncode:
            raise DockerError("No se pudo consultar la instancia. Comprueba que existe y que el motor está disponible.")
        mounts = json.loads(result.stdout)
        descriptions = {
            "/root/.config/opencode/opencode.jsonc": "opencode.jsonc general",
            "/etc/ocode/global-AGENTS.md": "AGENTS.md general",
            "/etc/ocode/project-AGENTS.md": "AGENTS.md del proyecto",
            "/workspace/AGENTS.md": "AGENTS.md del proyecto (montaje antiguo)",
            "/opencode.jsonc": "opencode.jsonc del proyecto",
            "/root/.local/share/opencode": "Datos persistentes de OpenCode",
            "/root/.local/state/opencode": "Estado persistente de OpenCode",
            "/usr/local/lib/ocode-instructions.mjs": "Sincronizador de instrucciones",
            "/usr/local/lib/ocode-web-proxy.mjs": "Proxy Web",
        }
        shown = 0
        for mount in sorted(mounts, key=lambda item: item.get("Destination", "")):
            destination = mount.get("Destination", "")
            if destination == "/workspace":
                continue
            shown += 1
            kind = mount.get("Type", "desconocido")
            source = mount.get("Source", "")
            permissions = "lectura y escritura" if mount.get("RW") else "solo lectura"
            if destination in descriptions:
                lines.append(descriptions[destination])
            lines.append(f"Contenedor: {destination}")
            lines.append(f"  Tipo: {kind} · {permissions}")
            if kind == "volume":
                volume_name = mount.get("Name", "")
                lines.append(f"  Volumen Docker: {volume_name or '(sin nombre)'}")
                lines.append(f"  Ruta en el host del motor: {source or '(no informada)'}")
                lines.append("  En Docker Desktop/WSL esta ruta vive en Linux, no en una carpeta de Windows.")
                if volume_name.startswith(f"ocode-{project.instance_key}-build-"):
                    lines.append(
                        "  Directorio de build redirigido: el compilador escribe aquí, "
                        "no en la carpeta de Windows correspondiente."
                    )
            elif kind == "bind":
                lines.append(f"  Origen host: {host_mount_path(source) or '(no informado)'}")
                if host_mount_path(source) != source:
                    lines.append(f"  Ruta vista por el motor: {source}")
                if destination.startswith("/workspace/") and (
                    source == "/dev/null" or "_sensitive_empty" in source.replace("\\", "/").split("/")
                ):
                    lines.append("  Máscara de protección: no es el fichero original del proyecto.")
            else:
                lines.append(f"  Origen en el motor: {source or '(sin fichero de origen)'}")
            lines.append("")
        if not shown:
            lines.extend(["No hay montajes adicionales a /workspace.", ""])
    except (DockerError, OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
        lines.extend([f"Montajes no disponibles: {exc}",
                      "No se muestran montajes previstos como si fueran reales.", ""])

    lines.extend([
        "ARCHIVOS GESTIONADOS EN EL HOST (CONFIGURACIÓN ACTUAL)",
        "Estas son las rutas de ocode; arriba se indica qué está montado realmente.",
        f"AGENTS.md general: {paths.global_agents}",
        f"opencode.jsonc general: {paths.opencode_config}",
        f"AGENTS.md del proyecto: {paths.project_agents(project.project_id)}",
        f"opencode.jsonc del proyecto: {paths.project_config(project.project_id)}",
        f"sensitive-paths.txt: {paths.project_sensitive_paths(project.project_id)}",
        "  No se monta: ocode lo utiliza para preparar las máscaras de protección.",
        f"Configuración de ocode: {paths.global_config}",
        f"Configuración del proyecto: {paths.project_json(project.project_id)}",
        "  Los dos ficheros de configuración anteriores tampoco se montan.",
        "",
        "ALMACENAMIENTO INTERNO",
        "Los ficheros ajenos a los montajes pertenecen a la imagen/capa del contenedor.",
        "No tienen una correspondencia individual editable con ficheros del host.",
        "Los volúmenes de datos, estado y compilación son persistentes; esta vista no los modifica.",
    ])
    return "\n".join(lines)
