from dataclasses import dataclass, field, replace, asdict
from pathlib import Path
import json
import re


OPENCODE_REGISTRY = "ghcr.io"
OPENCODE_REPOSITORY = "anomalyco/opencode"
OPENCODE_VERSION = "2.0.24"
OPENCODE_IMAGE = f"{OPENCODE_REGISTRY}/{OPENCODE_REPOSITORY}:{OPENCODE_VERSION}"
MAVEN_VERSION = "3.9.9"
MIN_OPENCODE_VERSION = (2, 0, 24)

_OPENCODE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")

SENSITIVE_PATHS_FILENAME = "sensitive-paths.txt"


class SensitivePathsError(ValueError):
    """Invalid or missing project protection file; do not ignore it."""


def normalize_sensitive_path(value: str) -> str:
    value = value.strip().replace("\\", "/").strip("/")
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if any(part == ".." or ":" in part for part in parts):
        raise ValueError("No se permiten rutas absolutas ni segmentos '..'.")
    normalized = "/".join(parts)
    if not normalized:
        return ""
    if normalized.startswith("**/"):
        return normalized
    return f"**/{normalized}"


def read_sensitive_paths(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise SensitivePathsError(f"No se puede leer el fichero de protección: {path}") from exc
    rules: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            value = normalize_sensitive_path(line)
            if not value:
                raise ValueError("Regla vacía.")
        except ValueError as exc:
            raise SensitivePathsError(f"{path}, línea {number}: {exc}") from exc
        if value not in rules:
            rules.append(value)
    return rules


BUILD_PATH_SEPARATOR = ";"


def normalize_build_path(value: str) -> str:
    """Relative directory of the working copy whose build output moves to Linux."""
    value = value.strip().replace("\\", "/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ValueError(
            "Indica una ruta relativa a la working copy, no una absoluta."
        )
    parts = [part for part in value.strip("/").split("/") if part not in ("", ".")]
    if not parts:
        return ""
    if any(part == ".." or ":" in part for part in parts):
        raise ValueError("No se permiten rutas absolutas ni segmentos '..'.")
    normalized = "/".join(parts)
    if "," in normalized or "\n" in normalized:
        raise ValueError("La ruta no puede contener comas ni saltos de línea.")
    return normalized


def parse_build_paths(value: str) -> list[str]:
    """Read the TUI field: relative directories separated by ';' or newlines."""
    paths: list[str] = []
    for raw in value.replace("\n", BUILD_PATH_SEPARATOR).split(BUILD_PATH_SEPARATOR):
        normalized = normalize_build_path(raw)
        if normalized and normalized not in paths:
            paths.append(normalized)
    return paths


def format_build_paths(paths: list[str]) -> str:
    return f"{BUILD_PATH_SEPARATOR} ".join(paths)


@dataclass
class RuntimeProfile:
    name: str
    image: str
    java: str | None = None
    buildable: bool = False


class OpenCodeVersionError(ValueError):
    """The OpenCode version must be a Docker tag such as 2.0.24."""


def normalize_opencode_version(value: str) -> str:
    candidate = str(value).strip()
    if candidate[:1] in ("v", "V"):
        candidate = candidate[1:]
    if not _OPENCODE_VERSION_RE.fullmatch(candidate):
        raise OpenCodeVersionError(
            f"Versión de OpenCode no válida: {value!r}. Usa el formato 2.0.24."
        )
    numeric = tuple(
        int(part) for part in candidate.split("-", 1)[0].split(".")
    )
    if numeric < MIN_OPENCODE_VERSION:
        raise OpenCodeVersionError(
            f"OpenCode {candidate} no es compatible: ocode usa OpenCode 2.0.24 "
            "o superior."
        )
    return candidate


def runtime_profiles() -> dict[str, RuntimeProfile]:
    """Built-in Java runtimes, keyed by profile.

    The image is the Java/Maven base built from the ocode-pinned OpenCode
    image. The actual OpenCode version is upgraded in place with npm, so it is
    not part of the runtime tag.
    """
    java_versions = {"java8": "8", "java11": "11", "java17": "17", "java21": "21"}
    return {
        name: RuntimeProfile(
            name,
            f"ocode-runtime:{OPENCODE_VERSION}-maven{MAVEN_VERSION}-java{java}",
            java,
            True,
        )
        for name, java in java_versions.items()
    }


@dataclass
class ContainerRuntime:
    # auto | docker | podman | wsl-docker | custom
    kind: str = "unset"
    command: list[str] = field(default_factory=list)


@dataclass
class GlobalConfig:
    repositories_root: str = r"C:\repositorios"
    default_mode: str = "tui"
    default_port: int = 4096
    bind_address: str = "127.0.0.1"
    opencode_version: str = OPENCODE_VERSION
    opencode_image: str = OPENCODE_IMAGE
    security_no_new_privileges: bool = True
    cap_drop_all: bool = True
    default_maven_repository: str = ""
    check_updates: bool = True
    skipped_update_version: str = ""
    web_show_agent: bool = True
    web_color_scheme: str = "dark"
    container_runtime: ContainerRuntime = field(default_factory=ContainerRuntime)
    runtimes: dict[str, RuntimeProfile] = field(default_factory=dict)

    @classmethod
    def defaults(cls):
        return cls(
            opencode_version=OPENCODE_VERSION,
            opencode_image=OPENCODE_IMAGE,
            container_runtime=ContainerRuntime(kind="unset", command=[]),
            runtimes=runtime_profiles(),
        )

    def with_opencode_default(self, version: str) -> "GlobalConfig":
        """Return a copy with another default version for new instances.

        Runtime images are the fixed Java/Maven base; the OpenCode version is
        applied inside the container with npm, so it does not change the image.
        """
        return replace(
            self,
            opencode_version=normalize_opencode_version(version),
        )

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        defaults = cls.defaults()
        try:
            version = normalize_opencode_version(
                str(data.get("opencode_version") or OPENCODE_VERSION)
            )
        except OpenCodeVersionError:
            version = OPENCODE_VERSION
        show_agent = data.get("web_show_agent", defaults.web_show_agent)
        color_scheme = data.get("web_color_scheme", defaults.web_color_scheme)
        if type(show_agent) is not bool:
            raise ValueError("web_show_agent debe ser un booleano")
        if color_scheme not in ("light", "dark"):
            raise ValueError("web_color_scheme debe ser light o dark")
        runtimes = runtime_profiles()
        runtimes.update({
            k: RuntimeProfile(**v)
            for k, v in data.get("runtimes", {}).items()
            if k not in runtimes and k != "base"
        })

        runtime_data = data.get("container_runtime", {})
        runtime = ContainerRuntime(
            kind=runtime_data.get("kind", "unset"),
            command=list(runtime_data.get("command", [])),
        )

        return cls(
            repositories_root=data.get("repositories_root", defaults.repositories_root),
            default_mode=data.get("default_mode", defaults.default_mode),
            default_port=int(data.get("default_port", defaults.default_port)),
            bind_address=data.get("bind_address", defaults.bind_address),
            opencode_version=version,
            opencode_image=OPENCODE_IMAGE,
            security_no_new_privileges=bool(
                data.get("security_no_new_privileges", True)
            ),
            cap_drop_all=bool(data.get("cap_drop_all", True)),
            default_maven_repository=data.get("default_maven_repository", ""),
            check_updates=bool(data.get("check_updates", True)),
            skipped_update_version=str(data.get("skipped_update_version", "")),
            web_show_agent=show_agent,
            web_color_scheme=color_scheme,
            container_runtime=runtime,
            runtimes=runtimes,
        )


@dataclass
class ProjectConfig:
    project_id: str
    name: str
    branch: str
    workspace: str
    runtime: str = "java8"
    default_mode: str = "tui"
    port: int = 4096
    sensitive_paths: list[str] = field(default_factory=list)
    project_agents_enabled: bool = True
    project_config_enabled: bool = False
    maven_repository: str = ""
    build_paths: list[str] = field(default_factory=list)
    web_initial_preferences: dict | None = None
    opencode_version: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_file(cls, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        rules_path = path.with_name(SENSITIVE_PATHS_FILENAME)
        if rules_path.exists():
            data["sensitive_paths"] = read_sensitive_paths(rules_path)
        elif "sensitive_paths" not in data:
            raise SensitivePathsError(f"Falta el fichero de protección: {rules_path}")
        project = cls(**data)
        if not isinstance(project.opencode_version, str):
            project.opencode_version = ""
        elif project.opencode_version:
            try:
                project.opencode_version = normalize_opencode_version(
                    project.opencode_version
                )
            except OpenCodeVersionError:
                # Keep the invalid value visible so the instance can be repaired
                # from the UI instead of disappearing from the project list.
                pass
        return project

    def save(self, path: Path, *, save_sensitive_paths: bool = False):
        rules = list(dict.fromkeys(
            normalize_sensitive_path(value) for value in self.sensitive_paths
        ))
        if "" in rules:
            raise SensitivePathsError("No se permiten reglas sensibles vacías.")
        rules_path = path.with_name(SENSITIVE_PATHS_FILENAME)
        if rules_path.exists():
            existing_rules = read_sensitive_paths(rules_path)
        elif path.exists() and "sensitive_paths" not in json.loads(
            path.read_text(encoding="utf-8")
        ):
            raise SensitivePathsError(f"Falta el fichero de protección: {rules_path}")
        else:
            existing_rules = None
        if existing_rules is None or (save_sensitive_paths and existing_rules != rules):
            rules_path.write_text(
                "# Sensitive paths de ocode: una regla por línea, relativa a la working copy.\n"
                + "\n".join(rules) + "\n",
                encoding="utf-8",
            )
        data = self.to_dict()
        data.pop("sensitive_paths")
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @property
    def instance_key(self):
        raw = f"{self.name}-{self.branch}-{self.project_id[:8]}".lower()
        return re.sub(r"[^a-z0-9_.-]+", "-", raw).strip("-")[:63]

    @property
    def container_name(self):
        return f"ocode-{self.instance_key}"

    @property
    def data_volume(self):
        return f"ocode-{self.instance_key}-data"

    @property
    def state_volume(self):
        return f"ocode-{self.instance_key}-state"

    @property
    def build_volumes(self) -> list[tuple[str, str]]:
        """(relative directory, volume name) of each redirected build output.

        Each directory gets its own volume: Maven modules must not share build
        output. Names derive from the path so they stay stable when the list
        changes, and collisions get a numeric suffix.
        """
        result: list[tuple[str, str]] = []
        names: set[str] = set()

        for path in self.build_paths:
            slug = re.sub(r"[^a-z0-9_.-]+", "-", path.lower()).strip("-.")[:40]
            slug = slug.strip("-.") or "build"

            candidate = slug
            index = 1
            while f"ocode-{self.instance_key}-build-{candidate}" in names:
                index += 1
                candidate = f"{slug}-{index}"

            name = f"ocode-{self.instance_key}-build-{candidate}"
            names.add(name)
            result.append((path, name))

        return result


def next_project_port(default_port: int, assigned: set[int]) -> int:
    if not 1 <= default_port <= 65535:
        raise ValueError("Puerto predeterminado no válido (1–65535).")
    for port in range(default_port, 65536):
        if port not in assigned:
            return port
    raise ValueError("No quedan puertos disponibles desde el puerto predeterminado.")
