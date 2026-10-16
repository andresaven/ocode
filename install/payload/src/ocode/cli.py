from __future__ import annotations

import argparse
import sys

from . import __version__
from .docker import (
    DockerError,
    build_launch_command,
    build_runtime,
    launch_foreground,
    sensitive_applied_paths,
)
from .doctor import doctor
from .paths import Paths
from .store import Store
from .tui import LaunchRequest, OcodeApp


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocode",
        description="Gestiona instancias Docker de OpenCode por working copy.",
    )
    parser.add_argument("--version", action="version", version=f"ocode {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="Comprueba Docker, configuración y proyectos.")

    runtime = sub.add_parser("runtime", help="Gestiona imágenes runtime.")
    runtime_sub = runtime.add_subparsers(dest="runtime_command")
    build = runtime_sub.add_parser("build", help="Construye un runtime.")
    build.add_argument("profile", help="Ej.: java17, java21")

    return parser


def run_tui(store: Store, paths: Paths) -> int:
    while True:
        app = OcodeApp(store, paths)
        result = app.run()

        if not isinstance(result, LaunchRequest):
            return 0

        project = store.get_project(result.project_id)
        cfg = store.load_global()

        try:
            preview = build_launch_command(
                paths=paths,
                global_cfg=cfg,
                project=project,
                mode=result.mode,
                detached=False,
            )

            protected = sensitive_applied_paths(project)
            if protected:
                print(
                    f"[ocode] Sensitive paths bloqueados: {len(protected)}"
                )
                for path in protected:
                    print(f"[ocode]   - {path}")
            else:
                print("[ocode] Sensitive paths bloqueados: 0")

            print(
                "[ocode] El contenedor se conserva al salir. "
                + ("Cerrar la TUI no detiene el servidor ni la Web."
                   if preview.foreground_args is not None else
                   "Para desconectar sin detener: Ctrl+P, después Ctrl+Q.")
            )
            if preview.foreground_args is not None:
                print(f"[ocode] TUI: contenedor={preview.foreground_args[-4]}", flush=True)
            rc = launch_foreground(preview)
        except DockerError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            input("Pulsa Enter para volver a ocode...")
            continue

        # Al cerrar OpenCode volvemos automáticamente a la TUI.
        if rc != 0:
            print(f"OpenCode terminó con código {rc}.")
            input("Pulsa Enter para volver a ocode...")


def main() -> int:
    parser = _parser()
    args = parser.parse_args()

    paths = Paths()
    store = Store(paths)
    cfg = store.initialize()

    if args.command == "doctor":
        print(doctor(paths, cfg, store.list_projects()))
        return 0

    if args.command == "runtime":
        if args.runtime_command == "build":
            if args.profile not in cfg.runtimes:
                parser.error(f"Runtime desconocido: {args.profile}")
            try:
                out = build_runtime(paths, cfg, args.profile)
                print(out)
                return 0
            except DockerError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
        parser.error("Indica una operación de runtime.")

    return run_tui(store, paths)


if __name__ == "__main__":
    raise SystemExit(main())
