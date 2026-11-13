from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from . import docker, providers
from .model import (
    GlobalConfig,
    OpenCodeVersionError,
    ProjectConfig,
    SensitivePathsError,
    normalize_opencode_version,
)
from .paths import Paths

SENSITIVE_PERMISSION_PATTERNS = ['*src/main/resources/configuration/des*', '*src/main/resources/configuration/pre*', '*src/main/resources/configuration/prod*', '*src/main/resources/certs*', '*src/main/resources/application-desa.yaml*', '*src/main/resources/application-pre.yaml*', '*src/main/resources/application-pro.yaml*', '*src/main/resources/db/production*', '*src/main/resources/db/db.changelog-desa.yaml*', '*src/main/resources/db/db.changelog-pre.yaml*', '*src/main/resources/db/liquibase-desa.properties*', '*src/main/resources/db/liquibase-pre.properties*', '*k8s*']

BUILTIN_SENSITIVE_PATHS = ['**/src/main/resources/configuration/des', '**/src/main/resources/configuration/pre', '**/src/main/resources/configuration/prod', '**/src/main/resources/certs', '**/src/main/resources/application-desa.yaml', '**/src/main/resources/application-pre.yaml', '**/src/main/resources/application-pro.yaml', '**/src/main/resources/db/production', '**/src/main/resources/db/db.changelog-desa.yaml', '**/src/main/resources/db/db.changelog-pre.yaml', '**/src/main/resources/db/liquibase-desa.properties', '**/src/main/resources/db/liquibase-pre.properties', '**/k8s']


def _ensure_permission_section(
    text: str,
    section: str,
    next_section: str | None,
    patterns: list[str],
) -> str:
    marker = f'"{section}": {{'
    start = text.find(marker)
    if start < 0:
        return text

    end = (
        text.find(f'"{next_section}": {{', start)
        if next_section
        else len(text)
    )
    if end < 0:
        end = len(text)

    section_text = text[start:end]
    missing = [
        pattern
        for pattern in patterns
        if f'"{pattern}": "deny"' not in section_text
    ]
    if not missing:
        return text

    # Prefer inserting after a general allow if present. Otherwise insert
    # immediately after the section opening; this also works for deny-only
    # project permission blocks.
    allow_marker = '"*": "allow",'
    allow_pos = text.find(allow_marker, start, end)
    if allow_pos >= 0:
        insert_pos = allow_pos + len(allow_marker)
    else:
        insert_pos = start + len(marker)

    insertion = "".join(
        f'\n      "{pattern}": "deny",'
        for pattern in missing
    )
    return text[:insert_pos] + insertion + text[insert_pos:]


def _ensure_sensitive_permissions(path: Path) -> None:
    """Add ocode built-in sensitive path denies without replacing user config."""
    if not path.exists():
        return

    text = path.read_text(encoding="utf-8")
    original = text

    text = _ensure_permission_section(
        text, "read", "edit", SENSITIVE_PERMISSION_PATTERNS
    )
    text = _ensure_permission_section(
        text, "edit", "list", SENSITIVE_PERMISSION_PATTERNS
    )

    if '"list": {' in text:
        text = _ensure_permission_section(
            text, "list", "bash", SENSITIVE_PERMISSION_PATTERNS
        )

    if '"list": {' not in text:
        bash_marker = '    "bash": {'
        if bash_marker in text:
            lines = [
                '    "list": {',
                '      "*": "allow",',
            ]
            for pattern in SENSITIVE_PERMISSION_PATTERNS:
                lines.append(f'      "{pattern}": "deny",')
            lines[-1] = lines[-1].rstrip(",")
            lines.append("    },")
            block = "\n".join(lines) + "\n"
            text = text.replace(bash_marker, block + bash_marker, 1)

    if text != original:
        backup = path.with_name(path.name + ".bak-pre-0.4.3")
        if not backup.exists():
            shutil.copy2(path, backup)
        path.write_text(text, encoding="utf-8")



