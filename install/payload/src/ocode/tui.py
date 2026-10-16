from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, Iterable
import os
import shlex
import webbrowser

from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.command import CommandPalette
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    LoadingIndicator,
    Select,
    Static,
    Switch,
)

from . import __version__
from .docker import (
    DockerError,
    build_launch_command,
    build_runtime,
    container_exit_report,
    container_exists,
    container_logs,
    container_published_ports,
    container_running,
    container_status,
    docker_available,
    image_exists,
    inspect_volumes,
    launch_detached,
    maven_repository_path,
    prepare_web_login,
    recreate_container,
    remove_container,
    remove_state,
    sensitive_applied_paths,
    sensitive_mount_report,
    stop_container,
    validate_build_paths,
)
from .doctor import doctor
from .storage import storage_report
from .model import ProjectConfig, format_build_paths, parse_build_paths
from .paths import Paths
from .store import Store


@dataclass
class LaunchRequest:
    project_id: str
    mode: str


def infer_project_name(workspace: Path) -> str:
    """Infer the logical project name from common SVN working-copy layouts."""
    leaf = workspace.name
    parent = workspace.parent

    # C:\repositorios\UGV\trunk -> UGV
    # C:\repositorios\UGV\UGV-MUERTEMER -> UGV
    # C:\repositorios\UGV\branches\release-x -> UGV
    if parent.name.lower() in {"branches", "tags"} and parent.parent.name:
        return parent.parent.name

    if parent.name:
        return parent.name

    return leaf


def infer_branch(workspace: Path) -> str:
    """Use the selected working-copy directory as the branch/copy name."""
    return workspace.name

class MessageScreen(ModalScreen[None]):
    CSS = """
    MessageScreen { align: center middle; }
    #box {
        width: 88%;
        height: auto;
        max-height: 85%;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    #message {
        height: auto;
        max-height: 28;
        overflow-y: auto;
    }
    """

    def __init__(self, title: str, message: str):
        super().__init__()
        self.title_text = title
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label(self.title_text, classes="title")
            yield Static(self.message, id="message")
            yield Button("Cerrar", id="close", variant="primary")

    @on(Button.Pressed, "#close")
    def close(self) -> None:
        self.dismiss(None)


class BusyScreen(ModalScreen[None]):
    CSS = """
    BusyScreen { align: center middle; }
    #busy-box {
        width: 48;
        max-width: 92%;
        height: 9;
        border: round $accent;
        padding: 1 2;
        background: $surface;
        align: center middle;
    }
    #busy-box LoadingIndicator { height: 3; }
    #busy-box Static { height: auto; }
    """

    def __init__(self, message: str = "Procesando..."):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="busy-box"):
            yield LoadingIndicator()
            yield Static(self.message)


class ConfirmScreen(ModalScreen[bool]):
    CSS = """
    ConfirmScreen { align: center middle; }
    #confirm {
        width: 85%;
        height: auto;
        max-height: 90%;
        overflow-y: auto;
        border: round $warning;
        padding: 1 2;
        background: $surface;
    }
    #confirm Horizontal { height: 3; }
    #confirm Button { width: auto; }
    """

    def __init__(self, text: str, confirm_label: str = "Confirmar"):
        super().__init__()
        self.text = text
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Static(self.text)
            with Horizontal():
                yield Button("Cancelar", id="no")
                yield Button(self.confirm_label, id="yes", variant="error")

    @on(Button.Pressed, "#yes")
    def yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def no(self) -> None:
        self.dismiss(False)


class DirectoryPickerScreen(ModalScreen[Path | None]):
    CSS = """
    DirectoryPickerScreen { align: center middle; }
    #picker {
        width: 92%;
        height: 90%;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    #tree { height: 1fr; border: round $primary; }
    #selected { height: auto; margin-top: 1; }
    """

    def __init__(self, initial: Path):
        super().__init__()
        self.initial = initial if initial.exists() else Path.home()
        self.selected_path = self.initial

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Label("Seleccionar working copy", classes="title")
            yield Static(
                "Navega con las flechas y Enter, como al completar rutas en terminal."
            )
            yield DirectoryTree(str(self.initial), id="tree")
            yield Static(str(self.selected_path), id="selected")
            with Horizontal():
                yield Button("Usar esta carpeta", id="choose", variant="success")
                yield Button("Cancelar", id="cancel")

    @on(DirectoryTree.DirectorySelected)
    def directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.selected_path = Path(event.path)
        self.query_one("#selected", Static).update(str(self.selected_path))

    @on(Button.Pressed, "#choose")
    def choose(self) -> None:
        self.dismiss(self.selected_path)

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class AddProjectScreen(Screen):
    CSS = """
    AddProjectScreen { padding: 1 3; }
    .field { margin-bottom: 1; }
    #buttons { height: 3; }
    #workspace-actions { height: 3; }
    #browse { width: auto; }
    #add-content { height: 1fr; }
    .hint { color: $text-muted; margin-bottom: 1; }
    """

    def compose(self) -> ComposeResult:
        cfg = self.app.global_cfg
        java_options = [
            (f"Java {profile.java}", name)
            for name, profile in cfg.runtimes.items()
            if profile.java
        ]

        yield Header()
        yield Label("Añadir working copy", classes="title")

        with VerticalScroll(id="add-content"):
            yield Label("Ruta de la working copy")
            yield Input(
                value="",
                placeholder=cfg.repositories_root,
                id="workspace",
                classes="field",
            )
            with Horizontal(id="workspace-actions"):
                yield Button("Navegar...", id="browse", variant="primary")

            yield Label("Nombre del proyecto")
            yield Input(placeholder="UGV", id="name", classes="field")

            yield Label("Rama / working copy")
            yield Input(value="trunk", id="branch", classes="field")

            yield Label("Java")
            yield Static("Maven y npm ya vienen instalados en el runtime.", classes="hint")
            yield Select(java_options, value="java8", id="runtime", classes="field")

            yield Label("Repositorio Maven local (opcional)")
            yield Input(value=cfg.default_maven_repository, id="maven-repository", classes="field")

            yield Label("Directorios de build en volumen Linux (opcional)")
            yield Static(
                "Rutas relativas a la working copy separadas por ';' "
                "(ej.: wseganadero/target). El compilador escribe ahí y no en la "
                "carpeta de Windows. Cada una requiere guardar y recrear la instancia.",
                classes="hint",
            )
            yield Input(value="", id="build-paths", classes="field")

            yield Label("Modo por defecto")
            yield Select(
                [("TUI", "tui"), ("Web / serve", "serve")],
                value=cfg.default_mode,
                id="mode",
                classes="field",
            )

            yield Label("Puerto web")
            yield Input(
                value=str(cfg.default_port),
                type="integer",
                id="port",
                classes="field",
            )

        with Horizontal(id="buttons"):
            yield Button("Guardar", id="save", variant="success")
            yield Button("Cancelar", id="cancel")
        yield Footer()

    @on(Button.Pressed, "#browse")
    def browse(self) -> None:
        cfg = self.app.global_cfg
        current = self.query_one("#workspace", Input).value.strip()
        initial = Path(current) if current and Path(current).exists() else Path(cfg.repositories_root)

        def selected(path: Path | None) -> None:
            if path is None:
                return
            workspace = self.query_one("#workspace", Input)
            name = self.query_one("#name", Input)
            branch = self.query_one("#branch", Input)
            workspace.value = str(path)
            if not name.value.strip():
                name.value = infer_project_name(path)
            branch.value = infer_branch(path)

        self.app.push_screen(DirectoryPickerScreen(initial), selected)

    @on(Input.Changed, "#workspace")
    def workspace_changed(self, event: Input.Changed) -> None:
        value = event.value.strip()
        if not value:
            return
        p = Path(value)
        if p.exists() and p.is_dir():
            name = self.query_one("#name", Input)
            if not name.value.strip():
                name.value = infer_project_name(p)

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        workspace = self.query_one("#workspace", Input).value.strip()
        name = self.query_one("#name", Input).value.strip()
        branch = self.query_one("#branch", Input).value.strip()
        runtime = str(self.query_one("#runtime", Select).value)
        mode = str(self.query_one("#mode", Select).value)
        port_text = self.query_one("#port", Input).value.strip()
        maven_repository = self.query_one("#maven-repository", Input).value.strip()

        if not workspace or not Path(workspace).is_dir():
            self.app.push_screen(
                MessageScreen("Error", "La working copy indicada no existe.")
            )
            return

        if not name:
            name = infer_project_name(Path(workspace))
        if not branch:
            branch = infer_branch(Path(workspace))

        try:
            port = int(port_text)
        except ValueError:
            self.app.push_screen(MessageScreen("Error", "Puerto no válido."))
            return

        try:
            maven_repository_path(maven_repository)
        except DockerError as exc:
            self.app.push_screen(MessageScreen("Error", str(exc)))
            return

        try:
            build_paths = parse_build_paths(
                self.query_one("#build-paths", Input).value
            )
            validate_build_paths(Path(workspace).resolve(), build_paths)
        except (ValueError, DockerError) as exc:
            self.app.push_screen(MessageScreen("Error", str(exc)))
            return

        project = self.app.store.create_project(
            name=name,
            branch=branch,
            workspace=workspace,
            runtime=runtime,
            default_mode=mode,
            port=port,
            maven_repository=maven_repository,
            build_paths=build_paths,
        )
        self.app.pop_screen()
        self.app.open_project(project.project_id)


