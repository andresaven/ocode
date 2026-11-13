from pathlib import Path
from . import providers
from .docker import (
    container_opencode_version,
    container_status,
    docker_available,
    image_exists,
)
from .store import provider_env_snippet, provider_env_status, project_overrides_providers
from .versions import VersionListError, list_remote_versions, npm_version_available


def doctor(paths, cfg, projects):
    lines = []

    ok, detail = docker_available(cfg)
    lines.append(("✓" if ok else "✗") + f" Runtime contenedores: {detail}")

    lines.append(
        f"• OpenCode de reserva: {cfg.opencode_version} ({cfg.opencode_image})"
    )

    try:
        versions = list_remote_versions()
    except VersionListError as exc:
        lines.append(
            f"• Última OpenCode publicada: no se pudo consultar ({exc})"
        )
    else:
        if versions:
            lines.append(f"• Última OpenCode publicada: {versions[0]}")

    versions_to_check = {cfg.opencode_version}
    versions_to_check.update(
        project.opencode_version or cfg.opencode_version
        for project in projects
    )
    for version in sorted(versions_to_check):
        try:
            available = npm_version_available(version)
        except (VersionListError, ValueError) as exc:
            lines.append(
                f"• OpenCode {version} en npm: no se pudo comprobar ({exc})"
            )
        else:
            lines.append(
                ("✓" if available else "✗")
                + f" OpenCode {version} en npm: "
                + ("disponible" if available else "no disponible")
            )

    lines.append(
        ("✓" if paths.global_agents.exists() else "✗")
        + f" AGENTS: {paths.global_agents}"
    )
    lines.append(
        ("✓" if paths.opencode_config.exists() else "✗")
        + f" OpenCode config: {paths.opencode_config}"
    )

    separated, separation_detail = provider_env_status(paths.opencode_config)
    if separated:
        lines.append(
            "✓ Separación Console/Go: OpenCode Go usa su propia variable"
        )
    else:
        lines.append(f"✗ Separación Console/Go: {separation_detail}")
        lines.append("  Añade a config\\opencode.jsonc:")
        lines.extend(f"  {line}" for line in provider_env_snippet().splitlines())

    for provider in providers.PROVIDERS.values():
        if providers.key_configured(paths, provider.id):
            last4 = providers.key_last4(paths, provider.id)
            suffix = f" (…{last4})" if last4 else ""
            lines.append(
                f"✓ Clave {provider.name}: configurada{suffix} · fichero legible"
            )
        elif providers.env_file(paths, provider.id).exists():
            lines.append(f"✗ Clave {provider.name}: fichero ilegible o no válido")
        else:
            suffix = " (modo gratuito sin clave)" if provider.id == "opencode" else ""
            lines.append(f"• Clave {provider.name}: no configurada{suffix}")

    for name, runtime in cfg.runtimes.items():
        exists = image_exists(cfg, runtime.image)
        lines.append(
            f"{'✓' if exists else '•'} Runtime {name}: {runtime.image}"
        )

    for p in projects:
        exists = Path(p.workspace).is_dir()
        version = p.opencode_version or cfg.opencode_version
        status = ""
        installed = ""
        try:
            status = container_status(cfg, p.container_name)
        except Exception:
            status = ""
        if status and status != "absent":
            try:
                installed = container_opencode_version(cfg, p.container_name)
            except Exception:
                installed = ""
        state = {
            "running": "en ejecución",
            "exited": "detenida",
            "created": "detenida",
            "absent": "sin iniciar",
            "paused": "en pausa",
            "restarting": "reiniciándose",
            "dead": "con error",
            "removing": "eliminándose",
        }.get(status, "estado desconocido")
        detail = f" · OpenCode configurado {version}"
        if installed:
            detail += f" · instalado {installed}"
            if installed != version:
                detail += " ⚠ (se sincronizará al abrir la instancia)"
        if status:
            detail += f" · {state}"
        lines.append(
            ("✓" if exists else "✗")
            + f" {p.name}/{p.branch}: {p.workspace}{detail}"
        )
        project_config = paths.project_config(p.project_id)
        if (
            p.project_config_enabled
            and project_config.exists()
            and project_overrides_providers(project_config)
        ):
            lines.append(
                f"• {p.name}/{p.branch}: su opencode.jsonc declara providers y "
                "puede anular la separación Console/Go"
            )

    return "\n".join(lines)