def _ensure_project_sensitive_permissions(path: Path) -> None:
    """Ensure project config reinforces built-in sensitive path restrictions."""
    if not path.exists():
        return

    text = path.read_text(encoding="utf-8")

    if '"permission"' not in text:
        permission = {
            "read": {
                pattern: "deny"
                for pattern in SENSITIVE_PERMISSION_PATTERNS
            },
            "edit": {
                pattern: "deny"
                for pattern in SENSITIVE_PERMISSION_PATTERNS
            },
            "list": {
                pattern: "deny"
                for pattern in SENSITIVE_PERMISSION_PATTERNS
            },
        }
        block = json.dumps(
            {"permission": permission},
            indent=2,
            ensure_ascii=False,
        )
        inner = block.strip()[1:-1].strip()

        close = text.rfind("}")
        if close >= 0:
            prefix = text[:close].rstrip()
            if not prefix.endswith("{") and not prefix.endswith(","):
                prefix += ","
            text = prefix + "\n  " + inner.replace("\n", "\n  ") + "\n}\n"
            path.write_text(text, encoding="utf-8")
            return

    _ensure_sensitive_permissions(path)


def _ensure_project_skills_source(path: Path) -> None:
    """Add the external per-project skill source without replacing user config."""
    text = path.read_text(encoding="utf-8")
    entry = "/etc/ocode/project-skills"
    marker = '"skills"'
    start = text.find(marker)
    if start >= 0:
        match = re.match(r"\s*:\s*\[", text[start + len(marker):])
        if not match:
            raise RuntimeError(f"El campo skills no es un array en {path}")
        opening = start + len(marker) + match.end() - 1
        depth = 0
        quoted = False
        escaped = False
        closing = -1
        for index in range(opening, len(text)):
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
                continue
            if char == '"':
                quoted = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing < 0:
            raise RuntimeError(f"Array skills sin cerrar en {path}")
        if entry in text[opening:closing]:
            return
        content = text[opening + 1:closing]
        separator = "" if not content.strip() or content.rstrip().endswith(",") else ","
        updated = content.rstrip() + separator + f'\n    "{entry}",\n  '
        text = text[:opening + 1] + updated + text[closing:]
    else:
        closing = text.rfind("}")
        if closing < 0:
            raise RuntimeError(f"JSONC inválido en {path}")
        prefix = text[:closing].rstrip()
        separator = "" if prefix.endswith("{") or prefix.endswith(",") else ","
        text = prefix + separator + f'\n  "skills": ["{entry}"],\n' + text[closing:]
    backup = path.with_name(path.name + ".bak-pre-project-skills")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text, encoding="utf-8")


def _ensure_external_edit_rules(path: Path) -> None:
    """Allow only the two ocode-managed project resources to be edited."""
    text = path.read_text(encoding="utf-8")
    rules = [
        {"action": action, "resource": resource, "effect": "allow"}
        for resource in ("/etc/ocode/project-skills/*", "/etc/ocode/project-AGENTS.md")
        for action in ("external_directory", "read", "edit")
    ]
    marker = '"permissions"'
    start = text.find(marker)
    if start >= 0:
        match = re.match(r"\s*:\s*\[", text[start + len(marker):])
        if not match:
            raise RuntimeError(f"El campo permissions no es un array en {path}")
        opening = start + len(marker) + match.end() - 1
        depth = 0
        quoted = escaped = False
        closing = -1
        for index in range(opening, len(text)):
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing < 0:
            raise RuntimeError(f"Array permissions sin cerrar en {path}")
        section = text[opening:closing]
        def already_present(rule):
            expression = (
                r'\{\s*"action"\s*:\s*"' + re.escape(rule["action"]) +
                r'"\s*,\s*"resource"\s*:\s*"' + re.escape(rule["resource"]) +
                r'"\s*,\s*"effect"\s*:\s*"allow"\s*\}'
            )
            return re.search(expression, section) is not None

        missing = [rule for rule in rules if not already_present(rule)]
        if not missing:
            return
        content = text[opening + 1:closing]
        separator = "" if not content.strip() or content.rstrip().endswith(",") else ","
        inserted = ",\n    ".join(json.dumps(rule, ensure_ascii=False) for rule in missing)
        text = text[:opening + 1] + content.rstrip() + separator + "\n    " + inserted + ",\n  " + text[closing:]
    else:
        closing = text.rfind("}")
        if closing < 0:
            raise RuntimeError(f"JSONC inválido en {path}")
        prefix = text[:closing].rstrip()
        separator = "" if prefix.endswith("{") or prefix.endswith(",") else ","
        serialized = ",\n    ".join(json.dumps(rule, ensure_ascii=False) for rule in rules)
        text = prefix + separator + '\n  "permissions": [\n    ' + serialized + "\n  ],\n" + text[closing:]
    backup = path.with_name(path.name + ".bak-pre-project-skills")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text, encoding="utf-8")

