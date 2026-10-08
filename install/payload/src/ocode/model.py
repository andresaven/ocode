from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import re


OPENCODE_VERSION = "2.0.24"
OPENCODE_IMAGE = f"ghcr.io/anomalyco/opencode:{OPENCODE_VERSION}"

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


@dataclass
class RuntimeProfile:
    name: str
    image: str
    java: str | None = None
    buildable: bool = False


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
    opencode_image: str = OPENCODE_IMAGE
    security_no_new_privileges: bool = True
    cap_drop_all: bool = True
    container_runtime: ContainerRuntime = field(default_factory=ContainerRuntime)
    runtimes: dict[str, RuntimeProfile] = field(default_factory=dict)

    @classmethod
    def defaults(cls):
        return cls(
            opencode_image=OPENCODE_IMAGE,
            container_runtime=ContainerRuntime(kind="unset", command=[]),
            runtimes={
                "java8": RuntimeProfile(
                    "java8", f"ocode-runtime:{OPENCODE_VERSION}-java8", "8", True
                ),
                "java11": RuntimeProfile(
                    "java11", f"ocode-runtime:{OPENCODE_VERSION}-java11", "11", True
                ),
                "java17": RuntimeProfile(
                    "java17", f"ocode-runtime:{OPENCODE_VERSION}-java17", "17", True
                ),
                "java21": RuntimeProfile(
                    "java21", f"ocode-runtime:{OPENCODE_VERSION}-java21", "21", True
                ),
            },
        )

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        defaults = cls.defaults()
        # Built-in Java runtimes are managed by ocode and tied to the
        # OpenCode version. This intentionally replaces legacy tags such as
        # `ocode-runtime:java8`, forcing a rebuild on OpenCode upgrades.
        runtimes = dict(defaults.runtimes)
        runtimes.update({
            k: RuntimeProfile(**v)
            for k, v in data.get("runtimes", {}).items()
            if k not in defaults.runtimes and k != "base"
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
            # OpenCode base image is managed by this ocode release.
            opencode_image=defaults.opencode_image,
            security_no_new_privileges=bool(
                data.get("security_no_new_privileges", True)
            ),
            cap_drop_all=bool(data.get("cap_drop_all", True)),
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
        return cls(**data)

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