class SaveRecreateScreen(Screen):
    def confirm_save_recreate(self, project: ProjectConfig, *, save_sensitive_paths: bool = False) -> None:
        def confirmed(ok: bool | None) -> None:
            if ok:
                asyncio.create_task(self._save_recreate_async(
                    project, save_sensitive_paths=save_sensitive_paths,
                ))

        self.app.push_screen(
            ConfirmScreen(
                f"¿Guardar y recrear el contenedor de '{project.name} / {project.branch}'?\n\n"
                "Se guardarán los cambios y se aplicarán la protección de archivos, "
                "opencode.jsonc, imagen, puertos y montajes. El contenedor nuevo "
                "tendrá servidor compartido Web/TUI.\n\n"
                "Se conservarán la working copy, los ajustes y los volúmenes "
                "de sesiones, datos, estado y compilación.\n\n"
                "Se interrumpirán las tareas activas. Se perderán los ficheros "
                "guardados únicamente dentro del contenedor, fuera de montajes "
                "y volúmenes persistentes.\n\n"
                "El nuevo contenedor quedará en ejecución.",
                confirm_label="Guardar y recrear contenedor",
            ),
            confirmed,
        )

    async def _save_recreate_async(self, project: ProjectConfig, *, save_sensitive_paths: bool = False) -> None:
        busy = BusyScreen("Guardando y recreando contenedor; conservando datos...")
        await self.app.push_screen(busy)
        saved = False
        try:
            if save_sensitive_paths:
                self.app.store.save_project(project, save_sensitive_paths=True)
            else:
                self.app.store.save_project(project)
            saved = True
            await asyncio.to_thread(
                recreate_container,
                paths=self.app.paths, global_cfg=self.app.global_cfg, project=project,
            )
        except Exception as exc:
            busy.dismiss(None)
            prefix = "Los cambios se han guardado, pero no se pudo recrear el contenedor.\n\n" if saved else ""
            self.app.push_screen(MessageScreen("Error al guardar y recrear", prefix + str(exc)))
            return
        busy.dismiss(None)
        self.app.pop_screen()
        self.app.push_screen(MessageScreen(
            "Contenedor recreado",
            "Los cambios se han guardado y aplicado. Se han conservado la working "
            "copy, las sesiones y los datos persistentes.\n\n"
            "Puedes abrir TUI o Web desde Uso.",
        ))