def _skip_jsonc_space(text: str, index: int) -> int:
    """Avanza espacios y comentarios JSONC sin interpretarlos."""
    length = len(text)
    while index < length:
        char = text[index]
        if char in " \t\r\n":
            index += 1
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end < 0 else end + 1
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
        else:
            break
    return index


def _jsonc_string_end(text: str, index: int) -> int:
    """Índice justo después de la cadena que empieza en `index`."""
    index += 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
        elif char == '"':
            return index + 1
        else:
            index += 1
    raise RuntimeError("Cadena sin cerrar")


def _jsonc_value_end(text: str, index: int) -> int:
    """Índice justo después del valor JSONC que empieza en `index`."""
    if index >= len(text):
        raise RuntimeError("JSONC incompleto")
    char = text[index]
    if char == '"':
        return _jsonc_string_end(text, index)
    if char in "{[":
        depth = 0
        while index < len(text):
            char = text[index]
            if char == '"':
                index = _jsonc_string_end(text, index)
                continue
            if text.startswith("//", index):
                end = text.find("\n", index)
                index = len(text) if end < 0 else end + 1
                continue
            if text.startswith("/*", index):
                end = text.find("*/", index + 2)
                index = len(text) if end < 0 else end + 2
                continue
            if char in "{[":
                depth += 1
            elif char in "}]":
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        raise RuntimeError("Objeto o array sin cerrar")
    while index < len(text) and text[index] not in ",}] \t\r\n":
        index += 1
    return index


def _jsonc_members(text: str, opening: int, closing: int):
    """Recorre los miembros directos del objeto `text[opening:closing + 1]`."""
    index = _skip_jsonc_space(text, opening + 1)
    while index < closing:
        if text[index] != '"':
            raise RuntimeError("Clave JSONC no válida")
        key_end = _jsonc_string_end(text, index)
        key = text[index + 1:key_end - 1]
        index = _skip_jsonc_space(text, key_end)
        if index >= closing or text[index] != ":":
            raise RuntimeError(f"Falta ':' tras la clave \"{key}\"")
        value_start = _skip_jsonc_space(text, index + 1)
        value_end = _jsonc_value_end(text, value_start)
        yield key, value_start, value_end
        index = _skip_jsonc_space(text, value_end)
        if index < closing and text[index] == ",":
            index = _skip_jsonc_space(text, index + 1)
        elif index < closing and text[index] != "}":
            raise RuntimeError(f"Falta ',' tras la clave \"{key}\"")


def _jsonc_root(text: str) -> tuple[int, int]:
    """Posiciones de las llaves que abren y cierran el objeto raíz."""
    opening = _skip_jsonc_space(text, 0)
    if opening >= len(text) or text[opening] != "{":
        raise RuntimeError("La configuración no es un objeto JSONC")
    closing = _jsonc_value_end(text, opening) - 1
    if text[closing] != "}":
        raise RuntimeError("Objeto raíz sin cerrar")
    return opening, closing


def _jsonc_key_span(
    text: str, opening: int, closing: int, key: str
) -> tuple[int, int] | None:
    """Tramo `(inicio, fin)` del valor de `key` en el objeto indicado."""
    found = None
    for name, value_start, value_end in _jsonc_members(text, opening, closing):
        if name != key:
            continue
        if found is not None:
            raise RuntimeError(f'La clave "{key}" está duplicada')
        found = (value_start, value_end)
    return found


def _jsonc_string_array(text: str, start: int, end: int) -> list[str] | None:
    """Contenido de un array de cadenas, o None si no lo es."""
    if start >= end or text[start] != "[":
        return None
    values = []
    index = _skip_jsonc_space(text, start + 1)
    while index < end - 1:
        if text[index] != '"':
            return None
        stop = _jsonc_string_end(text, index)
        values.append(text[index + 1:stop - 1])
        index = _skip_jsonc_space(text, stop)
        if index < end - 1 and text[index] == ",":
            index = _skip_jsonc_space(text, index + 1)
        elif index < end - 1:
            return None
    return values


def _jsonc_last_value_end(text: str, opening: int, closing: int) -> int | None:
    last = None
    for _, _, value_end in _jsonc_members(text, opening, closing):
        last = value_end
    return last


