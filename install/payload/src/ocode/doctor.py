from pathlib import Path
from . import providers
from .docker import docker_available, image_exists


def doctor(paths, cfg, projects):
    lines = []

    ok, detail = docker_available(cfg)
    lines.append(("✓" if ok else "✗") + f" Runtime contenedores: {detail}")

    lines.append(
        ("✓" if paths.global_agents.exists() else "✗")
        + f" AGENTS: {paths.global_agents}"
    )
    lines.append(
        ("✓" if paths.opencode_config.exists() else "✗")
        + f" OpenCode config: {paths.opencode_config}"
    )
    if providers.go_key_configured(paths):
        last4 = providers.go_key_last4(paths)
        suffix = f" (…{last4})" if last4 else ""
        lines.append(f"✓ Clave OpenCode Go: configurada{suffix} · fichero legible")
    elif providers.go_env_file(paths).exists():
        lines.append("✗ Clave OpenCode Go: fichero ilegible o no válido")
    else:
        lines.append("• Clave OpenCode Go: no configurada")

    for name, runtime in cfg.runtimes.items():
        exists = image_exists(cfg, runtime.image)
        lines.append(
            f"{'✓' if exists else '•'} Runtime {name}: {runtime.image}"
        )

    for p in projects:
        exists = Path(p.workspace).is_dir()
        lines.append(
            ("✓" if exists else "✗")
            + f" {p.name}/{p.branch}: {p.workspace}"
        )

    return "\n".join(lines)