class SensitivePathsScreen(SaveRecreateScreen):
    CSS = """
    SensitivePathsScreen { padding: 1 3; }
    #protection-content { height: 1fr; }
    #protection-content Horizontal { height: 3; }
    #pathlist { height: 10; border: round $primary; }
    #protection-report { height: auto; border: round $primary; padding: 1 2; }
    #addrow { height: 3; }
    #protection-navigation { height: 3; }
    #back { width: auto; margin-bottom: 1; }
    #save { width: auto; }
    .hint { color: $text-muted; margin-bottom: 1; }
    """

    def __init__(self, project_id: str):
        super().__init__()
        self.project_id = project_id
        self.draft: ProjectConfig | None = None

    def compose(self) -> ComposeResult:
        project = self.app.store.get_project(self.project_id)
        self.draft = replace(project, sensitive_paths=list(project.sensitive_paths))

        yield Header()
        yield Button("← Volver", id="back", variant="primary")
        yield Label(
            f"Protección de archivos · {project.name} / {project.branch}",
            classes="title",
        )
        with VerticalScroll(id="protection-content"):
            yield from self._protection_content(project)

        with Horizontal(id="protection-navigation"):
            yield Button("Guardar y recrear contenedor", id="save", variant="warning")

        yield Footer()

    def _protection_content(self, project: ProjectConfig) -> ComposeResult:
        yield Static(
            "Las rutas añadidas se tratan como sufijos genéricos. "
            "Ej.: src/main/resources/foo protege también "
            "modulo/src/main/resources/foo.\n"
            "Puedes editar las reglas directamente en este fichero, sin usar la TUI, "
            "o copiarlo al mismo lugar en el proyecto destino:\n"
            f"{self.app.paths.project_sensitive_paths(self.project_id)}\n"
            "Una regla por línea; comentarios con #. Reabre esta pantalla tras copiarlo. "
            "El informe refleja las reglas actuales de la working copy, no los montajes "
            "del contenedor existente. Añadir y eliminar modifica un borrador; "
            "para guardar y aplicar cambios, usa Guardar y recrear contenedor.",
            classes="hint", markup=False,
        )

        yield Label("Reglas de protección", classes="title")
        yield ListView(
            *[
                ListItem(Label(path), id=f"path-{i}")
                for i, path in enumerate(project.sensitive_paths)
            ],
            id="pathlist",
        )

        with Horizontal(id="addrow"):
            yield Input(
                placeholder="Ej.: src/main/resources/foo o /k8s/",
                id="newpath",
            )
            yield Button("Añadir", id="add", variant="success")

        with Horizontal():
            yield Button(
                "Eliminar seleccionado",
                id="remove",
                variant="warning",
            )
        yield Label("Informe de protección", classes="title")
        yield Static("Consultando protección...", id="protection-report", markup=False)

    def on_mount(self) -> None:
        self.refresh_protection_report()

    @work(exclusive=True)
    async def refresh_protection_report(self) -> None:
        try:
            report = await asyncio.to_thread(
                sensitive_mount_report,
                self.app.paths,
                self.app.global_cfg,
                self.draft,
            )
        except Exception as exc:
            report = f"No se pudo consultar la protección: {exc}"
        self.query_one("#protection-report", Static).update(report)

    @staticmethod
    def _normalize_sensitive_input(value: str) -> str:
        from .model import normalize_sensitive_path
        return normalize_sensitive_path(value)

    def _add_sensitive_value(self, raw_value: str) -> None:
        try:
            value = self._normalize_sensitive_input(raw_value)
        except ValueError as exc:
            self.app.push_screen(
                MessageScreen("Ruta no válida", str(exc))
            )
            return

        if not value:
            return

        project = self.draft

        if value in project.sensitive_paths:
            self.app.push_screen(
                MessageScreen(
                    "Sensitive path",
                    f"La regla ya existe:\n\n{value}",
                )
            )
            return

        project.sensitive_paths.append(value)
        self.query_one("#newpath", Input).value = ""
        self.refresh_rules()

    @work(exclusive=True, group="rules")
    async def refresh_rules(self) -> None:
        view = self.query_one("#pathlist", ListView)
        await view.clear()
        await view.extend([
            ListItem(Label(path), id=f"path-{i}")
            for i, path in enumerate(self.draft.sensitive_paths)
        ])
        self.refresh_protection_report()

    @on(Button.Pressed, "#add")
    def add_path(self) -> None:
        raw = self.query_one("#newpath", Input).value
        self._add_sensitive_value(raw)

    @on(Input.Submitted, "#newpath")
    def add_path_enter(self, event: Input.Submitted) -> None:
        self._add_sensitive_value(event.value)

    @on(Button.Pressed, "#remove")
    def remove_path(self) -> None:
        view = self.query_one("#pathlist", ListView)
        index = view.index

        if index is None:
            return

        project = self.draft

        if 0 <= index < len(project.sensitive_paths):
            project.sensitive_paths.pop(index)
            self.refresh_rules()

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        project = self.app.store.get_project(self.project_id)
        self.confirm_save_recreate(
            replace(project, sensitive_paths=list(self.draft.sensitive_paths)),
            save_sensitive_paths=True,
        )

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.action_navigation_back()


class ProjectSettingsScreen(SaveRecreateScreen):
    CSS = """
    ProjectSettingsScreen { padding: 1 3; }
    #settings-content { height: 1fr; }
    #settings-content Horizontal { height: 3; }
    #settings-navigation { height: 3; }
    #back { width: auto; margin-bottom: 1; }
    #save { width: auto; }
    .field { margin-bottom: 1; }
    .hint { color: $text-muted; margin-bottom: 1; }
    """

    def __init__(self, project_id: str):
        super().__init__()
        self.project_id = project_id

    def compose(self) -> ComposeResult:
        p = self.app.store.get_project(self.project_id)
        runtimes = [
            (f"Java {profile.java}", name)
            for name, profile in self.app.global_cfg.runtimes.items()
            if profile.java
        ]

        yield Header()
        yield Button("← Volver", id="back", variant="primary")
        yield Label(f"Configurar · {p.name} / {p.branch}", classes="title")

        with VerticalScroll(id="settings-content"):
            yield from self._settings_content(p, runtimes)
        with Horizontal(id="settings-navigation"):
            yield Button("Guardar y recrear contenedor", id="save", variant="warning")
        yield Footer()

    def _settings_content(self, p: ProjectConfig, runtimes: list) -> ComposeResult:
        yield Label("Working copy")
        yield Input(value=p.workspace, id="workspace", classes="field")

        yield Label("Java")
        yield Static("Maven y npm ya vienen instalados en el runtime.", classes="hint")
        yield Select(runtimes, value=p.runtime, id="runtime", classes="field")

        yield Label("Repositorio Maven local (opcional)")
        yield Static(
            "Solo repository, en lectura; OpenCode podrá ver sus artefactos y metadatos. "
            "Usa mvn -o para compilar sin acceder a repositorios remotos.",
            classes="hint",
        )
        yield Input(value=p.maven_repository, id="maven-repository", classes="field")

        yield Label("Directorios de build en volumen Linux (opcional)")
        yield Static(
            "Rutas relativas a la working copy separadas por ';' "
            "(ej.: wseganadero/target). El compilador escribe ahí y evita los "
            "permisos del montaje de Windows; el target de Windows no se actualiza. "
            "No puede solaparse con las reglas de protección.",
            classes="hint",
        )
        yield Input(
            value=format_build_paths(p.build_paths),
            id="build-paths",
            classes="field",
        )

        yield Label("Modo predeterminado")
        yield Select(
            [("TUI", "tui"), ("Web / serve", "serve")],
            value=p.default_mode,
            id="mode",
            classes="field",
        )

        yield Label("Puerto web")
        yield Input(value=str(p.port), type="integer", id="port", classes="field")

        with Horizontal():
            yield Label("Cargar AGENTS.md específico (fuera del workspace)")
            yield Switch(value=p.project_agents_enabled, id="agents_enabled")

        with Horizontal():
            yield Label("Cargar opencode.jsonc específico (fuera del workspace)")
            yield Switch(value=p.project_config_enabled, id="config_enabled")

        with Horizontal():
            yield Button("Abrir configuración", id="open-config")

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        p = replace(self.app.store.get_project(self.project_id))
        workspace = self.query_one("#workspace", Input).value.strip()
        if not Path(workspace).is_dir():
            self.app.push_screen(
                MessageScreen("Error", "La working copy indicada no existe.")
            )
            return

        p.workspace = str(Path(workspace).resolve())
        p.runtime = str(self.query_one("#runtime", Select).value)
        p.maven_repository = self.query_one("#maven-repository", Input).value.strip()
        try:
            maven_repository_path(p.maven_repository)
        except DockerError as exc:
            self.app.push_screen(MessageScreen("Error", str(exc)))
            return
        try:
            p.build_paths = parse_build_paths(
                self.query_one("#build-paths", Input).value
            )
            validate_build_paths(
                Path(p.workspace),
                p.build_paths,
                sensitive_applied_paths(p),
            )
        except (ValueError, DockerError) as exc:
            self.app.push_screen(MessageScreen("Error", str(exc)))
            return
        p.default_mode = str(self.query_one("#mode", Select).value)
        try:
            p.port = int(self.query_one("#port", Input).value)
            if not 1 <= p.port <= 65535:
                raise ValueError
        except ValueError:
            self.app.push_screen(MessageScreen("Error", "Puerto no válido (1–65535)."))
            return
        p.project_agents_enabled = self.query_one("#agents_enabled", Switch).value
        p.project_config_enabled = self.query_one("#config_enabled", Switch).value
        self.confirm_save_recreate(p)

    @on(Button.Pressed, "#open-config")
    def open_config(self) -> None:
        directory = self.app.paths.project_dir(
            self.project_id
        )
        try:
            os.startfile(directory)  # type: ignore[attr-defined]
        except Exception as exc:
            self.app.push_screen(
                MessageScreen("Error", str(exc))
            )

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.action_navigation_back()


