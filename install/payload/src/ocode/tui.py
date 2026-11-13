from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
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
from . import providers
from .docker import (
    DockerError,
    apply_project_opencode_version,
    build_launch_command,
    build_runtime,
    container_exit_report,
    container_exists,
    container_logs,
    container_opencode_version,
    container_published_ports,
    published_port_owners,
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
    stale_providers,
    stop_container,
    update_project_opencode_version,
    validate_build_paths,
)
from .doctor import doctor
from .storage import container_directories, storage_report
from .model import (
    ProjectConfig,
    format_build_paths,
    next_project_port,
    normalize_opencode_version,
    parse_build_paths,
)
from .paths import Paths
from .store import Store, provider_env_snippet, provider_env_status
from .update import Release, check_for_update, stage_update
from .versions import (
    RELEASES_PAGE,
    VersionListError,
    list_remote_versions,
    npm_version_available,
    release_summary,
)


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


class OptionsScreen(ModalScreen[None]):
    CSS = """
    OptionsScreen { align: center middle; }
    #options-box {
        width: 72%;
        max-width: 90%;
        height: auto;
        max-height: 85%;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    #options-list { height: auto; max-height: 22; }
    #options-list Button { width: 1fr; }
    """

    def __init__(self, project_id: str | None, options: list[tuple[str, str]]):
        super().__init__()
        self.project_id = project_id
        self.options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="options-box"):
            yield Label("Opciones", classes="title")
            with VerticalScroll(id="options-list"):
                for index, (label, _) in enumerate(self.options):
                    yield Button(label, id=f"option-{index}")
            yield Button("Cerrar", id="options-close")

    @on(Button.Pressed, "#options-close")
    def close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "Button")
    def choose(self, event: Button.Pressed) -> None:
        if not event.button.id or not event.button.id.startswith("option-"):
            return
        index = int(event.button.id.removeprefix("option-"))
        _, action = self.options[index]
        self.dismiss(None)
        self.app.perform_option(action, self.project_id)


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
        border: round $warning;
        padding: 1 2;
        background: $surface;
    }
    #confirm-text {
        height: auto;
        max-height: 10;
        overflow-y: auto;
    }
    #confirm Horizontal { height: 3; }
    #confirm Button { width: auto; }
    """

    def __init__(
        self,
        text: str,
        confirm_label: str = "Confirmar",
        details_url: str | None = None,
    ):
        super().__init__()
        self.text = text
        self.confirm_label = confirm_label
        self.details_url = details_url

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            with VerticalScroll(id="confirm-text"):
                yield Static(self.text, markup=False)
            with Horizontal():
                if self.details_url:
                    yield Button("Abrir cambios", id="details")
                yield Button("Cancelar", id="no")
                yield Button(self.confirm_label, id="yes", variant="error")

    @on(Button.Pressed, "#details")
    def details(self) -> None:
        if self.details_url:
            try:
                webbrowser.open(self.details_url, new=2)
            except Exception:
                pass

    @on(Button.Pressed, "#yes")
    def yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def no(self) -> None:
        self.dismiss(False)


async def _latest_opencode_version(app: OcodeApp, hint: str) -> str | None:
    busy = BusyScreen("Buscando la última versión de OpenCode...")
    await app.push_screen(busy)
    await asyncio.sleep(0)
    try:
        versions = await asyncio.to_thread(list_remote_versions)
    except VersionListError as exc:
        busy.dismiss(None)
        app.push_screen(
            MessageScreen(
                "No se puede actualizar",
                f"No se pudo consultar la última versión:\n{exc}\n\n{hint}",
            )
        )
        return None
    if not versions:
        busy.dismiss(None)
        app.push_screen(
            MessageScreen(
                "No se puede actualizar",
                f"El registro no devolvió versiones compatibles. {hint}",
            )
        )
        return None
    busy.dismiss(None)
    return versions[0]


async def _opencode_release_notes(version: str) -> tuple[str, str]:
    try:
        notes, url = await asyncio.to_thread(release_summary, version)
    except Exception:
        notes, url = "", RELEASES_PAGE
    return notes, url or RELEASES_PAGE


def _release_notes_text(version: str, notes: str) -> str:
    if notes:
        return f"\n\nNovedades de OpenCode {version}:\n{notes}"
    return f"\n\nNo hay notas publicadas para OpenCode {version}."


class UpdateScreen(ModalScreen[str | None]):
    CSS = """
    UpdateScreen { align: center middle; }
    #update {
        width: 88%;
        height: auto;
        max-height: 85%;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    #update #update-notes {
        height: auto;
        max-height: 18;
        overflow-y: auto;
        margin: 1 0;
    }
    #update Horizontal { height: 3; }
    #update Button { width: auto; }
    """

    def __init__(self, release: Release, current: str):
        super().__init__()
        self.release = release
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="update"):
            yield Label(
                f"Nueva versión de ocode: {self.release.version}",
                classes="title",
            )
            yield Static(
                f"Instalada: ocode {self.current}\n"
                f"Paquete: {self.release.archive_url}",
            )
            if self.release.notes:
                yield Static(self.release.notes, id="update-notes")
            with Horizontal():
                yield Button("Actualizar", id="update-now", variant="success")
                yield Button("Omitir esta versión", id="update-skip")
                yield Button("Cancelar", id="update-cancel")

    @on(Button.Pressed, "#update-now")
    def choose_update(self) -> None:
        self.dismiss("update")

    @on(Button.Pressed, "#update-skip")
    def choose_skip(self) -> None:
        self.dismiss("skip")

    @on(Button.Pressed, "#update-cancel")
    def choose_cancel(self) -> None:
        self.dismiss(None)


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
    #opencode-version-actions { height: 3; }
    #browse { width: auto; }
    #add-content { height: 1fr; }
    .hint { color: $text-muted; margin-bottom: 1; }
    """

    def compose(self) -> ComposeResult:
        cfg = self.app.global_cfg
        self.port_owners: dict[int, list[str]] = {}
        self.port_error = "Consultando puertos del motor de contenedores…"
        self.port_ready = False
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

            yield Label("OpenCode")
            yield Static(
                "Por defecto se usa la última versión publicada. Si no se puede "
                "consultar, se usa la versión de reserva. Puedes cambiarla.",                classes="hint",
            )
            yield Input(
                value=cfg.opencode_version,
                id="opencode-version",
                classes="field",
            )
            with Horizontal(id="opencode-version-actions"):
                yield Button("Elegir versión…", id="pick-opencode-version")
            yield Static("", id="opencode-version-notice", classes="hint")

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
                disabled=True,
            )
            yield Static(self.port_error, id="port-notice", classes="hint")

        with Horizontal(id="buttons"):
            yield Button("Guardar", id="save", variant="success", disabled=True)
            yield Button("Cancelar", id="cancel")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_port_suggestion()
        self.load_latest_opencode_version()

    @work(exclusive=True, group="latest-opencode")
    async def load_latest_opencode_version(self) -> None:
        version_input = self.query_one("#opencode-version", Input)
        notice = self.query_one("#opencode-version-notice", Static)
        original = version_input.value
        try:
            versions = await asyncio.to_thread(list_remote_versions)
        except VersionListError:
            notice.update(
                "No se pudo consultar la última versión; se usa la indicada. "
                "Puedes escribir otra."
            )
            return
        if not versions:
            notice.update("No hay versiones publicadas; se usa la indicada.")
            return
        latest = versions[0]
        if version_input.value == original:
            version_input.value = latest
        notice.update(f"Última publicada: {latest}. Puedes cambiarla.")

    @on(Button.Pressed, "#pick-opencode-version")
    def pick_opencode_version(self) -> None:
        current = (
            self.query_one("#opencode-version", Input).value.strip()
            or self.app.global_cfg.opencode_version
        )
        self.app.push_screen(
            OpenCodeVersionScreen(
                current=current,
                title="OpenCode para la nueva instancia",
                action="Usar",
                note=(
                    "Se instalará al crear la instancia. La última publicada "
                    "aparece en la lista; también puedes escribir otra."
                ),
            ),
            self.apply_opencode_version,
        )

    def apply_opencode_version(self, version: str | None) -> None:
        if version is not None:
            self.query_one("#opencode-version", Input).value = version

    @work(exclusive=True, group="suggest-port")
    async def refresh_port_suggestion(self) -> None:
        port_input = self.query_one("#port", Input)
        original = port_input.value
        try:
            owners = await asyncio.to_thread(published_port_owners, self.app.global_cfg)
            suggested = next_project_port(self.app.global_cfg.default_port, set(owners))
        except (DockerError, ValueError) as exc:
            self.port_error = str(exc)
            self.query_one("#port-notice", Static).update(self.port_error)
            return
        else:
            self.port_owners = owners
            self.port_error = ""
            if port_input.value == original:
                port_input.value = str(suggested)
            self.show_port_notice(port_input.value)
        finally:
            port_input.disabled = False
            self.query_one("#save", Button).disabled = False
            self.port_ready = True

    def show_port_notice(self, value: str) -> None:
        try:
            port = int(value)
        except ValueError:
            port = 0
        owners = self.port_owners.get(port, [])
        notice = (
            f"Aviso: el puerto {port} ya está asociado a "
            + ", ".join(owners) + ". Elige otro puerto."
            if owners else self.port_error
        )
        self.query_one("#port-notice", Static).update(notice)

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

    @on(Input.Changed, "#port")
    def port_changed(self, event: Input.Changed) -> None:
        self.show_port_notice(event.value)

    @on(Button.Pressed, "#save")
    async def save(self) -> None:
        if not self.port_ready:
            self.app.push_screen(MessageScreen(
                "Consulta en curso",
                "Espera a que termine la consulta de puertos del motor de contenedores.",
            ))
            return

        workspace = self.query_one("#workspace", Input).value.strip()
        name = self.query_one("#name", Input).value.strip()
        branch = self.query_one("#branch", Input).value.strip()
        runtime = str(self.query_one("#runtime", Select).value)
        mode = str(self.query_one("#mode", Select).value)
        port_text = self.query_one("#port", Input).value.strip()
        maven_repository = self.query_one("#maven-repository", Input).value.strip()
        try:
            opencode_version = normalize_opencode_version(
                self.query_one("#opencode-version", Input).value
            )
        except ValueError as exc:
            self.app.push_screen(MessageScreen("Versión no válida", str(exc)))
            return

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
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.app.push_screen(MessageScreen("Error", "Puerto no válido (1–65535)."))
            return

        try:
            owners = (await asyncio.to_thread(published_port_owners, self.app.global_cfg)).get(port, [])
        except DockerError as exc:
            self.app.push_screen(MessageScreen("Error", str(exc)))
            return
        if owners:
            self.app.push_screen(MessageScreen(
                "Puerto ya asociado",
                f"El puerto {port} ya está asociado a {', '.join(owners)}. Elige otro puerto.",
            ))
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

        try:
            project = self.app.store.create_project(
                name=name,
                branch=branch,
                workspace=workspace,
                runtime=runtime,
                default_mode=mode,
                port=port,
                maven_repository=maven_repository,
                build_paths=build_paths,
                opencode_version=opencode_version,
            )
        except ValueError as exc:
            self.app.push_screen(MessageScreen("Error", str(exc)))
            return
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
        yield Label(f"Configurar · {p.name} / {p.branch}", classes="title")

        with VerticalScroll(id="settings-content"):
            yield from self._settings_content(p, runtimes)
        with Horizontal(id="settings-navigation"):
            yield Button("← Volver", id="back", variant="primary")
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
            yield Label("Cargar AGENTS.md específico editable (fuera del workspace)")
            yield Switch(value=p.project_agents_enabled, id="agents_enabled")

        with Horizontal():
            yield Label("Cargar opencode.jsonc específico (fuera del workspace)")
            yield Switch(value=p.project_config_enabled, id="config_enabled")

        with Horizontal():
            yield Button("Abrir configuración", id="open-config")

    @on(Button.Pressed, "#save")
    async def save(self) -> None:
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
        try:
            owners = [name for name in (
                await asyncio.to_thread(published_port_owners, self.app.global_cfg)
            ).get(p.port, []) if name != p.container_name]
        except DockerError as exc:
            self.app.push_screen(MessageScreen("Error", str(exc)))
            return
        if owners:
            self.app.push_screen(MessageScreen(
                "Puerto ya asociado",
                f"El puerto {p.port} ya está asociado a {', '.join(owners)}. Elige otro puerto.",
            ))
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
    #storage-navigation Button { width: auto; }
    #top-actions { height: 3; align: right middle; }
    #top-actions Button { width: auto; }
    """

    def __init__(self, project_id: str):
        super().__init__()
        self.project_id = project_id

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="top-actions"):
            yield Button("Opciones", id="options", variant="primary")
        yield Button("← Volver", id="storage-back", variant="primary")
        yield Label("Almacenamiento · contenedor y host", classes="title")
        with VerticalScroll(id="storage-scroll"):
            yield Static("Consultando montajes…", id="storage-report", markup=False)
        with Horizontal(id="storage-navigation"):
            yield Button("Actualizar", id="storage-refresh")
            yield Button("Abrir working copy", id="storage-open-workspace")
            yield Button("Explorar contenedor", id="storage-explore")
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

    @on(Button.Pressed, "#storage-open-workspace")
    def open_workspace(self) -> None:
        project = self.app.store.get_project(self.project_id)
        self.app.open_host_path(Path(project.workspace))

    @on(Button.Pressed, "#storage-explore")
    def explore_container(self) -> None:
        self.app.push_screen(ContainerDirectoryScreen(self.project_id))

    @on(Button.Pressed, "#options")
    def options(self) -> None:
        self.app.open_options(self)

    @on(Button.Pressed, "#storage-back")
    def back(self) -> None:
        self.app.action_navigation_back()


class ContainerDirectoryScreen(Screen):
    CSS = """
    ContainerDirectoryScreen { padding: 1 3; }
    #container-path { width: 1fr; }
    #container-list { height: 1fr; }
    #container-back { width: auto; }
    """

    def __init__(self, project_id: str):
        super().__init__()
        self.project_id = project_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Explorar directorios del contenedor · solo lectura", classes="title")
        yield Static(
            "Solo se listan directorios. La instancia debe estar en ejecución; "
            "ocode no la iniciará ni leerá contenidos de archivos.",
            markup=False,
        )
        with Horizontal():
            yield Input(value="/", id="container-path")
            yield Button("Abrir directorio", id="container-open", variant="primary")
        table = DataTable(id="container-list")
        table.add_column("Directorios", key="directory")
        table.cursor_type = "row"
        yield table
        yield Button("← Volver", id="container-back")
        yield Footer()

    def on_mount(self) -> None:
        self.list_directory()

    @work(exclusive=True, group="container-directories")
    async def list_directory(self) -> None:
        path = self.query_one("#container-path", Input).value.strip()
        table = self.query_one("#container-list", DataTable)
        table.clear()
        table.add_row("Consultando…", key="loading")
        try:
            project = self.app.store.get_project(self.project_id)
            directories = await asyncio.to_thread(
                container_directories, self.app.global_cfg, project, path,
            )
            table.clear()
            if path != "/":
                table.add_row("..", key="..")
            for directory in directories:
                table.add_row(PurePosixPath(directory).name, key=directory)
            if not directories:
                table.add_row("(sin subdirectorios)", key="empty")
        except Exception as exc:
            table.clear()
            table.add_row(f"No se pudo explorar: {exc}", key="error")

    @on(Button.Pressed, "#container-open")
    def open_directory(self) -> None:
        self.list_directory()

    @on(DataTable.RowSelected, "#container-list")
    def select_directory(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value)
        if key in {"loading", "empty", "error"}:
            return
        current = self.query_one("#container-path", Input).value.rstrip("/") or "/"
        parent = str(PurePosixPath(current).parent) if key == ".." else key
        self.query_one("#container-path", Input).value = parent
        self.list_directory()

    @on(Button.Pressed, "#container-back")
    def back(self) -> None:
        self.app.action_navigation_back()


LATEST_OPENCODE = "latest"


class OpenCodeVersionScreen(ModalScreen[str | None]):
    CSS = """
    OpenCodeVersionScreen { align: center middle; }
    #version-box {
        width: 78%;
        height: 80%;
        max-height: 88%;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    #version-content { height: 1fr; }
    #opencode-version-input { margin-bottom: 1; }
    #versions-status { height: auto; color: $text-muted; margin-bottom: 1; }
    #versions-list {
        height: auto;
        max-height: 16;
        border: round $primary;
        margin-bottom: 1;
    }
    #versions-list ListItem { padding: 0 1; }
    #version-actions { height: 3; }
    #version-actions Button { width: 1fr; margin-right: 1; }
    .hint { color: $text-muted; margin-bottom: 1; }
    """

    def __init__(
        self,
        *,
        current: str,
        title: str,
        action: str,
        note: str,
        prefer_latest: bool = False,
    ):
        super().__init__()
        self.current = current
        self.title_text = title
        self.action_label = action
        self.note = note
        self.prefer_latest = prefer_latest
        self.versions: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="version-box"):
            yield Label(self.title_text, classes="title")
            with VerticalScroll(id="version-content"):
                yield Static(self.note, classes="hint", markup=False)
                yield Input(
                    value=self.current,
                    placeholder="2.0.24",
                    id="opencode-version-input",
                )
                yield Static(
                    "Consultando versiones publicadas…",
                    id="versions-status",
                    markup=False,
                )
                yield ListView(id="versions-list")
            with Horizontal(id="version-actions"):
                yield Button(self.action_label, id="version-apply", variant="success")
                yield Button("Cancelar", id="version-cancel")

    def on_mount(self) -> None:
        self.load_versions()

    @work(exclusive=True, group="opencode-versions")
    async def load_versions(self) -> None:
        status = self.query_one("#versions-status", Static)
        try:
            versions = await asyncio.to_thread(list_remote_versions)
        except VersionListError as exc:
            status.update(
                f"No se pudo consultar la lista ({exc}). "
                "Escribe la versión a mano y pulsa el botón."
            )
            return
        if not self.is_attached:
            return
        self.versions = versions
        view = self.query_one("#versions-list", ListView)
        await view.clear()
        for version in versions:
            suffix = " · actual" if version == self.current else ""
            view.append(ListItem(Label(f"{version}{suffix}", markup=False)))
        if not versions:
            status.update(
                "El registro no devolvió versiones estables. Escribe la versión a mano."
            )
            return
        status.update(
            f"{len(versions)} versiones publicadas. La más reciente: {versions[0]}. "
            "Selecciona una o escribe otra."
        )
        if self.prefer_latest:
            self.query_one("#opencode-version-input", Input).value = versions[0]
            view.index = 0
        elif self.current in versions:
            view.index = versions.index(self.current)
        else:
            view.index = 0
        self.on_versions_loaded()

    def on_versions_loaded(self) -> None:
        pass

    def on_version_selected(self, version: str) -> None:
        pass

    @on(ListView.Selected, "#versions-list")
    def selected(self, event: ListView.Selected) -> None:
        if event.index is not None and 0 <= event.index < len(self.versions):
            version = self.versions[event.index]
            self.query_one("#opencode-version-input", Input).value = version
            self.on_version_selected(version)

    def _input_version(self) -> str | None:
        value = self.query_one("#opencode-version-input", Input).value
        try:
            return normalize_opencode_version(value)
        except ValueError as exc:
            self.query_one("#versions-status", Static).update(str(exc))
            return None

    @on(Button.Pressed, "#version-apply")
    def apply(self) -> None:
        version = self._input_version()
        if version is not None:
            self.dismiss(version)

    @on(Button.Pressed, "#version-cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class OpenCodeUpdateAllScreen(OpenCodeVersionScreen):
    CSS = OpenCodeVersionScreen.CSS + """
    OpenCodeUpdateAllScreen { align: center middle; }
    OpenCodeUpdateAllScreen #version-box { width: 88%; }
    OpenCodeUpdateAllScreen #versions-list { max-height: 10; }
    #version-notes {
        height: auto;
        border: round $primary;
        padding: 0 1;
        margin-bottom: 1;
    }
    #version-notes-title { text-style: bold; }
    #version-notes-open { width: auto; margin-top: 1; }
    #version-actions #version-cancel { width: auto; }
    """

    def __init__(self, *, project_count: int, reserve: str):
        super().__init__(
            current=reserve,
            title="Actualizar OpenCode en todas las instancias",
            action="Actualizar seleccionada",
            note=(
                "Actualiza todas a la última publicada o a la versión elegida "
                f"de la lista. Se revisarán {project_count} instancias. Las que "
                "ya estén en la versión elegida no se reiniciarán. Se instala con "
                "npm y se reinician; se conservan sesiones y datos, y se "
                "interrumpen las tareas. Las detenidas se arrancan y se vuelven a "
                "detener. Si una falla, se detiene el proceso. "
                f"Reserva actual: {reserve}."
            ),
            prefer_latest=True,
        )
        self.notes_url = RELEASES_PAGE
        self.notes_cache: dict[str, tuple[str, str]] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="version-box"):
            yield Label(self.title_text, classes="title")
            with VerticalScroll(id="version-content"):
                yield Static(self.note, classes="hint", markup=False)
                yield Input(
                    value=self.current,
                    placeholder="2.0.24",
                    id="opencode-version-input",
                )
                yield Static(
                    "Consultando versiones publicadas…",
                    id="versions-status",
                    markup=False,
                )
                yield ListView(id="versions-list")
                with Vertical(id="version-notes"):
                    yield Label(
                        "Novedades de OpenCode",
                        id="version-notes-title",
                        classes="section-title",
                    )
                    yield Static(
                        "Selecciona una versión para ver sus novedades.",
                        id="version-notes-body",
                        markup=False,
                    )
                    yield Button("Abrir cambios", id="version-notes-open")
            with Horizontal(id="version-actions"):
                yield Button(
                    "A la última",
                    id="version-latest",
                    variant="success",
                )
                yield Button("Actualizar elegida", id="version-selected")
                yield Button("Cancelar", id="version-cancel")

    def on_versions_loaded(self) -> None:
        if self.versions:
            self.load_notes(self.query_one("#opencode-version-input", Input).value)

    def on_version_selected(self, version: str) -> None:
        self.load_notes(version)

    @on(Input.Submitted, "#opencode-version-input")
    def submitted(self, event: Input.Submitted) -> None:
        self.load_notes(event.value)

    @work(exclusive=True, group="opencode-notes")
    async def load_notes(self, version: str) -> None:
        try:
            version = normalize_opencode_version(version)
        except ValueError:
            return
        title = self.query_one("#version-notes-title", Label)
        body = self.query_one("#version-notes-body", Static)
        title.update(f"Novedades de OpenCode {version}")
        if version in self.notes_cache:
            self.show_notes(version, *self.notes_cache[version])
            return
        body.update("Cargando novedades...")
        notes, url = await _opencode_release_notes(version)
        if not self.is_attached:
            return
        result = (notes, url or RELEASES_PAGE)
        self.notes_cache[version] = result
        self.show_notes(version, *result)

    def show_notes(self, version: str, notes: str, url: str) -> None:
        self.notes_url = url or RELEASES_PAGE
        self.query_one("#version-notes-title", Label).update(
            f"Novedades de OpenCode {version}"
        )
        body = self.query_one("#version-notes-body", Static)
        if notes:
            body.update(notes)
        else:
            body.update(f"No hay notas publicadas para OpenCode {version}.")
        self.query_one("#version-notes-open", Button).label = (
            "Abrir cambios" if notes else "Abrir releases"
        )

    @on(Button.Pressed, "#version-notes-open")
    def open_notes(self) -> None:
        try:
            webbrowser.open(self.notes_url, new=2)
        except Exception:
            pass

    @on(Button.Pressed, "#version-latest")
    def apply_latest(self) -> None:
        self.dismiss(self.versions[0] if self.versions else LATEST_OPENCODE)

    @on(Button.Pressed, "#version-selected")
    def apply_selected(self) -> None:
        version = self._input_version()
        if version is not None:
            self.dismiss(version)


class JavaScreen(Screen):
    CSS = """
    JavaScreen { padding: 1 3; }
    #java-content { height: 1fr; }
    #runtimes { height: 8; }
    .hint { color: $text-muted; margin-bottom: 1; }
    #back { width: auto; margin-bottom: 1; }
    #opencode-section {
        height: auto;
        border: round $primary;
        padding: 1 2;
        margin-bottom: 2;
    }
    #opencode-section .section-title { text-style: bold; }
    #opencode-section Static { height: auto; }
    #opencode-section Horizontal { height: 3; }
    #opencode-section Button { width: 1fr; margin-right: 1; }
    #maven-section {
        height: auto;
        border: round $primary;
        padding: 1 2;
        margin-top: 2;
    }
    #save-maven { margin-top: 1; }
    """

    def on_screen_resume(self) -> None:
        self.refresh_opencode_summary()

    def refresh_opencode_summary(self) -> None:
        try:
            self.query_one("#opencode-current", Static).update(
                self._opencode_summary()
            )
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        yield Header()
        yield Button("← Volver", id="back", variant="primary")
        yield Label("Runtimes de OpenCode", classes="title")
        with VerticalScroll(id="java-content"):
            with Vertical(id="opencode-section"):
                yield Label("OpenCode", classes="section-title")
                yield Static(self._opencode_summary(), id="opencode-current", markup=False)
                yield Static(
                    "La versión se instala con npm dentro de cada contenedor, sin "
                    "reconstruir la imagen. «Actualizar todas» abre el selector: "
                    "puedes ir a la última o elegir una concreta.",
                    classes="hint",
                    markup=False,
                )
                with Horizontal():
                    yield Button(
                        "Versión de reserva…",
                        id="change-default-version",
                        variant="success",
                    )
                    yield Button(
                        "Actualizar todas",
                        id="update-all-instances",
                        variant="warning",
                    )
            yield Static("Maven y npm ya vienen instalados en todos los runtimes.", classes="hint")

            table = DataTable(id="runtimes")
            table.add_columns("Java", "Imagen", "Estado")
            self._add_runtimes(table)
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

    def _opencode_summary(self) -> str:
        cfg = self.app.global_cfg
        return (
            f"Versión de reserva: {cfg.opencode_version}\n"
            f"Se usa si no se puede consultar la última publicada. "
            f"Imagen base: {cfg.opencode_image}"
        )

    def _add_runtimes(self, table: DataTable) -> None:
        for name, profile in self.app.global_cfg.runtimes.items():
            if not profile.java:
                continue
            table.add_row(
                f"Java {profile.java}",
                profile.image,
                "sin comprobar",
                key=name,
            )

    @on(Button.Pressed, "#change-default-version")
    def change_default_version(self) -> None:
        cfg = self.app.global_cfg
        self.app.push_screen(
            OpenCodeVersionScreen(
                current=cfg.opencode_version,
                title="Versión de reserva de OpenCode",
                action="Guardar",
                note=(
                    "Se usará al crear una instancia si no se puede consultar la "
                    "última publicada. No cambia las instancias existentes ni "
                    "reconstruye imágenes."
                ),
            ),
            self.apply_default_version,
        )

    def apply_default_version(self, version: str | None) -> None:
        if version is None:
            return
        try:
            cfg = self.app.global_cfg.with_opencode_default(version)
            if cfg.opencode_version == self.app.global_cfg.opencode_version:
                self.app.push_screen(
                    MessageScreen(
                        "OpenCode",
                        f"La versión de reserva ya es {cfg.opencode_version}.",
                    )
                )
                return
            self.app.store.save_global(cfg)
        except Exception as exc:
            self.app.push_screen(
                MessageScreen("Error", f"No se pudo guardar la versión: {exc}")
            )
            return
        self.app.global_cfg = cfg
        self.query_one("#opencode-current", Static).update(self._opencode_summary())
        self.app.push_screen(
            MessageScreen(
                "OpenCode",
                f"Versión de reserva: {cfg.opencode_version}.\n\n"
                "Las instancias existentes conservan la suya. Para cambiarlas, usa "
                "«Actualizar OpenCode» en cada instancia o «Actualizar todas».",
            )
        )

    @on(Button.Pressed, "#update-all-instances")
    def update_all_instances(self) -> None:
        self.app.open_opencode_update_all()

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


class ProviderKeyScreen(ModalScreen[str | None]):
    CSS = """
    ProviderKeyScreen { align: center middle; }
    #provider-key {
        width: 72%;
        height: auto;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    #provider-status { margin-bottom: 1; }
    #provider-hint, #provider-description {
        color: $text-muted;
        margin-bottom: 1;
    }
    #provider-input { margin-bottom: 1; }
    #provider-actions { height: 3; }
    #provider-actions Button { width: 1fr; margin-right: 1; }
    """

    def __init__(self, provider_id: str):
        super().__init__()
        self.provider = providers.provider(provider_id)

    def compose(self) -> ComposeResult:
        with Vertical(id="provider-key"):
            yield Label(self.provider.name, classes="title")
            yield Static(self.provider.hint, id="provider-hint", markup=False)
            if self.provider.description:
                yield Static(
                    self.provider.description, id="provider-description", markup=False
                )
            yield Static("", id="provider-status", markup=False)
            yield Input(
                value="",
                placeholder=f"Introduce la API key de {self.provider.name}",
                password=True,
                id="provider-input",
            )
            with Horizontal(id="provider-actions"):
                yield Button("Guardar", id="provider-save", variant="success")
                if providers.key_configured(self.app.paths, self.provider.id):
                    yield Button("Borrar", id="provider-clear", variant="warning")
                yield Button("Cancelar", id="provider-cancel")

    def on_mount(self) -> None:
        self.refresh_status()
        self.query_one("#provider-input", Input).focus()

    def refresh_status(self) -> None:
        status = self.query_one("#provider-status", Static)
        if not providers.key_configured(self.app.paths, self.provider.id):
            suffix = (
                " Modo gratuito: Console funciona sin clave."
                if self.provider.id == "opencode"
                else ""
            )
            status.update(f"Clave no configurada.{suffix}")
            return
        last4 = providers.key_last4(self.app.paths, self.provider.id)
        suffix = f" (…{last4})" if last4 else ""
        status.update(f"Clave configurada{suffix}.")

    @on(Button.Pressed, "#provider-save")
    async def save(self) -> None:
        value = self.query_one("#provider-input", Input).value
        try:
            providers.save_key(self.app.paths, self.provider.id, value)
        except ValueError as exc:
            self.app.push_screen(MessageScreen("Error", str(exc)))
            return
        except OSError as exc:
            self.app.push_screen(
                MessageScreen("Error", f"No se pudo guardar la clave: {exc}")
            )
            return
        self.dismiss("Clave guardada")

    @on(Input.Submitted, "#provider-input")
    async def submit(self) -> None:
        await self.save()

    @on(Button.Pressed, "#provider-clear")
    def clear(self) -> None:
        try:
            providers.clear_key(self.app.paths, self.provider.id)
        except OSError as exc:
            self.app.push_screen(
                MessageScreen("Error", f"No se pudo borrar la clave: {exc}")
            )
            return
        self.dismiss("Clave borrada")

    @on(Button.Pressed, "#provider-cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class ProveedoresScreen(Screen):
    CSS = """
    ProveedoresScreen { padding: 1 3; }
    #proveedores-content { height: 1fr; }
    .hint { color: $text-muted; margin-bottom: 1; }
    #back { width: auto; margin-bottom: 1; }
    #providers-list {
        height: auto;
        max-height: 12;
        border: round $primary;
        margin-bottom: 1;
    }
    #providers-list ListItem { padding: 0 1; }
    #provider-separation { margin-bottom: 1; height: auto; }
    .section-title { text-style: bold; margin-bottom: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Button("← Volver", id="back", variant="primary")
        yield Label("Proveedores", classes="title")
        with VerticalScroll(id="proveedores-content"):
            yield Static(
                "Cada clave se guarda en config\\secrets y se inyecta en los "
                "contenedores con --env-file: no aparece en la línea de "
                "comandos ni en «Comando Docker». Son claves globales; los "
                "contenedores existentes necesitan «Guardar y recrear "
                "contenedor».",
                classes="hint",
                markup=False,
            )
            yield Static(
                "Selecciona un proveedor para configurar o borrar su clave.",
                classes="hint",
                markup=False,
            )
            yield Static("", id="provider-separation", markup=False)
            yield ListView(id="providers-list")
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_status()

    async def refresh_status(self) -> None:
        self.refresh_separation()
        view = self.query_one("#providers-list", ListView)
        await view.clear()
        for entry in providers.PROVIDERS.values():
            if providers.key_configured(self.app.paths, entry.id):
                last4 = providers.key_last4(self.app.paths, entry.id)
                suffix = f" (…{last4})" if last4 else ""
                status = f"configurada{suffix}"
            elif entry.id == "opencode":
                status = "no configurada · modo gratuito"
            else:
                status = "no configurada"
            view.append(
                ListItem(
                    Label(
                        f"{entry.name} · {entry.hint} · {status}",
                        markup=False,
                    ),
                    id=entry.id,
                )
            )

    def refresh_separation(self) -> None:
        notice = self.query_one("#provider-separation", Static)
        separated, detail = provider_env_status(self.app.paths.opencode_config)
        if separated:
            notice.update(
                "Separación Console/Go: activa. OpenCode Go usa "
                f"{providers.PROVIDERS['opencode-go'].env_var} y Console "
                f"{providers.PROVIDERS['opencode'].env_var}."
            )
            return
        notice.update(
            f"Separación Console/Go: pendiente. {detail}\n"
            "Añade este bloque a config\\opencode.jsonc y reinicia:\n"
            + provider_env_snippet()
        )

    @on(ListView.Selected, "#providers-list")
    def provider_selected(self, event: ListView.Selected) -> None:
        if event.item.id:
            self.app.push_screen(
                ProviderKeyScreen(event.item.id),
                self.provider_closed,
            )

    def provider_closed(self, action: str | None) -> None:
        if action is None:
            return
        self.run_worker(self.finish_provider_change(action))

    async def finish_provider_change(self, action: str) -> None:
        await self.refresh_status()
        await self.report_stale(action)

    async def report_stale(self, action: str) -> None:
        names: list[str] = []
        unknown = False
        for project in self.app.store.list_projects():
            try:
                exists = await asyncio.to_thread(
                    container_exists,
                    self.app.global_cfg,
                    project.container_name,
                )
                if not exists:
                    continue
                stale = await asyncio.to_thread(
                    stale_providers,
                    self.app.global_cfg,
                    self.app.paths,
                    project.container_name,
                )
            except Exception:
                unknown = True
                continue
            if stale:
                affected = ", ".join(
                    providers.PROVIDERS[provider_id].name for provider_id in stale
                )
                names.append(
                    f"- {project.name} / {project.branch} ({affected})"
                )

        if names:
            message = (
                f"{action}. Instancias con contenedor desactualizado:\n"
                + "\n".join(names)
                + "\n\nAbre Ajustes en cada instancia y usa «Guardar y recrear "
                "contenedor» para aplicar la clave. No se ha recreado nada "
                "automáticamente."
            )
        elif unknown:
            message = (
                f"{action}. No se pudo comprobar si hay instancias "
                "desactualizadas. Revisa el motor en Diagnóstico."
            )
        else:
            message = f"{action}. No hay instancias que necesiten recreación."
        self.app.push_screen(MessageScreen("Proveedores", message))

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
    #opencode-version-status { height: auto; margin-bottom: 1; color: $text-muted; }
    #provider-stale-notice { height: auto; margin-bottom: 1; color: $warning; }
    #navigation { height: auto; }
    #back { width: auto; margin-bottom: 1; }
    #top-actions { height: 3; align: right middle; }
    #top-actions Button { width: auto; }
    """

    def __init__(self, project_id: str):
        super().__init__()
        self.project_id = project_id

    def project(self) -> ProjectConfig:
        return self.app.store.get_project(self.project_id)

    def compose(self) -> ComposeResult:
        p = self.project()
        yield Header()
        with Horizontal(id="top-actions"):
            yield Button("Opciones", id="options", variant="primary")
        yield Button("← Volver", id="back", variant="primary")
        yield Label(f"Gestionar instancia · {p.name} / {p.branch}", classes="title")
        with VerticalScroll(id="instance-content"):
            yield Static(self._summary(), id="info", markup=False)
            yield Static("Estado: consultando...", id="instance-status", markup=False)
            yield Static("", id="opencode-version-status", markup=False)
            yield Static("", id="provider-stale-notice", markup=False)
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
                yield Label("OpenCode", classes="section-title")
                yield Static(
                    "La versión se instala con npm dentro del contenedor. Se conservan "
                    "sesiones y datos; se interrumpen las tareas.",
                    classes="section-help",
                    markup=False,
                )
                with Horizontal():
                    yield Button(
                        "Actualizar a la última",
                        id="update-opencode-latest",
                        variant="success",
                    )
                    yield Button(
                        "Elegir versión…",
                        id="update-opencode",
                    )
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

    @on(Button.Pressed, "#options")
    def options(self) -> None:
        self.app.open_options(self)

    def _summary(self) -> str:
        p = self.project()
        profile = self.app.global_cfg.runtimes.get(p.runtime)
        java = profile.java if profile else p.runtime
        mode = {"tui": "TUI", "serve": "Web"}.get(p.default_mode, p.default_mode)
        return "\n".join([
            f"Proyecto: {p.name} · Rama: {p.branch}",
            f"Working copy: {p.workspace}",
            f"OpenCode: {p.opencode_version or self.app.global_cfg.opencode_version} · "
            f"Java: {java} · Modo por defecto: {mode} · Puerto Web: {p.port}",
            f"Reglas de protección: {len(p.sensitive_paths)}",
        ])

    def _exists(self) -> bool:
        try:
            self.app.store.get_project(self.project_id)
        except (OSError, ValueError):
            return False
        return True

    def on_screen_resume(self) -> None:
        if not self._exists():
            return
        self.query_one("#info", Static).update(self._summary())
        self.refresh_status()

    @on(Button.Pressed, "#refresh-status")
    def refresh_status_clicked(self) -> None:
        self.refresh_status()

    @work(exclusive=True)
    async def refresh_status(self) -> None:
        if not self._exists():
            return
        status_widget = self.query_one("#instance-status", Static)
        version_widget = self.query_one("#opencode-version-status", Static)
        notice = self.query_one("#provider-stale-notice", Static)
        status_widget.update("Estado: consultando...")
        version_widget.update("")
        notice.update("")
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
            return

        project = self.project()
        target_version = (
            project.opencode_version or self.app.global_cfg.opencode_version
        )
        if status == "absent":
            version_widget.update(
                f"OpenCode configurado: {target_version}. "
                "Se aplicará al crear la instancia."
            )
            return

        try:
            running_version = await asyncio.to_thread(
                container_opencode_version,
                self.app.global_cfg,
                project.container_name,
            )
        except Exception:
            running_version = ""
        if running_version:
            if project.opencode_version != running_version:
                try:
                    latest = self.app.store.get_project(project.project_id)
                    if latest.opencode_version != running_version:
                        latest.opencode_version = running_version
                        self.app.store.save_project(latest)
                except Exception:
                    pass
                try:
                    self.query_one("#info", Static).update(self._summary())
                except Exception:
                    pass
            state = (
                "en ejecución"
                if status in ("running", "restarting")
                else "instalado"
            )
            version_widget.update(f"OpenCode {state}: {running_version}.")
        else:
            version_widget.update(
                f"OpenCode configurado: {target_version}. "
                "La instancia no está en ejecución; no se puede verificar la "
                "versión instalada."
            )

        try:
            stale = await asyncio.to_thread(
                stale_providers,
                self.app.global_cfg,
                self.app.paths,
                self.project().container_name,
            )
        except Exception:
            return
        if stale:
            affected = ", ".join(
                providers.PROVIDERS[provider_id].name for provider_id in stale
            )
            if len(stale) == 1:
                subject = f"Clave de {affected} desactualizada"
            else:
                subject = f"Claves de {affected} desactualizadas"
            notice.update(
                subject
                + " en este contenedor. Usa «Guardar y recrear contenedor» "
                "en Ajustes."
            )

    @on(Button.Pressed, "#update-opencode")
    def update_opencode(self) -> None:
        p = self.project()
        current = p.opencode_version or self.app.global_cfg.opencode_version
        self.app.push_screen(
            OpenCodeVersionScreen(
                current=current,
                title=f"Elegir versión de OpenCode · {p.name} / {p.branch}",
                action="Continuar",
                note=(
                    "Selecciona la versión; después confirmarás la actualización. "
                    "Se instala con npm y se reinicia el contenedor; se conservan "
                    "sesiones y datos, y se interrumpen las tareas."
                ),
            ),
            self.apply_opencode_version,
        )

    @on(Button.Pressed, "#update-opencode-latest")
    def update_opencode_latest(self) -> None:
        self.run_worker(
            self.prepare_instance_update(None),
            group="opencode-plan",
            exclusive=True,
        )

    def apply_opencode_version(self, version: str | None) -> None:
        if version is not None:
            self.run_worker(
                self.prepare_instance_update(version),
                group="opencode-plan",
                exclusive=True,
            )

    async def prepare_instance_update(self, version: str | None) -> None:
        if version is None:
            version = await _latest_opencode_version(
                self.app, "Usa «Elegir versión…» para indicarla a mano."
            )
            if version is None:
                return
        busy = BusyScreen("Preparando la actualización...")
        await self.app.push_screen(busy)
        await asyncio.sleep(0)
        notes, details_url = await _opencode_release_notes(version)
        busy.dismiss(None)
        text = (
            f"¿Actualizar esta instancia a OpenCode {version}?\n\n"
            "Se instalará con npm y se reiniciará el contenedor. Se conservan "
            "las sesiones y los datos; se interrumpen las tareas. Si está "
            "detenido, se arranca y se vuelve a detener."
        ) + _release_notes_text(version, notes)

        def confirmed(ok: bool | None) -> None:
            if ok:
                self.update_instance_opencode(version)

        self.app.push_screen(
            ConfirmScreen(
                text,
                confirm_label="Actualizar",
                details_url=details_url,
            ),
            confirmed,
        )

    @work(exclusive=True, group="opencode-update")
    async def update_instance_opencode(self, version: str) -> None:
        p = self.project()
        busy = BusyScreen(f"Actualizando esta instancia a OpenCode {version}...")
        await self.app.push_screen(busy)
        await asyncio.sleep(0)
        try:
            actual = await asyncio.to_thread(
                update_project_opencode_version,
                self.app.global_cfg,
                p,
                version,
            )
            p.opencode_version = version
            self.app.store.save_project(p)
        except Exception as exc:
            busy.dismiss(None)
            self.app.push_screen(
                MessageScreen("Error al actualizar OpenCode", str(exc))
            )
            return
        busy.dismiss(None)
        if actual:
            message = (
                f"Instancia actualizada a OpenCode {version}.\n\n"
                "Se conservan sus sesiones y datos."
            )
        else:
            message = (
                f"OpenCode configurado: {version}. "
                "Se aplicará al crear la instancia."
            )
        self.app.push_screen(MessageScreen("OpenCode", message))


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

            if not existing:
                ports = await asyncio.to_thread(published_port_owners, self.app.global_cfg)
                owners = ports.get(p.port, [])
                if owners:
                    raise DockerError(
                        f"El puerto {p.port} ya está asociado a {', '.join(owners)}. "
                        "Elige un puerto distinto en Ajustes antes de iniciar esta instancia. "
                        "No se ha modificado ninguna otra instancia."
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
                owners = await asyncio.to_thread(published_port_owners, self.app.global_cfg)
                conflicts = sorted({name for port in ports for name in owners.get(port, [])
                                    if name != p.container_name})

            if conflicts:
                names = "\n".join(f"- {name}" for name in conflicts)
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
                    busy.query_one(Static).update(
                        f"Aplicando OpenCode {p.opencode_version or self.app.global_cfg.opencode_version}..."
                    )
                    await asyncio.to_thread(
                        apply_project_opencode_version,
                        self.app.global_cfg,
                        p,
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
    #toolbar { height: 6; }
    #toolbar-primary {
        height: 3;
        grid-size: 4;
        grid-columns: 1fr 1fr 1fr 1fr;
    }
    #toolbar-secondary { height: 3; }
    #toolbar Button { width: 1fr; }
    #top-actions { height: 3; align: right middle; }
    #top-actions Button { width: auto; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="top-actions"):
            yield Button(
                "Actualizar OpenCode",
                id="opencode-all",
                variant="warning",
            )
            yield Button("Opciones", id="options", variant="primary")
        yield Label("ocode · instancias OpenCode", classes="title")

        table = DataTable(id="projects")
        table.add_columns(
            "Proyecto", "Rama", "Java", "OpenCode", "Estado", "Puerto",
            "Working copy",
        )
        for p in self.app.store.list_projects():
            profile = self.app.global_cfg.runtimes.get(p.runtime)
            java = profile.java if profile else p.runtime
            table.add_row(
                p.name,
                p.branch,
                java,
                self._opencode_version(p),
                "Consultando…",
                str(p.port),
                p.workspace,
                key=p.project_id,
            )
        table.cursor_type = "row"
        yield table

        with Vertical(id="toolbar"):
            with Grid(id="toolbar-primary"):
                yield Button("Añadir", id="add", variant="success")
                yield Button("Gestionar instancia", id="open", variant="primary")
                yield Button("Runtimes", id="java")
                yield Button("Proveedores", id="providers")
            with Horizontal(id="toolbar-secondary"):
                yield Button("Motor", id="container-runtime")
                yield Button("Diagnóstico", id="doctor")
                yield Button("Config global", id="global-config")
        yield Footer()

    @on(Button.Pressed, "#options")
    def options(self) -> None:
        self.app.open_options(self)

    def on_mount(self) -> None:
        self.refresh_instance_statuses()

    def on_screen_resume(self) -> None:
        table = self.query_one("#projects", DataTable)
        projects = self.app.store.list_projects()
        existing = {key.value for key in table.rows}
        for project in projects:
            if project.project_id in existing:
                row = table.get_row_index(project.project_id)
                profile = self.app.global_cfg.runtimes.get(project.runtime)
                java = profile.java if profile else project.runtime
                table.update_cell_at(Coordinate(row, 2), java)
                table.update_cell_at(
                    Coordinate(row, 3), self._opencode_version(project)
                )
                table.update_cell_at(Coordinate(row, 5), str(project.port))
            else:
                profile = self.app.global_cfg.runtimes.get(project.runtime)
                java = profile.java if profile else project.runtime
                table.add_row(
                    project.name, project.branch, java,
                    self._opencode_version(project), "Consultando…",
                    str(project.port), project.workspace,
                    key=project.project_id,
                )
        self.refresh_instance_statuses()

    def _opencode_version(self, project: ProjectConfig) -> str:
        return (
            project.opencode_version
            or self.app.global_cfg.opencode_version
        )

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
        for project in projects:
            if not any(key.value == project.project_id for key in table.rows):
                continue
            status = ""
            try:
                status = await asyncio.to_thread(
                    container_status,
                    self.app.global_cfg,
                    project.container_name,
                )
                label = labels.get(status, status)
            except Exception:
                label = "No disponible"
            version = (
                project.opencode_version
                or self.app.global_cfg.opencode_version
            )
            if status and status != "absent":
                try:
                    actual = await asyncio.to_thread(
                        container_opencode_version,
                        self.app.global_cfg,
                        project.container_name,
                    )
                except Exception:
                    actual = ""
                if actual:
                    version = actual
                    if project.opencode_version != actual:
                        try:
                            latest = self.app.store.get_project(
                                project.project_id
                            )
                            if latest.opencode_version != actual:
                                latest.opencode_version = actual
                                self.app.store.save_project(latest)
                        except Exception:
                            pass
            if any(key.value == project.project_id for key in table.rows):
                row = table.get_row_index(project.project_id)
                table.update_cell_at(Coordinate(row, 3), version)
                table.update_cell_at(Coordinate(row, 4), label)

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

    @on(Button.Pressed, "#providers")
    def open_providers(self) -> None:
        self.app.push_screen(ProveedoresScreen())

    @on(Button.Pressed, "#container-runtime")
    def container_runtime(self) -> None:
        self.app.push_screen(ContainerRuntimeScreen(required=False))

    @on(Button.Pressed, "#opencode-all")
    def opencode_all(self) -> None:
        self.app.open_opencode_update_all()

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
        Binding("ctrl+left", "navigation_back", "Atrás", priority=True),
        Binding("ctrl+right", "navigation_forward", "Adelante", priority=True),
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

    def open_opencode_update_all(self) -> None:
        self.push_screen(
            OpenCodeUpdateAllScreen(
                project_count=len(self.store.list_projects()),
                reserve=self.global_cfg.opencode_version,
            ),
            self.apply_opencode_update_all,
        )

    def apply_opencode_update_all(self, choice: str | None) -> None:
        if choice is None:
            return
        version = None if choice == LATEST_OPENCODE else choice
        self.run_worker(
            self.prepare_opencode_update_all(version),
            group="opencode-plan",
            exclusive=True,
        )

    async def prepare_opencode_update_all(self, version: str | None) -> None:
        if version is None:
            version = await _latest_opencode_version(
                self,
                "Abre «Actualizar todas» y escribe la versión a mano.",
            )
            if version is None:
                return
        cfg = self.global_cfg
        projects = self.store.list_projects()
        busy = BusyScreen("Preparando la actualización...")
        await self.push_screen(busy)
        await asyncio.sleep(0)

        labels = {
            "running": "en ejecución",
            "exited": "detenida",
            "created": "detenida",
            "absent": "sin iniciar",
            "paused": "en pausa",
            "restarting": "reiniciándose",
            "dead": "con error",
            "removing": "eliminándose",
        }
        pending: list[ProjectConfig] = []
        plan_lines: list[str] = []
        blocked: list[str] = []
        for project in projects:
            try:
                status = await asyncio.to_thread(
                    container_status, cfg, project.container_name
                )
            except Exception:
                status = ""
            actual = ""
            if status and status != "absent":
                try:
                    actual = await asyncio.to_thread(
                        container_opencode_version,
                        cfg,
                        project.container_name,
                    )
                except Exception:
                    actual = ""
            current = (
                actual
                or project.opencode_version
                or cfg.opencode_version
            )
            if current == version:
                continue
            if status in ("paused", "dead", "removing"):
                blocked.append(
                    f"- {project.name}/{project.branch}: "
                    f"{labels.get(status, status)}"
                )
                continue
            pending.append(project)
            plan_lines.append(
                f"- {project.name}/{project.branch}: {current} → {version} "
                f"({labels.get(status, 'estado desconocido')})"
            )

        if blocked:
            busy.dismiss(None)
            self.push_screen(
                MessageScreen(
                    "No se puede actualizar",
                    "Estas instancias no se pueden actualizar en su estado actual:\n"
                    + "\n".join(blocked)
                    + "\n\nDetenlas o arráncalas desde ocode y vuelve a intentarlo. "
                    "No se ha tocado ninguna instancia.",
                )
            )
            return

        if not pending:
            busy.dismiss(None)
            try:
                self._save_opencode_default(version)
            except Exception as exc:
                self.push_screen(
                    MessageScreen("Error", f"No se pudo guardar la versión: {exc}")
                )
                return
            self.push_screen(
                MessageScreen(
                    "OpenCode",
                    f"Todas las instancias ya están en OpenCode {version}. "
                    "No se ha reiniciado ninguna.",
                )
            )
            return

        try:
            available = await asyncio.to_thread(npm_version_available, version)
        except (VersionListError, ValueError) as exc:
            busy.dismiss(None)
            self.push_screen(
                MessageScreen(
                    "No se puede actualizar",
                    f"No se pudo comprobar OpenCode {version} en npm:\n{exc}\n\n"
                    "No se ha tocado ninguna instancia.",
                )
            )
            return
        if not available:
            busy.dismiss(None)
            self.push_screen(
                MessageScreen(
                    "No se puede actualizar",
                    f"OpenCode {version} no está disponible en npm. "
                    "No se ha tocado ninguna instancia.",
                )
            )
            return
        try:
            notes, details_url = await _opencode_release_notes(version)
        except Exception:
            notes, details_url = "", RELEASES_PAGE
        busy.dismiss(None)
        self.confirm_opencode_update_all(
            version, pending, plan_lines, notes, details_url
        )

    def confirm_opencode_update_all(
        self,
        version: str,
        pending: list[ProjectConfig],
        plan_lines: list[str],
        notes: str,
        details_url: str,
    ) -> None:
        text = (
            f"¿Actualizar OpenCode a {version} en {len(pending)} instancia(s)?\n\n"
            + "\n".join(plan_lines)
            + "\n\nSe instala con npm y se reinicia cada contenedor. Se conservan "
            "las sesiones y los datos; se interrumpen las tareas. Las instancias "
            "detenidas se arrancan y se vuelven a detener. Si una falla, se detiene "
            "el proceso y las siguientes no se tocan. Al terminar, será también la "
            "versión de reserva."
        ) + _release_notes_text(version, notes)

        def confirmed(ok: bool | None) -> None:
            if ok:
                self.run_worker(
                    self.update_all_opencode_versions(version, pending),
                    group="opencode-update-all",
                    exclusive=True,
                )

        self.push_screen(
            ConfirmScreen(
                text,
                confirm_label="Actualizar",
                details_url=details_url or RELEASES_PAGE,
            ),
            confirmed,
        )

    async def update_all_opencode_versions(
        self, version: str, pending: list[ProjectConfig]
    ) -> None:
        cfg = self.global_cfg
        if not pending:
            try:
                self._save_opencode_default(version)
            except Exception as exc:
                self.push_screen(
                    MessageScreen("Error", f"No se pudo guardar la versión: {exc}")
                )
                return
            self.push_screen(
                MessageScreen(
                    "OpenCode",
                    f"No había instancias que cambiar. Versión de reserva: {version}.",
                )
            )
            return

        busy = BusyScreen(
            f"Actualizando {len(pending)} instancias a OpenCode {version}..."
        )
        await self.push_screen(busy)
        await asyncio.sleep(0)
        updated: list[str] = []
        failed: tuple[ProjectConfig, Exception] | None = None
        try:
            for index, project in enumerate(pending, 1):
                busy.query_one(Static).update(
                    f"Actualizando {project.name}/{project.branch} a OpenCode "
                    f"{version}... ({index}/{len(pending)})"
                )
                try:
                    await asyncio.to_thread(
                        update_project_opencode_version, cfg, project, version
                    )
                    project.opencode_version = version
                    self.store.save_project(project)
                    updated.append(f"{project.name}/{project.branch}")
                except Exception as exc:
                    failed = (project, exc)
                    break
        finally:
            busy.dismiss(None)

        if failed is not None:
            project, exc = failed
            remaining = len(pending) - len(updated) - 1
            self.push_screen(
                MessageScreen(
                    "Actualización incompleta",
                    f"Actualizadas: {len(updated)}.\n"
                    f"Falló: {project.name}/{project.branch}:\n{exc}\n"
                    f"Sin tocar: {remaining}.\n\n"
                    "No se ha cambiado la versión de reserva.",
                )
            )
            return

        try:
            self._save_opencode_default(version)
        except Exception as exc:
            self.push_screen(
                MessageScreen(
                    "OpenCode actualizado",
                    f"Instancias actualizadas: {len(updated)}.\n\n"
                    f"No se pudo guardar la versión de reserva: {exc}",
                )
            )
            return
        self.push_screen(
            MessageScreen(
                "OpenCode actualizado",
                f"Actualizadas {len(updated)} instancias a OpenCode {version}.\n"
                f"Versión de reserva: {version}.",
            )
        )

    def _save_opencode_default(self, version: str) -> None:
        cfg = self.global_cfg.with_opencode_default(version)
        if cfg.opencode_version == self.global_cfg.opencode_version:
            return
        self.store.save_global(cfg)
        self.global_cfg = cfg
        for screen in self.screen_stack:
            if isinstance(screen, JavaScreen):
                screen.refresh_opencode_summary()

    def on_mount(self) -> None:
        if self.global_cfg.container_runtime.kind == "unset":
            self.push_screen(ContainerRuntimeScreen(required=True))
        else:
            self.push_screen(MainScreen())
            self.check_updates_command(False)

    def open_project(self, project_id: str) -> None:
        self.push_screen(ProjectScreen(project_id))

    def action_command_palette(self) -> None:
        if not CommandPalette.is_open(self):
            self.push_screen(CommandPalette(placeholder="Buscar atajos…"))

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        if self.can_navigate_back(screen):
            yield SystemCommand("Atrás · Ctrl+←", "Volver a la pantalla anterior", self.action_navigation_back)
        if self.can_navigate_forward(screen):
            yield SystemCommand("Adelante · Ctrl+→", "Recuperar la pantalla siguiente", self.action_navigation_forward)
        yield SystemCommand("Salir · q", "Cerrar ocode sin detener las instancias", self.action_quit)

    def option_items(self, project_id: str | None) -> list[tuple[str, str]]:
        if project_id is None:
            return [
                ("Abrir AGENTS.md general", "global_agents"),
                ("Abrir opencode.jsonc general", "global_config"),
                ("Abrir Skills generales", "global_skills"),
                ("Buscar actualizaciones", "updates"),
                ("Actualizar ocode", "update"),
                ("Salir", "quit"),
            ]
        return [
            ("Abrir AGENTS.md del proyecto", "agents"),
            ("Abrir opencode.jsonc del proyecto", "config"),
            ("Abrir Skills del proyecto", "skills"),
            ("Abrir sensitive-paths.txt del proyecto", "sensitive"),
            ("Ver almacenamiento del proyecto", "storage"),
            ("Buscar actualizaciones", "updates"),
            ("Actualizar ocode", "update"),
            ("Salir", "quit"),
        ]

    def open_options(self, screen: Screen) -> None:
        project_id = None
        if not isinstance(screen, MainScreen):
            for candidate in reversed(self.screen_stack):
                if isinstance(candidate, ProjectScreen):
                    project_id = candidate.project_id
                    break
                if isinstance(candidate, MainScreen):
                    break
                project_id = getattr(candidate, "project_id", None) or project_id
        self.push_screen(OptionsScreen(project_id, self.option_items(project_id)))

    def perform_option(self, action: str, project_id: str | None) -> None:
        if action == "global_agents":
            self.open_host_path(self.paths.global_agents)
        elif action == "global_config":
            self.open_host_path(self.paths.opencode_config)
        elif action == "global_skills":
            self.open_host_path(self.paths.global_skills)
        elif action in {"agents", "config", "skills", "sensitive", "storage"}:
            self.open_project_path(project_id, action)
        elif action == "updates":
            self.check_updates_command(True)
        elif action == "update":
            self.confirm_update()
        elif action == "quit":
            self.action_quit()

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
            "skills": self.paths.project_skills,
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

    @work(exclusive=True, group="update-check")
    async def check_updates_command(self, manual: bool = False) -> None:
        """Check GitHub for a newer ocode release.

        Automatic checks stay silent unless a new version is available; manual
        checks always report their result.
        """
        if not manual and not self.global_cfg.check_updates:
            return

        busy = None
        if manual:
            busy = BusyScreen("Buscando actualizaciones...")
            self.push_screen(busy)
            await asyncio.sleep(0)

        try:
            release = await asyncio.to_thread(check_for_update, __version__)
        except Exception as exc:
            # An update check must never break or block the TUI.
            if busy is not None:
                busy.dismiss(None)
            if manual:
                self.push_screen(MessageScreen("Actualizaciones", str(exc)))
            return

        if busy is not None:
            busy.dismiss(None)

        if release is None:
            if manual:
                self.push_screen(MessageScreen(
                    "Actualizaciones",
                    f"Ya tienes la última versión (ocode {__version__}).",
                ))
            return

        if not manual and release.version == self.global_cfg.skipped_update_version:
            return

        self.prompt_update(release)

    def prompt_update(self, release: Release) -> None:
        def chosen(result: str | None) -> None:
            if result == "update":
                self.perform_update(release)
            elif result == "skip":
                self.global_cfg.skipped_update_version = release.version
                self.store.save_global(self.global_cfg)
                self.push_screen(MessageScreen(
                    "Actualizaciones",
                    f"Se omitirá ocode {release.version} en las comprobaciones automáticas.",
                ))

        self.push_screen(UpdateScreen(release, __version__), chosen)

    @work(exclusive=True, group="update-install")
    async def perform_update(self, release: Release) -> None:
        busy = BusyScreen(f"Descargando ocode {release.version}...")
        self.push_screen(busy)
        await asyncio.sleep(0)
        try:
            launcher = await asyncio.to_thread(
                stage_update, release, self.paths.home
            )
        except Exception as exc:
            busy.dismiss(None)
            self.push_screen(MessageScreen("Error de actualización", str(exc)))
            return
        busy.dismiss(None)
        if self.open_host_path(launcher):
            self.exit()
