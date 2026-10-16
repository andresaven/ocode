from pathlib import Path
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