class ProjectStorageScreen(Screen):
    CSS = """
    ProjectStorageScreen { padding: 1 3; }
    #storage-scroll { height: 1fr; }
    #storage-report { height: auto; }
    #storage-navigation { height: 3; }
    #storage-back { width: auto; margin-bottom: 1; }
    """

    def __init__(self, project_id: str):
        super().__init__()
        self.project_id = project_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield Button("← Volver", id="storage-back", variant="primary")
        yield Label("Almacenamiento · contenedor y host", classes="title")
        with VerticalScroll(id="storage-scroll"):
            yield Static("Consultando montajes…", id="storage-report", markup=False)
        with Horizontal(id="storage-navigation"):
            yield Button("Actualizar", id="storage-refresh")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_storage()

    @work(exclusive=True, group="storage-report")
    async def refresh_storage(self) -> None:
        report = self.query_one("#storage-report", Static)
        report.update("Consultando montajes…")
        try:
            project = self.app.store.get_project(self.project_id)
            text = await asyncio.to_thread(
                storage_report, self.app.paths, self.app.global_cfg, project,
            )
            report.update(text)
        except Exception as exc:
            report.update(f"No se pudo consultar el almacenamiento: {exc}")

    @on(Button.Pressed, "#storage-refresh")
    def reload_storage(self) -> None:
        self.refresh_storage()

    @on(Button.Pressed, "#storage-back")
    def back(self) -> None:
        self.app.action_navigation_back()