def _jsonc_insert_member(
    text: str,
    opening: int,
    closing: int,
    block: str,
    empty_suffix: str,
) -> str:
    """Añade `block` como último miembro sin tocar el resto del texto."""
    last = _jsonc_last_value_end(text, opening, closing)
    if last is None:
        return text[:opening + 1] + block + empty_suffix + text[opening + 1:]

    index = _skip_jsonc_space(text, last)
    if index < closing and text[index] == ",":
        following = _skip_jsonc_space(text, index + 1)
        if following >= closing:
            # Coma final: el bloque entra después y no necesita otra.
            return text[:index + 1] + block + text[index + 1:]
        return text[:index + 1] + block + "," + text[index + 1:]
    return text[:last] + "," + block + text[last:]


def provider_env_snippet() -> str:
    """Bloque que separa la clave de OpenCode Go de la de Console."""
    env_var = providers.PROVIDERS["opencode-go"].env_var
    return (
        '"providers": {\n'
        f'  "opencode-go": {{ "env": ["{env_var}"] }}\n'
        "}"
    )


def _with_provider_env(text: str) -> str:
    """Devuelve el JSONC con la variable propia de OpenCode Go, o el mismo texto."""
    env_var = providers.PROVIDERS["opencode-go"].env_var
    root = _jsonc_root(text)
    span = _jsonc_key_span(text, *root, "providers")

    if span is None:
        block = (
            "\n  // ocode: OpenCode Go usa su propia variable para no compartir\n"
            "  // la clave con OpenCode Console (opencode/<modelo>).\n"
            '  "providers": {\n'
            f'    "opencode-go": {{ "env": ["{env_var}"] }}\n'
            "  }"
        )
        return _jsonc_insert_member(text, *root, block, "\n")

    if text[span[0]] != "{":
        raise RuntimeError('El campo "providers" no es un objeto')
    provider_object = (span[0], span[1] - 1)

    entry = _jsonc_key_span(text, *provider_object, "opencode-go")
    if entry is None:
        block = f'\n    "opencode-go": {{ "env": ["{env_var}"] }}'
        return _jsonc_insert_member(text, *provider_object, block, "\n  ")

    if text[entry[0]] != "{":
        raise RuntimeError('El campo "providers.opencode-go" no es un objeto')

    env = _jsonc_key_span(text, entry[0], entry[1] - 1, "env")
    if env is None:
        block = f'\n      "env": ["{env_var}"]'
        return _jsonc_insert_member(text, entry[0], entry[1] - 1, block, "\n    ")

    if _jsonc_string_array(text, *env) == [env_var]:
        return text
    return text[:env[0]] + f'["{env_var}"]' + text[env[1]:]


def _ensure_provider_env(path: Path) -> str | None:
    """Añade la variable propia de Go a la config, respetando la del usuario.

    Devuelve un mensaje cuando la estructura existente no se puede fusionar con
    seguridad. En ese caso el fichero no se modifica.
    """
    if not path.exists():
        return None

    try:
        text = path.read_text(encoding="utf-8")
        updated = _with_provider_env(text)
    except (OSError, UnicodeError) as exc:
        return f"No se pudo leer {path.name}: {exc}"
    except RuntimeError as exc:
        return f"{path.name}: {exc}"

    if updated == text:
        return None

    try:
        backup = path.with_name(path.name + ".bak-pre-0.12.2")
        if not backup.exists():
            shutil.copy2(path, backup)
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return f"No se pudo actualizar {path.name}: {exc}"
    return None


def provider_env_status(path: Path) -> tuple[bool, str]:
    """Comprueba que la config global separa la clave de Go de la de Console."""
    env_var = providers.PROVIDERS["opencode-go"].env_var
    if not path.exists():
        return False, f"No existe {path}."
    try:
        text = path.read_text(encoding="utf-8")
        root = _jsonc_root(text)
        span = _jsonc_key_span(text, *root, "providers")
        if span is None:
            return False, 'La configuración no declara el bloque "providers".'
        if text[span[0]] != "{":
            return False, 'El campo "providers" no es un objeto.'
        entry = _jsonc_key_span(text, span[0], span[1] - 1, "opencode-go")
        if entry is None:
            return False, 'Falta la entrada "providers.opencode-go".'
        if text[entry[0]] != "{":
            return False, 'El campo "providers.opencode-go" no es un objeto.'
        env = _jsonc_key_span(text, entry[0], entry[1] - 1, "env")
        if env is None:
            return False, 'Falta "providers.opencode-go.env".'
        if _jsonc_string_array(text, *env) != [env_var]:
            return False, (
                f'"providers.opencode-go.env" debe ser exactamente ["{env_var}"].'
            )
        return True, ""
    except (OSError, UnicodeError) as exc:
        return False, f"No se pudo leer {path.name}: {exc}"
    except RuntimeError as exc:
        return False, f"{path.name}: {exc}"