class JavaScreen(Screen):
    CSS = """
    JavaScreen { padding: 1 3; }
    #java-content { height: 1fr; }
    #runtimes { height: 8; }
    .hint { color: $text-muted; margin-bottom: 1; }
    #back { width: auto; margin-bottom: 1; }
    #maven-section {
        height: auto;
        border: round $primary;
        padding: 1 2;
        margin-top: 2;
    }
    #save-maven { margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Button("← Volver", id="back", variant="primary")
        yield Label("Java y Maven", classes="title")
        with VerticalScroll(id="java-content"):
            yield Static("Maven y npm ya vienen instalados en todos los runtimes.", classes="hint")

            table = DataTable(id="runtimes")
            table.add_columns("Java", "Imagen", "Estado")
            for name, profile in self.app.global_cfg.runtimes.items():
                if not profile.java:
                    continue
                table.add_row(
                    f"Java {profile.java}",
                    profile.image,
                    "sin comprobar",
                    key=name,
                )
            table.cursor_type = "row"
            yield table

            with Horizontal():
                yield Button("Construir seleccionado", id="build", variant="success")

            with Vertical(id="maven-section"):
                yield Label("Maven · repositorio local por defecto", classes="title")
                yield Static(
                    "Solo repository, en lectura. Puedes cambiarlo al añadir una instancia "
                    "o en sus Ajustes. No modifica instancias existentes.",
                    classes="hint",
                )
                yield Input(
                    value=self.app.global_cfg.default_maven_repository,
                    id="default-maven-repository",
                )
                yield Button("Guardar ruta por defecto", id="save-maven", variant="success")
        yield Footer()

    @on(Button.Pressed, "#save-maven")
    def save_maven(self) -> None:
        value = self.query_one("#default-maven-repository", Input).value.strip()
        try:
            maven_repository_path(value)
        except DockerError as exc:
            self.app.push_screen(MessageScreen("Error", str(exc)))
            return
        cfg = replace(self.app.global_cfg, default_maven_repository=value)
        try:
            self.app.store.save_global(cfg)
        except OSError as exc:
            self.app.push_screen(MessageScreen("Error", f"No se pudo guardar la ruta: {exc}"))
            return
        self.app.global_cfg = cfg
        self.app.push_screen(MessageScreen("Maven", "Ruta por defecto guardada para nuevas instancias."))

    @on(Button.Pressed, "#build")
    async def build(self) -> None:
        table = self.query_one("#runtimes", DataTable)
        if table.cursor_row < 0:
            return

        key = str(
            table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        )
        busy = BusyScreen(f"Construyendo {key}... Puede tardar.")
        self.app.push_screen(busy)
        await asyncio.sleep(0)

        try:
            output = await asyncio.to_thread(
                build_runtime, self.app.paths, self.app.global_cfg, key
            )
            busy.dismiss(None)
            self.app.push_screen(
                MessageScreen("Build completado", output[-6000:] or "Build completado.")
            )
        except Exception as exc:
            busy.dismiss(None)
            self.app.push_screen(MessageScreen("Error de build", str(exc)))

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.action_navigation_back()


class ProjectScreen(Screen):
    CSS = """
    ProjectScreen { padding: 1 3; }
    #instance-content { height: 1fr; }
    #info {
        border: round $primary;
        padding: 1 2;
        margin-bottom: 1;
        height: auto;
    }
    #instance-content Horizontal { height: auto; }
    #instance-content Button {
        width: 1fr;
        margin: 0 1 1 0;
    }
    .action-group {
        height: auto;
        border: round $primary;
        padding: 0 1;
        margin-bottom: 1;
    }
    .section-title { text-style: bold; margin-bottom: 1; }
    .section-help { height: auto; margin-bottom: 1; }
    #danger-zone { border: round $error; }
    #instance-status { height: auto; margin-bottom: 1; }
    #navigation { height: auto; }
    #back { width: auto; margin-bottom: 1; }
    """

    def __init__(self, project_id: str):
        super().__init__()
        self.project_id = project_id

    def project(self) -> ProjectConfig:
        return self.app.store.get_project(self.project_id)

    def compose(self) -> ComposeResult:
        p = self.project()
        yield Header()
        yield Button("← Volver", id="back", variant="primary")
        yield Label(f"Gestionar instancia · {p.name} / {p.branch}", classes="title")
        with VerticalScroll(id="instance-content"):
            yield Static(self._summary(), id="info", markup=False)
            yield Static("Estado: consultando...", id="instance-status", markup=False)
            with Vertical(classes="action-group"):
                yield Label("Uso", classes="section-title")
                yield Static(
                    "Abrir inicia o reutiliza la instancia. Detener conserva sus sesiones y datos.",
                    classes="section-help",
                )
                with Horizontal():
                    yield Button("Abrir TUI", id="tui", variant="success")
                    yield Button("Abrir Web", id="serve", variant="success")
                    yield Button("Detener", id="stop", variant="warning")
                with Horizontal():
                    yield Button("Actualizar estado", id="refresh-status")
            with Vertical(classes="action-group"):
                yield Label("Configuración", classes="section-title")
                yield Static(
                    "Para aplicar cambios en la protección de archivos, la configuración "
                    "de OpenCode (opencode.jsonc) o los montajes, usa Guardar y recrear "
                    "contenedor en Ajustes o Protección de archivos. "
                    "Las reglas AGENTS.md se sincronizan automáticamente en los "
                    "contenedores nuevos.",
                    id="configuration-help", classes="section-help", markup=False,
                )
                with Horizontal():
                    yield Button("Ajustes", id="settings")
                    yield Button("Protección de archivos", id="sensitive")
            with Vertical(classes="action-group"):
                yield Label("Diagnóstico", classes="section-title")
                with Horizontal():
                    yield Button("Logs", id="logs")
                    yield Button("Comando Docker", id="command")
            with Vertical(id="danger-zone", classes="action-group"):
                yield Label("Zona peligrosa", classes="section-title")
                yield Static(
                    "Eliminar borra la configuración, el contenedor y sus sesiones y datos "
                    "persistentes. No modifica la working copy. No se puede deshacer.",
                    classes="section-help",
                )
                with Horizontal():
                    yield Button("Eliminar instancia", id="delete-instance", variant="error")
        yield Footer()

    def _summary(self) -> str:
        p = self.project()
        profile = self.app.global_cfg.runtimes.get(p.runtime)
        java = profile.java if profile else p.runtime
        mode = {"tui": "TUI", "serve": "Web"}.get(p.default_mode, p.default_mode)
        return "\n".join([
            f"Proyecto: {p.name} · Rama: {p.branch}",
            f"Working copy: {p.workspace}",
            f"Java: {java} · Modo por defecto: {mode} · Puerto Web: {p.port}",
            f"Reglas de protección: {len(p.sensitive_paths)}",
        ])

    def on_screen_resume(self) -> None:
        self.query_one("#info", Static).update(self._summary())
        self.refresh_status()

    @on(Button.Pressed, "#refresh-status")
    def refresh_status_clicked(self) -> None:
        self.refresh_status()

    @work(exclusive=True)
    async def refresh_status(self) -> None:
        status_widget = self.query_one("#instance-status", Static)
        status_widget.update("Estado: consultando...")
        try:
            status = await asyncio.to_thread(
                container_status, self.app.global_cfg, self.project().container_name,
            )
            label = {
                "absent": "Sin crear; se creará al abrir TUI o Web",
                "running": "En ejecución",
                "exited": "Detenida; conserva sus sesiones y datos",
                "created": "Creada, pendiente de iniciar",
                "paused": "En pausa",
                "restarting": "Reiniciándose",
                "dead": "Error del contenedor",
                "removing": "Eliminándose",
            }.get(status, status)
            status_widget.update(f"Estado: {label}")
        except Exception:
            status_widget.update("Estado: no disponible. Comprueba el motor en Diagnóstico.")


    def _protection_notice(self, project: ProjectConfig) -> str:
        protected = sensitive_applied_paths(project)
        if not protected:
            return (
                "Sensitive paths bloqueados: "
                "ninguno encontrado en esta working copy."
            )

        lines = [f"Sensitive paths bloqueados: {len(protected)}"]
        lines.extend(f"  - {path}" for path in protected)
        return "\n".join(lines)

    async def launch(self, mode: str) -> None:
        p = self.project()

        busy = BusyScreen("Comprobando instancia y runtime...")
        await self.app.push_screen(busy)

        try:
            existing = await asyncio.to_thread(
                container_exists,
                self.app.global_cfg,
                p.container_name,
            )

            profile = self.app.global_cfg.runtimes.get(p.runtime)
            if profile is None:
                raise DockerError(f"Runtime desconocido: {p.runtime}")

            exists = await asyncio.to_thread(
                image_exists,
                self.app.global_cfg,
                profile.image,
            )
            if not exists and not existing:
                busy.query_one(Static).update(
                    f"Preparando Java {profile.java} con Maven y npm... "
                    "Puede tardar."
                )
                try:
                    await asyncio.to_thread(
                        build_runtime,
                        self.app.paths,
                        self.app.global_cfg,
                        p.runtime,
                    )
                except Exception as exc:
                    raise DockerError(
                        f"No se pudo preparar Java {profile.java}:\n{exc}"
                    ) from exc

            preview = await asyncio.to_thread(
                build_launch_command,
                paths=self.app.paths,
                global_cfg=self.app.global_cfg,
                project=p,
                mode=mode,
                detached=(mode == "serve"),
            )

            ports = (
                await asyncio.to_thread(
                    container_published_ports, self.app.global_cfg, p.container_name,
                )
                if existing else {p.port}
            )
            web_port = next(iter(ports)) if len(ports) == 1 else p.port
            conflicts = []
            if preview.args and ports:
                projects = await asyncio.to_thread(self.app.store.list_projects)
                for other in projects:
                    if other.container_name == p.container_name:
                        continue
                    status = await asyncio.to_thread(
                        container_status, self.app.global_cfg, other.container_name,
                    )
                    if status not in ("running", "restarting", "paused"):
                        continue
                    other_ports = await asyncio.to_thread(
                        container_published_ports, self.app.global_cfg, other.container_name,
                    )
                    if ports & other_ports:
                        conflicts.append(other)

            if conflicts:
                names = "\n".join(f"- {other.name} / {other.branch}" for other in conflicts)
                raise DockerError(
                    f"El puerto de esta instancia está ocupado por:\n{names}\n\n"
                    "Configura un puerto distinto en Ajustes. Si el contenedor ya "
                    "existe, cambiar su puerto requiere recrearlo explícitamente.\n\n"
                    "No se ha iniciado esta instancia ni detenido o modificado "
                    "ninguna otra instancia."
                )

            if mode == "serve":
                busy.query_one(Static).update(
                    "Iniciando OpenCode Web..."
                )
                container_id = p.container_name
                if preview.args:
                    container_id = await asyncio.to_thread(
                        launch_detached,
                        preview,
                    )

                # docker run -d may succeed even if OpenCode exits
                # immediately afterwards.
                await asyncio.sleep(0.75)
                running_after_start = await asyncio.to_thread(
                    container_running,
                    self.app.global_cfg,
                    p.container_name,
                )
                if not running_after_start:
                    report = await asyncio.to_thread(
                        container_exit_report,
                        self.app.global_cfg,
                        p.container_name,
                    )
                    if "missing OPENCODE_SERVER_PASSWORD" in report:
                        raise DockerError(
                            "Este contenedor fue creado sin la configuración de "
                            "autenticación que requiere el proxy actual. Detener e "
                            "iniciar no puede añadirla.\n\n"
                            "En Ajustes de esta instancia, usa Guardar y recrear "
                            "contenedor y confirma el aviso. Se conservarán los "
                            "volúmenes de sesiones y datos. No uses Eliminar instancia."
                        )
                    raise DockerError(
                        "El contenedor Web terminó inmediatamente después "
                        "del arranque. Se conserva para diagnóstico.\n\n"
                        f"{report}"
                    )

                await asyncio.to_thread(
                    prepare_web_login,
                    self.app.global_cfg,
                    p.container_name,
                    preview,
                )
                clean_url = (
                    f"http://{self.app.global_cfg.bind_address}:"
                    f"{web_port}/"
                )
                login_url = preview.web_login_url(
                    self.app.global_cfg.bind_address,
                    web_port,
                )
                try:
                    webbrowser.open(login_url, new=2)
                except Exception:
                    pass

                busy.dismiss(None)
                self.app.push_screen(
                    MessageScreen(
                        "OpenCode Web iniciado",
                        (
                            f"Contenedor: {container_id}\n"
                            f"URL: {clean_url}\n"
                            "Login automático: activado\n\n"
                            f"{self._protection_notice(p)}"
                        ),
                    )
                )
            else:
                busy.dismiss(None)
                self.app.exit(
                    LaunchRequest(
                        project_id=p.project_id,
                        mode="tui",
                    )
                )

        except Exception as exc:
            busy.dismiss(None)
            self.app.push_screen(
                MessageScreen("Error", str(exc))
            )

    @on(Button.Pressed, "#tui")
    async def start_tui(self) -> None:
        await self.launch("tui")

    @on(Button.Pressed, "#serve")
    async def start_serve(self) -> None:
        await self.launch("serve")

    @on(Button.Pressed, "#sensitive")
    def sensitive(self) -> None:
        self.app.push_screen(
            SensitivePathsScreen(self.project_id)
        )

    @on(Button.Pressed, "#settings")
    def settings(self) -> None:
        self.app.push_screen(
            ProjectSettingsScreen(self.project_id)
        )

    @on(Button.Pressed, "#command")
    def command(self) -> None:
        p = self.project()
        try:
            preview = build_launch_command(
                paths=self.app.paths,
                global_cfg=self.app.global_cfg,
                project=p,
                mode=p.default_mode,
                detached=(p.default_mode == "serve"),
            )
            self.app.push_screen(
                MessageScreen(
                    "Comando de contenedor",
                    preview.powershell(),
                )
            )
        except Exception as exc:
            self.app.push_screen(
                MessageScreen("Error", str(exc))
            )

    @on(Button.Pressed, "#logs")
    async def logs(self) -> None:
        p = self.project()
        busy = BusyScreen("Leyendo logs...")
        self.app.push_screen(busy)
        await asyncio.sleep(0)

        try:
            result = await asyncio.to_thread(
                container_logs,
                self.app.global_cfg,
                p.container_name,
            )
            busy.dismiss(None)
            self.app.push_screen(
                MessageScreen(
                    f"Logs · {p.container_name}",
                    result,
                )
            )
        except Exception as exc:
            busy.dismiss(None)
            self.app.push_screen(
                MessageScreen("Error", str(exc))
            )

    @on(Button.Pressed, "#stop")
    async def stop(self) -> None:
        p = self.project()
        busy = BusyScreen("Deteniendo instancia...")
        self.app.push_screen(busy)
        await asyncio.sleep(0)

        try:
            running = await asyncio.to_thread(
                container_running,
                self.app.global_cfg,
                p.container_name,
            )
            if not running:
                busy.dismiss(None)
                self.app.push_screen(
                    MessageScreen(
                        "Instancia",
                        "La instancia no está ejecutándose.",
                    )
                )
                return

            out = await asyncio.to_thread(
                stop_container,
                self.app.global_cfg,
                p.container_name,
            )
            busy.dismiss(None)
            self.app.push_screen(
                MessageScreen(
                    "Instancia detenida",
                    out,
                )
            )
        except Exception as exc:
            busy.dismiss(None)
            self.app.push_screen(
                MessageScreen("Error", str(exc))
            )

    @on(Button.Pressed, "#delete-instance")
    def delete_instance(self) -> None:
        p = self.project()

        def confirmed(ok: bool | None) -> None:
            if not ok:
                return
            asyncio.create_task(
                self._delete_instance_async(p)
            )

        self.app.push_screen(
            ConfirmScreen(
                f"Eliminar la instancia "
                f"'{p.name} / {p.branch}'?\n\n"
                "Se eliminarán:\n"
                "- la configuración de la instancia en ocode;\n"
                "- el contenedor persistente;\n"
                "- el volumen de datos de OpenCode;\n"
                "- el volumen de estado/sesiones de OpenCode.\n\n"
                "NO se modificará la working copy.\n"
                "Esta operación no se puede deshacer."
            ),
            confirmed,
        )

    async def _delete_instance_async(
        self,
        p: ProjectConfig,
    ) -> None:
        busy = BusyScreen(
            "Eliminando instancia y volúmenes..."
        )
        self.app.push_screen(busy)
        await asyncio.sleep(0)

        try:
            running = await asyncio.to_thread(
                container_running,
                self.app.global_cfg,
                p.container_name,
            )
            if running:
                busy.dismiss(None)
                self.app.push_screen(
                    MessageScreen(
                        "Instancia activa",
                        "Detén la instancia antes de eliminarla.",
                    )
                )
                return

            if await asyncio.to_thread(
                container_exists, self.app.global_cfg, p.container_name,
            ):
                await asyncio.to_thread(
                    remove_container, self.app.global_cfg, p.container_name,
                )
            volume_result = await asyncio.to_thread(
                remove_state,
                self.app.global_cfg,
                p,
            )
            self.app.store.delete_project_config(
                p.project_id
            )

            busy.dismiss(None)
            self.app.pop_screen()

            if isinstance(self.app.screen, MainScreen):
                self.app.pop_screen()
                self.app.push_screen(MainScreen())

            detail = (
                volume_result.strip()
                or "Volúmenes inexistentes o ya eliminados."
            )
            self.app.push_screen(
                MessageScreen(
                    "Instancia eliminada",
                    (
                        "Se ha eliminado la configuración de la "
                        "instancia, su contenedor y sus volúmenes persistentes."
                        "\n\n"
                        f"{detail}\n\n"
                        "La working copy no se ha modificado."
                    ),
                )
            )
        except Exception as exc:
            busy.dismiss(None)
            self.app.push_screen(
                MessageScreen("Error", str(exc))
            )

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.action_navigation_back()


class ContainerRuntimeScreen(Screen):
    """Configura cómo invoca ocode al motor de contenedores."""

    CSS = """
    ContainerRuntimeScreen { padding: 1 3; }
    """

    def __init__(self, required: bool = False):
        super().__init__()
        self.required = required

    def compose(self) -> ComposeResult:
        current = self.app.global_cfg.container_runtime
        current_kind = current.kind if current.kind != "unset" else "wsl-docker"
        current_command = " ".join(current.command)

        yield Header()
        yield Label(
            "Motor de contenedores"
            + (" · configuración obligatoria" if self.required else ""),
            classes="title",
        )
        yield Static(
            "Elige cómo debe invocar ocode al motor. "
            "No se modifican aliases ni $PROFILE."
        )

        yield Label("Modalidad")
        yield Select(
            [
                ("Docker", "docker"),
                ("Docker vía WSL", "wsl-docker"),
                ("Podman", "podman"),
                ("Detección automática", "auto"),
                ("Comando personalizado", "custom"),
            ],
            value=current_kind,
            id="runtime-kind",
        )

        yield Label("Comando personalizado")
        yield Input(
            value=current_command,
            placeholder="Ej.: wsl -d Ubuntu docker",
            id="runtime-command",
        )

        with Horizontal():
            yield Button("Guardar y probar", id="save-test", variant="success")
            if not self.required:
                yield Button("Cancelar", id="cancel")
        yield Footer()

    @on(Button.Pressed, "#save-test")
    async def save_and_test(self) -> None:
        kind = str(self.query_one("#runtime-kind", Select).value)
        command_text = self.query_one("#runtime-command", Input).value.strip()

        if kind == "custom":
            if not command_text:
                self.app.push_screen(
                    MessageScreen(
                        "Configuración inválida",
                        "Indica el comando del runtime personalizado.",
                    )
                )
                return
            try:
                command = shlex.split(command_text, posix=False)
                command = [
                    x[1:-1]
                    if len(x) >= 2 and x[0] == x[-1] and x[0] in ("'", '"')
                    else x
                    for x in command
                ]
            except ValueError as exc:
                self.app.push_screen(MessageScreen("Comando inválido", str(exc)))
                return
        else:
            command = []

        cfg = self.app.global_cfg
        cfg.container_runtime.kind = kind
        cfg.container_runtime.command = command
        self.app.store.save_global(cfg)
        self.app.global_cfg = self.app.store.load_global()

        busy = BusyScreen("Probando motor de contenedores...")
        self.app.push_screen(busy)
        await asyncio.sleep(0)
        ok, detail = await asyncio.to_thread(docker_available, self.app.global_cfg)
        busy.dismiss(None)

        if self.required:
            # La selección ya queda guardada. No hay forma de saltar esta pantalla
            # sin haber elegido una modalidad.
            self.app.pop_screen()
            self.app.push_screen(MainScreen())

        if ok:
            self.app.push_screen(
                MessageScreen(
                    "Motor configurado",
                    f"Configuración guardada.\n\nPrueba correcta:\n{detail}",
                )
            )
        else:
            self.app.push_screen(
                MessageScreen(
                    "Motor configurado",
                    "La selección se ha guardado, pero la prueba no ha respondido "
                    f"correctamente:\n\n{detail}",
                )
            )

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        if not self.required:
            self.app.pop_screen()


class MainScreen(Screen):
    CSS = """
    MainScreen { padding: 1 3; }
    #projects { height: 1fr; border: round $primary; }
    #toolbar {
        height: 6;
        grid-size: 3;
        grid-columns: 1fr 1fr 1fr;
        grid-rows: 3 3;
    }
    #toolbar Button { width: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("ocode · instancias OpenCode", classes="title")

        table = DataTable(id="projects")
        table.add_columns("Proyecto", "Rama", "Java", "Estado", "Puerto", "Working copy")
        for p in self.app.store.list_projects():
            profile = self.app.global_cfg.runtimes.get(p.runtime)
            java = profile.java if profile else p.runtime
            table.add_row(
                p.name,
                p.branch,
                java,
                "Consultando…",
                str(p.port),
                p.workspace,
                key=p.project_id,
            )
        table.cursor_type = "row"
        yield table

        with Grid(id="toolbar"):
            yield Button("Añadir", id="add", variant="success")
            yield Button("Gestionar instancia", id="open", variant="primary")
            yield Button("Java / Maven", id="java")
            yield Button("Motor", id="container-runtime")
            yield Button("Diagnóstico", id="doctor")
            yield Button("Config global", id="global-config")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_instance_statuses()

    def on_screen_resume(self) -> None:
        self.refresh_instance_statuses()

    @work(exclusive=True, group="instance-statuses")
    async def refresh_instance_statuses(self) -> None:
        table = self.query_one("#projects", DataTable)
        projects = self.app.store.list_projects()
        labels = {
            "absent": "Sin iniciar",
            "running": "En ejecución",
            "exited": "Detenida",
            "created": "Detenida",
            "paused": "En pausa",
            "restarting": "Reiniciándose",
            "dead": "Error",
            "removing": "Eliminándose",
        }
        for row, project in enumerate(projects):
            try:
                status = await asyncio.to_thread(
                    container_status,
                    self.app.global_cfg,
                    project.container_name,
                )
                label = labels.get(status, status)
            except Exception:
                label = "No disponible"
            table.update_cell_at(Coordinate(row, 3), label)

    def selected_project(self) -> str | None:
        table = self.query_one("#projects", DataTable)
        if table.row_count == 0 or table.cursor_row < 0:
            return None
        return str(
            table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        )

    @on(Button.Pressed, "#add")
    def add(self) -> None:
        self.app.push_screen(AddProjectScreen())

    @on(Button.Pressed, "#open")
    def open_selected(self) -> None:
        project_id = self.selected_project()
        if project_id:
            self.app.open_project(project_id)

    @on(DataTable.RowSelected, "#projects")
    def row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.open_project(str(event.row_key.value))

    @on(Button.Pressed, "#java")
    def java(self) -> None:
        self.app.push_screen(JavaScreen())

    @on(Button.Pressed, "#container-runtime")
    def container_runtime(self) -> None:
        self.app.push_screen(ContainerRuntimeScreen(required=False))

    @on(Button.Pressed, "#doctor")
    async def doctor_clicked(self) -> None:
        busy = BusyScreen("Ejecutando diagnóstico...")
        self.app.push_screen(busy)
        await asyncio.sleep(0)
        try:
            report = await asyncio.to_thread(
                doctor,
                self.app.paths,
                self.app.global_cfg,
                self.app.store.list_projects(),
            )
            busy.dismiss(None)
            self.app.push_screen(MessageScreen("Diagnóstico", report))
        except Exception as exc:
            busy.dismiss(None)
            self.app.push_screen(MessageScreen("Error", str(exc)))

    @on(Button.Pressed, "#global-config")
    def open_global(self) -> None:
        try:
            os.startfile(self.app.paths.config_dir)  # type: ignore[attr-defined]
        except Exception as exc:
            self.app.push_screen(MessageScreen("Error", str(exc)))


class OcodeApp(App[LaunchRequest | None]):
    TITLE = f"ocode {__version__}"
    CSS = """
    .title {
        text-style: bold;
        margin-bottom: 1;
    }
    Button {
        min-width: 16;
    }
    """

    BINDINGS = [
        ("q", "quit", "Salir"),
        Binding("ctrl+p", "command_palette", "Atajos", priority=True, show=False),
        Binding("alt+left", "navigation_back", "Atrás", priority=True),
        Binding("alt+right", "navigation_forward", "Adelante", priority=True),
    ]

    def __init__(self, store: Store, paths: Paths):
        super().__init__()
        self.store = store
        self.paths = paths
        self.global_cfg = store.load_global()
        self._navigation_forward: list[Screen] = []
        self._navigation_cached: set[Screen] = set()
        self._navigation_sequence = 0

    def _clear_navigation_forward(self) -> None:
        for screen in self._navigation_forward:
            self.uninstall_screen(screen)
            self._navigation_cached.discard(screen)
            screen.remove()
        self._navigation_forward.clear()

    def push_screen(self, screen: Screen | str, callback: Any = None,
                    wait_for_dismiss: bool = False) -> Any:
        target = self.get_screen(screen) if isinstance(screen, str) else screen
        if not isinstance(target, ModalScreen):
            self._clear_navigation_forward()
        return super().push_screen(screen, callback, wait_for_dismiss)

    def pop_screen(self) -> Any:
        # Programmatic exits (save, cancel, delete) are not browser history.
        current = self.screen
        if not isinstance(current, ModalScreen):
            self._clear_navigation_forward()
        result = super().pop_screen()
        if current in self._navigation_cached:
            self.uninstall_screen(current)
            self._navigation_cached.discard(current)
        return result

    def can_navigate_back(self, screen: Screen | None = None) -> bool:
        screen = self.screen if screen is None else screen
        return (
            screen in self.screen_stack[2:]
            and not isinstance(screen, ModalScreen)
            and not getattr(screen, "required", False)
        )

    def can_navigate_forward(self, screen: Screen | None = None) -> bool:
        screen = self.screen if screen is None else screen
        return bool(self._navigation_forward) and not isinstance(screen, ModalScreen)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "navigation_back":
            return self.can_navigate_back()
        if action == "navigation_forward":
            return self.can_navigate_forward()
        return super().check_action(action, parameters)

    def action_navigation_back(self) -> None:
        if not self.can_navigate_back():
            return
        screen = self.screen
        if not self.is_screen_installed(screen):
            self._navigation_sequence += 1
            self.install_screen(screen, f"navigation-{self._navigation_sequence}")
            self._navigation_cached.add(screen)
        self._navigation_forward.append(screen)
        # Bypass the programmatic-exit cleanup so the form and selection survive.
        super().pop_screen()
        if len(self._navigation_forward) > 20:
            oldest = self._navigation_forward.pop(0)
            self.uninstall_screen(oldest)
            self._navigation_cached.discard(oldest)
            oldest.remove()
        self.refresh_bindings()

    def action_navigation_forward(self) -> None:
        if not self.can_navigate_forward():
            return
        screen = self._navigation_forward.pop()
        super().push_screen(screen)
        self.refresh_bindings()

    def on_mount(self) -> None:
        if self.global_cfg.container_runtime.kind == "unset":
            self.push_screen(ContainerRuntimeScreen(required=True))
        else:
            self.push_screen(MainScreen())

    def open_project(self, project_id: str) -> None:
        self.push_screen(ProjectScreen(project_id))

    def action_command_palette(self) -> None:
        if not CommandPalette.is_open(self):
            self.push_screen(CommandPalette(placeholder="Buscar atajos…"))

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield SystemCommand("Salir", "Cerrar ocode sin detener las instancias", self.action_quit)
        yield SystemCommand(
            "Abrir AGENTS.md general", "Abrir en la aplicación predeterminada del host",
            partial(self.open_host_path, self.paths.global_agents),
        )
        yield SystemCommand(
            "Abrir opencode.jsonc general", "Abrir en la aplicación predeterminada del host",
            partial(self.open_host_path, self.paths.opencode_config),
        )
        project_id = self.shortcut_project(screen)
        for title, target in (
            ("Abrir AGENTS.md del proyecto", "agents"),
            ("Abrir opencode.jsonc del proyecto", "config"),
            ("Abrir sensitive-paths.txt del proyecto", "sensitive"),
            ("Ver almacenamiento del proyecto", "storage"),
        ):
            yield SystemCommand(
                title, "Usar el proyecto abierto o seleccionado",
                partial(self.open_project_path, project_id, target),
            )
        yield SystemCommand("Actualizar ocode", "Ejecutar update.cmd en el host", self.confirm_update)
        if self.can_navigate_back(screen):
            yield SystemCommand("Atrás", "Alt+← · Volver a la pantalla anterior", self.action_navigation_back)
        if self.can_navigate_forward(screen):
            yield SystemCommand("Adelante", "Alt+→ · Recuperar la pantalla siguiente", self.action_navigation_forward)

    def shortcut_project(self, screen: Screen) -> str | None:
        # Prefer the current project's settings/dialog over the main table.
        project_id = getattr(screen, "project_id", None)
        if project_id:
            return project_id
        if isinstance(screen, MainScreen):
            return screen.selected_project()
        for candidate in reversed(self.screen_stack):
            project_id = getattr(candidate, "project_id", None)
            if project_id:
                return project_id
            if isinstance(candidate, MainScreen):
                return candidate.selected_project()
        return None

    def open_host_path(self, path: Path) -> bool:
        try:
            if not path.exists():
                raise FileNotFoundError(f"No existe: {path}")
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:
            self.push_screen(MessageScreen("Error", str(exc)))
            return False
        return True

    def open_project_path(self, project_id: str | None, target: str) -> None:
        if project_id is None:
            self.push_screen(MessageScreen("Proyecto", "Selecciona o abre un proyecto primero."))
            return
        if target == "storage":
            self.push_screen(ProjectStorageScreen(project_id))
            return
        path = {
            "agents": self.paths.project_agents,
            "config": self.paths.project_config,
            "sensitive": self.paths.project_sensitive_paths,
        }[target](project_id)
        self.open_host_path(path)

    def confirm_update(self) -> None:
        def confirmed(result: bool) -> None:
            if result and self.open_host_path(self.paths.home / "update.cmd"):
                self.exit()

        self.push_screen(ConfirmScreen(
            "Se ejecutará update.cmd y se cerrará ocode. Las instancias seguirán funcionando.\n"
            "Extrae primero la nueva distribución en la carpeta de instalación.",
            confirm_label="Actualizar",
        ), confirmed)