def project_overrides_providers(path: Path) -> bool:
    """True si un opencode.jsonc de proyecto declara `providers`."""
    try:
        text = path.read_text(encoding="utf-8")
        root = _jsonc_root(text)
    except (OSError, UnicodeError, RuntimeError):
        return False
    try:
        return _jsonc_key_span(text, *root, "providers") is not None
    except RuntimeError:
        return False


def _project_folder_base(name: str, branch: str) -> str:
    raw = f"{name}-{branch}".strip()
    # Windows-invalid filename characters and control chars.
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", raw)
    safe = re.sub(r"\s+", " ", safe).strip().rstrip(". ")
    return safe or "project"


def _available_project_folder(projects_dir: Path, base: str, current: Path | None = None) -> Path:
    candidate = projects_dir / base
    if current is not None and candidate.resolve() == current.resolve():
        return candidate
    if not candidate.exists():
        return candidate

    index = 2
    while True:
        candidate = projects_dir / f"{base}-{index}"
        if current is not None and candidate.resolve() == current.resolve():
            return candidate
        if not candidate.exists():
            return candidate
        index += 1


class Store:
    def __init__(self, paths: Paths):
        self.paths = paths

    def initialized(self) -> bool:
        return self.paths.global_config.exists()

    def _require_default(self, path: Path) -> None:
        if not path.exists():
            raise RuntimeError(f"Falta un fichero distribuido por ocode: {path}")

    def _migrate_project_directories(self) -> None:
        """Rename legacy UUID folders to Proyecto-Rama without changing project ids."""
        if not self.paths.projects_dir.exists():
            return

        directories = [
            p for p in self.paths.projects_dir.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        ]

        for directory in directories:
            try:
                project = ProjectConfig.from_file(directory / "project.json")
            except SensitivePathsError:
                raise
            except Exception:
                continue

            desired_base = _project_folder_base(project.name, project.branch)
            if directory.name.casefold() == desired_base.casefold():
                continue

            target = _available_project_folder(
                self.paths.projects_dir,
                desired_base,
                current=directory,
            )

            if target.resolve() != directory.resolve():
                directory.rename(target)

    def initialize(self) -> GlobalConfig:
        self.paths.ensure_dirs()
        providers.migrate_legacy_keys(self.paths)
        self._migrate_project_directories()

        for path in (
            self.paths.default_agents,
            self.paths.default_opencode_config,
            self.paths.default_project_agents,
            self.paths.default_project_config,
        ):
            self._require_default(path)

        if not self.paths.global_config.exists():
            cfg = GlobalConfig.defaults()
            self.save_global(cfg)
        else:
            cfg = self.load_global()
            # Persist version-added defaults (e.g. new Java profiles) without
            # overwriting user-selected values.
            self.save_global(cfg)

        # Los defaults solo inicializan. Nunca pisan personalizaciones existentes.
        if not self.paths.opencode_config.exists():
            shutil.copy2(self.paths.default_opencode_config, self.paths.opencode_config)

        if not self.paths.global_agents.exists():
            shutil.copy2(self.paths.default_agents, self.paths.global_agents)

        self.paths.global_skills.mkdir(parents=True, exist_ok=True)
        self._install_default_skills()
        _ensure_project_skills_source(self.paths.opencode_config)
        _ensure_external_edit_rules(self.paths.opencode_config)
        _ensure_provider_env(self.paths.opencode_config)

        # Keep user configuration, but append mandatory ocode sensitive denies
        # if this installation predates them.
        _ensure_sensitive_permissions(self.paths.opencode_config)

        # Normalize legacy built-ins and add newly introduced built-ins.
        # User-added entries are preserved.
        legacy_defaults = {
            "src/main/resources/configuration/des": "**/src/main/resources/configuration/des",
            "src/main/resources/configuration/pre": "**/src/main/resources/configuration/pre",
            "src/main/resources/configuration/prod": "**/src/main/resources/configuration/prod",
            "src/main/resources/certs": "**/src/main/resources/certs",
        }

        for project in self.list_projects():
            self.paths.project_skills(project.project_id).mkdir(parents=True, exist_ok=True)
            changed = not self.paths.project_sensitive_paths(project.project_id).exists()
            if not project.opencode_version:
                detected = docker.container_package_version(
                    cfg, project.container_name
                )
                try:
                    project.opencode_version = (
                        normalize_opencode_version(detected)
                        if detected
                        else cfg.opencode_version
                    )
                except OpenCodeVersionError:
                    project.opencode_version = cfg.opencode_version
                changed = True
            migrated: list[str] = []

            for value in project.sensitive_paths:
                normalized = legacy_defaults.get(value, value)
                if normalized != value:
                    changed = True
                if normalized not in migrated:
                    migrated.append(normalized)

            # Defaults are applied only during legacy migration. The editable
            # file is authoritative, including intentionally removed rules.
            if not self.paths.project_sensitive_paths(project.project_id).exists():
                for value in BUILTIN_SENSITIVE_PATHS:
                    if value not in migrated:
                        migrated.append(value)
                        changed = True

            if changed:
                project.sensitive_paths = migrated
                self.save_project(project)

            _ensure_project_sensitive_permissions(
                self.paths.project_opencode_config(project.project_id)
            )

        return cfg

    def _install_default_skills(self) -> None:
        """Copy bundled global skills without replacing host customizations."""
        if not self.paths.default_skills.is_dir():
            return

        for source in sorted(self.paths.default_skills.glob("*/SKILL.md")):
            if not source.is_file():
                continue
            target = self.paths.global_skills / source.parent.name / "SKILL.md"
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def load_global(self) -> GlobalConfig:
        data = json.loads(self.paths.global_config.read_text(encoding="utf-8"))
        return GlobalConfig.from_dict(data)

    def save_global(self, cfg: GlobalConfig) -> None:
        self.paths.ensure_dirs()
        self.paths.global_config.write_text(
            json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_projects(self) -> list[ProjectConfig]:
        projects: list[ProjectConfig] = []
        if not self.paths.projects_dir.exists():
            return projects

        for path in self.paths.projects_dir.glob("*/project.json"):
            try:
                projects.append(ProjectConfig.from_file(path))
            except SensitivePathsError:
                raise
            except Exception:
                continue

        return sorted(projects, key=lambda p: (p.name.lower(), p.branch.lower()))

    def get_project(self, project_id: str) -> ProjectConfig:
        return ProjectConfig.from_file(self.paths.project_json(project_id))

    def create_project(
        self,
        *,
        name: str,
        branch: str,
        workspace: str,
        runtime: str,
        default_mode: str,
        port: int,
        maven_repository: str = "",
        build_paths: list[str] | None = None,
        opencode_version: str | None = None,
    ) -> ProjectConfig:
        if not 1 <= port <= 65535:
            raise ValueError("Puerto no válido (1–65535).")
        project_id = str(uuid.uuid4())
        global_cfg = self.load_global()
        project = ProjectConfig(
            project_id=project_id,
            name=name,
            branch=branch,
            workspace=str(Path(workspace).resolve()),
            runtime=runtime,
            default_mode=default_mode,
            port=port,
            maven_repository=maven_repository,
            build_paths=list(build_paths or []),
            web_initial_preferences={
                "show_agent": global_cfg.web_show_agent,
                "color_scheme": global_cfg.web_color_scheme,
            },
            opencode_version=normalize_opencode_version(
                opencode_version or global_cfg.opencode_version
            ),
            sensitive_paths=list(BUILTIN_SENSITIVE_PATHS),
        )

        folder_base = _project_folder_base(name, branch)
        project_dir = _available_project_folder(
            self.paths.projects_dir,
            folder_base,
        )
        project_dir.mkdir(parents=True, exist_ok=False)
        (project_dir / "skills").mkdir()

        project.save(project_dir / "project.json")

        shutil.copy2(
            self.paths.default_project_agents,
            project_dir / "AGENTS.md",
        )
        shutil.copy2(
            self.paths.default_project_config,
            project_dir / "opencode.jsonc",
        )
        return project

    def save_project(self, project: ProjectConfig, *, save_sensitive_paths: bool = False) -> None:
        project.save(
            self.paths.project_json(project.project_id),
            save_sensitive_paths=save_sensitive_paths,
        )

    def delete_project_config(self, project_id: str) -> None:
        directory = self.paths.project_dir(project_id)
        if directory.exists():
            shutil.rmtree(directory)
